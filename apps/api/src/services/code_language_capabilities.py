"""Load the repository-owned code execution capability manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _manifest_path() -> Path:
    service_path = Path(__file__).resolve()
    candidate = service_path.parents[2] / "config" / "code-language-capabilities.json"
    if candidate.is_file():
        return candidate
    raise RuntimeError("code language capability manifest is missing")


def _load_manifest() -> dict[str, Any]:
    with _manifest_path().open(encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    if manifest.get("version") != 1:
        raise RuntimeError("unsupported code language capability manifest version")
    return manifest


CODE_LANGUAGE_CAPABILITIES = _load_manifest()
EXECUTOR_LANGUAGE_IDS = frozenset(
    int(language_id)
    for language_id in CODE_LANGUAGE_CAPABILITIES["executor_language_ids"]
)
API_LANGUAGE_ADAPTERS = {
    int(adapter["language_id"]): adapter
    for adapter in CODE_LANGUAGE_CAPABILITIES["api_adapters"]
}
API_EXECUTION_LANGUAGE_IDS = frozenset(
    (*EXECUTOR_LANGUAGE_IDS, *API_LANGUAGE_ADAPTERS.keys())
)
PREVIEW_LANGUAGE_IDS = frozenset(
    int(language_id)
    for language_id in CODE_LANGUAGE_CAPABILITIES["preview_language_ids"]
)

_SQLITE_ADAPTERS = [
    adapter
    for adapter in API_LANGUAGE_ADAPTERS.values()
    if adapter.get("requires") == ["sqlite_db_path"]
]
if len(_SQLITE_ADAPTERS) != 1:
    raise RuntimeError("exactly one SQLite code language adapter is required")

SQL_LANGUAGE_ID = int(_SQLITE_ADAPTERS[0]["language_id"])
PYTHON3_LANGUAGE_ID = int(_SQLITE_ADAPTERS[0]["executor_language_id"])
if PYTHON3_LANGUAGE_ID not in EXECUTOR_LANGUAGE_IDS:
    raise RuntimeError("the SQLite adapter must target a bundled executor runtime")
if len(PREVIEW_LANGUAGE_IDS) != 1:
    raise RuntimeError("exactly one browser preview language is required")
HTML_PREVIEW_LANGUAGE_ID = next(iter(PREVIEW_LANGUAGE_IDS))
