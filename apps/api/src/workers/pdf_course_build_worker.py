from __future__ import annotations

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from src.core.events.database import _async_session_factory, engine
from src.db.organizations import Organization
from src.db.pdf_course_build_jobs import (
    PDFBuildCreditState,
    PDFBuildStage,
    PDFCourseBuildJob,
    utc_now,
)
from src.security.features_utils.usage import (
    refund_ai_credit_once,
    reserve_ai_credit_once,
)
from src.services.ai.base import AIProviderError
from src.services.ai.pdf_build_jobs import (
    claim_next_pdf_build_job,
    cleanup_pdf_build_staging,
    load_staged_pdf_uploads,
)
from src.services.ai.pdf_course_builder import build_course_from_pdfs

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
POLL_SECONDS = 2.0
LEASE_SECONDS = 900
PDF_WORKER_ADVISORY_LOCK_ID = 0x50444642


def _safe_failure(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, AIProviderError):
        return (
            exc.code,
            "AI 暫時無法完成 PDF 建課，系統會按情況自動重試。",
            exc.retryable,
        )
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "pdf_course_build_failed")[:80]
            retryable = bool(detail.get("retryable", exc.status_code >= 500))
        else:
            code = "pdf_course_input_invalid" if exc.status_code < 500 else "pdf_course_build_failed"
            retryable = exc.status_code >= 500
        if exc.status_code in {403, 404}:
            code = "pdf_build_credit_unavailable"
            message = "AI 功能或點數目前不可用，請聯絡管理員後重新建立。"
        elif not retryable:
            message = "PDF 教材無法讀取，請檢查檔案後重新建立。"
        else:
            message = "PDF 建課暫時失敗，系統會按情況自動重試。"
        return code, message, retryable
    if str(exc) == "pdf_build_staging_missing":
        return (
            "pdf_build_staging_missing",
            "暫時無法讀取已上傳的 PDF，系統會自動重試。",
            True,
        )
    if str(exc) == "pdf_build_resume_scope_mismatch":
        return (
            "pdf_build_recovery_scope_invalid",
            "PDF 建課的復原資料不一致，工作已安全停止。",
            False,
        )
    return (
        "pdf_course_build_failed",
        "PDF 建課暫時失敗，系統會按情況自動重試。",
        True,
    )


async def _refund_job(job: PDFCourseBuildJob) -> bool:
    try:
        refund_ai_credit_once(
            job.org_id,
            job.job_uuid,
            job.credit_amount,
            period_token=job.credit_period_token,
        )
        job.credit_state = PDFBuildCreditState.REFUNDED.value
        return True
    except Exception as exc:
        logger.warning(
            "ai.pdf_course.credit_refund_deferred",
            extra={"operation": "refund_pdf_build", "error_code": type(exc).__name__},
        )
        return False


async def _recover_deferred_refunds() -> None:
    async with _async_session_factory() as db_session:
        jobs = list(
            (
                await db_session.exec(
                    select(PDFCourseBuildJob)
                    .where(
                        PDFCourseBuildJob.stage == PDFBuildStage.FAILED.value,
                        PDFCourseBuildJob.credit_state == PDFBuildCreditState.RESERVED.value,
                    )
                    .limit(20)
                )
            ).all()
        )
        changed = False
        for job in jobs:
            changed = await _refund_job(job) or changed
            if changed:
                job.updated_at = utc_now()
                db_session.add(job)
        if changed:
            await db_session.commit()


async def _recover_terminal_staging() -> None:
    async with _async_session_factory() as db_session:
        jobs = list(
            (
                await db_session.exec(
                    select(PDFCourseBuildJob)
                    .where(
                        PDFCourseBuildJob.stage.in_(
                            [PDFBuildStage.DONE.value, PDFBuildStage.FAILED.value]
                        ),
                        PDFCourseBuildJob.staging_cleaned_at.is_(None),
                    )
                    .limit(20)
                )
            ).all()
        )
        for job in jobs:
            if await cleanup_pdf_build_staging(job.job_uuid):
                job.staging_cleaned_at = utc_now()
                job.updated_at = job.staging_cleaned_at
                db_session.add(job)
        if jobs:
            await db_session.commit()


async def process_pdf_build_job(job_uuid: str) -> None:
    async with _async_session_factory() as db_session:
        job = (
            await db_session.exec(
                select(PDFCourseBuildJob).where(PDFCourseBuildJob.job_uuid == job_uuid)
            )
        ).first()
        if not job or job.stage not in {
            PDFBuildStage.EXTRACTING.value,
            PDFBuildStage.PLANNING.value,
            PDFBuildStage.CREATING.value,
            PDFBuildStage.INDEXING.value,
        }:
            return

        uploads = []
        try:
            if job.credit_state == PDFBuildCreditState.PENDING.value:
                await reserve_ai_credit_once(
                    job.org_id,
                    db_session,
                    operation_key=job.job_uuid,
                    amount=job.credit_amount,
                    period_token=job.credit_period_token,
                )
                job.credit_state = PDFBuildCreditState.RESERVED.value
                db_session.add(job)
                await db_session.commit()

            org = await db_session.get(Organization, job.org_id)
            if org is None:
                raise RuntimeError("pdf_build_organization_missing")
            uploads = await load_staged_pdf_uploads(job)

            async def checkpoint(
                stage: str,
                current: int,
                total: int,
                data: dict | None,
            ) -> None:
                job.stage = stage
                job.progress_current = max(0, current)
                job.progress_total = max(0, total)
                job.updated_at = utc_now()
                job.lease_expires_at = utc_now() + timedelta(seconds=LEASE_SECONDS)
                if data:
                    if data.get("plan") is not None:
                        job.plan_checkpoint = data["plan"]
                    if data.get("course_id") is not None:
                        job.course_id = int(data["course_id"])
                    if data.get("chapters") is not None:
                        job.chapters_created = int(data["chapters"])
                    if data.get("activities") is not None:
                        job.activities_created = int(data["activities"])
                    if data.get("sources") is not None:
                        job.source_documents_created = int(data["sources"])
                    if data.get("indexing_status") is not None:
                        job.indexing_status = data["indexing_status"]
                        job.indexing_code = data.get("indexing_code")
                        job.chunks_indexed = int(data.get("chunks") or 0)
                db_session.add(job)
                await db_session.commit()

            options = job.options
            response = await build_course_from_pdfs(
                org=org,
                creator_user_id=job.creator_user_id,
                files=uploads,
                course_name=options.get("course_name"),
                instructions=options.get("instructions"),
                language=options.get("language") or "zh-Hant",
                ai_model=options.get("ai_model") or "gemini-2.5-flash",
                db_session=db_session,
                auto_index=bool(options.get("auto_index", True)),
                checkpoint=checkpoint,
                plan_checkpoint=job.plan_checkpoint,
                resume_course_id=job.course_id,
                build_job_uuid=job.job_uuid,
            )

            job.course_id = response.course_id
            job.chapters_created = response.chapters_created
            job.activities_created = response.activities_created
            job.source_documents_created = response.source_documents_created
            job.indexing_status = response.indexing_status
            job.indexing_code = response.indexing_error
            job.chunks_indexed = response.chunks_indexed
            if response.indexing_status in {"failed", "degraded"}:
                job.warning_code = (
                    "pdf_build_indexing_degraded"
                    if response.indexing_status == "degraded"
                    else "pdf_build_indexing_failed"
                )
                job.warning_message = "課程草稿已建立，但教材搜尋索引暫時不可用；可先由老師檢視內容。"
            job.stage = PDFBuildStage.DONE.value
            job.progress_current = max(job.progress_current, job.progress_total)
            job.credit_state = PDFBuildCreditState.CONSUMED.value
            job.lease_owner = None
            job.lease_expires_at = None
            job.next_attempt_at = None
            job.finished_at = utc_now()
            job.updated_at = job.finished_at
            db_session.add(job)
            await db_session.commit()
            if await cleanup_pdf_build_staging(job.job_uuid):
                job.staging_cleaned_at = utc_now()
                job.updated_at = job.staging_cleaned_at
                db_session.add(job)
                await db_session.commit()
            logger.info(
                "ai.pdf_course.job_done",
                extra={"operation": "pdf_build_worker", "job_uuid": job.job_uuid},
            )
        except Exception as exc:
            await db_session.rollback()
            job = (
                await db_session.exec(
                    select(PDFCourseBuildJob).where(PDFCourseBuildJob.job_uuid == job_uuid)
                )
            ).first()
            if job is None:
                return
            code, message, retryable = _safe_failure(exc)
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = utc_now()
            if retryable and job.attempts < MAX_ATTEMPTS:
                delay_seconds = 15 * (2 ** max(0, job.attempts - 1))
                job.next_attempt_at = utc_now() + timedelta(seconds=delay_seconds)
            else:
                job.stage = PDFBuildStage.FAILED.value
                job.error_code = code
                job.error_message = message.replace("系統會按情況自動重試。", "請稍後重新建立。")
                job.error_retryable = retryable
                job.finished_at = utc_now()
                job.next_attempt_at = None
                if job.credit_state == PDFBuildCreditState.RESERVED.value:
                    await _refund_job(job)
                if await cleanup_pdf_build_staging(job.job_uuid):
                    job.staging_cleaned_at = utc_now()
            db_session.add(job)
            await db_session.commit()
            logger.warning(
                "ai.pdf_course.job_attempt_failed",
                extra={
                    "operation": "pdf_build_worker",
                    "job_uuid": job.job_uuid,
                    "error_code": code,
                    "retryable": retryable,
                    "attempt": job.attempts,
                },
            )
        finally:
            for upload in uploads:
                try:
                    await upload.close()
                except Exception:
                    logger.warning(
                        "ai.pdf_course.staged_file_close_failed",
                        extra={"operation": "pdf_build_worker"},
                    )


@asynccontextmanager
async def _single_worker_slot():
    """Hold one PostgreSQL advisory lock for the full job execution.

    ``SKIP LOCKED`` makes claiming safe, while this process-independent slot
    keeps total PDF generation concurrency at one even if two application
    containers briefly overlap during a rolling replacement.
    """
    async with engine.connect() as connection:
        if connection.dialect.name != "postgresql":
            yield True
            return
        acquired = bool(
            (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": PDF_WORKER_ADVISORY_LOCK_ID},
                )
            ).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": PDF_WORKER_ADVISORY_LOCK_ID},
                )


async def run_worker() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    logger.info("ai.pdf_course.worker_started", extra={"operation": "pdf_build_worker"})
    while True:
        await _recover_deferred_refunds()
        await _recover_terminal_staging()
        async with _single_worker_slot() as acquired:
            if not acquired:
                job = None
            else:
                async with _async_session_factory() as db_session:
                    job = await claim_next_pdf_build_job(
                        db_session,
                        worker_id=worker_id,
                        lease_seconds=LEASE_SECONDS,
                    )
                if job is not None:
                    await process_pdf_build_job(job.job_uuid)
        if job is None:
            await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
