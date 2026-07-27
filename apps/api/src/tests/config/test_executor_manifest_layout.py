import runpy
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
EXECUTOR_APP = REPO_ROOT / "docker" / "executor" / "app.py"


def test_executor_manifest_candidates_support_shallow_image_path():
    namespace = runpy.run_path(str(EXECUTOR_APP))

    candidates = namespace["_manifest_candidates_for"](Path("/app/app.py"))

    assert candidates[0] == Path("/app/code-language-capabilities.json")
    assert all(
        candidate.name == "code-language-capabilities.json"
        for candidate in candidates
    )
