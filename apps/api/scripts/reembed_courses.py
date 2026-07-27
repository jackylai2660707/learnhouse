#!/usr/bin/env python3
"""Re-index course content with the real embedding backend.

Legacy deployments may contain blake2b lexical fingerprints because the old
code silently substituted a hash when a chat provider returned 404 on
``/v1/embeddings``. Those rows are indistinguishable from real embeddings by
shape but retrieve nothing — measured cosine("volcano", "volcanoes") was 0.0.

With ``LEARNHOUSE_EMBEDDING_BASE_URL`` pointed at the self-hosted embedding
service, this rebuilds every course's index from its current content.

Usage (inside the API container)::

    /app/api/.venv/bin/python scripts/reembed_courses.py --dry-run
    /app/api/.venv/bin/python scripts/reembed_courses.py
    /app/api/.venv/bin/python scripts/reembed_courses.py --course-id 7 --verify
    /app/api/.venv/bin/python scripts/reembed_courses.py --verify-only

Safety:
* ``embed_course_content`` builds all new vectors before deleting the old ones,
  so a mid-run backend failure leaves the existing index intact.
* A course whose embedding call fails is reported and skipped; the run
  continues and exits non-zero so a wrapper notices.
* ``--verify-only`` is read-only and checks bilingual semantic quality plus
  pgvector, HNSW, dimensions, and stored-vector norms. It is safe to schedule.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from sqlmodel import select

from src.core.events.database import _async_session_factory
from src.db.courses.courses import Course
from src.services.ai.base import embedding_backend_description
from src.services.ai.rag.embedding_service import (
    EmbeddingUnavailableError,
    embed_course_content,
    embed_single_text,
)

logger = logging.getLogger("reembed")

# Probe pair used by --verify. Near-synonyms that share no useful token
# structure for a hash ("volcano"/"volcanoes" hash to unrelated buckets),
# versus text about a different topic entirely.
SEMANTIC_PROBES = {
    "en": ("volcano eruption", "erupting volcano", "accounting spreadsheet"),
    "zh-Hant": ("火山爆發", "火山噴發", "會計試算表"),
}
MIN_RELATED_SIMILARITY = 0.7
MIN_SEPARATION = 0.3
EXPECTED_DIMENSIONS = 768
MIN_PGVECTOR_VERSION = (0, 8, 0)


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = sum(a * a for a in left) ** 0.5 * sum(b * b for b in right) ** 0.5
    return dot / norm if norm else 0.0


async def verify_semantic() -> dict[str, Any]:
    """Prove the backend produces meaning-bearing Chinese and English vectors."""
    checks: dict[str, Any] = {}
    all_ok = True
    for language, (first_text, second_text, unrelated_text) in SEMANTIC_PROBES.items():
        results = [
            await embed_single_text(first_text),
            await embed_single_text(second_text),
            await embed_single_text(unrelated_text),
        ]
        first, second, other = (result.vector for result in results)
        related = cosine(first, second)
        unrelated = cosine(first, other)
        dimensions_ok = all(len(vector) == EXPECTED_DIMENSIONS for vector in (first, second, other))
        degraded = any(result.degraded for result in results)
        ok = (
            dimensions_ok
            and not degraded
            and related >= MIN_RELATED_SIMILARITY
            and (related - unrelated) >= MIN_SEPARATION
        )
        all_ok = all_ok and ok
        checks[language] = {
            "related_similarity": round(related, 4),
            "unrelated_similarity": round(unrelated, 4),
            "separation": round(related - unrelated, 4),
            "dimensions": len(first),
            "degraded": degraded,
            "healthy": ok,
        }
    return {"semantic": all_ok, "language_checks": checks}


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = []
    for token in value.split(".")[:3]:
        digits = "".join(character for character in token if character.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


async def verify_vector_database(session) -> dict[str, Any]:
    """Check the stored index without reading course content or changing data."""
    result = await session.execute(
        text(
            """
            SELECT
              COALESCE((SELECT extversion FROM pg_extension WHERE extname = 'vector'), '') AS pgvector_version,
              COALESCE(pg_get_indexdef(to_regclass('ix_course_embedding_embedding_hnsw_cosine')), '') AS index_definition,
              count(*) AS vectors_total,
              count(*) FILTER (WHERE embedding IS NULL OR vector_dims(embedding) <> 768) AS invalid_dimensions,
              count(*) FILTER (
                WHERE embedding IS NOT NULL
                  AND abs(vector_norm(embedding) - 1.0) > 0.01
              ) AS invalid_norms
            FROM course_embedding
            """
        )
    )
    row = result.mappings().one()
    version = str(row["pgvector_version"])
    index_definition = str(row["index_definition"])
    vectors_total = int(row["vectors_total"])
    version_ok = _version_tuple(version) >= MIN_PGVECTOR_VERSION
    hnsw_ok = "USING hnsw" in index_definition and "vector_cosine_ops" in index_definition
    healthy = (
        version_ok
        and hnsw_ok
        and vectors_total > 0
        and int(row["invalid_dimensions"]) == 0
        and int(row["invalid_norms"]) == 0
    )
    return {
        "healthy": healthy,
        "pgvector_version": version,
        "minimum_pgvector_version": ".".join(map(str, MIN_PGVECTOR_VERSION)),
        "version_ok": version_ok,
        "hnsw_cosine_index": hnsw_ok,
        "vectors_total": vectors_total,
        "invalid_dimensions": int(row["invalid_dimensions"]),
        "invalid_norms": int(row["invalid_norms"]),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    backend, base_url = embedding_backend_description()
    report: dict[str, Any] = {
        "backend": backend,
        "dry_run": args.dry_run,
        "courses": [],
        "counts": {"indexed": 0, "skipped": 0, "failed": 0, "degraded": 0},
    }
    if not args.verify_only:
        report["base_url"] = base_url

    if backend != "dedicated":
        # Re-embedding through the chat provider is what produced the hash
        # vectors in the first place. Refuse rather than rewrite them.
        logger.error(
            "No dedicated embedding endpoint configured "
            "(LEARNHOUSE_EMBEDDING_BASE_URL). Refusing to re-embed via the "
            "chat provider, which does not serve /embeddings."
        )
        report["error"] = "no_dedicated_embedding_endpoint"
        return report

    if args.verify_only:
        async with _async_session_factory() as session:
            database_check = await verify_vector_database(session)
        try:
            semantic_check = await verify_semantic()
            report["verification"] = {
                **semantic_check,
                "database": database_check,
                "healthy": bool(semantic_check["semantic"] and database_check["healthy"]),
            }
        except EmbeddingUnavailableError as exc:
            report["verification"] = {
                "semantic": False,
                "database": database_check,
                "healthy": False,
                "error_code": exc.code,
            }
        return report

    async with _async_session_factory() as session:

        statement = select(Course).order_by(Course.id)
        if args.course_id:
            statement = statement.where(Course.id.in_(args.course_id))  # type: ignore[attr-defined]
        courses = (await session.exec(statement)).all()

        # Row counts before the swap, so the report shows what actually moved.
        before = dict(
            (await session.execute(
                text(
                    "SELECT course_id, count(*) FROM course_embedding "
                    "GROUP BY course_id"
                )
            )).all()
        )

        logger.info(
            "Re-embedding %d course(s) via %s (%s existing vectors)",
            len(courses),
            base_url,
            sum(before.values()),
        )

        for course in courses:
            entry: dict[str, Any] = {
                "course_id": course.id,
                "course_uuid": course.course_uuid,
                "name": course.name,
                "vectors_before": before.get(course.id, 0),
            }

            if args.dry_run:
                entry["status"] = "dry_run"
                report["counts"]["skipped"] += 1
                report["courses"].append(entry)
                logger.info(
                    "[dry-run] course %s (%s): %d existing vectors",
                    course.id,
                    course.name,
                    entry["vectors_before"],
                )
                continue

            started = time.perf_counter()
            try:
                result = await embed_course_content(course.id, course.org_id, session)
            except EmbeddingUnavailableError as exc:
                entry["status"] = "failed"
                entry["error_code"] = exc.code
                report["counts"]["failed"] += 1
                report["courses"].append(entry)
                logger.error(
                    "course %s: embeddings unavailable (%s) — index left untouched",
                    course.id,
                    exc.code,
                )
                continue
            except Exception as exc:  # noqa: BLE001 - one bad course must not stop the run
                entry["status"] = "failed"
                entry["error_code"] = type(exc).__name__
                report["counts"]["failed"] += 1
                report["courses"].append(entry)
                logger.exception("course %s: indexing failed", course.id)
                continue

            entry["vectors_after"] = result.chunks_indexed
            entry["seconds"] = round(time.perf_counter() - started, 2)
            entry["degraded"] = result.degraded
            entry["degraded_reason"] = result.degraded_reason
            if result.degraded:
                entry["status"] = "degraded"
                report["counts"]["degraded"] += 1
                logger.error(
                    "course %s: indexed %d chunks with LEXICAL HASHES (%s) — "
                    "this is not semantic search",
                    course.id,
                    result.chunks_indexed,
                    result.degraded_reason,
                )
            elif result.chunks_indexed == 0:
                entry["status"] = "empty"
                report["counts"]["skipped"] += 1
                logger.info("course %s: no indexable content", course.id)
            else:
                entry["status"] = "indexed"
                report["counts"]["indexed"] += 1
                logger.info(
                    "course %s (%s): %d chunks in %.1fs",
                    course.id,
                    course.name,
                    result.chunks_indexed,
                    entry["seconds"],
                )

            report["courses"].append(entry)

        if args.verify:
            database_check = await verify_vector_database(session)

    if args.verify:
        try:
            semantic_check = await verify_semantic()
            report["verification"] = {
                **semantic_check,
                "database": database_check,
                "healthy": bool(semantic_check["semantic"] and database_check["healthy"]),
            }
        except EmbeddingUnavailableError as exc:
            report["verification"] = {
                "semantic": False,
                "healthy": False,
                "error_code": exc.code,
            }

    return report


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--course-id",
        type=int,
        action="append",
        help="Limit to these course ids (repeatable). Default: every course.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List courses and current vector counts without writing.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After indexing, prove the vectors are semantic rather than hashes.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Read-only bilingual semantic and vector-database verification.",
    )
    parser.add_argument("--report", type=Path, help="Write the JSON report here.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_only and (args.course_id or args.dry_run or args.verify):
        parser.error("--verify-only cannot be combined with indexing options")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    report = asyncio.run(run(args))
    if args.report:
        path = args.report.expanduser().resolve()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report.get("error"):
        return 2
    if report["counts"]["failed"] or report["counts"]["degraded"]:
        return 1
    verification = report.get("verification")
    if verification is not None and not verification.get("healthy"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
