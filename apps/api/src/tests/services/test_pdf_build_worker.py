from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.pdf_course_build_jobs import (
    PDFBuildCreditState,
    PDFBuildStage,
    PDFCourseBuildJob,
    utc_now,
)
from src.services.ai.base import AIProviderError
from src.workers import pdf_course_build_worker as worker


async def _job(db, org, admin_user, *, uuid: str, attempts: int = 1):
    now = utc_now()
    job = PDFCourseBuildJob(
        job_uuid=uuid,
        org_id=org.id,
        creator_user_id=admin_user.id,
        idempotency_key=uuid,
        request_fingerprint="b" * 64,
        options={
            "course_name": "課程",
            "instructions": "private",
            "language": "zh-Hant",
            "auto_index": True,
            "ai_model": "test-model",
            "files": [{"storage_key": "private"}],
        },
        credit_state=PDFBuildCreditState.RESERVED.value,
        attempts=attempts,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.commit()
    return job


@pytest.mark.asyncio
async def test_worker_persists_checkpoints_and_finishes_when_indexing_fails(
    db, engine, org, admin_user, monkeypatch
):
    job = await _job(db, org, admin_user, uuid="pdfbuild_worker_done")
    job_uuid = job.job_uuid
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker, "_async_session_factory", factory)
    monkeypatch.setattr(worker, "load_staged_pdf_uploads", AsyncMock(return_value=[object()]))
    cleanup = AsyncMock()
    monkeypatch.setattr(worker, "cleanup_pdf_build_staging", cleanup)

    async def build(**kwargs):
        checkpoint = kwargs["checkpoint"]
        await checkpoint("planning", 1, 1, {"plan": {"name": "safe plan"}})
        await checkpoint(
            "creating",
            3,
            3,
            {"course_id": 41, "chapters": 2, "activities": 2, "sources": 1},
        )
        await checkpoint(
            "indexing",
            0,
            0,
            {
                "course_id": 41,
                "chapters": 2,
                "activities": 2,
                "sources": 1,
                "indexing_status": "failed",
                "indexing_code": "embedding_unavailable",
                "chunks": 0,
            },
        )
        return SimpleNamespace(
            course_id=41,
            chapters_created=2,
            activities_created=2,
            source_documents_created=1,
            indexing_status="failed",
            indexing_error="embedding_unavailable",
            chunks_indexed=0,
        )

    monkeypatch.setattr(worker, "build_course_from_pdfs", build)
    await worker.process_pdf_build_job(job_uuid)

    db.expire_all()
    completed = (
        await db.exec(
            select(PDFCourseBuildJob).where(PDFCourseBuildJob.job_uuid == job_uuid)
        )
    ).one()
    assert completed.stage == PDFBuildStage.DONE.value
    assert completed.credit_state == PDFBuildCreditState.CONSUMED.value
    assert completed.plan_checkpoint == {"name": "safe plan"}
    assert completed.course_id == 41
    assert completed.warning_code == "pdf_build_indexing_failed"
    assert completed.error_code is None
    cleanup.assert_awaited_once_with(job_uuid)


@pytest.mark.asyncio
async def test_worker_terminal_failure_refunds_once_and_keeps_safe_error(
    db, engine, org, admin_user, monkeypatch
):
    job = await _job(
        db, org, admin_user, uuid="pdfbuild_worker_failed", attempts=worker.MAX_ATTEMPTS
    )
    job_uuid = job.job_uuid
    org_id = org.id
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker, "_async_session_factory", factory)
    monkeypatch.setattr(worker, "load_staged_pdf_uploads", AsyncMock(return_value=[object()]))
    monkeypatch.setattr(
        worker,
        "build_course_from_pdfs",
        AsyncMock(side_effect=AIProviderError("ai_text_timeout", retryable=True)),
    )
    refund = Mock(return_value=0)
    monkeypatch.setattr(worker, "refund_ai_credit_once", refund)
    monkeypatch.setattr(worker, "cleanup_pdf_build_staging", AsyncMock())

    await worker.process_pdf_build_job(job_uuid)

    db.expire_all()
    failed = (
        await db.exec(
            select(PDFCourseBuildJob).where(PDFCourseBuildJob.job_uuid == job_uuid)
        )
    ).one()
    assert failed.stage == PDFBuildStage.FAILED.value
    assert failed.credit_state == PDFBuildCreditState.REFUNDED.value
    assert failed.error_code == "ai_text_timeout"
    assert "provider" not in failed.error_message.lower()
    assert failed.lease_owner is None
    refund.assert_called_once_with(org_id, job_uuid, 8, period_token="0")


@pytest.mark.asyncio
async def test_terminal_staging_cleanup_failure_is_not_marked_complete(
    db, engine, org, admin_user, monkeypatch
):
    job = await _job(db, org, admin_user, uuid="pdfbuild_cleanup_retry")
    job.stage = PDFBuildStage.DONE.value
    job.finished_at = utc_now()
    db.add(job)
    await db.commit()
    job_uuid = job.job_uuid
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker, "_async_session_factory", factory)
    cleanup = AsyncMock(return_value=False)
    monkeypatch.setattr(worker, "cleanup_pdf_build_staging", cleanup)

    await worker._recover_terminal_staging()

    db.expire_all()
    persisted = (
        await db.exec(
            select(PDFCourseBuildJob).where(PDFCourseBuildJob.job_uuid == job_uuid)
        )
    ).one()
    assert persisted.staging_cleaned_at is None
    cleanup.assert_awaited_once_with(job_uuid)
