import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import src.routers.ai.courseplanning as courseplanning_router
import src.services.ai.pdf_course_builder as pdf_builder
from src.routers.ai.rag import rag_chat_event_generator
from src.routers.ai.rag import citation_audit, citation_footer
from src.services.ai.base import AIProviderError
from src.services.ai.schemas.courseplanning import ActivityPlan, ChapterPlan, CoursePlan


@pytest.mark.asyncio
async def test_pdf_course_preserves_created_course_when_rag_indexing_fails(
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(pdf_builder, "_prepare_pdfs", AsyncMock(return_value=["pdf"]))
    monkeypatch.setattr(pdf_builder, "_generate_course_plan", AsyncMock(return_value="plan"))
    monkeypatch.setattr(
        pdf_builder,
        "_create_course_records",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=41, course_uuid="course_synthetic"),
                3,
                7,
                1,
            )
        ),
    )
    monkeypatch.setattr(
        pdf_builder,
        "embed_course_content",
        AsyncMock(
            side_effect=AIProviderError(
                "ai_embedding_request_rejected",
                status_code=400,
                retryable=False,
            )
        ),
    )

    response = await pdf_builder.build_course_from_pdfs(
        org=SimpleNamespace(id=5),
        creator_user_id=8,
        files=[],
        course_name="合成課程",
        instructions=None,
        language="zh-Hant",
        ai_model="synthetic-model",
        db_session=SimpleNamespace(),
        auto_index=True,
    )

    assert response.status == "success"
    assert response.extraction_status == "success"
    assert response.generation_status == "success"
    assert response.persistence_status == "success"
    assert response.course_uuid == "course_synthetic"
    assert response.indexing_status == "failed"
    assert response.indexing_error == "ai_embedding_request_rejected"
    assert "provider-internal-secret" not in caplog.text


@pytest.mark.asyncio
async def test_pdf_course_record_creation_resumes_without_duplicate_draft_content(
    monkeypatch, db, org, admin_user
):
    plan = CoursePlan(
        name="澳門科學",
        description="草稿",
        chapters=[
            ChapterPlan(
                name="第一章",
                description="基礎",
                activities=[
                    ActivityPlan(name="活動一", description="內容", suggested_blocks=[])
                ],
            )
        ],
    )
    prepared = [
        pdf_builder._PreparedPDF(
            original_name="teacher.pdf",
            safe_filename="source_0.pdf",
            content=b"%PDF-1.4\nlesson",
            extracted_text="教材內容",
        )
    ]
    monkeypatch.setattr(
        pdf_builder,
        "_generate_activity_content",
        AsyncMock(return_value={"type": "doc", "content": []}),
    )
    upload = AsyncMock()
    monkeypatch.setattr(pdf_builder, "upload_content", upload)

    first = await pdf_builder._create_course_records(
        org=org,
        creator_user_id=admin_user.id,
        plan=plan,
        pdfs=prepared,
        instructions=None,
        language="zh-Hant",
        ai_model="test-model",
        db_session=db,
        build_job_uuid="pdfbuild_resume_test",
    )
    second = await pdf_builder._create_course_records(
        org=org,
        creator_user_id=admin_user.id,
        plan=plan,
        pdfs=prepared,
        instructions=None,
        language="zh-Hant",
        ai_model="test-model",
        db_session=db,
        build_job_uuid="pdfbuild_resume_test",
        resume_course_id=first[0].id,
    )

    from sqlmodel import func, select
    from src.db.courses.activities import Activity
    from src.db.courses.chapters import Chapter
    from src.db.courses.courses import Course

    assert first[0].public is False and first[0].published is False
    assert second[0].id == first[0].id
    assert first[1:] == second[1:] == (2, 1, 1)
    assert (await db.execute(select(func.count()).select_from(Course))).scalar_one() == 1
    assert (await db.execute(select(func.count()).select_from(Chapter))).scalar_one() == 2
    assert (await db.execute(select(func.count()).select_from(Activity))).scalar_one() == 2
    upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_pdfs_keeps_every_source_after_ai_prompt_text_cap(
    monkeypatch,
):
    from io import BytesIO
    from fastapi import UploadFile

    monkeypatch.setattr(pdf_builder, "MAX_TOTAL_SOURCE_CHARS", 12)
    monkeypatch.setattr(
        pdf_builder,
        "extract_text_from_pdf",
        lambda content: "甲" * 100 if b"first" in content else "第二份教材內容",
    )
    checkpoints = []

    async def checkpoint(stage, current, total, data):
        checkpoints.append((stage, current, total))

    prepared = await pdf_builder._prepare_pdfs(
        [
            UploadFile(filename="first.pdf", file=BytesIO(b"%PDF-1.4\nfirst")),
            UploadFile(filename="second.pdf", file=BytesIO(b"%PDF-1.4\nsecond")),
        ],
        checkpoint=checkpoint,
    )

    assert [item.original_name for item in prepared] == ["first.pdf", "second.pdf"]
    assert len(prepared[0].extracted_text) >= 12
    assert prepared[1].extracted_text == ""
    assert checkpoints[-1] == ("extracting", 2, 2)


@pytest.mark.asyncio
async def test_prepare_pdfs_uses_retry_stable_job_source_filename(monkeypatch):
    from io import BytesIO
    from uuid import NAMESPACE_URL, uuid5

    from fastapi import UploadFile

    build_uuid = "pdfbuild_stable_prepare"
    content_hash = "a" * 64
    monkeypatch.setattr(pdf_builder, "extract_text_from_pdf", lambda content: "教材")

    def staged_upload():
        upload = UploadFile(
            filename="Teacher-Handout.PDF",
            file=BytesIO(b"%PDF-1.4\nsame source"),
        )
        setattr(upload, "pdf_build_storage_key", "content/_internal/source.pdf")
        setattr(upload, "pdf_build_sha256", content_hash)
        return upload

    first = await pdf_builder._prepare_pdfs(
        [staged_upload()], build_job_uuid=build_uuid
    )
    second = await pdf_builder._prepare_pdfs(
        [staged_upload()], build_job_uuid=build_uuid
    )
    expected = (
        f"source_0_{uuid5(NAMESPACE_URL, f'{build_uuid}:0:{content_hash}:.pdf')}.pdf"
    )

    assert first[0].safe_filename == second[0].safe_filename == expected


@pytest.mark.asyncio
async def test_source_upload_retry_reuses_deterministic_activity_storage_path(
    monkeypatch, db, org, course
):
    course_id = course.id
    org_id = org.id
    prepared = [
        pdf_builder._PreparedPDF(
            original_name="teacher.pdf",
            safe_filename="source_0.pdf",
            content=b"%PDF-1.4\nlesson",
            extracted_text="教材",
            content_hash="d" * 64,
        )
    ]
    uploads = []
    stored_keys = []

    async def upload(**kwargs):
        uploads.append(kwargs)
        stored_keys.append(
            f"content/orgs/{kwargs['uuid']}/{kwargs['directory']}/"
            f"{kwargs['file_and_format']}"
        )

    monkeypatch.setattr(pdf_builder, "upload_content", upload)
    original_commit = db.commit
    commit_count = 0

    async def fail_after_upload_once():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            await db.rollback()
            raise RuntimeError("synthetic crash after source upload")
        await original_commit()

    monkeypatch.setattr(db, "commit", fail_after_upload_once)
    with pytest.raises(RuntimeError):
        await pdf_builder._create_source_document_chapter(
            org=org,
            course=course,
            pdfs=prepared,
            db_session=db,
            chapter_order=0,
            build_job_uuid="pdfbuild_deterministic_source",
        )

    monkeypatch.setattr(db, "commit", original_commit)
    course = await db.get(type(course), course_id)
    org = await db.get(type(org), org_id)
    # Simulate a fresh retry preparation whose old implementation generated a
    # different random safe filename for the same staged source.
    retry_prepared = [
        prepared[0].model_copy(update={"safe_filename": "another-random-name.pdf"})
    ]
    created = await pdf_builder._create_source_document_chapter(
        org=org,
        course=course,
        pdfs=retry_prepared,
        db_session=db,
        chapter_order=0,
        build_job_uuid="pdfbuild_deterministic_source",
    )

    assert created == 1
    assert len(uploads) == 2
    assert uploads[0]["directory"] == uploads[1]["directory"]
    assert uploads[0]["file_and_format"] == uploads[1]["file_and_format"]
    assert stored_keys[0] == stored_keys[1]
    assert len(set(stored_keys)) == 1  # Retry did not leave an orphan object key.
    from uuid import NAMESPACE_URL, uuid5

    build_uuid = "pdfbuild_deterministic_source"
    content_hash = "d" * 64
    expected_activity_uuid = f"activity_{uuid5(NAMESPACE_URL, f'{build_uuid}:0:{content_hash}')}"
    expected_filename = (
        f"source_0_{uuid5(NAMESPACE_URL, f'{build_uuid}:0:{content_hash}:.pdf')}.pdf"
    )
    expected_key = (
        f"content/orgs/{org.org_uuid}/courses/{course.course_uuid}/activities/"
        f"{expected_activity_uuid}/documentpdf/{expected_filename}"
    )
    assert uploads[0]["file_and_format"] == expected_filename
    assert stored_keys == [expected_key, expected_key]


@pytest.mark.asyncio
async def test_legacy_pdf_course_router_is_gone_without_credit_or_builder_calls(
    monkeypatch,
    db,
    regular_user,
    mock_request,
    org,
):
    reserve_credit = AsyncMock()
    monkeypatch.setattr(courseplanning_router, "reserve_ai_credit", reserve_credit)

    with pytest.raises(HTTPException) as exc_info:
        await courseplanning_router.build_pdf_course(
            request=mock_request,
            org_id=org.id,
            course_name="合成課程",
            instructions=None,
            language="zh-Hant",
            auto_index=True,
            files=[],
            current_user=regular_user,
                db_session=db,
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {
        "code": "pdf_build_legacy_endpoint_retired",
        "message": "同步 PDF 建課已停用，請改用新的背景建課功能。",
        "retryable": False,
        "replacement": "/api/v1/ai/courseplanning/pdf-builds",
    }
    reserve_credit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_stream_failure_returns_traditional_chinese_without_raw_error(caplog):
    async def broken_stream():
        raise RuntimeError("provider-internal-secret")
        yield "unreachable"

    events = []
    async for event in rag_chat_event_generator(
        broken_stream(),
        aichat_uuid="aichat_synthetic",
        user_message="合成問題",
        sources=[],
        context_text="合成教材",
        ai_model="synthetic-model",
    ):
        events.append(event)

    payloads = [json.loads(event.removeprefix("data: ").strip()) for event in events]
    assert payloads[0]["type"] == "start"
    assert payloads[-1] == {
        "type": "error",
        "message": "教材問答暫時不可用，請稍後重試。",
    }
    assert "provider-internal-secret" not in caplog.text


def test_rag_citation_audit_rejects_out_of_range_numbers_and_footer_is_exact():
    audit = citation_audit("教材內容 [1] [2, 9] [0]", source_count=2)

    assert audit == {"referenced": [1, 2], "invalid": [0, 9]}
    assert citation_footer(2) == "\n\n---\n**教材來源：** [1] [2]"


@pytest.mark.asyncio
async def test_rag_stream_enforces_numbered_source_footer(monkeypatch):
    async def stream():
        yield "火山由岩漿活動形成 [1]，不是來源 [99]。"

    monkeypatch.setattr("src.routers.ai.rag.save_message_to_history", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.routers.ai.rag.generate_follow_up_suggestions", AsyncMock(return_value=[]))

    events = []
    async for event in rag_chat_event_generator(
        stream(),
        aichat_uuid="aichat_synthetic",
        user_message="甚麼是火山？",
        sources=[{"activity_uuid": "activity_source"}],
        context_text="合成教材",
        ai_model="synthetic-model",
    ):
        events.append(json.loads(event.removeprefix("data: ").strip()))

    chunks = "".join(event["content"] for event in events if event["type"] == "chunk")
    source_event = next(event for event in events if event["type"] == "sources")
    assert chunks.endswith("**教材來源：** [1]")
    assert source_event["sources"][0]["citation_number"] == 1
