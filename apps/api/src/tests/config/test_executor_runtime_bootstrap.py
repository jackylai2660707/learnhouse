import runpy
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
EXECUTOR_APP = REPO_ROOT / "docker" / "executor" / "app.py"


def _executor_namespace():
    return runpy.run_path(str(EXECUTOR_APP))


def test_runtime_bootstrap_builds_only_missing_images_sequentially(monkeypatch, tmp_path):
    namespace = _executor_namespace()
    module_globals = namespace["ensure_runtime_images"].__globals__
    image_spec = namespace["RuntimeImageSpec"]
    specs = (
        image_spec("example/python:latest", "python.Dockerfile"),
        image_spec("example/node:latest", "node.Dockerfile"),
    )
    present = {"example/python:latest"}
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, int(command[-1] not in present))
        if command[1] == "build":
            present.add(command[3])
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setitem(module_globals, "RUNTIME_IMAGE_SPECS", specs)
    monkeypatch.setitem(module_globals, "RUNTIME_IMAGE_LOCK_PATH", tmp_path / "images.lock")
    monkeypatch.setattr(module_globals["subprocess"], "run", fake_run)

    namespace["ensure_runtime_images"]()

    assert [call for call in calls if call[1] == "build"] == [
        [
            "docker",
            "build",
            "-t",
            "example/node:latest",
            "-f",
            "/app/images/node.Dockerfile",
            "/app/images",
        ]
    ]


async def test_runtime_health_fails_closed_when_an_image_is_missing(monkeypatch):
    namespace = _executor_namespace()
    module_globals = namespace["health"].__globals__
    monkeypatch.setitem(module_globals, "runtime_images_ready", lambda: False)

    response = await namespace["health"]()

    assert response.status_code == 503
    assert response.body == b'{"status":"unready","detail":"runtime_images_missing"}'
