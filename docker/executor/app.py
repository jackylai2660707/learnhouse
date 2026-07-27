"""Self-hosted code execution sandbox exposing a Judge0-compatible API subset.

Replaces Judge0, which cannot run on this host: Judge0's `isolate` backend
requires cgroup v1, and this machine runs cgroup v2 (`cgroup2fs`). Switching
would need a kernel boot parameter plus a full host reboot, which is not
acceptable on a box hosting many unrelated services.

The API surface deliberately mirrors Judge0 so the existing callers
(`src/services/judge0.py`, `src/routers/code_execution.py`) need no changes
beyond pointing `LEARNHOUSE_JUDGE0_API_URL` at this service.

Isolation is provided by short-lived Docker containers:
  --network=none      no egress
  --memory / --cpus   resource caps
  --pids-limit        fork bomb containment
  --read-only         immutable rootfs, writable work dir only
  --cap-drop=ALL      no capabilities
  --security-opt no-new-privileges
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("executor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Judge0 status ids. Callers branch on `status.id == 3` (Accepted); the rest
# are surfaced for display only.
# ---------------------------------------------------------------------------
STATUS_ACCEPTED = (3, "Accepted")
STATUS_WRONG_ANSWER = (4, "Wrong Answer")
STATUS_TIME_LIMIT = (5, "Time Limit Exceeded")
STATUS_COMPILE_ERROR = (6, "Compilation Error")
STATUS_RUNTIME_ERROR = (11, "Runtime Error (NZEC)")
STATUS_INTERNAL_ERROR = (13, "Internal Error")

MAX_SOURCE_CODE_LENGTH = 200_000
MAX_STDIN_LENGTH = 100_000
MAX_ADDITIONAL_FILES_LENGTH = 4_000_000
MAX_OUTPUT_BYTES = 64 * 1024

WALL_TIMEOUT_SECONDS = float(os.environ.get("EXECUTOR_TIMEOUT_SECONDS", "10"))
MEMORY_LIMIT = os.environ.get("EXECUTOR_MEMORY_LIMIT", "256m")
CPU_LIMIT = os.environ.get("EXECUTOR_CPU_LIMIT", "1.0")
PIDS_LIMIT = os.environ.get("EXECUTOR_PIDS_LIMIT", "64")
TMPFS_SIZE = os.environ.get("EXECUTOR_TMPFS_SIZE", "64m")
# Tuned by measurement on this 4-core host, 20 concurrent submissions:
#   6 slots -> 20/20 accepted, avg 0.94s, max 1.16s
#   8 slots -> 0/20 accepted, every run hit the wall clock limit
# The degradation is a cliff, not a slope: past ~6 sandboxes the CPU contention
# means nothing finishes inside its budget. Raise only alongside host cores.
MAX_CONCURRENCY = int(os.environ.get("EXECUTOR_MAX_CONCURRENCY", "6"))
# Once every slot is busy, queueing silently turns into a timeout for someone.
# Wait briefly, then fail fast with a retryable signal the API already handles.
QUEUE_WAIT_SECONDS = float(os.environ.get("EXECUTOR_QUEUE_WAIT_SECONDS", "15"))

CLIENT_ID = os.environ.get("EXECUTOR_CLIENT_ID") or None
CLIENT_SECRET = os.environ.get("EXECUTOR_CLIENT_SECRET") or None

WORK_ROOT = os.environ.get("EXECUTOR_WORK_ROOT", "/executor-work")
# Path to WORK_ROOT as seen by the Docker daemon. When this service runs inside
# a container, bind mounts are resolved by the host daemon, so a host-side path
# is required. Defaults to WORK_ROOT for bare-metal runs.
HOST_WORK_ROOT = os.environ.get("EXECUTOR_HOST_WORK_ROOT", WORK_ROOT)


class Runtime(BaseModel):
    image: str
    filename: str
    command: list[str]
    # Run inside the sandbox before `command`; a non-zero exit is reported as a
    # compilation error rather than a runtime error.
    compile_command: list[str] | None = None


PY_IMAGE = os.environ.get("EXECUTOR_IMAGE_PYTHON", "learnhouse-exec-python:latest")
NODE_IMAGE = os.environ.get("EXECUTOR_IMAGE_NODE", "learnhouse-exec-node:latest")
C_IMAGE = os.environ.get("EXECUTOR_IMAGE_C", "learnhouse-exec-c:latest")
JAVA_IMAGE = os.environ.get("EXECUTOR_IMAGE_JAVA", "learnhouse-exec-java:latest")


@dataclass(frozen=True)
class RuntimeImageSpec:
    """A local sandbox image that the executor owns and can install."""

    image: str
    dockerfile: str


# Runtime images are intentionally separate from the long-running executor so
# submissions do not pay an image-build cost.  The executor installs missing
# tags at startup, using these bundled Dockerfiles and the host Docker daemon.
RUNTIME_IMAGE_BUILD_CONTEXT = Path("/app/images")
RUNTIME_IMAGE_LOCK_PATH = Path("/tmp/learnhouse-executor-runtime-images.lock")
RUNTIME_IMAGE_SPECS = (
    RuntimeImageSpec(PY_IMAGE, "python.Dockerfile"),
    RuntimeImageSpec(NODE_IMAGE, "node.Dockerfile"),
    RuntimeImageSpec(C_IMAGE, "c.Dockerfile"),
    RuntimeImageSpec(JAVA_IMAGE, "java.Dockerfile"),
)


def _runtime_image_exists(image: str) -> bool:
    """Return whether the host daemon has the configured runtime tag."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _build_runtime_image(spec: RuntimeImageSpec) -> None:
    """Build one bundled runtime image without exposing Docker output in logs."""
    try:
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                spec.image,
                "-f",
                str(RUNTIME_IMAGE_BUILD_CONTEXT / spec.dockerfile),
                str(RUNTIME_IMAGE_BUILD_CONTEXT),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"unable to build required executor runtime image {spec.image}"
        ) from exc


def ensure_runtime_images() -> None:
    """Install only absent runtime tags, serially, under a process-safe lock."""
    RUNTIME_IMAGE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNTIME_IMAGE_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            for spec in RUNTIME_IMAGE_SPECS:
                if _runtime_image_exists(spec.image):
                    continue
                logger.info("executor.runtime_image.build image=%s", spec.image)
                _build_runtime_image(spec)

            missing = [
                spec.image
                for spec in RUNTIME_IMAGE_SPECS
                if not _runtime_image_exists(spec.image)
            ]
            if missing:
                raise RuntimeError("required executor runtime images are unavailable")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def runtime_images_ready() -> bool:
    """Fail closed if Docker is unavailable or any configured runtime tag is absent."""
    return all(_runtime_image_exists(spec.image) for spec in RUNTIME_IMAGE_SPECS)


# Judge0 language ids are kept identical so existing stored submissions remain
# valid while LearnHouse continues to use this bundled local executor.
# Note: 82 (SQL) never reaches this service — the API layer rewrites it into a
# Python program that queries the bundled SQLite file.
RUNTIMES: dict[int, Runtime] = {
    71: Runtime(  # Python 3
        image=PY_IMAGE,
        filename="main.py",
        command=["python3", "-I", "-B", "main.py"],
    ),
    63: Runtime(  # JavaScript (Node)
        image=NODE_IMAGE,
        filename="main.js",
        command=["node", "main.js"],
    ),
    74: Runtime(  # TypeScript
        image=NODE_IMAGE,
        filename="main.ts",
        # Invoke tsx's entrypoint directly. Going through `npx` costs several
        # seconds of package resolution per run, which pushed TypeScript close
        # to the wall-clock limit on a cold sandbox.
        command=["node", "/usr/local/lib/node_modules/tsx/dist/cli.mjs", "main.ts"],
    ),
    50: Runtime(  # C
        image=C_IMAGE,
        filename="main.c",
        compile_command=["gcc", "-O2", "-std=c17", "-w", "main.c", "-o", "main", "-lm"],
        command=["./main"],
    ),
    54: Runtime(  # C++
        image=C_IMAGE,
        filename="main.cpp",
        compile_command=["g++", "-O2", "-std=c++20", "-w", "main.cpp", "-o", "main"],
        command=["./main"],
    ),
    62: Runtime(  # Java
        image=JAVA_IMAGE,
        filename="Main.java",
        compile_command=["javac", "-nowarn", "Main.java"],
        command=["java", "-XX:+UseSerialGC", "-Xshare:auto", "Main"],
    ),
}

def _manifest_candidates_for(module_path: Path) -> tuple[Path, ...]:
    resolved = module_path.resolve()
    return (
        resolved.with_name("code-language-capabilities.json"),
        *(
            parent
            / "apps"
            / "api"
            / "config"
            / "code-language-capabilities.json"
            for parent in resolved.parents
        ),
    )


_manifest_candidates = _manifest_candidates_for(Path(__file__))
_manifest_path = next(
    (candidate for candidate in _manifest_candidates if candidate.is_file()),
    None,
)
if _manifest_path is None:
    raise RuntimeError("code language capability manifest is missing")
with _manifest_path.open(encoding="utf-8") as capability_file:
    _capability_manifest = json.load(capability_file)
_manifest_runtime_ids = {
    int(language_id)
    for language_id in _capability_manifest.get("executor_language_ids", [])
}
if set(RUNTIMES) != _manifest_runtime_ids:
    raise RuntimeError(
        "executor runtimes do not match apps/api/config/code-language-capabilities.json"
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Docker builds can take several minutes on a clean host.  Keep them off
    # the event loop, but do not accept submissions until all required local
    # runtimes are present.
    await asyncio.to_thread(ensure_runtime_images)
    yield


app = FastAPI(
    title="LearnHouse Executor", docs_url=None, redoc_url=None, lifespan=lifespan
)
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


class SubmissionRequest(BaseModel):
    language_id: int = Field(ge=1)
    source_code: str
    stdin: str = ""
    additional_files: str | None = None


def _truncate(raw: bytes) -> str | None:
    """Decode process output, capping size so one submission cannot flood the API."""
    if not raw:
        return None
    if len(raw) > MAX_OUTPUT_BYTES:
        raw = raw[:MAX_OUTPUT_BYTES] + b"\n[output truncated]"
    return raw.decode("utf-8", errors="replace")


def _extract_additional_files(encoded: str, dest: str) -> None:
    """Unpack the base64 zip Judge0 callers send, rejecting path traversal."""
    try:
        blob = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid additional_files") from exc

    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if (
                    path.is_absolute()
                    or not path.parts
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or "\x00" in name
                ):
                    raise HTTPException(
                        status_code=400, detail="invalid additional_files entry"
                    )
                target = os.path.join(dest, *path.parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 64)
    except HTTPException:
        raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="invalid additional_files") from exc


COMPILE_MARKER = "__LH_COMPILE_FAILED__"


def _shell_quote(parts: list[str]) -> str:
    return " ".join("'" + p.replace("'", "'\\''") + "'" for p in parts)


def _entrypoint(runtime: Runtime) -> list[str]:
    """Build the in-sandbox command.

    Compilation must happen inside the sandbox (never on the host), so
    compile-and-run is expressed as a single shell invocation. A failed compile
    prints a marker on stderr, which `_run` maps to Judge0's Compilation Error
    status. `exec` replaces the shell so the program owns the process slot.
    """
    if runtime.compile_command is None:
        return runtime.command
    return [
        "/bin/sh",
        "-c",
        f"{_shell_quote(runtime.compile_command)} || "
        f"{{ echo {COMPILE_MARKER} >&2; exit 1; }}; "
        f"exec {_shell_quote(runtime.command)}",
    ]


def _docker_args(runtime: Runtime, host_dir: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--network=none",
        f"--memory={MEMORY_LIMIT}",
        f"--memory-swap={MEMORY_LIMIT}",
        f"--cpus={CPU_LIMIT}",
        f"--pids-limit={PIDS_LIMIT}",
        # Compiled languages write object files and binaries into /work, so the
        # rootfs stays read-only while the bind-mounted work dir stays writable.
        "--read-only",
        f"--tmpfs=/tmp:size={TMPFS_SIZE},exec",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=1000:1000",
        "--workdir=/work",
        "-v",
        f"{host_dir}:/work",
        runtime.image,
        *_entrypoint(runtime),
    ]


async def _run(request: SubmissionRequest) -> dict[str, Any]:
    runtime = RUNTIMES.get(request.language_id)
    if runtime is None:
        raise HTTPException(status_code=422, detail="unsupported language_id")

    workdir = tempfile.mkdtemp(prefix="exec-", dir=WORK_ROOT)
    # The bind mount is resolved by the host daemon, so translate the path.
    host_dir = os.path.join(HOST_WORK_ROOT, os.path.basename(workdir))
    try:
        if request.additional_files:
            _extract_additional_files(request.additional_files, workdir)

        with open(os.path.join(workdir, runtime.filename), "w", encoding="utf-8") as fh:
            fh.write(request.source_code)

        # The sandbox runs as uid 1000 and needs to write into the work dir
        # (e.g. sqlite journals), so relax ownership on the host side.
        os.chmod(workdir, 0o777)
        for root, dirs, files in os.walk(workdir):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o666 if name in files else 0o777)
                except OSError:
                    pass

        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *_docker_args(runtime, host_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(request.stdin.encode()),
                timeout=WALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "status": {"id": STATUS_TIME_LIMIT[0], "description": STATUS_TIME_LIMIT[1]},
                "stdout": None,
                "stderr": None,
                "compile_output": None,
                "message": "wall clock limit exceeded",
                "time": f"{WALL_TIMEOUT_SECONDS:.3f}",
                "memory": None,
                "exit_code": None,
            }

        elapsed = time.perf_counter() - started
        exit_code = proc.returncode

        stderr_text = _truncate(stderr)
        compile_output = None

        if stderr_text and COMPILE_MARKER in stderr_text:
            # Compilation failed: report the compiler diagnostics in the field
            # callers expect, and keep stderr clean of the internal marker.
            status = STATUS_COMPILE_ERROR
            compile_output = stderr_text.replace(COMPILE_MARKER, "").strip() or None
            stderr_text = None
        elif exit_code == 0:
            status = STATUS_ACCEPTED
        else:
            # 137 is SIGKILL, nearly always the cgroup OOM killer; Judge0 has no
            # distinct id for it, so it stays a runtime error with a hint.
            status = STATUS_RUNTIME_ERROR

        return {
            "status": {"id": status[0], "description": status[1]},
            "stdout": _truncate(stdout),
            "stderr": stderr_text,
            "compile_output": compile_output,
            "message": "out of memory" if exit_code == 137 else None,
            "time": f"{elapsed:.3f}",
            "memory": None,
            "exit_code": exit_code,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/submissions")
async def create_submission(body: SubmissionRequest) -> dict[str, Any]:
    if (
        not body.source_code
        or len(body.source_code) > MAX_SOURCE_CODE_LENGTH
        or len(body.stdin) > MAX_STDIN_LENGTH
        or (
            body.additional_files is not None
            and len(body.additional_files) > MAX_ADDITIONAL_FILES_LENGTH
        )
    ):
        raise HTTPException(status_code=400, detail="request too large")

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=QUEUE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        # 429 maps to judge0_rate_limited (retryable) in the API's error table,
        # which shows students "busy, try again" instead of a wrong verdict.
        raise HTTPException(status_code=429, detail="executor busy") from None

    try:
        try:
            return await _run(body)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "executor.run.failed language_id=%s error=%r",
                body.language_id,
                exc,
            )
            return {
                "status": {
                    "id": STATUS_INTERNAL_ERROR[0],
                    "description": STATUS_INTERNAL_ERROR[1],
                },
                "stdout": None,
                "stderr": None,
                "compile_output": None,
                "message": "internal executor error",
                "time": None,
                "memory": None,
                "exit_code": None,
            }
    finally:
        _semaphore.release()


@app.get("/languages")
async def languages() -> list[dict[str, Any]]:
    """Judge0-compatible language listing.

    The frontend offers many more languages than this service runs, so it needs
    a way to tell which ones actually execute. Mirroring Judge0's endpoint means
    the UI can grey out anything absent here without any bespoke protocol.
    """
    names = {
        50: "C (GCC)",
        54: "C++ (GCC)",
        62: "Java (OpenJDK 21)",
        63: "JavaScript (Node 22)",
        71: "Python (3.12)",
        74: "TypeScript (tsx)",
    }
    return [
        {"id": lang_id, "name": names.get(lang_id, str(lang_id))}
        for lang_id in sorted(RUNTIMES)
    ]


@app.get("/health", response_model=None)
async def health() -> dict[str, Any] | JSONResponse:
    if not runtime_images_ready():
        return JSONResponse(
            status_code=503,
            content={"status": "unready", "detail": "runtime_images_missing"},
        )
    return {
        "status": "ok",
        "languages": sorted(RUNTIMES),
        "concurrency": MAX_CONCURRENCY,
        "slots_free": _semaphore._value,  # noqa: SLF001 - cheap liveness signal
        "timeout_seconds": WALL_TIMEOUT_SECONDS,
    }
