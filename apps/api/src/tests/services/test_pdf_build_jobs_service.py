from datetime import timedelta
from unittest.mock import AsyncMock
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from src.db.pdf_course_build_jobs import PDFBuildCreditState, PDFBuildStage, utc_now
from src.services.ai import pdf_build_jobs


def _pdf(name: str = "teacher.pdf", body: bytes = b"lesson") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(b"%PDF-1.4\n" + body))


def _mock_descriptor(name: str = "teacher.pdf") -> dict:
    return {
        "storage_key": "content/_internal/pdf-builds/job/source_0.pdf",
        "original_name": name,
        "safe_filename": "source_0.pdf",
        "sha256": "c" * 64,
        "size": 16,
    }


@pytest.mark.asyncio
async def test_create_reuses_identical_idempotent_request_and_rejects_mismatch(
    db, org, admin_user, monkeypatch
):
    stage_files = AsyncMock(
        return_value=[_mock_descriptor()]
    )
    reserve = AsyncMock(return_value=8)
    monkeypatch.setattr(pdf_build_jobs, "_stage_files", stage_files)
    monkeypatch.setattr(pdf_build_jobs, "reserve_ai_credit_once", reserve)
    monkeypatch.setattr(pdf_build_jobs, "get_ai_credit_period_token", lambda org_id: "0")

    kwargs = dict(
        org_id=org.id,
        creator_user_id=admin_user.id,
        idempotency_key="stable-request-key",
        course_name="澳門地理",
        instructions="private teacher instruction",
        language="zh-Hant",
        auto_index=True,
        ai_model="test-model",
        db_session=db,
    )
    created, reused = await pdf_build_jobs.create_pdf_build_job(
        **kwargs, files=[_pdf()]
    )
    replay, replayed = await pdf_build_jobs.create_pdf_build_job(
        **kwargs, files=[_pdf()]
    )

    assert reused is False
    assert replayed is True
    assert replay.id == created.id
    assert replay.credit_state == PDFBuildCreditState.RESERVED.value
    # Replay uploads are streamed and hashed again to prove the fingerprint,
    # then their temporary staging prefix is removed.
    assert stage_files.await_count == 2
    # The second reservation call is skipped because PostgreSQL already records
    # the reservation; Redis is independently protected by the operation key.
    reserve.assert_awaited_once_with(
        org.id,
        db,
        operation_key=created.job_uuid,
        amount=8,
        period_token="0",
    )

    with pytest.raises(HTTPException) as exc_info:
        await pdf_build_jobs.create_pdf_build_job(
            **{**kwargs, "course_name": "另一課程"}, files=[_pdf()]
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "pdf_build_idempotency_conflict"


@pytest.mark.asyncio
async def test_snapshot_never_exposes_private_worker_checkpoints(
    db, org, admin_user, monkeypatch
):
    monkeypatch.setattr(
        pdf_build_jobs,
        "_stage_files",
        AsyncMock(
            return_value=[_mock_descriptor("student-name.pdf")]
        ),
    )
    monkeypatch.setattr(pdf_build_jobs, "reserve_ai_credit_once", AsyncMock(return_value=8))
    monkeypatch.setattr(pdf_build_jobs, "get_ai_credit_period_token", lambda org_id: "0")
    job, _ = await pdf_build_jobs.create_pdf_build_job(
        org_id=org.id,
        creator_user_id=admin_user.id,
        idempotency_key="privacy-key",
        course_name="課程",
        instructions="provider-private-instruction",
        language="zh-Hant",
        auto_index=True,
        ai_model="private-provider-model",
        files=[_pdf("student-name.pdf")],
        db_session=db,
    )
    job.plan_checkpoint = {"description": "raw extracted lesson text"}
    job.lease_owner = "private-worker-host"
    db.add(job)
    await db.commit()

    payload = (await pdf_build_jobs.pdf_build_snapshot(job, db)).model_dump_json()
    for secret in (
        "provider-private-instruction",
        "private-provider-model",
        "student-name.pdf",
        "raw extracted lesson text",
        "private-worker-host",
        "storage_key",
    ):
        assert secret not in payload


@pytest.mark.asyncio
async def test_local_staging_round_trip_uses_real_files_and_cleanup(
    tmp_path, monkeypatch
):
    from src.services.courses.transfer import storage_utils

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pdf_build_jobs, "get_content_delivery_type", lambda: "filesystem")
    monkeypatch.setattr(storage_utils, "get_content_delivery_type", lambda: "filesystem")
    descriptors = await pdf_build_jobs._stage_files(
        "pdfbuild_real_roundtrip", [_pdf(body=b"real staged content")]
    )
    staged_path = tmp_path / descriptors[0]["storage_key"]
    assert staged_path.is_file()
    assert descriptors[0]["size"] == staged_path.stat().st_size

    job = SimpleNamespace(options={"files": descriptors})
    uploads = await pdf_build_jobs.load_staged_pdf_uploads(job)
    try:
        assert uploads[0].file.read() == b"%PDF-1.4\nreal staged content"
        assert getattr(uploads[0], "pdf_build_storage_key") == descriptors[0]["storage_key"]
    finally:
        await uploads[0].close()

    assert await pdf_build_jobs.cleanup_pdf_build_staging(
        "pdfbuild_real_roundtrip"
    ) is True
    assert not staged_path.exists()


@pytest.mark.asyncio
async def test_staging_streams_bounded_chunks_and_enforces_aggregate_limit(
    tmp_path, monkeypatch
):
    from src.services.courses.transfer import storage_utils

    class GuardedBytesIO(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 8
            return super().read(size)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pdf_build_jobs, "get_content_delivery_type", lambda: "filesystem")
    monkeypatch.setattr(storage_utils, "get_content_delivery_type", lambda: "filesystem")
    monkeypatch.setattr(pdf_build_jobs, "PDF_BUILD_UPLOAD_CHUNK_BYTES", 8)
    monkeypatch.setattr(pdf_build_jobs, "PDF_BUILD_MAX_FILE_BYTES", 32)
    monkeypatch.setattr(pdf_build_jobs, "PDF_BUILD_MAX_TOTAL_BYTES", 40)
    files = [
        UploadFile(filename="one.pdf", file=GuardedBytesIO(b"%PDF-" + b"a" * 20)),
        UploadFile(filename="two.pdf", file=GuardedBytesIO(b"%PDF-" + b"b" * 20)),
    ]

    with pytest.raises(HTTPException) as exc_info:
        await pdf_build_jobs._stage_files("pdfbuild_aggregate", files)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "pdf_build_total_too_large"
    await pdf_build_jobs.cleanup_pdf_build_staging("pdfbuild_aggregate")


@pytest.mark.asyncio
async def test_claim_sets_a_recoverable_lease_and_skips_unexpired_job(
    db, org, admin_user
):
    from src.db.pdf_course_build_jobs import PDFCourseBuildJob

    now = utc_now()
    job = PDFCourseBuildJob(
        job_uuid="pdfbuild_claimable",
        org_id=org.id,
        creator_user_id=admin_user.id,
        idempotency_key="claimable",
        request_fingerprint="a" * 64,
        options={},
        credit_state=PDFBuildCreditState.RESERVED.value,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.commit()

    claimed = await pdf_build_jobs.claim_next_pdf_build_job(
        db, worker_id="worker-a", lease_seconds=60
    )
    assert claimed is not None
    assert claimed.lease_owner == "worker-a"
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None

    assert (
        await pdf_build_jobs.claim_next_pdf_build_job(
            db, worker_id="worker-b", lease_seconds=60
        )
        is None
    )

    claimed.lease_expires_at = utc_now() - timedelta(seconds=1)
    db.add(claimed)
    await db.commit()
    reclaimed = await pdf_build_jobs.claim_next_pdf_build_job(
        db, worker_id="worker-b", lease_seconds=60
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempts == 2


def test_postgresql_claim_statement_uses_skip_locked_transaction_lock():
    from sqlalchemy.dialects import postgresql

    sql = str(
        pdf_build_jobs.pdf_build_claim_statement(utc_now()).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "lease_expires_at" in sql
    assert "next_attempt_at" in sql

    course_gate_sql = str(
        pdf_build_jobs.active_pdf_build_for_course_statement(41).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "FOR UPDATE" in course_gate_sql
    assert "course_id" in course_gate_sql


@pytest.mark.asyncio
async def test_active_pdf_job_blocks_course_update_publish_and_delete_until_terminal(
    db, org, admin_user, course, mock_request, monkeypatch
):
    from src.db.courses.courses import CourseUpdate
    from src.db.pdf_course_build_jobs import PDFCourseBuildJob
    from src.services.courses import courses as course_service
    from src.services.courses.transfer import storage_utils

    now = utc_now()
    job = PDFCourseBuildJob(
        job_uuid="pdfbuild_course_gate",
        org_id=org.id,
        creator_user_id=admin_user.id,
        idempotency_key="course-gate",
        request_fingerprint="e" * 64,
        options={},
        course_id=course.id,
        credit_state=PDFBuildCreditState.RESERVED.value,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.commit()
    job_id = job.id
    course_uuid = course.course_uuid
    monkeypatch.setattr(course_service, "check_resource_access", AsyncMock())
    decrease = AsyncMock()
    monkeypatch.setattr(course_service, "decrease_feature_usage", decrease)
    monkeypatch.setattr(course_service, "dispatch_webhooks", AsyncMock())
    monkeypatch.setattr(storage_utils, "delete_storage_directory", lambda path: True)

    for update in (
        CourseUpdate(name="不可修改"),
        CourseUpdate(public=True),
        CourseUpdate(published=True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await course_service.update_course(
                mock_request, update, course_uuid, admin_user, db
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "pdf_build_course_locked"
        await db.rollback()

    with pytest.raises(HTTPException) as exc_info:
        await course_service.delete_course(
            mock_request, course_uuid, admin_user, db
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "pdf_build_course_locked"
    decrease.assert_not_awaited()
    await db.rollback()

    job = await db.get(PDFCourseBuildJob, job_id)
    job.stage = PDFBuildStage.DONE.value
    db.add(job)
    await db.commit()
    updated = await course_service.update_course(
        mock_request,
        CourseUpdate(name="完成後可修改"),
        course_uuid,
        admin_user,
        db,
    )
    assert updated.name == "完成後可修改"
    assert (await course_service.delete_course(
        mock_request, course_uuid, admin_user, db
    )) == {"detail": "Course deleted"}
