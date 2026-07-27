#!/usr/bin/env python3
"""Run sanitized live diagnostics for optional LearnHouse integrations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from config.config import get_learnhouse_config
from src.services.ai.base import (
    AIProviderError,
    ImageGenerationProviderError,
    embedding_backend_description,
    generate_openai_compatible_image,
    get_gemini_client,
    validate_generated_image,
)
from src.services.ai.rag.embedding_service import (
    EmbeddingUnavailableError,
    generate_embeddings,
)
from src.services.email.utils import email_configuration_status
from src.services.judge0 import Judge0ServiceError, submit_judge0


Diagnostic = dict[str, Any]


def _latency_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


async def diagnose_text() -> Diagnostic:
    started_at = time.perf_counter()
    try:
        client = get_gemini_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": "這是系統連線測試。請只回答 READY_42，不要加入其他內容。"
                        }
                    ],
                }
            ],
        )
        text = response.text
        return {
            "code": "ai_text_ready" if "READY_42" in text else "ai_text_unexpected_output",
            "latency_ms": _latency_ms(started_at),
            "marker_matched": "READY_42" in text,
            "status": "ready" if "READY_42" in text else "degraded",
        }
    except AIProviderError as exc:
        return {
            "code": exc.code,
            "latency_ms": _latency_ms(started_at),
            "retryable": exc.retryable,
            "status": "failed",
        }
    except Exception as exc:
        return {
            "code": "ai_text_diagnostic_failed",
            "error_type": type(exc).__name__,
            "latency_ms": _latency_ms(started_at),
            "status": "failed",
        }


async def diagnose_embedding() -> Diagnostic:
    """Check the embedding backend, and prove it is semantic rather than lexical.

    A dimension count alone cannot tell real embeddings from the hash
    fallback — both are 768 non-zero floats. The similarity probe can:
    "volcano"/"volcanoes" scores ~0.9 with a real model and 0.0 with hashes.
    """
    started_at = time.perf_counter()
    backend, base_url = embedding_backend_description()
    probes = ["volcano", "volcanoes", "the water cycle and evaporation"]

    try:
        result = await generate_embeddings(probes)
    except EmbeddingUnavailableError as exc:
        return {
            "backend": backend,
            "base_url": base_url,
            "code": exc.code,
            "latency_ms": _latency_ms(started_at),
            "status": "failed",
        }

    values = result.vectors[0] if result.vectors else []

    def _cosine(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right))
        norm = (
            sum(a * a for a in left) ** 0.5 * sum(b * b for b in right) ** 0.5
        )
        return round(dot / norm, 4) if norm else 0.0

    related = _cosine(result.vectors[0], result.vectors[1])
    unrelated = _cosine(result.vectors[0], result.vectors[2])
    # A real model puts near-synonyms far above unrelated text. The hash
    # fallback cannot: it has no notion of meaning, only shared tokens.
    semantic = related > 0.7 and related - unrelated > 0.3

    return {
        "backend": backend,
        "base_url": base_url,
        "code": (
            "ai_embedding_hash_fallback"
            if result.degraded
            else "ai_embedding_ready"
            if semantic
            else "ai_embedding_not_semantic"
        ),
        "dimensions": len(values),
        "latency_ms": _latency_ms(started_at),
        "nonzero": any(value != 0 for value in values),
        "provider_code": result.degraded_reason,
        "semantic": semantic,
        "similarity_related": related,
        "similarity_unrelated": unrelated,
        "status": "ready" if semantic and not result.degraded else "degraded",
    }


async def diagnose_image(*, allow_costly_image: bool) -> Diagnostic:
    if not allow_costly_image:
        return {
            "code": "ai_image_explicit_approval_required",
            "status": "skipped",
        }
    started_at = time.perf_counter()
    try:
        data, image_format, _ = await asyncio.to_thread(
            generate_openai_compatible_image,
            "為澳門小學科學課製作一張簡潔、無文字、展示水循環的教育插圖。",
            size="1024x1024",
            quality="low",
        )
        detected_format, width, height = validate_generated_image(
            data,
            claimed_format=image_format,
        )
        return {
            "bytes": len(data),
            "code": "ai_image_ready",
            "format": detected_format,
            "height": height,
            "latency_ms": _latency_ms(started_at),
            "status": "ready",
            "width": width,
        }
    except ImageGenerationProviderError as exc:
        return {
            "code": exc.code,
            "latency_ms": _latency_ms(started_at),
            "provider_status": exc.status_code,
            "retryable": exc.retryable,
            "status": "failed",
        }
    except Exception as exc:
        return {
            "code": "ai_image_diagnostic_failed",
            "error_type": type(exc).__name__,
            "latency_ms": _latency_ms(started_at),
            "status": "failed",
        }


async def diagnose_judge0() -> Diagnostic:
    started_at = time.perf_counter()
    config = get_learnhouse_config().judge0_config
    if config is None:
        return {"code": "judge0_not_configured", "status": "degraded"}
    try:
        result = await submit_judge0(
            config,
            language_id=71,
            source_code="print(6 * 7)",
            stdin="",
        )
        accepted = result.get("status", {}).get("id") == 3
        output_matches = result.get("stdout", "").strip() == "42"
        return {
            "code": "judge0_ready" if accepted and output_matches else "judge0_unexpected_output",
            "latency_ms": _latency_ms(started_at),
            "output_matched": output_matches,
            "status": "ready" if accepted and output_matches else "degraded",
        }
    except Judge0ServiceError as exc:
        return {
            "code": exc.code,
            "latency_ms": _latency_ms(started_at),
            "retryable": exc.retryable,
            "status": "failed",
        }


async def diagnose_email() -> Diagnostic:
    started_at = time.perf_counter()
    mailing = get_learnhouse_config().mailing_config
    result = email_configuration_status(mailing)
    return {
        "code": result.code,
        "latency_ms": _latency_ms(started_at),
        "provider": mailing.email_provider,
        "status": "configured" if result.configured else "degraded",
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)


async def run_diagnostics(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    selected = {
        "ai_text": args.text or args.all,
        "ai_embedding": args.embedding or args.all,
        "ai_image": args.image or args.all,
        "judge0": args.judge0 or args.all,
        "email": args.email or args.all,
    }
    runners: dict[str, Callable[[], Awaitable[Diagnostic]]] = {
        "ai_text": diagnose_text,
        "ai_embedding": diagnose_embedding,
        "ai_image": lambda: diagnose_image(allow_costly_image=args.allow_costly_image),
        "judge0": diagnose_judge0,
        "email": diagnose_email,
    }
    results: dict[str, Diagnostic] = {}
    for name, enabled in selected.items():
        if enabled:
            results[name] = await runners[name]()

    statuses = {result["status"] for result in results.values()}
    overall = "failed" if "failed" in statuses else "degraded" if statuses & {"degraded", "skipped"} else "ready"
    exit_code = 2 if overall == "failed" else 1 if overall == "degraded" else 0
    return {"overall": overall, "results": results}, exit_code


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--text", action="store_true")
    parser.add_argument("--embedding", action="store_true")
    parser.add_argument("--image", action="store_true")
    parser.add_argument("--judge0", action="store_true")
    parser.add_argument("--email", action="store_true")
    parser.add_argument(
        "--allow-costly-image",
        action="store_true",
        help="Allow one synthetic image-generation request that may incur provider cost.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not any((args.all, args.text, args.embedding, args.image, args.judge0, args.email)):
        parser.error("select at least one capability or use --all")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = parse_args(argv)
    payload, exit_code = asyncio.run(run_diagnostics(args))
    if args.report:
        _write_report(args.report.expanduser().resolve(), payload)
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
