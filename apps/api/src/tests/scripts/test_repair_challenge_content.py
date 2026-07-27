import copy
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from scripts import repair_challenge_content as repair
from scripts.challenge_content import build_rewrite_manifest
from src.db.coding_challenges import (
    CodingChallenge,
    CodingChallengeProgress,
    CodingChallengeSubmission,
    CodingChallengeTest,
    CodingChallengeTestVisibility,
)
from src.db.courses.activities import Activity
from src.db.courses.courses import Course


MANIFEST_PATH = Path(repair.__file__).parent / "challenge_content" / "rewrite_manifest.v1.json"
SCHEMA_PATH = Path(repair.__file__).parent / "challenge_content" / "rewrite_manifest.v1.schema.json"


class DeterministicRunner:
    def __init__(self, solution, expected_by_input):
        self.solution = solution
        self.expected_by_input = expected_by_input
        self.calls = 0

    async def run(self, _language_id, source, stdin):
        self.calls += 1
        if source == self.solution:
            return {
                "accepted": True,
                "stdout": repair._normalize_output(self.expected_by_input[stdin]),
                "status": {"id": 3},
                "error": None,
            }
        return {
            "accepted": False,
            "stdout": "",
            "status": {"id": 1},
            "error": None,
        }


def _single_manifest_entry(course, activity, challenge, tests):
    rewrite_tests = [
        {
            "test_uuid": "rewrite_visible",
            "label": "範例測試",
            "stdin": "1\n",
            "expected_stdout": "2\n",
            "visibility": "visible",
            "order": 0,
        },
        {
            "test_uuid": "rewrite_hidden_1",
            "label": "隱藏測試 1",
            "stdin": "4\n",
            "expected_stdout": "8\n",
            "visibility": "hidden",
            "order": 1,
        },
        {
            "test_uuid": "rewrite_hidden_2",
            "label": "隱藏測試 2",
            "stdin": "9\n",
            "expected_stdout": "18\n",
            "visibility": "hidden",
            "order": 2,
        },
    ]
    entry = {
        "identity": {
            "course_uuid": course.course_uuid,
            "activity_uuid": activity.activity_uuid,
            "challenge_uuid": challenge.challenge_uuid,
            "block_id": challenge.block_id,
            "language_id": challenge.language_id,
        },
        "source_fingerprint": repair._source_fingerprint(
            course, activity, challenge, tests
        ),
        "rewrite": {
            "title": "把輸入整數乘以二",
            "description": "讀取一個整數並計算兩倍。\n\n輸入：一個整數。\n輸出：該整數的兩倍。",
            "starter_code": "value = int(input())\n# TODO\n",
            "solution_code": "value = int(input())\nprint(value * 2)\n",
            "hints": ["先把輸入轉成整數。"],
            "difficulty": "easy",
            "tests": rewrite_tests,
        },
    }
    return {
        "schema_version": 1,
        "inventory": {"total": 1, "languages": {"71": 1}},
        "rewrites": [entry],
    }


async def _seed_legacy_challenge(db, course, activity, *, legacy=False):
    activity.content = {
        "type": "doc",
        "content": [
            {
                "type": "codeBlock" if legacy else "blockCode",
                "attrs": (
                    {"language": "python"}
                    if legacy
                    else {
                        "id": "block_rewrite",
                        "challengeUuid": "challenge_rewrite",
                        "languageId": 71,
                        "solutionCode": "must not remain public",
                        "hiddenTestCases": [{"stdin": "protected"}],
                    }
                ),
                "content": ([{"type": "text", "text": "print('old')"}] if legacy else []),
            }
        ],
    }
    db.add(activity)
    challenge = CodingChallenge(
        challenge_uuid="challenge_rewrite",
        org_id=activity.org_id,
        course_id=course.id,
        activity_id=activity.id,
        block_id="block_rewrite",
        language_id=71,
        title="固定輸出",
        description="舊內容",
        starter_code="print('old')",
        solution_code="print('old')",
        difficulty="easy",
    )
    db.add(challenge)
    await db.flush()
    old_test = CodingChallengeTest(
        challenge_id=challenge.id,
        test_uuid="old_visible",
        label="舊測試",
        stdin="",
        expected_stdout="old\n",
        visibility=CodingChallengeTestVisibility.VISIBLE,
        order=0,
    )
    db.add(old_test)
    await db.commit()
    await db.refresh(activity)
    await db.refresh(challenge)
    await db.refresh(old_test)
    return challenge, [old_test]


@pytest.fixture
def one_item_inventory(monkeypatch):
    monkeypatch.setattr(repair, "EXPECTED_REWRITE_INVENTORY", {71: 1})
    monkeypatch.setattr(repair, "EXPECTED_REWRITE_TOTAL", 1)


def test_checked_in_manifest_is_complete_and_generator_is_reproducible():
    manifest = repair.load_rewrite_manifest(MANIFEST_PATH)

    assert manifest == build_rewrite_manifest.build_manifest()
    assert len(manifest["rewrites"]) == 51
    assert Counter(
        entry["identity"]["language_id"] for entry in manifest["rewrites"]
    ) == {63: 40, 71: 11}
    assert len(
        {
            (
                entry["identity"]["course_uuid"],
                entry["identity"]["activity_uuid"],
                entry["identity"]["challenge_uuid"],
                entry["identity"]["block_id"],
            )
            for entry in manifest["rewrites"]
        }
    ) == 51


def test_checked_in_schema_documents_fail_closed_inventory():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["properties"]["rewrites"]["minItems"] == 51
    assert schema["properties"]["rewrites"]["maxItems"] == 51
    assert schema["properties"]["inventory"]["properties"]["languages"]["properties"] == {
        "63": {"const": 40},
        "71": {"const": 11},
    }


def test_checked_in_manifest_solutions_pass_real_python_and_node_runtimes():
    manifest = repair.load_rewrite_manifest(MANIFEST_PATH)
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js runtime is required for JavaScript manifest verification")

    executed_cases = 0
    for entry in manifest["rewrites"]:
        identity = entry["identity"]
        rewrite = entry["rewrite"]
        command = (
            [sys.executable, "-c", rewrite["solution_code"]]
            if identity["language_id"] == 71
            else [node, "-e", rewrite["solution_code"]]
        )
        for test in rewrite["tests"]:
            result = subprocess.run(
                command,
                input=test["stdin"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            location = f"{identity['challenge_uuid']}:{test['test_uuid']}"
            assert result.returncode == 0, f"runtime failure at {location}"
            assert repair._normalize_output(result.stdout) == repair._normalize_output(
                test["expected_stdout"]
            ), f"output mismatch at {location}"
            executed_cases += 1

    assert executed_cases >= 155


def test_reviewed_manifest_content_contracts_and_difficulties_are_explicit():
    manifest = repair.load_rewrite_manifest(MANIFEST_PATH)
    by_uuid = {
        entry["identity"]["challenge_uuid"]: entry for entry in manifest["rewrites"]
    }

    html_root = by_uuid["challenge_d5361b43-b766-4775-8d92-17c77ed7fa21"]["rewrite"]
    assert "<!doctype html>" in html_root["solution_code"]
    assert "doctype svg" not in json.dumps(html_root, ensure_ascii=False).lower()
    assert "doctype mathml" not in json.dumps(html_root, ensure_ascii=False).lower()

    api_path = by_uuid["challenge_ecb942cd-01ff-4501-903e-b0cbc003e4f8"]["rewrite"]
    assert any(
        test["stdin"] == "courses//12\n"
        and test["expected_stdout"] == "/api/courses/12\n"
        and test["visibility"] == "hidden"
        for test in api_path["tests"]
    )

    state_machine = by_uuid["challenge_c41c303a-e04b-4cb1-9c5d-d809d66d424c"]["rewrite"]
    assert "transitions[current]?.[action]" in state_machine["solution_code"]
    assert sum(
        test["expected_stdout"] == "invalid\n" for test in state_machine["tests"]
    ) >= 2

    responsive = by_uuid["challenge_c74918f5-251f-4868-9dee-12de4f85518b"]["rewrite"]
    boundary_inputs = {test["stdin"] for test in responsive["tests"]}
    assert {"600\n", "1024\n"} <= boundary_inputs

    parsed_type = by_uuid["challenge_135ab329-ab96-4308-93e1-5550d98d7100"]["rewrite"]
    assert "文字代表的資料類型" in parsed_type["title"]
    assert "解析" in parsed_type["description"]

    expected_medium = {
        "challenge_a8c199ad-8298-4929-bab5-609baf629c90",
        "challenge_8e768d78-e2d6-4f87-af5b-aba3ab81d7ec",
        "challenge_94eafa2a-980b-44a3-90a5-e027cf18de97",
        "challenge_5da834b4-8ced-47ca-add7-2565236b742c",
        "challenge_f874efbd-4f55-4854-a6c3-fac467c709c0",
        "challenge_c41c303a-e04b-4cb1-9c5d-d809d66d424c",
        "challenge_ecb942cd-01ff-4501-903e-b0cbc003e4f8",
    }
    assert {
        challenge_uuid
        for challenge_uuid, entry in by_uuid.items()
        if entry["rewrite"]["difficulty"] == "medium"
    } == expected_medium


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda payload: payload["rewrites"].pop(), "exactly 51"),
        (
            lambda payload: payload["rewrites"][0]["rewrite"].update(
                description="沒有格式契約"
            ),
            "document 輸入",
        ),
        (
            lambda payload: payload["rewrites"][0]["rewrite"]["tests"].__setitem__(
                slice(1, None), []
            ),
            "at least two hidden",
        ),
        (
            lambda payload: payload["rewrites"][0].update(
                source_fingerprint="sha256:not-a-digest"
            ),
            "sha256 digest",
        ),
    ],
)
def test_manifest_validation_fails_closed(mutate, match):
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mutate(payload)

    with pytest.raises(repair.RewriteManifestError, match=match):
        repair.validate_rewrite_manifest(payload)


@pytest.mark.asyncio
async def test_prepare_rewrite_updates_existing_block_and_strips_legacy_child_content(
    course, activity
):
    challenge = CodingChallenge(
        challenge_uuid="challenge_rewrite",
        org_id=activity.org_id,
        course_id=course.id,
        activity_id=activity.id,
        block_id="block_rewrite",
        language_id=71,
    )
    entry = {
        "identity": {
            "course_uuid": course.course_uuid,
            "activity_uuid": activity.activity_uuid,
            "challenge_uuid": challenge.challenge_uuid,
            "block_id": challenge.block_id,
            "language_id": 71,
        },
        "source_fingerprint": "sha256:" + "0" * 64,
        "rewrite": {
            "title": "輸入驅動",
            "description": "輸入：數字。\n輸出：結果。",
            "starter_code": "# TODO",
            "solution_code": "print(input())",
            "hints": [],
            "difficulty": "easy",
            "tests": [
                {"test_uuid": "v", "label": "v", "stdin": "1", "expected_stdout": "1", "visibility": "visible", "order": 0},
                {"test_uuid": "h1", "label": "h1", "stdin": "2", "expected_stdout": "2", "visibility": "hidden", "order": 1},
                {"test_uuid": "h2", "label": "h2", "stdin": "3", "expected_stdout": "3", "visibility": "hidden", "order": 2},
            ],
        },
    }
    activity.content = {
        "type": "doc",
        "content": [{"type": "codeBlock", "attrs": {}, "content": [{"type": "text", "text": "old"}]}],
    }

    content, converted = repair._prepare_rewrite_content(
        activity, challenge, entry, {"entry_checksum": "digest"}
    )

    node = content["content"][0]
    assert converted is True
    assert node["type"] == "blockCode"
    assert "content" not in node
    assert node["attrs"]["challengeUuid"] == challenge.challenge_uuid
    assert node["attrs"]["id"] == challenge.block_id


@pytest.mark.asyncio
async def test_prepare_rewrite_rejects_zero_or_multiple_legacy_nodes(course, activity):
    challenge = CodingChallenge(
        challenge_uuid="challenge_rewrite",
        org_id=activity.org_id,
        course_id=course.id,
        activity_id=activity.id,
        block_id="block_rewrite",
        language_id=71,
    )
    entry = {
        "identity": {"course_uuid": course.course_uuid, "activity_uuid": activity.activity_uuid, "challenge_uuid": challenge.challenge_uuid, "block_id": challenge.block_id, "language_id": 71},
        "rewrite": {"title": "t", "description": "輸入：x。輸出：y。", "starter_code": "x", "solution_code": "y", "hints": [], "difficulty": "easy", "tests": []},
    }
    for nodes in ([], [{"type": "codeBlock"}, {"type": "codeBlock"}]):
        activity.content = {"type": "doc", "content": nodes}
        with pytest.raises(repair.RewriteManifestError, match="exactly one codeBlock"):
            repair._prepare_rewrite_content(activity, challenge, entry, {})


@pytest.mark.asyncio
async def test_apply_replaces_tests_canonically_and_is_idempotent(
    db, course, activity, regular_user, one_item_inventory, monkeypatch
):
    challenge, old_tests = await _seed_legacy_challenge(db, course, activity)
    manifest = _single_manifest_entry(course, activity, challenge, old_tests)
    expected = {
        test["stdin"]: test["expected_stdout"]
        for test in manifest["rewrites"][0]["rewrite"]["tests"]
    }
    runner = DeterministicRunner(
        manifest["rewrites"][0]["rewrite"]["solution_code"], expected
    )
    events = []
    canonical_resolve = repair._resolve_manifest_rows
    canonical_declare = repair.declare_course_index_write_set

    async def record_resolve(session, payload, *, lock_rows):
        events.append(("rows", lock_rows))
        return await canonical_resolve(session, payload, lock_rows=lock_rows)

    async def record_declare(course_ids, session):
        events.append(("advisory", tuple(sorted(course_ids))))
        return await canonical_declare(course_ids, session)

    monkeypatch.setattr(repair, "_resolve_manifest_rows", record_resolve)
    monkeypatch.setattr(repair, "declare_course_index_write_set", record_declare)

    await db.rollback()
    async with db.begin():
        report = await repair.verify_or_apply_rewrite_manifest(
            db, runner, manifest, apply=True
        )

    assert report["pending"] == 1
    assert events[0] == ("rows", False)
    assert events[1][0] == "advisory"
    assert events[2] == ("rows", True)
    assert report["legacy_conversions"] == 0
    await db.refresh(activity)
    await db.refresh(challenge)
    stored_tests = (
        await db.exec(
            select(CodingChallengeTest)
            .where(CodingChallengeTest.challenge_id == challenge.id)
            .order_by(CodingChallengeTest.order)
        )
    ).all()
    assert [repair._test_payload(test) for test in stored_tests] == manifest["rewrites"][0]["rewrite"]["tests"]
    attrs = activity.content["content"][0]["attrs"]
    assert "solutionCode" not in attrs
    assert "hiddenTestCases" not in attrs
    assert [item["testUuid"] for item in attrs["testCases"]] == ["rewrite_visible"]
    marker = challenge.extra_metadata[repair.MANIFEST_METADATA_KEY]
    assert marker["entry_checksum"].startswith("sha256:")
    assert activity.extra_metadata[repair.MANIFEST_METADATA_KEY]["manifest_checksum"] == marker["manifest_checksum"]

    # Evidence created after the exact rewrite must not block harmless verify
    # or idempotent apply; only pending destructive replacement is gated.
    db.add(
        CodingChallengeProgress(
            challenge_id=challenge.id,
            user_id=regular_user.id,
            activity_id=activity.id,
            course_id=course.id,
            org_id=activity.org_id,
        )
    )
    await db.commit()
    for apply in (False, True):
        second_runner = DeterministicRunner("unused", {})
        await db.rollback()
        async with db.begin():
            second = await repair.verify_or_apply_rewrite_manifest(
                db, second_runner, manifest, apply=apply
            )
        assert second["pending"] == 0
        assert second["already_applied"] == 1
        assert second_runner.calls == 0


@pytest.mark.asyncio
async def test_apply_converts_one_legacy_codeblock_and_preserves_identity(
    db, course, activity, one_item_inventory
):
    challenge, old_tests = await _seed_legacy_challenge(
        db, course, activity, legacy=True
    )
    manifest = _single_manifest_entry(course, activity, challenge, old_tests)
    rewrite = manifest["rewrites"][0]["rewrite"]
    runner = DeterministicRunner(
        rewrite["solution_code"],
        {test["stdin"]: test["expected_stdout"] for test in rewrite["tests"]},
    )

    await db.rollback()
    async with db.begin():
        report = await repair.verify_or_apply_rewrite_manifest(
            db, runner, manifest, apply=True
        )

    assert report["legacy_conversions"] == 1
    node = activity.content["content"][0]
    assert node["type"] == "blockCode"
    assert node["attrs"]["challengeUuid"] == challenge.challenge_uuid
    assert node["attrs"]["id"] == challenge.block_id


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_type", ["submission", "progress"])
async def test_manifest_refuses_existing_submission_or_progress(
    db, course, activity, regular_user, one_item_inventory, evidence_type
):
    challenge, old_tests = await _seed_legacy_challenge(db, course, activity)
    manifest = _single_manifest_entry(course, activity, challenge, old_tests)
    if evidence_type == "submission":
        evidence = CodingChallengeSubmission(
            submission_uuid="submission_existing",
            challenge_id=challenge.id,
            user_id=regular_user.id,
            activity_id=activity.id,
            course_id=course.id,
            org_id=activity.org_id,
            language_id=71,
            source_code="redacted fixture",
        )
    else:
        evidence = CodingChallengeProgress(
            challenge_id=challenge.id,
            user_id=regular_user.id,
            activity_id=activity.id,
            course_id=course.id,
            org_id=activity.org_id,
        )
    db.add(evidence)
    await db.commit()
    runner = DeterministicRunner("unused", {})

    with pytest.raises(repair.RewriteManifestError, match="submission or progress"):
        async with db.begin():
            await repair.verify_or_apply_rewrite_manifest(
                db, runner, manifest, apply=False
            )
    assert runner.calls == 0


@pytest.mark.asyncio
async def test_fingerprint_drift_fails_before_executor_or_mutation(
    db, course, activity, one_item_inventory
):
    challenge, old_tests = await _seed_legacy_challenge(db, course, activity)
    manifest = _single_manifest_entry(course, activity, challenge, old_tests)
    manifest["rewrites"][0]["source_fingerprint"] = "sha256:" + "f" * 64
    before = copy.deepcopy(activity.content)
    runner = DeterministicRunner("unused", {})

    await db.rollback()
    with pytest.raises(repair.RewriteManifestError, match="fingerprint drift"):
        async with db.begin():
            await repair.verify_or_apply_rewrite_manifest(
                db, runner, manifest, apply=True
            )
    assert runner.calls == 0
    await db.refresh(activity)
    assert activity.content == before


@pytest.mark.asyncio
async def test_legacy_codeblock_source_edit_invalidates_fingerprint(
    db, course, activity, one_item_inventory
):
    challenge, old_tests = await _seed_legacy_challenge(
        db, course, activity, legacy=True
    )
    manifest = _single_manifest_entry(course, activity, challenge, old_tests)
    changed = copy.deepcopy(activity.content)
    changed["content"][0]["content"][0]["text"] = "print('teacher edit')"
    activity.content = changed
    db.add(activity)
    await db.commit()
    runner = DeterministicRunner("unused", {})

    with pytest.raises(repair.RewriteManifestError, match="fingerprint drift"):
        async with db.begin():
            await repair.verify_or_apply_rewrite_manifest(
                db, runner, manifest, apply=False
            )
    assert runner.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "solution",
        "hidden_collection",
        "hidden_visible",
        "canonical_language",
        "activity_marker",
    ],
)
async def test_already_applied_activity_tamper_is_rejected_without_executor(
    db, course, activity, one_item_inventory, tamper
):
    challenge, old_tests = await _seed_legacy_challenge(db, course, activity)
    manifest = _single_manifest_entry(course, activity, challenge, old_tests)
    rewrite = manifest["rewrites"][0]["rewrite"]
    apply_runner = DeterministicRunner(
        rewrite["solution_code"],
        {test["stdin"]: test["expected_stdout"] for test in rewrite["tests"]},
    )
    await db.rollback()
    async with db.begin():
        await repair.verify_or_apply_rewrite_manifest(
            db, apply_runner, manifest, apply=True
        )

    await db.refresh(activity)
    changed = copy.deepcopy(activity.content)
    attrs = changed["content"][0]["attrs"]
    if tamper == "solution":
        attrs["solutionCode"] = "protected tamper"
    elif tamper == "hidden_collection":
        attrs["hiddenTestCases"] = [{"hidden": True}]
    elif tamper == "hidden_visible":
        attrs["testCases"].append(
            {
                "testUuid": "tampered_hidden",
                "stdin": "redacted fixture",
                "expectedStdout": "redacted fixture",
                "hidden": True,
            }
        )
    elif tamper == "canonical_language":
        attrs["languageId"] = 63
    else:
        changed_activity_metadata = copy.deepcopy(activity.extra_metadata)
        changed_activity_metadata[repair.MANIFEST_METADATA_KEY][
            "manifest_checksum"
        ] = "sha256:" + "0" * 64
        activity.extra_metadata = changed_activity_metadata
    activity.content = changed
    db.add(activity)
    await db.commit()
    verify_runner = DeterministicRunner("unused", {})

    with pytest.raises(repair.RewriteManifestError):
        async with db.begin():
            await repair.verify_or_apply_rewrite_manifest(
                db, verify_runner, manifest, apply=False
            )
    assert verify_runner.calls == 0


@pytest.mark.asyncio
async def test_apply_rolls_back_the_whole_batch_when_second_sync_fails(
    db, course, activity, monkeypatch
):
    monkeypatch.setattr(repair, "EXPECTED_REWRITE_INVENTORY", {71: 2})
    monkeypatch.setattr(repair, "EXPECTED_REWRITE_TOTAL", 2)
    first, first_tests = await _seed_legacy_challenge(db, course, activity)
    second_course = Course(
        id=2,
        name="Second Course",
        description="",
        public=True,
        published=True,
        open_to_contributors=False,
        org_id=course.org_id,
        course_uuid="course_rewrite_second",
        creation_date="test",
        update_date="test",
    )
    db.add(second_course)
    await db.flush()
    second_activity = Activity(
        id=2,
        name="Second Activity",
        activity_type=activity.activity_type,
        activity_sub_type=activity.activity_sub_type,
        content={
            "type": "doc",
            "content": [
                {
                    "type": "blockCode",
                    "attrs": {
                        "id": "block_rewrite_2",
                        "challengeUuid": "challenge_rewrite_2",
                        "languageId": 71,
                    },
                }
            ],
        },
        published=True,
        org_id=activity.org_id,
        course_id=second_course.id,
        activity_uuid="activity_test_2",
        creation_date="test",
        update_date="test",
    )
    db.add(second_activity)
    second = CodingChallenge(
        challenge_uuid="challenge_rewrite_2",
        org_id=activity.org_id,
        course_id=second_course.id,
        activity_id=2,
        block_id="block_rewrite_2",
        language_id=71,
        title="固定輸出二",
        description="舊內容二",
        starter_code="print('old')",
        solution_code="print('old')",
        difficulty="easy",
    )
    db.add(second)
    await db.flush()
    second_test = CodingChallengeTest(
        challenge_id=second.id,
        test_uuid="old_visible_2",
        label="舊測試二",
        stdin="",
        expected_stdout="old\n",
        visibility=CodingChallengeTestVisibility.VISIBLE,
        order=0,
    )
    db.add(second_test)
    await db.commit()
    await db.refresh(second_activity)
    await db.refresh(second)
    await db.refresh(second_test)

    first_manifest = _single_manifest_entry(course, activity, first, first_tests)
    second_manifest = _single_manifest_entry(
        second_course, second_activity, second, [second_test]
    )
    manifest = {
        "schema_version": 1,
        "inventory": {"total": 2, "languages": {"71": 2}},
        "rewrites": first_manifest["rewrites"] + second_manifest["rewrites"],
    }
    first_uuid = first.challenge_uuid
    expected_course_ids = {course.id, second_course.id}
    rewrite = manifest["rewrites"][0]["rewrite"]
    runner = DeterministicRunner(
        rewrite["solution_code"],
        {test["stdin"]: test["expected_stdout"] for test in rewrite["tests"]},
    )
    canonical_sync = repair.sync_coding_challenges_for_activity
    canonical_declare = repair.declare_course_index_write_set
    sync_calls = 0
    declared_course_sets = []

    async def record_declared_courses(course_ids, session):
        declared_course_sets.append(list(course_ids))
        return await canonical_declare(course_ids, session)

    async def fail_second_sync(*args, **kwargs):
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            raise RuntimeError("synthetic second-sync failure")
        return await canonical_sync(*args, **kwargs)

    monkeypatch.setattr(repair, "sync_coding_challenges_for_activity", fail_second_sync)
    monkeypatch.setattr(
        repair,
        "declare_course_index_write_set",
        record_declared_courses,
    )
    await db.rollback()
    with pytest.raises(RuntimeError, match="second-sync failure"):
        async with db.begin():
            await repair.verify_or_apply_rewrite_manifest(
                db, runner, manifest, apply=True
            )

    assert len(declared_course_sets) == 1
    assert set(declared_course_sets[0]) == expected_course_ids

    await db.rollback()
    stored_first = (
        await db.exec(
            select(CodingChallenge).where(
                CodingChallenge.challenge_uuid == first_uuid
            )
        )
    ).one()
    stored_tests = await repair._tests_for_challenge(db, stored_first.id)
    assert stored_first.title == "固定輸出"
    assert [test.test_uuid for test in stored_tests] == ["old_visible"]


@pytest.mark.asyncio
async def test_quality_audit_reports_safe_issue_codes_only(
    db, engine, course, activity, monkeypatch
):
    await _seed_legacy_challenge(db, course, activity)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(repair, "_async_session_factory", factory)

    report = await repair.run_quality_audit(type("Args", (), {})())

    result = next(
        item for item in report["results"] if item["challenge_uuid"] == "challenge_rewrite"
    )
    assert result["issues"] == [
        "zero_hidden_tests",
        "all_inputs_empty",
    ]
    serialized = json.dumps(report)
    assert "expected_stdout" not in serialized
    assert "solution_code" not in serialized
