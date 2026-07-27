from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.pdf_course_build_jobs import (
    PDFBuildCreditState,
    PDFBuildStage,
    PDFCourseBuildJob,
    utc_now,
)
from src.security.features_utils.usage import (
    get_ai_credit_period_token,
    reserve_ai_credit_once,
)
from src.security.file_validation import get_safe_filename
from src.services.ai.schemas.pdf_build_jobs import (
    PDFBuildDraftCourse,
    PDFBuildIndexing,
    PDFBuildIssue,
    PDFBuildJobSnapshot,
)
from src.services.courses.transfer.storage_utils import (
    delete_storage_directory,
    get_content_delivery_type,
    read_file_content,
    upload_file_to_s3,
)

ACTIVE_PDF_BUILD_STAGES = (
    PDFBuildStage.EXTRACTING.value,
    PDFBuildStage.PLANNING.value,
    PDFBuildStage.CREATING.value,
    PDFBuildStage.INDEXING.value,
)
PDF_BUILD_CREDIT_COST = 8
PDF_BUILD_MAX_FILES = 8
MAX_IDEMPOTENCY_KEY_LENGTH = 255
PDF_BUILD_MAX_FILE_BYTES = 100 * 1024 * 1024
PDF_BUILD_MAX_TOTAL_BYTES = 200 * 1024 * 1024
PDF_BUILD_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _safe_idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "pdf_build_idempotency_key_required",
                "message": "請提供 Idempotency-Key，以便安全重試建課請求。",
                "retryable": False,
            },
        )
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "pdf_build_idempotency_key_invalid",
                "message": "Idempotency-Key 過長。",
                "retryable": False,
            },
        )
    return key


def _fingerprint_request(
    *,
    org_id: int,
    creator_user_id: int,
    course_name: str | None,
    instructions: str | None,
    language: str,
    auto_index: bool,
    staged_files: list[dict],
) -> str:
    material = {
        "org_id": org_id,
        "creator_user_id": creator_user_id,
        "course_name": course_name or None,
        "instructions": instructions or None,
        "language": language,
        "auto_index": auto_index,
        "files": [
            {
                "name": item["original_name"],
                "sha256": item["sha256"],
                "size": item["size"],
            }
            for item in staged_files
        ],
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_pdf_build_uploads(files: list[UploadFile]) -> None:
    if not files:
        raise HTTPException(status_code=400, detail="請至少上傳一份 PDF 教材。")
    if len(files) > PDF_BUILD_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"最多只可上傳 {PDF_BUILD_MAX_FILES} 份 PDF 教材。")

    for file in files:
        if not file.filename:
            raise HTTPException(status_code=400, detail="其中一份 PDF 缺少檔案名稱。")
        if Path(file.filename).suffix.lower() != ".pdf":
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "pdf_build_file_type_invalid",
                    "message": "只可上傳 PDF 教材。",
                    "retryable": False,
                },
            )


def _stream_upload_to_temporary(
    file: UploadFile,
    temporary: Path,
    *,
    aggregate_remaining: int,
) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    first_chunk = True
    file.file.seek(0)
    with temporary.open("wb") as destination:
        while True:
            chunk = file.file.read(PDF_BUILD_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            if first_chunk:
                first_chunk = False
                if not chunk.startswith(b"%PDF-"):
                    raise HTTPException(
                        status_code=415,
                        detail={
                            "code": "pdf_build_file_invalid",
                            "message": "其中一份檔案不是有效的 PDF。",
                            "retryable": False,
                        },
                    )
            size += len(chunk)
            if size > PDF_BUILD_MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "pdf_build_file_too_large",
                        "message": "每份 PDF 不可超過 100 MiB。",
                        "retryable": False,
                    },
                )
            if size > aggregate_remaining:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "pdf_build_total_too_large",
                        "message": "每次建課上傳的 PDF 總大小不可超過 200 MiB。",
                        "retryable": False,
                    },
                )
            digest.update(chunk)
            destination.write(chunk)
    file.file.seek(0)
    if first_chunk:
        raise HTTPException(
            status_code=415,
            detail={
                "code": "pdf_build_file_invalid",
                "message": "其中一份 PDF 是空白檔案。",
                "retryable": False,
            },
        )
    return size, digest.hexdigest()


async def _stage_files(job_uuid: str, files: list[UploadFile]) -> list[dict]:
    validate_pdf_build_uploads(files)
    descriptors: list[dict] = []
    aggregate_size = 0
    for index, file in enumerate(files):
        original_name = file.filename or "source.pdf"
        safe_filename = get_safe_filename(original_name, f"source_{index}")
        storage_key = f"content/_internal/pdf-builds/{job_uuid}/{safe_filename}"
        target = Path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            size, content_hash = await asyncio.to_thread(
                _stream_upload_to_temporary,
                file,
                temporary,
                aggregate_remaining=PDF_BUILD_MAX_TOTAL_BYTES - aggregate_size,
            )
            aggregate_size += size
            if get_content_delivery_type() == "s3api":
                stored = await asyncio.to_thread(
                    upload_file_to_s3, storage_key, str(temporary)
                )
                if not stored:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "code": "pdf_build_staging_unavailable",
                            "message": "暫時無法安全保存 PDF，請稍後重試。",
                            "retryable": True,
                        },
                    )
            else:
                os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        descriptors.append(
            {
                "storage_key": storage_key,
                "original_name": original_name,
                "safe_filename": safe_filename,
                "sha256": content_hash,
                "size": size,
            }
        )
    return descriptors


async def cleanup_pdf_build_staging(job_uuid: str) -> bool:
    path = f"content/_internal/pdf-builds/{job_uuid}"
    return await asyncio.to_thread(delete_storage_directory, path)


async def load_staged_pdf_uploads(job: PDFCourseBuildJob) -> list[UploadFile]:
    uploads: list[UploadFile] = []
    for descriptor in job.options.get("files", []):
        if get_content_delivery_type() == "filesystem":
            try:
                staged_file = open(descriptor["storage_key"], "rb")
            except OSError as exc:
                raise RuntimeError("pdf_build_staging_missing") from exc
        else:
            content = await asyncio.to_thread(
                read_file_content, descriptor["storage_key"]
            )
            if content is None:
                raise RuntimeError("pdf_build_staging_missing")
            staged_file = SpooledTemporaryFile(max_size=PDF_BUILD_UPLOAD_CHUNK_BYTES)
            staged_file.write(content)
            staged_file.seek(0)
            del content
        upload = UploadFile(filename=descriptor["original_name"], file=staged_file)
        setattr(upload, "pdf_build_storage_key", descriptor["storage_key"])
        setattr(upload, "pdf_build_sha256", descriptor.get("sha256"))
        uploads.append(upload)
    if not uploads:
        raise RuntimeError("pdf_build_staging_missing")
    return uploads


def _idempotency_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "pdf_build_idempotency_conflict",
            "message": "此 Idempotency-Key 已用於另一個 PDF 建課請求。",
            "retryable": False,
        },
    )


async def _reserve_job_credit(job: PDFCourseBuildJob, db_session: AsyncSession) -> None:
    if job.credit_state != PDFBuildCreditState.PENDING.value:
        return
    await reserve_ai_credit_once(
        job.org_id,
        db_session,
        operation_key=job.job_uuid,
        amount=job.credit_amount,
        period_token=job.credit_period_token,
    )
    job.credit_state = PDFBuildCreditState.RESERVED.value
    job.updated_at = utc_now()
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)


async def create_pdf_build_job(
    *,
    org_id: int,
    creator_user_id: int,
    idempotency_key: str | None,
    course_name: str | None,
    instructions: str | None,
    language: str,
    auto_index: bool,
    ai_model: str,
    files: list[UploadFile],
    db_session: AsyncSession,
) -> tuple[PDFCourseBuildJob, bool]:
    """Create a staged job, or return the identical prior request.

    Returns ``(job, reused)``. Credits are reserved after both the staging
    objects and ledger row are durable.
    """
    key = _safe_idempotency_key(idempotency_key)
    job_uuid = f"pdfbuild_{uuid4()}"
    descriptors: list[dict] = []
    try:
        descriptors = await _stage_files(job_uuid, files)
        fingerprint = _fingerprint_request(
            org_id=org_id,
            creator_user_id=creator_user_id,
            course_name=course_name,
            instructions=instructions,
            language=language,
            auto_index=auto_index,
            staged_files=descriptors,
        )
        existing = (
            await db_session.execute(
                select(PDFCourseBuildJob).where(
                    PDFCourseBuildJob.org_id == org_id,
                    PDFCourseBuildJob.creator_user_id == creator_user_id,
                    PDFCourseBuildJob.idempotency_key == key,
                )
            )
        ).scalars().first()
        if existing:
            await cleanup_pdf_build_staging(job_uuid)
            if existing.request_fingerprint != fingerprint:
                raise _idempotency_conflict()
            await _reserve_job_credit(existing, db_session)
            return existing, True
        now = utc_now()
        credit_period_token = get_ai_credit_period_token(org_id)
        job = PDFCourseBuildJob(
            job_uuid=job_uuid,
            org_id=org_id,
            creator_user_id=creator_user_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            stage=PDFBuildStage.EXTRACTING.value,
            progress_current=0,
            progress_total=len(descriptors),
            options={
                "course_name": course_name,
                "instructions": instructions,
                "language": language,
                "auto_index": auto_index,
                "ai_model": ai_model,
                "files": descriptors,
            },
            credit_amount=PDF_BUILD_CREDIT_COST,
            credit_period_token=credit_period_token,
            credit_state=PDFBuildCreditState.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)
    except IntegrityError:
        await db_session.rollback()
        await cleanup_pdf_build_staging(job_uuid)
        existing = (
            await db_session.execute(
                select(PDFCourseBuildJob).where(
                    PDFCourseBuildJob.org_id == org_id,
                    PDFCourseBuildJob.creator_user_id == creator_user_id,
                    PDFCourseBuildJob.idempotency_key == key,
                )
            )
        ).scalars().first()
        if not existing or existing.request_fingerprint != fingerprint:
            raise _idempotency_conflict()
        await _reserve_job_credit(existing, db_session)
        return existing, True
    except Exception:
        await db_session.rollback()
        # ``_stage_files`` may fail after writing only part of the batch and
        # before returning descriptors, so always remove the generated prefix.
        await cleanup_pdf_build_staging(job_uuid)
        raise

    # A crash after the ledger commit is recoverable: a retry sees the pending
    # job and repeats the idempotent reservation.
    await _reserve_job_credit(job, db_session)
    return job, False


async def get_pdf_build_job(
    job_uuid: str, org_id: int, db_session: AsyncSession
) -> PDFCourseBuildJob | None:
    return (
        await db_session.execute(
            select(PDFCourseBuildJob).where(
                PDFCourseBuildJob.job_uuid == job_uuid,
                PDFCourseBuildJob.org_id == org_id,
            )
        )
    ).scalars().first()


async def ensure_course_not_in_active_pdf_build(
    course_id: int, db_session: AsyncSession
) -> None:
    """Serialize normal course mutations against an active PDF builder."""
    active_job = (
        await db_session.execute(active_pdf_build_for_course_statement(course_id))
    ).scalars().first()
    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pdf_build_course_locked",
                "message": "PDF 建課仍在處理此課程草稿，完成後才可修改、發佈或刪除。",
                "retryable": True,
            },
        )


def active_pdf_build_for_course_statement(course_id: int):
    """Lock an active job row so completion and course mutation serialize."""
    return (
        select(PDFCourseBuildJob)
        .where(
            PDFCourseBuildJob.course_id == course_id,
            col(PDFCourseBuildJob.stage).in_(ACTIVE_PDF_BUILD_STAGES),
        )
        .with_for_update()
        .limit(1)
    )


async def list_pdf_build_jobs(
    *,
    org_id: int,
    creator_user_id: int | None,
    active: bool,
    limit: int,
    db_session: AsyncSession,
) -> list[PDFCourseBuildJob]:
    statement = select(PDFCourseBuildJob).where(PDFCourseBuildJob.org_id == org_id)
    if creator_user_id is not None:
        statement = statement.where(PDFCourseBuildJob.creator_user_id == creator_user_id)
    if active:
        statement = statement.where(col(PDFCourseBuildJob.stage).in_(ACTIVE_PDF_BUILD_STAGES))
    statement = statement.order_by(col(PDFCourseBuildJob.created_at).desc()).limit(limit)
    return list((await db_session.execute(statement)).scalars().all())


async def pdf_build_snapshot(
    job: PDFCourseBuildJob, db_session: AsyncSession
) -> PDFBuildJobSnapshot:
    draft = None
    if job.course_id is not None:
        course = await db_session.get(Course, job.course_id)
        if course is not None:
            # A PDF build result is always a private unpublished review draft.
            draft = PDFBuildDraftCourse(
                course_id=course.id,
                course_uuid=course.course_uuid,
                public=False,
                published=False,
            )

    indexing = None
    if job.indexing_status:
        indexing = PDFBuildIndexing(
            status=job.indexing_status,
            code=job.indexing_code,
            chunks=job.chunks_indexed,
        )
    warning = None
    if job.warning_code and job.warning_message:
        warning = PDFBuildIssue(
            code=job.warning_code,
            message=job.warning_message,
            retryable=False,
        )
    error = None
    if job.error_code and job.error_message:
        error = PDFBuildIssue(
            code=job.error_code,
            message=job.error_message,
            retryable=job.error_retryable,
        )
    return PDFBuildJobSnapshot(
        job_uuid=job.job_uuid,
        org_id=job.org_id,
        creator_user_id=job.creator_user_id,
        stage=job.stage,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        draft_course=draft,
        chapters_created=job.chapters_created,
        activities_created=job.activities_created,
        source_documents_created=job.source_documents_created,
        indexing=indexing,
        warning=warning,
        error=error,
        attempts=job.attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def claim_next_pdf_build_job(
    db_session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 900,
) -> PDFCourseBuildJob | None:
    """Atomically claim one eligible job using PostgreSQL ``SKIP LOCKED``."""
    now = utc_now()
    statement = pdf_build_claim_statement(now)
    job = (await db_session.execute(statement)).scalars().first()
    if not job:
        return None
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    job.started_at = job.started_at or now
    job.next_attempt_at = None
    job.updated_at = now
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


def pdf_build_claim_statement(now):
    """Build the PostgreSQL-safe atomic claim statement for regression tests."""
    return (
        select(PDFCourseBuildJob)
        .where(
            col(PDFCourseBuildJob.stage).in_(ACTIVE_PDF_BUILD_STAGES),
            or_(
                PDFCourseBuildJob.next_attempt_at.is_(None),
                PDFCourseBuildJob.next_attempt_at <= now,
            ),
            or_(
                PDFCourseBuildJob.lease_expires_at.is_(None),
                PDFCourseBuildJob.lease_expires_at <= now,
            ),
        )
        .order_by(col(PDFCourseBuildJob.created_at).asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
