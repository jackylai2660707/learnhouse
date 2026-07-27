"""Self-hosted embedding service exposing an OpenAI-compatible /v1/embeddings API.

Exists because the configured chat provider answers 404 on /v1/embeddings for
every model it advertises. Without this service the API silently degraded to a
blake2b lexical hash, which is not semantic search at all: cosine('volcano',
'volcanoes') measured 0.0 against the stored vectors.

The API surface deliberately mirrors OpenAI's embeddings endpoint so the caller
(`src/services/ai/base.py`) needs no new transport — only a separate base URL
(`LEARNHOUSE_EMBEDDING_BASE_URL`) pointed at this service.

Model: jinaai/jina-embeddings-v2-base-zh via fastembed (ONNX, CPU).
  * 768 dimensions natively, so CourseEmbedding's Vector(768) needs no padding
    or truncation.
  * Bilingual zh/en, which matters here — course content is Traditional Chinese
    and English, and questions cross languages.
  * ~640MB of weights baked into the image at build time, so the container
    never reaches the network at runtime.

Design notes:
  * Vectors are L2-normalised here, so pgvector's `<=>` cosine distance is
    exactly `1 - dot`, and callers can compare scores across requests.
  * A model-name mismatch is a 400, not a silent substitution. Quietly serving
    a different model than the caller asked for is the same failure mode this
    service was built to eliminate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("embeddings")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODEL_NAME = os.environ.get("EMBEDDINGS_MODEL", "jinaai/jina-embeddings-v2-base-zh")
# Native output width of MODEL_NAME. Asserted against the real model at startup
# so a model swap that changes the width fails loudly instead of writing
# wrong-sized vectors into a Vector(768) column.
EMBEDDINGS_DIMENSIONS = int(os.environ.get("EMBEDDINGS_DIMENSIONS", "768"))
MODEL_CACHE_PATH = os.environ.get("EMBEDDINGS_CACHE_PATH", "/models")

# Extra names accepted for the served model. Lets an operator keep a generic
# id in LEARNHOUSE_OPENAI_EMBEDDING_MODEL without the service pretending to be
# something it is not — the response always reports the real model.
MODEL_ALIASES = {
    alias.strip()
    for alias in os.environ.get("EMBEDDINGS_MODEL_ALIASES", "").split(",")
    if alias.strip()
}

MAX_INPUTS = int(os.environ.get("EMBEDDINGS_MAX_INPUTS", "128"))
MAX_INPUT_CHARS = int(os.environ.get("EMBEDDINGS_MAX_INPUT_CHARS", "32000"))
# ONNX runs multi-threaded internally, so parallel requests mostly fight over
# the same cores. Two in flight keeps the 4-core host responsive; the rest queue.
MAX_CONCURRENCY = int(os.environ.get("EMBEDDINGS_MAX_CONCURRENCY", "2"))
QUEUE_WAIT_SECONDS = float(os.environ.get("EMBEDDINGS_QUEUE_WAIT_SECONDS", "60"))
ONNX_THREADS = int(os.environ.get("EMBEDDINGS_THREADS", "2"))

# Optional shared secret. The service is not published to the host, so this is
# defence in depth for anything else on the internal network.
API_KEY = os.environ.get("EMBEDDINGS_API_KEY") or None

app = FastAPI(title="LearnHouse Embeddings", docs_url=None, redoc_url=None)
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
_model: Any = None


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    # Accepted and ignored: this model has one native width and never projects.
    # A caller asking for a different width gets a 400 rather than a silent
    # truncation, which is checked in `_validate`.
    dimensions: int | None = None
    encoding_format: str | None = None


def _load_model() -> Any:
    from fastembed import TextEmbedding

    started = time.perf_counter()
    model = TextEmbedding(
        model_name=MODEL_NAME,
        cache_dir=MODEL_CACHE_PATH,
        threads=ONNX_THREADS,
        local_files_only=True,
    )
    logger.info(
        "embeddings.model.loaded model=%s seconds=%.2f",
        MODEL_NAME,
        time.perf_counter() - started,
    )
    return model


@app.on_event("startup")
async def _startup() -> None:
    global _model
    _model = await asyncio.to_thread(_load_model)
    probe = await asyncio.to_thread(_encode, ["startup probe"])
    width = len(probe[0])
    if width != EMBEDDINGS_DIMENSIONS:
        # Refusing to start beats serving vectors the database column will
        # reject — or worse, that a caller silently pads to the right length.
        raise RuntimeError(
            f"model {MODEL_NAME} produced {width}-dim vectors, "
            f"expected {EMBEDDINGS_DIMENSIONS}"
        )


def _encode(texts: list[str]) -> list[list[float]]:
    """Embed texts and L2-normalise, so cosine similarity is a plain dot product."""
    vectors = list(_model.embed(texts))
    normalised: list[list[float]] = []
    for vector in vectors:
        norm = float((vector @ vector) ** 0.5)
        if norm > 0:
            vector = vector / norm
        normalised.append([float(value) for value in vector])
    return normalised


def _authorize(authorization: str | None) -> None:
    if API_KEY is None:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


def _validate(body: EmbeddingRequest) -> list[str]:
    requested = (body.model or "").strip()
    if requested and requested != MODEL_NAME and requested not in MODEL_ALIASES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown model {requested!r}; this service serves {MODEL_NAME!r}. "
                "Set EMBEDDINGS_MODEL_ALIASES to accept another name."
            ),
        )

    if body.dimensions is not None and body.dimensions != EMBEDDINGS_DIMENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"dimensions={body.dimensions} unsupported; "
                f"{MODEL_NAME!r} is natively {EMBEDDINGS_DIMENSIONS}-dimensional"
            ),
        )

    if body.encoding_format not in (None, "float"):
        raise HTTPException(status_code=400, detail="only encoding_format=float is supported")

    texts = [body.input] if isinstance(body.input, str) else list(body.input)
    if not texts:
        raise HTTPException(status_code=400, detail="input must not be empty")
    if len(texts) > MAX_INPUTS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_INPUTS} inputs per request")
    for text in texts:
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="input must be text")
        if len(text) > MAX_INPUT_CHARS:
            raise HTTPException(
                status_code=400, detail=f"input longer than {MAX_INPUT_CHARS} characters"
            )
    # An all-whitespace input yields a meaningless vector; the tokenizer also
    # dislikes a fully empty string. Substitute a single space deterministically.
    return [text if text.strip() else " " for text in texts]


@app.post("/v1/embeddings")
async def create_embeddings(
    body: EmbeddingRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    texts = _validate(body)

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=QUEUE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        # 429 is retryable in the API's error table, so the caller backs off
        # instead of falling through to a degraded embedding.
        raise HTTPException(status_code=429, detail="embedding service busy") from None

    started = time.perf_counter()
    try:
        vectors = await asyncio.to_thread(_encode, texts)
    except Exception as exc:
        logger.exception("embeddings.encode.failed count=%d error=%r", len(texts), exc)
        raise HTTPException(status_code=500, detail="embedding failed") from exc
    finally:
        _semaphore.release()

    logger.info(
        "embeddings.encode.ok count=%d seconds=%.3f",
        len(texts),
        time.perf_counter() - started,
    )

    # `usage` is reported in characters, not tokens: this service does not bill
    # anyone and an approximate count is more honest than a fabricated one.
    total_chars = sum(len(text) for text in texts)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "model": MODEL_NAME,
        "usage": {"prompt_tokens": total_chars, "total_tokens": total_chars},
    }


@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "owned_by": "learnhouse-embeddings",
                "dimensions": EMBEDDINGS_DIMENSIONS,
            }
        ],
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok" if _model is not None else "loading",
        "model": MODEL_NAME,
        "dimensions": EMBEDDINGS_DIMENSIONS,
        "concurrency": MAX_CONCURRENCY,
        "slots_free": _semaphore._value,  # noqa: SLF001 - cheap liveness signal
    }
