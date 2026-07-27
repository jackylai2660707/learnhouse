#!/usr/bin/env python3
"""Repair coding-challenge content with AI generation verified by the executor.

Two defects make the stored challenges ungradable:

* almost every challenge has an empty ``solution_code`` so "reveal solution"
  can never unlock (``challenges.py`` requires ``bool(challenge.solution_code)``);
* most challenges have no ``HIDDEN`` test, which makes ``run_visible_tests``
  and ``submit_challenge`` execute the exact same suite. A learner can then
  hardcode the visible expected output and be marked as passing.

This script asks the configured chat model for a reference solution plus
additional hidden tests, then refuses to persist anything until the executor
has proven the generated content. Every candidate must clear four gates:

1. the reference solution passes every pre-existing test;
2. the reference solution reproduces the model's claimed hidden expectations
   (the model's solution and its expectations must agree with each other);
3. the original ``starter_code`` does not already pass the hidden test;
4. a synthesized "hardcode the visible output" cheat program fails the hidden
   test — which is exactly the attack the missing hidden tests allowed.

Hidden tests that fail gates 3 or 4 are dropped as worthless rather than
stored. A challenge is only written when at least one hidden test survives,
unless the challenge has no input variance at all (every test has an empty
stdin), in which case no stdin-based hidden test can discriminate and only the
solution and title are repaired.

Usage (inside the API container)::

    /app/api/.venv/bin/python scripts/repair_challenge_content.py --verify-rewrite-manifest
    /app/api/.venv/bin/python scripts/repair_challenge_content.py --apply-rewrite-manifest
    /app/api/.venv/bin/python scripts/repair_challenge_content.py --quality-audit
    /app/api/.venv/bin/python scripts/repair_challenge_content.py --dry-run --limit 5
    /app/api/.venv/bin/python scripts/repair_challenge_content.py --limit 10 --report /tmp/repair.json
    /app/api/.venv/bin/python scripts/repair_challenge_content.py --audit

Re-running is safe: repaired challenges carry a ``content_repair`` marker in
their metadata and are skipped unless ``--force`` is given. ``--audit`` makes no
model calls and writes nothing; it just re-proves every stored solution and
re-attempts the cheat against the current test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from sqlmodel import col, select

from src.core.events.database import _async_session_factory
from src.db.coding_challenges import (
    CodingChallenge,
    CodingChallengeProgress,
    CodingChallengeSubmission,
    CodingChallengeTest,
    CodingChallengeTestVisibility,
)
from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.routers.code_execution import (
    PYTHON3_LANGUAGE_ID,
    SQL_LANGUAGE_ID,
    _get_judge0_config,
)

try:  # Preview-only pseudo language; absent on deployments without HTML preview.
    from src.routers.code_execution import HTML_PREVIEW_LANGUAGE_ID
except ImportError:  # pragma: no cover - depends on deployed revision
    HTML_PREVIEW_LANGUAGE_ID = 1000
from src.services.ai.base import AIProviderError, get_gemini_client
from src.services.coding_challenges.challenges import (
    MAX_TEST_VALUE_BYTES,
    _normalize_output,
    sanitize_coding_challenge_content,
    sync_coding_challenges_for_activity,
)
from src.services.judge0 import Judge0ServiceError, submit_judge0
from src.services.ai.rag.index_lock import (
    declare_course_index_write_set,
    install_indexed_content_write_lock,
)

# This standalone writer mutates Activity.content. Keep the database-wide RAG
# index serialization contract explicit even though database.py installs it at
# the central Session factory boundary as well.
install_indexed_content_write_lock()


logger = logging.getLogger("repair_challenge_content")

REPAIR_VERSION = 1
REPAIR_METADATA_KEY = "content_repair"

JS_LANGUAGE_ID = 63

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_METADATA_KEY = "challenge_content_rewrite"
MANIFEST_DIR = Path(__file__).resolve().parent / "challenge_content"
DEFAULT_REWRITE_MANIFEST = MANIFEST_DIR / "rewrite_manifest.v1.json"
EXPECTED_REWRITE_INVENTORY = {JS_LANGUAGE_ID: 40, PYTHON3_LANGUAGE_ID: 11}
EXPECTED_REWRITE_TOTAL = sum(EXPECTED_REWRITE_INVENTORY.values())
FINGERPRINT_PREFIX = "sha256:"

LANGUAGE_NAMES = {
    PYTHON3_LANGUAGE_ID: "Python 3",
    JS_LANGUAGE_ID: "JavaScript (Node.js)",
    SQL_LANGUAGE_ID: "SQL (SQLite)",
}
# Titles that carry no information about what the exercise actually asks for.
GENERIC_TITLES = {
    "",
    "python",
    "python3",
    "python 3",
    "javascript",
    "javascript (node)",
    "javascript (node.js)",
    "node",
    "nodejs",
    "sql",
    "sql (sqlite)",
    "sqlite",
    "coding challenge",
}

MIN_HIDDEN_TESTS = 2
MAX_HIDDEN_TESTS = 4
JUDGE0_ACCEPTED_STATUS_ID = 3
EXECUTOR_TIMEOUT = httpx.Timeout(60.0, connect=5.0, write=10.0, pool=5.0)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _language_name(language_id: int) -> str:
    return LANGUAGE_NAMES.get(language_id, f"language {language_id}")


def _is_generic_title(title: str) -> bool:
    return (title or "").strip().lower() in GENERIC_TITLES


def _extract_json_object(raw: str) -> dict:
    """Tolerate fenced or chatty JSON the way the AI assignment service does."""
    cleaned = (raw or "").strip()
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        if end != -1:
            cleaned = cleaned[start:end].strip()
    elif "```" in cleaned:
        start = cleaned.find("```") + 3
        end = cleaned.find("```", start)
        if end != -1:
            cleaned = cleaned[start:end].strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        if start != -1:
            cleaned = cleaned[start:]
    if not cleaned.endswith("}"):
        end = cleaned.rfind("}")
        if end != -1:
            cleaned = cleaned[: end + 1]
    return json.loads(cleaned)


# --------------------------------------------------------------------------
# Executor plumbing
# --------------------------------------------------------------------------


class ExecutorRunner:
    """Runs source code against the configured Judge0-compatible executor."""

    def __init__(self, concurrency: int) -> None:
        self._config = _get_judge0_config()
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._client: httpx.AsyncClient | None = None
        self.calls = 0

    async def __aenter__(self) -> "ExecutorRunner":
        self._client = httpx.AsyncClient(timeout=EXECUTOR_TIMEOUT)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def run(self, language_id: int, source_code: str, stdin: str) -> dict:
        """Return {"accepted": bool, "stdout": str, "status": dict, "error": str|None}."""
        async with self._semaphore:
            self.calls += 1
            try:
                result = await submit_judge0(
                    self._config,
                    language_id=language_id,
                    source_code=source_code,
                    stdin=stdin,
                    client=self._client,
                )
            except Judge0ServiceError as exc:
                return {"accepted": False, "stdout": "", "status": {}, "error": exc.code}
        status_obj = result.get("status") or {}
        return {
            "accepted": status_obj.get("id") == JUDGE0_ACCEPTED_STATUS_ID,
            "stdout": _normalize_output(result.get("stdout")),
            "stderr": (result.get("stderr") or "")[:400],
            "status": status_obj,
            "error": None,
        }


def _cheat_source(language_id: int, visible_expected: str) -> str | None:
    """Build the "ignore stdin, print the visible answer" program a learner would write.

    A hidden test that this program still passes adds no grading value, which is
    precisely the hole the missing hidden tests left open.
    """
    literal = json.dumps(visible_expected, ensure_ascii=True)
    if language_id == PYTHON3_LANGUAGE_ID:
        return f"import sys\nsys.stdout.write({literal})\n"
    if language_id == JS_LANGUAGE_ID:
        return f"process.stdout.write({literal});\n"
    return None


# --------------------------------------------------------------------------
# Deterministic rewrite manifests
# --------------------------------------------------------------------------


class RewriteManifestError(RuntimeError):
    """A fail-closed manifest, inventory, mapping, or verification failure."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return FINGERPRINT_PREFIX + hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _manifest_checksum(manifest: dict[str, Any]) -> str:
    return _sha256(manifest)


def _entry_checksum(entry: dict[str, Any]) -> str:
    return _sha256(entry)


def _source_fingerprint(
    course: Course,
    activity: Activity,
    challenge: CodingChallenge,
    tests: Sequence[CodingChallengeTest],
) -> str:
    """Hash rewrite-relevant state without exposing protected content in logs.

    Stable ownership identifiers are part of the digest, so a manifest entry
    cannot be replayed against an otherwise identical challenge in a different
    course, activity, or block. Volatile timestamps and database integer ids
    are deliberately excluded.
    """
    source_node, is_legacy = _select_activity_source_node(
        activity.content,
        challenge=challenge,
    )
    attrs = (
        source_node.get("attrs")
        if isinstance(source_node.get("attrs"), dict)
        else {}
    )
    node_snapshot = {
        "type": source_node.get("type"),
        "identity": {
            "id": attrs.get("id"),
            "blockId": attrs.get("blockId"),
            "challengeUuid": attrs.get("challengeUuid"),
            "challenge_uuid": attrs.get("challenge_uuid"),
        },
        "language": {
            "languageId": attrs.get("languageId"),
            "language_id": attrs.get("language_id"),
            "language": attrs.get("language"),
        },
        # A legacy codeBlock has no durable challenge identity in its attrs.
        # Hash its complete source node so even a one-character teacher edit
        # invalidates the reviewed conversion manifest.
        "legacy_source_node": source_node if is_legacy else None,
    }
    payload = {
        "identity": {
            "course_uuid": course.course_uuid,
            "activity_uuid": activity.activity_uuid,
            "challenge_uuid": challenge.challenge_uuid,
            "block_id": challenge.block_id,
        },
        "challenge": {
            "language_id": challenge.language_id,
            "title": challenge.title,
            "description": challenge.description,
            "starter_code": challenge.starter_code,
            "solution_code": challenge.solution_code,
            "solution_visibility": challenge.solution_visibility.value,
            "hints": challenge.hints,
            "difficulty": challenge.difficulty,
            "time_limit_ms": challenge.time_limit_ms,
            "sqlite_db_path": challenge.sqlite_db_path,
            "additional_files": challenge.additional_files,
            "external_id": challenge.external_id,
            "source_template_uuid": challenge.source_template_uuid,
            "tags": challenge.tags,
        },
        "activity_node": node_snapshot,
        "tests": [
            {
                "test_uuid": test.test_uuid,
                "label": test.label,
                "stdin": test.stdin,
                "expected_stdout": test.expected_stdout,
                "visibility": test.visibility.value,
                "order": test.order,
            }
            for test in sorted(tests, key=lambda item: (item.order, item.test_uuid))
        ],
    }
    return _sha256(payload)


def _manifest_identity(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    identity = entry["identity"]
    return (
        identity["course_uuid"],
        identity["activity_uuid"],
        identity["challenge_uuid"],
        identity["block_id"],
    )


def _iter_document_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_document_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_document_nodes(child)


def _matching_block_nodes(
    content: Any,
    *,
    challenge_uuid: str,
    block_id: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for node in _iter_document_nodes(content):
        if node.get("type") != "blockCode":
            continue
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        if attrs.get("challengeUuid") == challenge_uuid or attrs.get("id") == block_id:
            matches.append(node)
    return matches


def _legacy_code_nodes(content: Any) -> list[dict[str, Any]]:
    return [
        node
        for node in _iter_document_nodes(content)
        if node.get("type") == "codeBlock"
    ]


def _select_activity_source_node(
    content: Any,
    *,
    challenge: CodingChallenge,
) -> tuple[dict[str, Any], bool]:
    matches = _matching_block_nodes(
        content,
        challenge_uuid=challenge.challenge_uuid,
        block_id=challenge.block_id,
    )
    if len(matches) > 1:
        raise RewriteManifestError(
            f"multiple blockCode mappings for {challenge.challenge_uuid}"
        )
    if matches:
        return matches[0], False
    legacy = _legacy_code_nodes(content)
    if len(legacy) != 1:
        raise RewriteManifestError(
            f"legacy mapping for {challenge.challenge_uuid} requires exactly one codeBlock"
        )
    return legacy[0], True


def _description_has_io_contract(description: str) -> bool:
    return bool(
        re.search(r"(?:輸入|input)\s*[：:]", description, flags=re.IGNORECASE)
        and re.search(r"(?:輸出|output)\s*[：:]", description, flags=re.IGNORECASE)
    )


def _validate_rewrite_entry_shape(entry: Any, index: int) -> list[str]:
    prefix = f"rewrites[{index}]"
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"{prefix} must be an object"]
    if set(entry) != {"identity", "source_fingerprint", "rewrite"}:
        errors.append(f"{prefix} has missing or unknown top-level fields")
    identity = entry.get("identity")
    required_identity = {
        "course_uuid",
        "activity_uuid",
        "challenge_uuid",
        "block_id",
        "language_id",
    }
    if not isinstance(identity, dict) or set(identity) != required_identity:
        errors.append(f"{prefix}.identity has missing or unknown fields")
    else:
        for key in required_identity - {"language_id"}:
            if not isinstance(identity.get(key), str) or not identity[key].strip():
                errors.append(f"{prefix}.identity.{key} must be a non-empty string")
        if identity.get("language_id") not in EXPECTED_REWRITE_INVENTORY:
            errors.append(f"{prefix}.identity.language_id is not a rewrite language")
    fingerprint = entry.get("source_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
        errors.append(f"{prefix}.source_fingerprint must be a sha256 digest")

    rewrite = entry.get("rewrite")
    required_rewrite = {
        "title",
        "description",
        "starter_code",
        "solution_code",
        "hints",
        "difficulty",
        "tests",
    }
    if not isinstance(rewrite, dict) or set(rewrite) != required_rewrite:
        errors.append(f"{prefix}.rewrite has missing or unknown fields")
        return errors
    for key in ("title", "description", "starter_code", "solution_code", "difficulty"):
        if not isinstance(rewrite.get(key), str) or not rewrite[key].strip():
            errors.append(f"{prefix}.rewrite.{key} must be a non-empty string")
    if isinstance(rewrite.get("description"), str) and not _description_has_io_contract(
        rewrite["description"]
    ):
        errors.append(f"{prefix}.rewrite.description must document 輸入： and 輸出：")
    if not isinstance(rewrite.get("hints"), list) or not all(
        isinstance(item, str) and item.strip() for item in rewrite.get("hints", [])
    ):
        errors.append(f"{prefix}.rewrite.hints must contain only non-empty strings")

    tests = rewrite.get("tests")
    if not isinstance(tests, list):
        errors.append(f"{prefix}.rewrite.tests must be an array")
        return errors
    uuids: set[str] = set()
    visible = 0
    hidden = 0
    nonempty_inputs: set[str] = set()
    outputs: set[str] = set()
    for test_index, test in enumerate(tests):
        test_prefix = f"{prefix}.rewrite.tests[{test_index}]"
        required_test = {
            "test_uuid",
            "label",
            "stdin",
            "expected_stdout",
            "visibility",
            "order",
        }
        if not isinstance(test, dict) or set(test) != required_test:
            errors.append(f"{test_prefix} has missing or unknown fields")
            continue
        for key in ("test_uuid", "label", "stdin", "expected_stdout"):
            if not isinstance(test.get(key), str):
                errors.append(f"{test_prefix}.{key} must be a string")
        test_uuid = test.get("test_uuid")
        if isinstance(test_uuid, str):
            if not test_uuid.strip() or test_uuid in uuids:
                errors.append(f"{test_prefix}.test_uuid must be non-empty and unique")
            uuids.add(test_uuid)
        visibility = test.get("visibility")
        if visibility == CodingChallengeTestVisibility.VISIBLE.value:
            visible += 1
        elif visibility == CodingChallengeTestVisibility.HIDDEN.value:
            hidden += 1
        else:
            errors.append(f"{test_prefix}.visibility must be visible or hidden")
        if test.get("order") != test_index:
            errors.append(f"{test_prefix}.order must equal its array index")
        stdin = test.get("stdin")
        expected = test.get("expected_stdout")
        if isinstance(stdin, str) and stdin.strip():
            nonempty_inputs.add(_normalize_output(stdin))
        if isinstance(expected, str):
            outputs.add(_normalize_output(expected))
        if isinstance(stdin, str) and len(stdin.encode("utf-8")) > MAX_TEST_VALUE_BYTES:
            errors.append(f"{test_prefix}.stdin is too large")
        if isinstance(expected, str) and len(expected.encode("utf-8")) > MAX_TEST_VALUE_BYTES:
            errors.append(f"{test_prefix}.expected_stdout is too large")
    if visible < 1:
        errors.append(f"{prefix} must have at least one visible test")
    if hidden < 2:
        errors.append(f"{prefix} must have at least two hidden tests")
    if len(nonempty_inputs) < 2:
        errors.append(f"{prefix} must have at least two distinct non-empty inputs")
    if len(outputs) < 2:
        errors.append(f"{prefix} must have output variance")
    return errors


def validate_rewrite_manifest(manifest: Any) -> dict[str, Any]:
    """Validate the strict v1 shape and the 40 JS + 11 Python inventory."""
    if not isinstance(manifest, dict):
        raise RewriteManifestError("manifest must be a JSON object")
    if set(manifest) != {"schema_version", "inventory", "rewrites"}:
        raise RewriteManifestError("manifest has missing or unknown top-level fields")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RewriteManifestError("unsupported manifest schema_version")
    inventory = manifest.get("inventory")
    expected_inventory = {
        "total": EXPECTED_REWRITE_TOTAL,
        "languages": {str(key): value for key, value in EXPECTED_REWRITE_INVENTORY.items()},
    }
    if inventory != expected_inventory:
        raise RewriteManifestError("manifest inventory must be exactly 40 JavaScript and 11 Python")
    rewrites = manifest.get("rewrites")
    if not isinstance(rewrites, list) or len(rewrites) != EXPECTED_REWRITE_TOTAL:
        raise RewriteManifestError(f"manifest must contain exactly {EXPECTED_REWRITE_TOTAL} rewrites")

    errors: list[str] = []
    identities: set[tuple[str, str, str, str]] = set()
    language_counts: Counter[int] = Counter()
    for index, entry in enumerate(rewrites):
        errors.extend(_validate_rewrite_entry_shape(entry, index))
        if isinstance(entry, dict) and isinstance(entry.get("identity"), dict):
            identity = entry["identity"]
            if all(isinstance(identity.get(key), str) for key in (
                "course_uuid", "activity_uuid", "challenge_uuid", "block_id"
            )):
                identity_key = _manifest_identity(entry)
                if identity_key in identities:
                    errors.append(f"rewrites[{index}] duplicates an ownership identity")
                identities.add(identity_key)
            if isinstance(identity.get("language_id"), int):
                language_counts[identity["language_id"]] += 1
    if dict(language_counts) != EXPECTED_REWRITE_INVENTORY:
        errors.append("rewrite language counts must be exactly 40 JavaScript and 11 Python")
    if errors:
        preview = "; ".join(errors[:10])
        suffix = f" (+{len(errors) - 10} more)" if len(errors) > 10 else ""
        raise RewriteManifestError(preview + suffix)
    return manifest


def load_rewrite_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RewriteManifestError(f"cannot load rewrite manifest: {exc}") from exc
    return validate_rewrite_manifest(payload)


# --------------------------------------------------------------------------
# AI generation
# --------------------------------------------------------------------------


def _build_prompt(
    challenge: CodingChallenge,
    tests: list[CodingChallengeTest],
    *,
    want_hidden_tests: bool,
    feedback: str | None,
) -> str:
    existing = [
        {
            "label": test.label,
            "stdin": test.stdin,
            "expected_output": test.expected_stdout,
            "visibility": test.visibility.value,
        }
        for test in tests
    ]
    title_rule = (
        "title：目前標題只是語言名稱，請改成具體描述這題在做什麼的繁體中文標題（8-20 字），"
        "不要出現語言名稱。"
        if _is_generic_title(challenge.title)
        else "title：目前標題已經足夠具體，請原樣回傳。"
    )
    if want_hidden_tests:
        hidden_rule = (
            f"hidden_tests：產生 {MIN_HIDDEN_TESTS} 到 {MAX_HIDDEN_TESTS} 個「新的」隱藏測試。"
            "每個隱藏測試的 stdin 必須和上面所有既有測試的 stdin 都不同，"
            "而且要針對邊界情況（例如 0、負數、最大/最小值、空白、重複元素、單一元素、較大的輸入）。"
            "expected_output 必須是參考解在該 stdin 下真正會印出的內容，不可以憑空編造。"
            "關鍵：把答案寫死（不讀 stdin、直接印出可見測試的輸出）的作弊程式必須無法通過這些隱藏測試。"
        )
    else:
        hidden_rule = (
            "hidden_tests：這一題所有測試的 stdin 都是空的，沒有輸入變化，"
            "無法用 stdin 區分作弊解，請回傳空陣列 []。"
        )
    feedback_block = f"\n上一次嘗試失敗的原因，請修正：\n{feedback}\n" if feedback else ""

    return f"""你是一位程式教學內容的審稿工程師。請修復下面這一題程式練習的內容。

語言：{_language_name(challenge.language_id)}（Judge0 language_id={challenge.language_id}）
目前標題：{challenge.title!r}
題目說明：
{challenge.description}

學生看到的起始程式碼（starter_code）：
```
{challenge.starter_code}
```

既有測試（不可修改，參考解必須全部通過）：
{json.dumps(existing, ensure_ascii=False, indent=2)}
{feedback_block}
請只輸出一個 JSON 物件，欄位如下：
{{
  "title": "字串",
  "solution_code": "字串，完整可直接執行的參考解",
  "hidden_tests": [
    {{"label": "字串", "stdin": "字串", "expected_output": "字串"}}
  ]
}}

規則：
- solution_code 必須是完整、可獨立執行的程式，用標準輸入讀資料、用標準輸出印結果。
- solution_code 在每一個既有測試的 stdin 下，輸出必須和該測試的 expected_output 完全相同。
- 比對方式會去掉每行結尾空白與結尾空行，其餘完全比對，請注意全形/半形與標點。
- 不要在 solution_code 裡輸出任何提示文字或多餘的說明。
- {title_rule}
- {hidden_rule}
- 只輸出 JSON，不要加上任何說明文字或 markdown 圍籬。
"""


async def _generate_repair(
    challenge: CodingChallenge,
    tests: list[CodingChallengeTest],
    *,
    want_hidden_tests: bool,
    feedback: str | None,
    temperature: float,
) -> dict:
    prompt = _build_prompt(
        challenge, tests, want_hidden_tests=want_hidden_tests, feedback=feedback
    )
    response = await asyncio.to_thread(
        get_gemini_client().models.generate_content,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config={
            "temperature": temperature,
            "max_output_tokens": 4000,
            "response_mime_type": "application/json",
        },
    )
    payload = _extract_json_object(getattr(response, "text", ""))
    if not isinstance(payload, dict):
        raise ValueError("model did not return a JSON object")
    solution_code = payload.get("solution_code")
    if not isinstance(solution_code, str) or not solution_code.strip():
        raise ValueError("model returned no solution_code")
    raw_hidden = payload.get("hidden_tests")
    hidden: list[dict[str, str]] = []
    for idx, item in enumerate(raw_hidden if isinstance(raw_hidden, list) else []):
        if not isinstance(item, dict):
            continue
        stdin = item.get("stdin")
        expected = item.get("expected_output", item.get("expected_stdout"))
        if not isinstance(stdin, str) or not isinstance(expected, str):
            continue
        if len(stdin.encode()) > MAX_TEST_VALUE_BYTES or len(expected.encode()) > MAX_TEST_VALUE_BYTES:
            continue
        label = item.get("label")
        hidden.append(
            {
                "label": label if isinstance(label, str) and label.strip() else f"隱藏測試 {idx + 1}",
                "stdin": stdin,
                "expected_output": expected,
            }
        )
    title = payload.get("title")
    return {
        "title": title.strip() if isinstance(title, str) and title.strip() else None,
        "solution_code": solution_code,
        "hidden_tests": hidden[:MAX_HIDDEN_TESTS],
    }


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def _dedupe_hidden_tests(
    hidden: list[dict[str, str]], tests: list[CodingChallengeTest]
) -> list[dict[str, str]]:
    seen = {
        (_normalize_output(t.stdin), _normalize_output(t.expected_stdout)) for t in tests
    }
    kept: list[dict[str, str]] = []
    for candidate in hidden:
        key = (
            _normalize_output(candidate["stdin"]),
            _normalize_output(candidate["expected_output"]),
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(candidate)
    return kept


async def _verify_candidate(
    runner: ExecutorRunner,
    challenge: CodingChallenge,
    tests: list[CodingChallengeTest],
    candidate: dict,
    *,
    require_hidden_tests: bool,
) -> tuple[list[dict[str, str]], dict[str, Any], str | None]:
    """Return (accepted_hidden_tests, stats, rejection_reason)."""
    language_id = challenge.language_id
    solution = candidate["solution_code"]
    stats: dict[str, Any] = {
        "existing_tests_verified": len(tests),
        "hidden_generated": len(candidate["hidden_tests"]),
        "hidden_dropped_starter_passes": 0,
        "hidden_dropped_cheat_passes": 0,
        "starter_passes_existing": False,
    }

    # Gate 1 — the reference solution must pass every pre-existing test.
    existing_runs = await asyncio.gather(
        *[runner.run(language_id, solution, test.stdin) for test in tests]
    )
    for test, run in zip(tests, existing_runs):
        if run["error"]:
            return [], stats, f"executor error on existing test {test.test_uuid}: {run['error']}"
        if not run["accepted"]:
            return [], stats, (
                f"reference solution did not run on existing test {test.test_uuid}: "
                f"{run['status'].get('description')}"
            )
        expected = _normalize_output(test.expected_stdout)
        if run["stdout"] != expected:
            return [], stats, (
                f"reference solution output mismatch on existing test {test.test_uuid}"
            )

    hidden = _dedupe_hidden_tests(candidate["hidden_tests"], tests)
    if not hidden:
        if require_hidden_tests:
            return [], stats, "model produced no new hidden test cases"
        return [], stats, None

    # Gate 2 — the model's solution and its claimed expectations must agree.
    hidden_runs = await asyncio.gather(
        *[runner.run(language_id, solution, item["stdin"]) for item in hidden]
    )
    agreed: list[dict[str, str]] = []
    for item, run in zip(hidden, hidden_runs):
        if run["error"]:
            return [], stats, f"executor error on generated hidden test: {run['error']}"
        if not run["accepted"]:
            return [], stats, (
                "reference solution crashed on a generated hidden test: "
                f"{run['status'].get('description')}"
            )
        if run["stdout"] != _normalize_output(item["expected_output"]):
            return [], stats, (
                "generated hidden expected_output disagrees with the reference solution"
            )
        agreed.append(item)

    # Gate 3 — a hidden test the untouched starter already passes is worthless.
    starter = challenge.starter_code or ""
    if starter.strip():
        starter_runs = await asyncio.gather(
            *[runner.run(language_id, starter, item["stdin"]) for item in agreed]
        )
        survivors: list[dict[str, str]] = []
        for item, run in zip(agreed, starter_runs):
            if run["accepted"] and run["stdout"] == _normalize_output(item["expected_output"]):
                stats["hidden_dropped_starter_passes"] += 1
                continue
            survivors.append(item)
        agreed = survivors

        starter_existing = await asyncio.gather(
            *[runner.run(language_id, starter, test.stdin) for test in tests]
        )
        stats["starter_passes_existing"] = bool(tests) and all(
            run["accepted"] and run["stdout"] == _normalize_output(test.expected_stdout)
            for test, run in zip(tests, starter_existing)
        )

    # Gate 4 — the hardcoded-visible-output cheat must fail every hidden test.
    visible_expected = next(
        (
            t.expected_stdout
            for t in tests
            if t.visibility == CodingChallengeTestVisibility.VISIBLE
        ),
        tests[0].expected_stdout if tests else "",
    )
    cheat = _cheat_source(language_id, visible_expected)
    if cheat and agreed:
        cheat_runs = await asyncio.gather(
            *[runner.run(language_id, cheat, item["stdin"]) for item in agreed]
        )
        survivors = []
        for item, run in zip(agreed, cheat_runs):
            if run["accepted"] and run["stdout"] == _normalize_output(item["expected_output"]):
                stats["hidden_dropped_cheat_passes"] += 1
                continue
            survivors.append(item)
        agreed = survivors

    if require_hidden_tests and not agreed:
        return [], stats, (
            "every generated hidden test was already passed by the starter code or by a "
            "hardcoded-output cheat"
        )
    return agreed, stats, None


async def _verify_manifest_rewrite(
    runner: ExecutorRunner,
    entry: dict[str, Any],
) -> None:
    """Execute proposed protected content without logging source or test data."""
    identity = entry["identity"]
    rewrite = entry["rewrite"]
    tests = rewrite["tests"]
    solution_runs = await asyncio.gather(
        *[
            runner.run(identity["language_id"], rewrite["solution_code"], test["stdin"])
            for test in tests
        ]
    )
    if not all(
        run["accepted"]
        and run["stdout"] == _normalize_output(test["expected_stdout"])
        for test, run in zip(tests, solution_runs)
    ):
        raise RewriteManifestError(
            f"reference solution verification failed for {identity['challenge_uuid']}"
        )

    starter_runs = await asyncio.gather(
        *[
            runner.run(identity["language_id"], rewrite["starter_code"], test["stdin"])
            for test in tests
        ]
    )
    starter_passes_all = all(
        run["accepted"]
        and run["stdout"] == _normalize_output(test["expected_stdout"])
        for test, run in zip(tests, starter_runs)
    )
    if starter_passes_all:
        raise RewriteManifestError(
            f"starter code passes the proposed suite for {identity['challenge_uuid']}"
        )

    visible = next(
        test
        for test in tests
        if test["visibility"] == CodingChallengeTestVisibility.VISIBLE.value
    )
    hidden = [
        test
        for test in tests
        if test["visibility"] == CodingChallengeTestVisibility.HIDDEN.value
    ]
    cheat = _cheat_source(identity["language_id"], visible["expected_stdout"])
    if cheat is None:
        raise RewriteManifestError(
            f"no hardcode-cheat verifier for {identity['challenge_uuid']}"
        )
    cheat_runs = await asyncio.gather(
        *[runner.run(identity["language_id"], cheat, test["stdin"]) for test in hidden]
    )
    if all(
        run["accepted"]
        and run["stdout"] == _normalize_output(test["expected_stdout"])
        for test, run in zip(hidden, cheat_runs)
    ):
        raise RewriteManifestError(
            f"visible-answer hardcode passes the hidden suite for {identity['challenge_uuid']}"
        )


async def _tests_for_challenge(
    session,
    challenge_id: int,
    *,
    lock_rows: bool = False,
) -> list[CodingChallengeTest]:
    statement = (
        select(CodingChallengeTest)
        .where(CodingChallengeTest.challenge_id == challenge_id)
        .order_by(CodingChallengeTest.order, CodingChallengeTest.id)
    )
    statement = statement.execution_options(populate_existing=True)
    if lock_rows:
        statement = statement.with_for_update()
    return list(
        (
            await session.exec(statement)
        ).all()
    )


async def _load_rewrite_inventory(session) -> list[tuple[CodingChallenge, Activity, Course, list[CodingChallengeTest]]]:
    rows = (
        await session.exec(
            select(CodingChallenge, Activity, Course)
            .join(Activity, Activity.id == CodingChallenge.activity_id)
            .join(Course, Course.id == CodingChallenge.course_id)
            .where(
                CodingChallenge.archived == False,  # noqa: E712
                col(CodingChallenge.language_id).in_(EXPECTED_REWRITE_INVENTORY),
            )
            .order_by(Course.course_uuid, Activity.activity_uuid, CodingChallenge.challenge_uuid)
        )
    ).all()
    inventory = []
    for challenge, activity, course in rows:
        tests = await _tests_for_challenge(session, challenge.id or 0)
        hidden_count = sum(
            test.visibility == CodingChallengeTestVisibility.HIDDEN for test in tests
        )
        if hidden_count == 0 and tests and all(not (test.stdin or "").strip() for test in tests):
            inventory.append((challenge, activity, course, tests))
    return inventory


def _row_identity(
    challenge: CodingChallenge,
    activity: Activity,
    course: Course,
) -> tuple[str, str, str, str]:
    return (
        course.course_uuid,
        activity.activity_uuid,
        challenge.challenge_uuid,
        challenge.block_id,
    )


def _test_payload(test: CodingChallengeTest) -> dict[str, Any]:
    return {
        "test_uuid": test.test_uuid,
        "label": test.label,
        "stdin": test.stdin,
        "expected_stdout": test.expected_stdout,
        "visibility": test.visibility.value,
        "order": test.order,
    }


def _challenge_matches_entry(
    challenge: CodingChallenge,
    tests: Sequence[CodingChallengeTest],
    entry: dict[str, Any],
) -> bool:
    rewrite = entry["rewrite"]
    return bool(
        challenge.language_id == entry["identity"]["language_id"]
        and challenge.title == rewrite["title"]
        and challenge.description == rewrite["description"]
        and challenge.starter_code == rewrite["starter_code"]
        and challenge.solution_code == rewrite["solution_code"]
        and challenge.hints == rewrite["hints"]
        and challenge.difficulty == rewrite["difficulty"]
        and [_test_payload(test) for test in tests] == rewrite["tests"]
    )


async def _resolve_manifest_rows(
    session,
    manifest: dict[str, Any],
    *,
    lock_rows: bool,
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for entry in manifest["rewrites"]:
        identity = entry["identity"]
        statement = (
            select(CodingChallenge, Activity, Course)
            .join(Activity, Activity.id == CodingChallenge.activity_id)
            .join(Course, Course.id == CodingChallenge.course_id)
            .where(
                Course.course_uuid == identity["course_uuid"],
                Activity.activity_uuid == identity["activity_uuid"],
                CodingChallenge.challenge_uuid == identity["challenge_uuid"],
                CodingChallenge.block_id == identity["block_id"],
                CodingChallenge.archived == False,  # noqa: E712
            )
        )
        statement = statement.execution_options(populate_existing=True)
        if lock_rows:
            statement = statement.with_for_update()
        row = (await session.exec(statement)).first()
        if row is None:
            raise RewriteManifestError(
                f"manifest ownership identity not found: {identity['challenge_uuid']}"
            )
        challenge, activity, course = row
        if challenge.language_id != identity["language_id"]:
            raise RewriteManifestError(
                f"language drift for {identity['challenge_uuid']}"
            )
        tests = await _tests_for_challenge(
            session,
            challenge.id or 0,
            lock_rows=lock_rows,
        )
        resolved.append(
            {
                "entry": entry,
                "challenge": challenge,
                "activity": activity,
                "course": course,
                "tests": tests,
            }
        )
    return resolved


async def _assert_no_attempt_evidence(session, challenge_ids: Sequence[int]) -> None:
    if not challenge_ids:
        return
    submissions = (
        await session.exec(
            select(CodingChallengeSubmission.id).where(
                col(CodingChallengeSubmission.challenge_id).in_(challenge_ids)
            )
        )
    ).first()
    progress = (
        await session.exec(
            select(CodingChallengeProgress.id).where(
                col(CodingChallengeProgress.challenge_id).in_(challenge_ids)
            )
        )
    ).first()
    if submissions is not None or progress is not None:
        raise RewriteManifestError(
            "rewrite inventory has submission or progress evidence; refusing destructive test replacement"
        )


def _assert_safe_activity_content(content: Any) -> None:
    for node in _iter_document_nodes(content):
        attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
        if "solutionCode" in attrs or "solution_code" in attrs or "hiddenTestCases" in attrs:
            raise RewriteManifestError("canonical sync left protected challenge data in activity content")
        for test in attrs.get("testCases", []) if isinstance(attrs.get("testCases"), list) else []:
            if isinstance(test, dict) and (
                test.get("hidden") is True
                or test.get("visibility") == CodingChallengeTestVisibility.HIDDEN.value
            ):
                raise RewriteManifestError("canonical sync left a hidden test in activity content")


def _assert_applied_activity_content(
    activity: Activity,
    challenge: CodingChallenge,
    entry: dict[str, Any],
    marker: dict[str, Any],
) -> None:
    """Fail if an idempotently applied activity is unsafe or no longer canonical."""
    _assert_safe_activity_content(activity.content)
    matches = _matching_block_nodes(
        activity.content,
        challenge_uuid=challenge.challenge_uuid,
        block_id=challenge.block_id,
    )
    if len(matches) != 1:
        raise RewriteManifestError(
            f"applied activity canonical node drift for {challenge.challenge_uuid}"
        )
    attrs = (
        matches[0].get("attrs")
        if isinstance(matches[0].get("attrs"), dict)
        else {}
    )
    rewrite = entry["rewrite"]
    expected_visible = [
        {
            "id": test["test_uuid"],
            "testUuid": test["test_uuid"],
            "label": test["label"],
            "stdin": test["stdin"],
            "expectedStdout": test["expected_stdout"],
            "hidden": False,
        }
        for test in rewrite["tests"]
        if test["visibility"] == CodingChallengeTestVisibility.VISIBLE.value
    ]
    metadata = attrs.get("metadata") if isinstance(attrs.get("metadata"), dict) else {}
    activity_metadata = (
        activity.extra_metadata.get(MANIFEST_METADATA_KEY)
        if isinstance(activity.extra_metadata, dict)
        else None
    )
    activity_marker_matches = bool(
        isinstance(activity_metadata, dict)
        and activity_metadata.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and activity_metadata.get("manifest_checksum") == marker.get("manifest_checksum")
        and isinstance(activity_metadata.get("entries"), dict)
        and activity_metadata["entries"].get(challenge.challenge_uuid)
        == marker.get("entry_checksum")
    )
    canonical_fields_match = bool(
        attrs.get("id") == challenge.block_id
        and attrs.get("challengeUuid") == challenge.challenge_uuid
        and attrs.get("languageId") == entry["identity"]["language_id"]
        and attrs.get("required") == challenge.required
        and attrs.get("title") == rewrite["title"]
        and attrs.get("description") == rewrite["description"]
        and attrs.get("starterCode") == rewrite["starter_code"]
        and attrs.get("solutionVisibility") == challenge.solution_visibility.value
        and attrs.get("hints") == rewrite["hints"]
        and attrs.get("difficulty") == rewrite["difficulty"]
        and attrs.get("timeLimitMs") == challenge.time_limit_ms
        and attrs.get("sqliteDbPath") == challenge.sqlite_db_path
        and attrs.get("additionalFiles") == challenge.additional_files
        and attrs.get("externalId") == challenge.external_id
        and attrs.get("sourceTemplateUuid") == challenge.source_template_uuid
        and attrs.get("tags") == challenge.tags
        and attrs.get("testCases") == expected_visible
        and metadata.get(MANIFEST_METADATA_KEY) == marker
        and activity_marker_matches
    )
    if not canonical_fields_match:
        raise RewriteManifestError(
            f"applied activity canonical node drift for {challenge.challenge_uuid}"
        )


def _prepare_rewrite_content(
    activity: Activity,
    challenge: CodingChallenge,
    entry: dict[str, Any],
    marker: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    content = copy.deepcopy(activity.content)
    if not isinstance(content, dict):
        raise RewriteManifestError(
            f"activity content is not a document for {challenge.challenge_uuid}"
        )
    node, converted_legacy = _select_activity_source_node(
        content,
        challenge=challenge,
    )
    if converted_legacy:
        node["type"] = "blockCode"
        node.pop("content", None)

    rewrite = entry["rewrite"]
    attrs = dict(node.get("attrs") if isinstance(node.get("attrs"), dict) else {})
    attrs.update(
        {
            "id": challenge.block_id,
            "challengeUuid": challenge.challenge_uuid,
            "languageId": entry["identity"]["language_id"],
            "required": challenge.required,
            "title": rewrite["title"],
            "description": rewrite["description"],
            "starterCode": rewrite["starter_code"],
            "solutionCode": rewrite["solution_code"],
            "solutionVisibility": challenge.solution_visibility.value,
            "hints": rewrite["hints"],
            "difficulty": rewrite["difficulty"],
            "timeLimitMs": challenge.time_limit_ms,
            "sqliteDbPath": challenge.sqlite_db_path,
            "additionalFiles": challenge.additional_files,
            "externalId": challenge.external_id,
            "sourceTemplateUuid": challenge.source_template_uuid,
            "tags": challenge.tags,
            "metadata": {
                **(challenge.extra_metadata or {}),
                **(
                    attrs.get("metadata")
                    if isinstance(attrs.get("metadata"), dict)
                    else {}
                ),
                MANIFEST_METADATA_KEY: marker,
            },
            "testCases": [
                {
                    "testUuid": test["test_uuid"],
                    "label": test["label"],
                    "stdin": test["stdin"],
                    "expectedStdout": test["expected_stdout"],
                    "visibility": test["visibility"],
                    "hidden": False,
                }
                for test in rewrite["tests"]
                if test["visibility"] == CodingChallengeTestVisibility.VISIBLE.value
            ],
            "hiddenTestCases": [
                {
                    "testUuid": test["test_uuid"],
                    "label": test["label"],
                    "stdin": test["stdin"],
                    "expectedStdout": test["expected_stdout"],
                    "visibility": test["visibility"],
                    "hidden": True,
                }
                for test in rewrite["tests"]
                if test["visibility"] == CodingChallengeTestVisibility.HIDDEN.value
            ],
        }
    )
    node["attrs"] = attrs
    return content, converted_legacy


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def _apply_title_to_activity_content(content: Any, challenge: CodingChallenge, title: str) -> Any:
    """Mirror the repaired title into the activity document.

    ``sync_coding_challenges_for_activity`` rewrites ``challenge.title`` from the
    block attrs on every activity save, so a DB-only title fix would not survive
    the next edit.
    """

    def walk(node: Any) -> bool:
        changed = False
        if isinstance(node, dict):
            attrs = node.get("attrs")
            if (
                node.get("type") == "blockCode"
                and isinstance(attrs, dict)
                and (
                    attrs.get("challengeUuid") == challenge.challenge_uuid
                    or attrs.get("id") == challenge.block_id
                )
            ):
                attrs["title"] = title
                changed = True
            for value in node.values():
                changed = walk(value) or changed
        elif isinstance(node, list):
            for item in node:
                changed = walk(item) or changed
        return changed

    updated = copy.deepcopy(content)
    return updated if walk(updated) else None


async def _persist_repair(
    session,
    challenge: CodingChallenge,
    tests: list[CodingChallengeTest],
    candidate: dict,
    hidden_tests: list[dict[str, str]],
    stats: dict[str, Any],
    attempts: int,
) -> str | None:
    challenge.solution_code = candidate["solution_code"]

    new_title: str | None = None
    if _is_generic_title(challenge.title) and candidate["title"] and not _is_generic_title(candidate["title"]):
        new_title = candidate["title"][:120]
        challenge.title = new_title

    next_order = max((test.order for test in tests), default=-1) + 1
    for offset, item in enumerate(hidden_tests):
        session.add(
            CodingChallengeTest(
                challenge_id=challenge.id or 0,
                test_uuid=f"test_{uuid4()}",
                label=item["label"][:200],
                stdin=item["stdin"],
                expected_stdout=item["expected_output"],
                visibility=CodingChallengeTestVisibility.HIDDEN,
                order=next_order + offset,
            )
        )

    metadata = dict(challenge.extra_metadata or {})
    metadata[REPAIR_METADATA_KEY] = {
        "version": REPAIR_VERSION,
        "repaired_at": _now(),
        "attempts": attempts,
        "hidden_tests_added": len(hidden_tests),
        "verified_against_tests": stats["existing_tests_verified"] + len(hidden_tests),
        "hidden_dropped_starter_passes": stats["hidden_dropped_starter_passes"],
        "hidden_dropped_cheat_passes": stats["hidden_dropped_cheat_passes"],
        "starter_passed_existing_tests": stats["starter_passes_existing"],
        "title_rewritten": bool(new_title),
    }
    challenge.extra_metadata = metadata
    challenge.updated_at = _now()
    session.add(challenge)

    if new_title:
        activity = (
            await session.exec(
                select(Activity).where(Activity.id == challenge.activity_id)
            )
        ).first()
        if activity is not None and isinstance(activity.content, (dict, list)):
            updated = _apply_title_to_activity_content(activity.content, challenge, new_title)
            if updated is not None:
                activity.content = updated
                session.add(activity)

    await session.commit()
    return new_title


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


async def verify_or_apply_rewrite_manifest(
    session,
    runner: ExecutorRunner,
    manifest: dict[str, Any],
    *,
    apply: bool,
) -> dict[str, Any]:
    """Verify the entire batch, then optionally apply it in one transaction."""
    validate_rewrite_manifest(manifest)
    checksum = _manifest_checksum(manifest)
    # Resolve identities without row locks first. For apply mode this gives us
    # the complete course write set without taking locks in an order that can
    # deadlock with indexed-content writers.
    resolved = await _resolve_manifest_rows(session, manifest, lock_rows=False)
    if apply:
        await declare_course_index_write_set(
            [item["course"].id for item in resolved],
            session,
        )
        # All indexed writers take the advisory lock before their row locks.
        # Re-resolve under deterministic FOR UPDATE only after the complete
        # sorted advisory set is held, then use these refreshed ORM rows for
        # fingerprint validation and mutation.
        resolved = await _resolve_manifest_rows(session, manifest, lock_rows=True)

    current_inventory = await _load_rewrite_inventory(session)
    current_identity_set = {
        _row_identity(challenge, activity, course)
        for challenge, activity, course, _tests in current_inventory
    }
    manifest_identity_set = {_manifest_identity(entry) for entry in manifest["rewrites"]}
    current_counts = Counter(challenge.language_id for challenge, *_rest in current_inventory)
    already_applied: set[tuple[str, str, str, str]] = set()
    prepared: list[dict[str, Any]] = []

    for item in resolved:
        entry = item["entry"]
        challenge = item["challenge"]
        activity = item["activity"]
        course = item["course"]
        tests = item["tests"]
        identity_key = _manifest_identity(entry)
        entry_digest = _entry_checksum(entry)
        marker = (challenge.extra_metadata or {}).get(MANIFEST_METADATA_KEY)
        marked = bool(
            isinstance(marker, dict)
            and marker.get("schema_version") == MANIFEST_SCHEMA_VERSION
            and marker.get("manifest_checksum") == checksum
            and marker.get("entry_checksum") == entry_digest
        )
        matching_nodes = _matching_block_nodes(
            activity.content,
            challenge_uuid=challenge.challenge_uuid,
            block_id=challenge.block_id,
        )
        if marked and _challenge_matches_entry(challenge, tests, entry) and len(matching_nodes) == 1:
            _assert_applied_activity_content(
                activity,
                challenge,
                entry,
                marker,
            )
            already_applied.add(identity_key)
            continue
        if identity_key not in current_identity_set:
            raise RewriteManifestError(
                f"target is neither the expected legacy inventory nor idempotently applied: {challenge.challenge_uuid}"
            )
        actual_fingerprint = _source_fingerprint(course, activity, challenge, tests)
        if actual_fingerprint != entry["source_fingerprint"]:
            raise RewriteManifestError(
                f"source fingerprint drift for {challenge.challenge_uuid}"
            )
        marker_payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "manifest_checksum": checksum,
            "entry_checksum": entry_digest,
            "source_fingerprint": actual_fingerprint,
        }
        content, converted = _prepare_rewrite_content(
            activity, challenge, entry, marker_payload
        )
        prepared.append(
            {
                **item,
                "content": content,
                "converted_legacy": converted,
                "marker": marker_payload,
            }
        )

    pending_identities = manifest_identity_set - already_applied
    if current_identity_set != pending_identities:
        raise RewriteManifestError(
            "database rewrite inventory does not exactly match the manifest pending set"
        )
    pending_counts = Counter(
        item["challenge"].language_id for item in prepared
    )
    expected_pending_counts = Counter(
        entry["identity"]["language_id"]
        for entry in manifest["rewrites"]
        if _manifest_identity(entry) in pending_identities
    )
    if pending_counts != expected_pending_counts or current_counts != expected_pending_counts:
        raise RewriteManifestError("database language inventory is not fail-closed")

    # Historical attempt evidence only blocks destructive rewrites. Once the
    # exact checksummed rewrite is already present, later learner evidence must
    # not make harmless verify/idempotent-apply impossible.
    await _assert_no_attempt_evidence(
        session,
        [item["challenge"].id or 0 for item in prepared],
    )

    # Verify every proposed rewrite before the first mutation. This intentionally
    # holds apply-mode row locks through executor verification so a concurrent
    # activity save cannot race the preflight fingerprint.
    await asyncio.gather(
        *[_verify_manifest_rewrite(runner, item["entry"]) for item in prepared]
    )

    converted_count = sum(bool(item["converted_legacy"]) for item in prepared)
    if apply:
        activity_markers: dict[int, dict[str, str]] = {}
        for item in prepared:
            activity = item["activity"]
            challenge = item["challenge"]
            course = item["course"]
            canonical = await sync_coding_challenges_for_activity(
                activity,
                course,
                item["content"],
                session,
            )
            canonical = sanitize_coding_challenge_content(canonical)
            _assert_safe_activity_content(canonical)
            activity.content = canonical
            entry_markers = activity_markers.setdefault(activity.id or 0, {})
            entry_markers[challenge.challenge_uuid] = item["marker"]["entry_checksum"]
            metadata = dict(activity.extra_metadata or {})
            metadata[MANIFEST_METADATA_KEY] = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "manifest_checksum": checksum,
                "entries": dict(sorted(entry_markers.items())),
            }
            activity.extra_metadata = metadata
            session.add(activity)
        await session.flush()

        # Exact postcondition: protected rows match the manifest byte-for-byte,
        # while activity JSON remains canonical and student-safe.
        for item in prepared:
            tests = await _tests_for_challenge(session, item["challenge"].id or 0)
            if not _challenge_matches_entry(item["challenge"], tests, item["entry"]):
                raise RewriteManifestError(
                    f"post-apply exact replacement failed for {item['challenge'].challenge_uuid}"
                )
            _assert_safe_activity_content(item["activity"].content)

    return {
        "mode": "apply_rewrite_manifest" if apply else "verify_rewrite_manifest",
        "manifest_checksum": checksum,
        "considered": len(resolved),
        "pending": len(prepared),
        "already_applied": len(already_applied),
        "legacy_conversions": converted_count,
        "executor_calls": runner.calls,
        "status": "ok",
    }


async def run_rewrite_manifest_mode(
    args: argparse.Namespace,
    *,
    apply: bool,
) -> dict[str, Any]:
    manifest = load_rewrite_manifest(args.manifest)
    async with _async_session_factory() as session:
        try:
            async with session.begin():
                async with ExecutorRunner(args.concurrency) as runner:
                    return await verify_or_apply_rewrite_manifest(
                        session,
                        runner,
                        manifest,
                        apply=apply,
                    )
        except Exception:
            await session.rollback()
            raise


async def run_quality_audit(args: argparse.Namespace) -> dict[str, Any]:
    """Static content audit; never calls AI, the executor, or write methods."""
    async with _async_session_factory() as session:
        rows = (
            await session.exec(
                select(CodingChallenge, Activity, Course)
                .join(Activity, Activity.id == CodingChallenge.activity_id, isouter=True)
                .join(Course, Course.id == CodingChallenge.course_id, isouter=True)
                .where(
                    CodingChallenge.archived == False,  # noqa: E712
                    CodingChallenge.language_id != HTML_PREVIEW_LANGUAGE_ID,
                )
                .order_by(CodingChallenge.id)
            )
        ).all()
        results: list[dict[str, Any]] = []
        issue_counts: Counter[str] = Counter()
        for challenge, activity, course in rows:
            tests = await _tests_for_challenge(session, challenge.id or 0)
            issues: list[str] = []
            if activity is None or course is None:
                issues.append("unmapped_owner")
            else:
                matches = _matching_block_nodes(
                    activity.content,
                    challenge_uuid=challenge.challenge_uuid,
                    block_id=challenge.block_id,
                )
                if len(matches) != 1:
                    issues.append("unmapped_activity_block")
            hidden = [
                test
                for test in tests
                if test.visibility == CodingChallengeTestVisibility.HIDDEN
            ]
            if not hidden:
                issues.append("zero_hidden_tests")
            if not tests or all(not (test.stdin or "").strip() for test in tests):
                issues.append("all_inputs_empty")
            issue_counts.update(issues)
            results.append(
                {
                    "challenge_id": challenge.id,
                    "challenge_uuid": challenge.challenge_uuid,
                    "language_id": challenge.language_id,
                    "status": "failed" if issues else "ok",
                    "issues": issues,
                }
            )
    return {
        "mode": "quality_audit",
        "considered": len(results),
        "counts": {
            "ok": sum(item["status"] == "ok" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
        },
        "issue_counts": dict(sorted(issue_counts.items())),
        "results": results,
    }


async def _load_targets(session, args: argparse.Namespace) -> list[int]:
    statement = select(CodingChallenge.id).where(
        CodingChallenge.archived == False,  # noqa: E712
        CodingChallenge.language_id != HTML_PREVIEW_LANGUAGE_ID,
    )
    if args.challenge_id:
        statement = statement.where(col(CodingChallenge.id).in_(args.challenge_id))
    if args.language_id:
        statement = statement.where(col(CodingChallenge.language_id).in_(args.language_id))
    statement = statement.order_by(CodingChallenge.id)
    return list((await session.exec(statement)).all())


async def _audit_one(challenge_id: int, runner: ExecutorRunner) -> dict[str, Any]:
    """Re-check a stored challenge without calling the model.

    Answers the two questions the repair is meant to fix: does the stored
    reference solution really pass, and can the hardcoded-visible-output cheat
    still get graded as passing?
    """
    async with _async_session_factory() as session:
        challenge = (
            await session.exec(select(CodingChallenge).where(CodingChallenge.id == challenge_id))
        ).first()
        if challenge is None:
            return {"challenge_id": challenge_id, "status": "skipped", "reason": "not found"}
        tests = list(
            (
                await session.exec(
                    select(CodingChallengeTest)
                    .where(CodingChallengeTest.challenge_id == challenge.id)
                    .order_by(CodingChallengeTest.order)
                )
            ).all()
        )

    outcome: dict[str, Any] = {
        "challenge_id": challenge_id,
        "title": challenge.title,
        "visible_tests": sum(
            1 for t in tests if t.visibility == CodingChallengeTestVisibility.VISIBLE
        ),
        "hidden_tests": sum(
            1 for t in tests if t.visibility == CodingChallengeTestVisibility.HIDDEN
        ),
        "status": "ok",
        "reason": "",
    }
    if not tests:
        outcome.update(status="failed", reason="no tests")
        return outcome
    if not (challenge.solution_code or "").strip():
        outcome.update(status="failed", reason="empty solution_code")
        return outcome

    runs = await asyncio.gather(
        *[runner.run(challenge.language_id, challenge.solution_code, t.stdin) for t in tests]
    )
    failed = [
        t.label
        for t, run in zip(tests, runs)
        if not (run["accepted"] and run["stdout"] == _normalize_output(t.expected_stdout))
    ]
    if failed:
        outcome.update(status="failed", reason=f"reference solution fails: {failed}")
        return outcome

    visible = [t for t in tests if t.visibility == CodingChallengeTestVisibility.VISIBLE]
    cheat = _cheat_source(challenge.language_id, visible[0].expected_stdout) if visible else None
    if cheat:
        cheat_runs = await asyncio.gather(
            *[runner.run(challenge.language_id, cheat, t.stdin) for t in tests]
        )
        cheat_passes = all(
            run["accepted"] and run["stdout"] == _normalize_output(t.expected_stdout)
            for t, run in zip(tests, cheat_runs)
        )
        outcome["cheat_passes_full_suite"] = cheat_passes
        if cheat_passes and outcome["hidden_tests"]:
            outcome.update(
                status="failed",
                reason="hardcoded-output cheat still passes the full suite",
            )
    return outcome


async def _repair_one(
    challenge_id: int,
    runner: ExecutorRunner,
    args: argparse.Namespace,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    async with _async_session_factory() as session:
        challenge = (
            await session.exec(
                select(CodingChallenge).where(CodingChallenge.id == challenge_id)
            )
        ).first()
        if challenge is None:
            return {"challenge_id": challenge_id, "status": "skipped", "reason": "not found"}

        outcome: dict[str, Any] = {
            "challenge_id": challenge_id,
            "challenge_uuid": challenge.challenge_uuid,
            "language_id": challenge.language_id,
            "title_before": challenge.title,
            "status": "skipped",
            "reason": "",
            "attempts": 0,
            "hidden_tests_added": 0,
        }

        marker = (challenge.extra_metadata or {}).get(REPAIR_METADATA_KEY)
        if isinstance(marker, dict) and marker.get("version") == REPAIR_VERSION and not args.force:
            outcome["reason"] = "already repaired (use --force to redo)"
            return outcome

        tests = list(
            (
                await session.exec(
                    select(CodingChallengeTest)
                    .where(CodingChallengeTest.challenge_id == challenge.id)
                    .order_by(CodingChallengeTest.order)
                )
            ).all()
        )
        if not tests:
            outcome["reason"] = "challenge has no tests to verify against"
            return outcome

        has_input_variance = any((test.stdin or "").strip() for test in tests)
        outcome["input_driven"] = has_input_variance

        feedback: str | None = None
        for attempt in range(1, args.max_attempts + 1):
            outcome["attempts"] = attempt
            try:
                candidate = await _generate_repair(
                    challenge,
                    tests,
                    want_hidden_tests=has_input_variance,
                    feedback=feedback,
                    temperature=args.temperature if attempt == 1 else min(1.0, args.temperature + 0.2 * attempt),
                )
            except (AIProviderError, ValueError, json.JSONDecodeError) as exc:
                feedback = f"generation error: {exc}"
                logger.warning(
                    "challenge %s attempt %s: generation failed: %s", challenge_id, attempt, exc
                )
                continue

            hidden, stats, rejection = await _verify_candidate(
                runner,
                challenge,
                tests,
                candidate,
                require_hidden_tests=has_input_variance,
            )
            if rejection:
                feedback = rejection
                logger.warning(
                    "challenge %s attempt %s rejected: %s", challenge_id, attempt, rejection
                )
                continue

            outcome["hidden_tests_added"] = len(hidden)
            outcome["stats"] = stats
            if args.dry_run:
                outcome["status"] = "would_repair"
                outcome["reason"] = "dry run, nothing written"
                outcome["title_after"] = candidate["title"]
                outcome["duration_s"] = round(time.perf_counter() - started_at, 2)
                logger.info(
                    "challenge %s VERIFIED (dry-run): %s hidden test(s), %s existing test(s)",
                    challenge_id,
                    len(hidden),
                    stats["existing_tests_verified"],
                )
                return outcome

            new_title = await _persist_repair(
                session, challenge, tests, candidate, hidden, stats, attempt
            )
            outcome["status"] = "repaired"
            outcome["reason"] = (
                "solution verified against all tests"
                if hidden
                else "solution verified; no input variance so hidden tests are not applicable"
            )
            outcome["title_after"] = new_title or challenge.title
            outcome["duration_s"] = round(time.perf_counter() - started_at, 2)
            logger.info(
                "challenge %s REPAIRED: +%s hidden test(s), title=%r, attempts=%s",
                challenge_id,
                len(hidden),
                outcome["title_after"],
                attempt,
            )
            return outcome

        outcome["status"] = "failed"
        outcome["reason"] = feedback or "exhausted attempts"
        outcome["duration_s"] = round(time.perf_counter() - started_at, 2)
        logger.error("challenge %s FAILED after %s attempts: %s", challenge_id, args.max_attempts, outcome["reason"])
        return outcome


async def run_repair(args: argparse.Namespace) -> dict[str, Any]:
    started_at = time.perf_counter()
    async with _async_session_factory() as session:
        target_ids = await _load_targets(session, args)

    if args.limit:
        target_ids = target_ids[: args.limit]

    logger.info(
        "%s starting: %s candidate challenge(s), dry_run=%s, max_attempts=%s, workers=%s",
        "audit" if args.audit else "repair",
        len(target_ids),
        args.dry_run,
        args.max_attempts,
        args.workers,
    )

    outcomes: list[dict[str, Any]] = []
    async with ExecutorRunner(args.concurrency) as runner:
        queue: asyncio.Queue[int] = asyncio.Queue()
        for challenge_id in target_ids:
            queue.put_nowait(challenge_id)

        async def worker() -> None:
            while True:
                try:
                    challenge_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    if args.audit:
                        outcomes.append(await _audit_one(challenge_id, runner))
                    else:
                        outcomes.append(await _repair_one(challenge_id, runner, args))
                except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
                    logger.exception("challenge %s raised", challenge_id)
                    outcomes.append(
                        {
                            "challenge_id": challenge_id,
                            "status": "failed",
                            "reason": f"unhandled error: {exc}",
                        }
                    )
                finally:
                    queue.task_done()

        await asyncio.gather(*[worker() for _ in range(max(1, args.workers))])

        executor_calls = runner.calls

    outcomes.sort(key=lambda item: item["challenge_id"])
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1

    return {
        "mode": "audit" if args.audit else "repair",
        "dry_run": args.dry_run,
        "considered": len(target_ids),
        "counts": counts,
        "hidden_tests_added": sum(o.get("hidden_tests_added", 0) for o in outcomes),
        "executor_calls": executor_calls,
        "duration_s": round(time.perf_counter() - started_at, 2),
        "results": outcomes,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    manifest_modes = parser.add_mutually_exclusive_group()
    manifest_modes.add_argument(
        "--verify-rewrite-manifest",
        action="store_true",
        help="Verify the complete deterministic rewrite batch; never write.",
    )
    manifest_modes.add_argument(
        "--apply-rewrite-manifest",
        action="store_true",
        help="Explicitly apply the fully verified rewrite batch in one transaction.",
    )
    manifest_modes.add_argument(
        "--quality-audit",
        action="store_true",
        help="No AI/executor/writes: report zero-hidden, empty-input, and mapping defects.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_REWRITE_MANIFEST,
        help="Path to a strict v1 deterministic rewrite manifest.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Repair at most N challenges.")
    parser.add_argument("--challenge-id", type=int, action="append", help="Restrict to specific challenge ids.")
    parser.add_argument("--language-id", type=int, action="append", help="Restrict to specific Judge0 language ids.")
    parser.add_argument("--dry-run", action="store_true", help="Generate and verify but write nothing.")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="No AI calls: re-check stored solutions and cheat-resistance only.",
    )
    parser.add_argument("--force", action="store_true", help="Redo challenges already marked repaired.")
    parser.add_argument("--max-attempts", type=int, default=3, help="Generation retries per challenge.")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--workers", type=int, default=2, help="Challenges processed in parallel.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max parallel executor submissions (the sandbox collapses above 6).",
    )
    parser.add_argument("--report", type=Path, help="Write the full JSON report to this path.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.verbose:
        # One line per executor submission drowns the progress log.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        if args.verify_rewrite_manifest:
            report = asyncio.run(run_rewrite_manifest_mode(args, apply=False))
        elif args.apply_rewrite_manifest:
            report = asyncio.run(run_rewrite_manifest_mode(args, apply=True))
        elif args.quality_audit:
            report = asyncio.run(run_quality_audit(args))
        else:
            report = asyncio.run(run_repair(args))
    except RewriteManifestError as exc:
        logger.error("rewrite manifest rejected: %s", exc)
        return 1
    if args.report:
        path = args.report.expanduser().resolve()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: value for key, value in report.items() if key != "results"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report.get("status") == "ok":
        return 0
    return 0 if not report.get("counts", {}).get("failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
