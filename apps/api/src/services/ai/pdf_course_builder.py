import asyncio
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException, UploadFile
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.security.file_validation import get_safe_filename, validate_upload
from src.services.ai.base import AIProviderError, get_gemini_client
from src.services.ai.courseplanning import (
    build_activity_content_system_prompt,
    build_course_planning_system_prompt,
    extract_content_from_response,
    extract_plan_from_response,
    get_language_name,
)
from src.services.ai.rag.content_extraction import extract_text_from_pdf
from src.services.ai.rag.embedding_service import (
    EmbeddingUnavailableError,
    embed_course_content,
)
from src.services.ai.schemas.courseplanning import CoursePlan
from src.services.utils.upload_content import upload_content
from src.services.courses.transfer.storage_utils import read_file_content

logger = logging.getLogger(__name__)

MAX_PDF_FILES = 8
MAX_PDF_SIZE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_SOURCE_CHARS = 220_000
MAX_PLAN_SOURCE_CHARS = 90_000
MAX_ACTIVITY_SOURCE_CHARS = 8_000
MAX_CHAPTERS = 8
MAX_ACTIVITIES_PER_CHAPTER = 5

PDFBuildCheckpoint = Callable[
    [str, int, int, dict | None], Awaitable[None]
]


class PDFCourseBuildResponse(BaseModel):
    status: str
    extraction_status: Literal["success"] = "success"
    generation_status: Literal["success"] = "success"
    persistence_status: Literal["success"] = "success"
    course_uuid: str
    course_id: int
    chapters_created: int
    activities_created: int
    source_documents_created: int
    chunks_indexed: int
    # "degraded" means the index was built from lexical hashes, not real
    # embeddings — semantic search will not work until the backend is fixed.
    indexing_status: Literal["success", "skipped", "failed", "degraded"]
    indexing_error: str | None = None


class _PreparedPDF(BaseModel):
    original_name: str
    safe_filename: str
    content: bytes | None = None
    extracted_text: str
    storage_key: str | None = None
    content_hash: str | None = None


def _source_safe_filename(
    *,
    original_name: str,
    source_index: int,
    content_hash: str,
    build_job_uuid: str,
) -> str:
    """Return the retry-stable filename for a durable source document."""
    extension = Path(original_name).suffix.lower() or ".pdf"
    identity = f"{build_job_uuid}:{source_index}:{content_hash}:{extension}"
    return f"source_{source_index}_{uuid5(NAMESPACE_URL, identity)}{extension}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _fallback_content(activity_name: str, activity_description: str, source_context: str) -> dict:
    source_excerpt = _truncate(source_context.strip(), 1200)
    return {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [{"type": "text", "text": activity_name}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": activity_description or "本課堂根據上傳的 PDF 教材生成。"}],
            },
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "學習重點"}],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "閱讀教材中的核心概念，整理關鍵詞與例子。"}],
                            }
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "用自己的話解釋概念，並完成基礎練習。"}],
                            }
                        ],
                    },
                ],
            },
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "教材摘錄"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": source_excerpt or "PDF 文字摘錄暫時不可用。"}],
            },
            {
                "type": "calloutInfo",
                "content": [{"type": "text", "text": "基礎任務完成可作為 80 分表現；完成挑戰任務可作為 100 分表現。"}],
            },
        ],
    }


def _build_source_context(pdfs: list[_PreparedPDF], limit: int) -> str:
    parts: list[str] = []
    remaining = limit
    for index, pdf in enumerate(pdfs, 1):
        if remaining <= 0:
            break
        header = f"--- PDF {index}: {pdf.original_name} ---\n"
        excerpt = _truncate(pdf.extracted_text, max(0, remaining - len(header)))
        parts.append(header + excerpt)
        remaining -= len(header) + len(excerpt)
    return "\n\n".join(parts)


async def _prepare_pdfs(
    files: list[UploadFile],
    checkpoint: PDFBuildCheckpoint | None = None,
    build_job_uuid: str | None = None,
) -> list[_PreparedPDF]:
    if not files:
        raise HTTPException(status_code=400, detail="請至少上傳一份 PDF 教材。")
    if len(files) > MAX_PDF_FILES:
        raise HTTPException(status_code=400, detail=f"最多只可上傳 {MAX_PDF_FILES} 份 PDF 教材。")

    prepared: list[_PreparedPDF] = []
    total_chars = 0

    for source_index, file in enumerate(files):
        if not file.filename:
            raise HTTPException(status_code=400, detail="其中一份 PDF 缺少檔案名稱。")

        _, content = validate_upload(file, ["document"], max_size=MAX_PDF_SIZE_BYTES)
        extracted_text = await asyncio.to_thread(extract_text_from_pdf, content)
        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="無法從其中一份 PDF 擷取文字；掃描版教材需要先完成 OCR。",
            )

        remaining = max(0, MAX_TOTAL_SOURCE_CHARS - total_chars)
        extracted_text = _truncate(extracted_text, remaining) if remaining else ""
        total_chars += len(extracted_text)

        storage_key = getattr(file, "pdf_build_storage_key", None)
        content_hash = getattr(file, "pdf_build_sha256", None) or hashlib.sha256(
            content
        ).hexdigest()
        safe_filename = (
            _source_safe_filename(
                original_name=file.filename,
                source_index=source_index,
                content_hash=content_hash,
                build_job_uuid=build_job_uuid,
            )
            if build_job_uuid
            else get_safe_filename(file.filename, f"{uuid4()}_documentpdf")
        )

        prepared.append(
            _PreparedPDF(
                original_name=file.filename,
                safe_filename=safe_filename,
                # Durable jobs retain the staged storage reference, not every
                # original PDF byte array. Direct non-job callers may still
                # carry bytes for backwards-compatible internal use.
                content=None if storage_key else content,
                extracted_text=extracted_text,
                storage_key=storage_key,
                content_hash=content_hash,
            )
        )
        if checkpoint:
            await checkpoint("extracting", len(prepared), len(files), None)

    if not prepared:
        raise HTTPException(status_code=400, detail="找不到可讀取的 PDF 文字內容。")

    return prepared


async def _generate_course_plan(
    *,
    pdfs: list[_PreparedPDF],
    course_name: str | None,
    instructions: str | None,
    language: str,
    ai_model: str,
) -> CoursePlan:
    client = get_gemini_client()
    source_context = _build_source_context(pdfs, MAX_PLAN_SOURCE_CHARS)
    title_hint = course_name.strip() if course_name else "Infer a precise course title from the PDFs."
    teacher_instructions = instructions.strip() if instructions else "No additional teacher instructions."
    language_name = get_language_name(language)

    prompt = f"""Create a complete online course plan from the uploaded PDF source material.

Output language:
- Write every course name, chapter name, activity name, description, learning outcome, tag, quiz/task title, and learner-facing phrase in {language_name}.
- If the PDF source material is written in another language, translate and localize the generated course for learners in {language_name}; do not copy English headings into the final plan unless the term is a proper noun or technical term that must remain unchanged.
- For Traditional Chinese, use Traditional Chinese characters and Hong Kong/Taiwan-style wording where natural.

Course title preference:
<user_content>{title_hint}</user_content>

Teacher requirements:
<user_content>{teacher_instructions}</user_content>

Source material:
<source_material>
{source_context}
</source_material>

Design requirements:
- Use the PDFs as the primary source of truth.
- Suitable for general school subjects such as science, geography, history, language, economics, or humanities.
- Build from basic concepts to harder applications.
- Include normal tasks and challenge tasks where appropriate.
- Include checks for understanding and quizzes in the suggested blocks.
- Keep the plan practical for classroom teaching.
- If the teacher explicitly asks for a small test course, a short unit, or a limited course length, respect that request; 1 chapter with 1 activity is valid for a smoke test or very short source PDF.
- Generate no more than {MAX_CHAPTERS} chapters and no more than {MAX_ACTIVITIES_PER_CHAPTER} activities per chapter.
"""

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=ai_model,
        contents=[
            {"role": "user", "parts": [{"text": build_course_planning_system_prompt(language)}]},
            {"role": "model", "parts": [{"text": "I will return only strict JSON for a LearnHouse course plan."}]},
            {"role": "user", "parts": [{"text": prompt}]},
        ],
        config=GenerateContentConfig(response_mime_type="application/json", temperature=0.35),
    )

    plan = extract_plan_from_response(response.text or "")
    if not plan:
        raise HTTPException(status_code=502, detail="AI failed to produce a valid course plan")

    if course_name and course_name.strip():
        plan.name = course_name.strip()

    plan.chapters = plan.chapters[:MAX_CHAPTERS]
    for chapter in plan.chapters:
        chapter.activities = chapter.activities[:MAX_ACTIVITIES_PER_CHAPTER]

    return plan


async def _generate_activity_content(
    *,
    plan: CoursePlan,
    chapter_name: str,
    activity_name: str,
    activity_description: str,
    source_context: str,
    instructions: str | None,
    language: str,
    ai_model: str,
) -> dict:
    client = get_gemini_client()
    system_prompt = build_activity_content_system_prompt(
        course_name=plan.name,
        course_description=plan.description,
        chapter_name=chapter_name,
        activity_name=activity_name,
        activity_description=activity_description,
        language=language,
    )
    teacher_instructions = instructions.strip() if instructions else "No additional teacher instructions."
    prompt = f"""Generate a publishable lesson page for this activity.

Teacher requirements:
<user_content>{teacher_instructions}</user_content>

Use these PDF excerpts as source material:
<source_material>
{_truncate(source_context, MAX_ACTIVITY_SOURCE_CHARS)}
</source_material>

Lesson requirements:
- Write every learner-facing phrase in {get_language_name(language)}.
- Explain the concept clearly for learners.
- Include examples grounded in the source material.
- Include at least one quick check, quiz, flipcard, or callout.
- Include a normal task that can count as 80 points.
- Include a challenge task that can count as 100 points.
- Do not mention unavailable pages or hidden PDF internals.
"""

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=ai_model,
            contents=[
                {"role": "user", "parts": [{"text": system_prompt}]},
                {"role": "model", "parts": [{"text": "I will return only valid ProseMirror JSON."}]},
                {"role": "user", "parts": [{"text": prompt}]},
            ],
            config=GenerateContentConfig(response_mime_type="application/json", temperature=0.55),
        )
        content = extract_content_from_response(response.text or "")
        if content:
            return content
    except Exception as exc:
        logger.warning(
            "ai.pdf_course.activity_content_fallback",
            extra={
                "integration": "ai_text",
                "operation": "generate_pdf_activity",
                "error_code": type(exc).__name__,
            },
        )

    return _fallback_content(activity_name, activity_description, source_context)


async def _create_course_records(
    *,
    org: Organization,
    creator_user_id: int,
    plan: CoursePlan,
    pdfs: list[_PreparedPDF],
    instructions: str | None,
    language: str,
    ai_model: str,
    db_session: AsyncSession,
    checkpoint: PDFBuildCheckpoint | None = None,
    build_job_uuid: str | None = None,
    resume_course_id: int | None = None,
) -> tuple[Course, int, int, int]:
    now = str(datetime.now())
    total_units = sum(len(chapter.activities) for chapter in plan.chapters) + len(pdfs)
    course = await db_session.get(Course, resume_course_id) if resume_course_id else None
    if course is not None and course.org_id != org.id:
        raise RuntimeError("pdf_build_resume_scope_mismatch")
    if course is None:
        course = Course(
            course_uuid=f"course_{uuid4()}",
            name=plan.name,
            description=plan.description,
            about=plan.description,
            learnings=plan.learnings,
            tags=plan.tags,
            public=False,
            published=False,
            open_to_contributors=False,
            org_id=org.id,
            creation_date=now,
            update_date=now,
            extra_metadata={
                "ai_pdf_build": {
                    "source_files": [pdf.original_name for pdf in pdfs],
                    "language": language,
                    "job_uuid": build_job_uuid,
                }
            },
        )
        db_session.add(course)
        if checkpoint:
            # Flush assigns the course id, then the checkpoint callback commits
            # the new draft and job.course_id in the same transaction. A crash
            # cannot leave an undiscoverable duplicate draft behind.
            await db_session.flush()
            await checkpoint(
                "creating",
                0,
                total_units,
                {"course_id": course.id, "chapters": 0, "activities": 0, "sources": 0},
            )
        else:
            await db_session.commit()
        await db_session.refresh(course)
    else:
        # Recovery must never turn a partial draft into visible course content.
        course.public = False
        course.published = False
        db_session.add(course)
        await db_session.commit()

    if checkpoint and resume_course_id:
        await checkpoint(
            "creating",
            0,
            total_units,
            {"course_id": course.id, "chapters": 0, "activities": 0, "sources": 0},
        )

    resource_author = (
        await db_session.execute(
            select(ResourceAuthor).where(
                ResourceAuthor.resource_uuid == course.course_uuid,
                ResourceAuthor.user_id == creator_user_id,
            )
        )
    ).scalars().first()
    if resource_author is None:
        resource_author = ResourceAuthor(
            resource_uuid=course.course_uuid,
            user_id=creator_user_id,
            authorship=ResourceAuthorshipEnum.CREATOR,
            authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
            creation_date=now,
            update_date=now,
        )
        db_session.add(resource_author)
        await db_session.commit()

    source_context = _build_source_context(pdfs, MAX_TOTAL_SOURCE_CHARS)
    chapters_created = 0
    activities_created = 0

    existing_chapters = list(
        (
            await db_session.execute(select(Chapter).where(Chapter.course_id == course.id))
        ).scalars().all()
    )
    existing_activities = list(
        (
            await db_session.execute(select(Activity).where(Activity.course_id == course.id))
        ).scalars().all()
    )

    for chapter_index, chapter_plan in enumerate(plan.chapters):
        chapter = next(
            (
                item
                for item in existing_chapters
                if (item.extra_metadata or {}).get("ai_pdf_build_job") == build_job_uuid
                and (item.extra_metadata or {}).get("plan_chapter_index") == chapter_index
            ),
            None,
        ) if build_job_uuid else None
        if chapter is None:
            chapter = Chapter(
                chapter_uuid=f"chapter_{uuid4()}",
                name=chapter_plan.name,
                description=chapter_plan.description,
                course_id=course.id,
                org_id=org.id,
                creation_date=now,
                update_date=now,
                extra_metadata={
                    "ai_pdf_build_job": build_job_uuid,
                    "plan_chapter_index": chapter_index,
                },
            )
            db_session.add(chapter)
            await db_session.commit()
            await db_session.refresh(chapter)
        chapter_link = (
            await db_session.execute(
                select(CourseChapter).where(
                    CourseChapter.course_id == course.id,
                    CourseChapter.chapter_id == chapter.id,
                )
            )
        ).scalars().first()
        if chapter_link is None:
            db_session.add(
                CourseChapter(
                    course_id=course.id,
                    chapter_id=chapter.id,
                    org_id=org.id,
                    creation_date=now,
                    update_date=now,
                    order=chapter_index,
                )
            )
            await db_session.commit()
        chapters_created += 1

        for activity_index, activity_plan in enumerate(chapter_plan.activities):
            activity = next(
                (
                    item
                    for item in existing_activities
                    if (item.extra_metadata or {}).get("ai_pdf_build_job") == build_job_uuid
                    and (item.extra_metadata or {}).get("plan_chapter_index") == chapter_index
                    and (item.extra_metadata or {}).get("plan_activity_index") == activity_index
                ),
                None,
            ) if build_job_uuid else None
            if activity is None:
                content = await _generate_activity_content(
                    plan=plan,
                    chapter_name=chapter_plan.name,
                    activity_name=activity_plan.name,
                    activity_description=activity_plan.description,
                    source_context=source_context,
                    instructions=instructions,
                    language=language,
                    ai_model=ai_model,
                )

                activity = Activity(
                    activity_uuid=f"activity_{uuid4()}",
                    name=activity_plan.name,
                    activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
                    activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
                    content=content,
                    details={"description": activity_plan.description},
                    published=False,
                    course_id=course.id,
                    org_id=org.id,
                    creation_date=now,
                    update_date=now,
                    extra_metadata={
                        "ai_pdf_build": True,
                        "ai_pdf_build_job": build_job_uuid,
                        "plan_chapter_index": chapter_index,
                        "plan_activity_index": activity_index,
                        "suggested_blocks": activity_plan.suggested_blocks,
                    },
                )
                db_session.add(activity)
                await db_session.commit()
                await db_session.refresh(activity)
            activity_link = (
                await db_session.execute(
                    select(ChapterActivity).where(
                        ChapterActivity.chapter_id == chapter.id,
                        ChapterActivity.activity_id == activity.id,
                    )
                )
            ).scalars().first()
            if activity_link is None:
                db_session.add(
                    ChapterActivity(
                        chapter_id=chapter.id,
                        activity_id=activity.id,
                        course_id=course.id,
                        org_id=org.id,
                        creation_date=now,
                        update_date=now,
                        order=activity_index,
                    )
                )
                await db_session.commit()
            activities_created += 1
            if checkpoint:
                await checkpoint(
                    "creating",
                    activities_created,
                    total_units,
                    {
                        "course_id": course.id,
                        "chapters": chapters_created,
                        "activities": activities_created,
                        "sources": 0,
                    },
                )

    source_documents_created = await _create_source_document_chapter(
        org=org,
        course=course,
        pdfs=pdfs,
        db_session=db_session,
        chapter_order=len(plan.chapters),
        checkpoint=checkpoint,
        build_job_uuid=build_job_uuid,
        progress_offset=activities_created,
        progress_total=total_units,
    )

    course.update_date = str(datetime.now())
    flag_modified(course, "extra_metadata")
    db_session.add(course)
    await db_session.commit()

    return course, chapters_created + (1 if source_documents_created else 0), activities_created, source_documents_created


async def _create_source_document_chapter(
    *,
    org: Organization,
    course: Course,
    pdfs: list[_PreparedPDF],
    db_session: AsyncSession,
    chapter_order: int,
    checkpoint: PDFBuildCheckpoint | None = None,
    build_job_uuid: str | None = None,
    progress_offset: int = 0,
    progress_total: int = 0,
) -> int:
    now = str(datetime.now())
    chapter = (
        await db_session.execute(
            select(Chapter).where(Chapter.course_id == course.id)
        )
    ).scalars().all()
    chapter = next(
        (
            item
            for item in chapter
            if (item.extra_metadata or {}).get("ai_pdf_source_documents") is True
            and (
                not build_job_uuid
                or (item.extra_metadata or {}).get("ai_pdf_build_job") == build_job_uuid
            )
        ),
        None,
    )
    if chapter is None:
        chapter = Chapter(
            chapter_uuid=f"chapter_{uuid4()}",
            name="原始 PDF 教材",
            description="老師上傳的原始教材文件。",
            course_id=course.id,
            org_id=org.id,
            creation_date=now,
            update_date=now,
            extra_metadata={
                "ai_pdf_source_documents": True,
                "ai_pdf_build_job": build_job_uuid,
            },
        )
        db_session.add(chapter)
        await db_session.commit()
        await db_session.refresh(chapter)
    chapter_link = (
        await db_session.execute(
            select(CourseChapter).where(
                CourseChapter.course_id == course.id,
                CourseChapter.chapter_id == chapter.id,
            )
        )
    ).scalars().first()
    if chapter_link is None:
        db_session.add(
            CourseChapter(
                course_id=course.id,
                chapter_id=chapter.id,
                org_id=org.id,
                creation_date=now,
                update_date=now,
                order=chapter_order,
            )
        )
        await db_session.commit()

    existing_activities = list(
        (
            await db_session.execute(select(Activity).where(Activity.course_id == course.id))
        ).scalars().all()
    )

    created = 0
    for index, pdf in enumerate(pdfs):
        existing = next(
            (
                item
                for item in existing_activities
                if (item.extra_metadata or {}).get("ai_pdf_source_document") is True
                and (item.extra_metadata or {}).get("source_index") == index
                and (
                    not build_job_uuid
                    or (item.extra_metadata or {}).get("ai_pdf_build_job") == build_job_uuid
                )
            ),
            None,
        )
        if existing is not None:
            activity_link = (
                await db_session.execute(
                    select(ChapterActivity).where(
                        ChapterActivity.chapter_id == chapter.id,
                        ChapterActivity.activity_id == existing.id,
                    )
                )
            ).scalars().first()
            if activity_link is None:
                db_session.add(
                    ChapterActivity(
                        chapter_id=chapter.id,
                        activity_id=existing.id,
                        course_id=course.id,
                        org_id=org.id,
                        creation_date=now,
                        update_date=now,
                        order=index,
                    )
                )
                await db_session.commit()
            created += 1
            if checkpoint:
                await checkpoint(
                    "creating",
                    progress_offset + created,
                    progress_total,
                    {
                        "course_id": course.id,
                        "chapters": chapter_order + 1,
                        "activities": progress_offset,
                        "sources": created,
                    },
                )
            continue
        deterministic_source = (
            f"{build_job_uuid}:{index}:{pdf.content_hash or pdf.safe_filename}"
            if build_job_uuid
            else None
        )
        activity_uuid = (
            f"activity_{uuid5(NAMESPACE_URL, deterministic_source)}"
            if deterministic_source
            else f"activity_{uuid4()}"
        )
        directory = f"courses/{course.course_uuid}/activities/{activity_uuid}/documentpdf"
        pdf_content = pdf.content
        if pdf_content is None and pdf.storage_key:
            pdf_content = await asyncio.to_thread(read_file_content, pdf.storage_key)
        if pdf_content is None:
            raise RuntimeError("pdf_build_staging_missing")
        source_filename = (
            _source_safe_filename(
                original_name=pdf.original_name,
                source_index=index,
                content_hash=pdf.content_hash or hashlib.sha256(pdf_content).hexdigest(),
                build_job_uuid=build_job_uuid,
            )
            if build_job_uuid
            else pdf.safe_filename
        )
        await upload_content(
            directory=directory,
            type_of_dir="orgs",
            uuid=org.org_uuid,
            file_binary=pdf_content,
            file_and_format=source_filename,
            allowed_formats=["pdf"],
        )
        file_path = f"content/orgs/{org.org_uuid}/{directory}/{source_filename}"
        activity = Activity(
            activity_uuid=activity_uuid,
            name=pdf.original_name,
            activity_type=ActivityTypeEnum.TYPE_DOCUMENT,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DOCUMENT_PDF,
            content={
                "filename": source_filename,
                "file_path": file_path,
                "file_format": "pdf",
                "source_name": pdf.original_name,
            },
            details={"description": "AI 建課使用的原始 PDF 教材。"},
            published=False,
            course_id=course.id,
            org_id=org.id,
            creation_date=now,
            update_date=now,
            extra_metadata={
                "ai_pdf_source_document": True,
                "ai_pdf_build_job": build_job_uuid,
                "source_index": index,
            },
        )
        db_session.add(activity)
        await db_session.commit()
        await db_session.refresh(activity)

        db_session.add(
            ChapterActivity(
                chapter_id=chapter.id,
                activity_id=activity.id,
                course_id=course.id,
                org_id=org.id,
                creation_date=now,
                update_date=now,
                order=index,
            )
        )
        await db_session.commit()
        created += 1
        if checkpoint:
            await checkpoint(
                "creating",
                progress_offset + created,
                progress_total,
                {
                    "course_id": course.id,
                    "chapters": chapter_order + 1,
                    "activities": progress_offset,
                    "sources": created,
                },
            )

    return created


async def build_course_from_pdfs(
    *,
    org: Organization,
    creator_user_id: int,
    files: list[UploadFile],
    course_name: str | None,
    instructions: str | None,
    language: str,
    ai_model: str,
    db_session: AsyncSession,
    auto_index: bool = True,
    checkpoint: PDFBuildCheckpoint | None = None,
    plan_checkpoint: dict | None = None,
    resume_course_id: int | None = None,
    build_job_uuid: str | None = None,
) -> PDFCourseBuildResponse:
    pdfs = await _prepare_pdfs(
        files,
        checkpoint=checkpoint,
        build_job_uuid=build_job_uuid,
    )
    if checkpoint:
        await checkpoint("planning", 0, 1, None)
    if plan_checkpoint:
        plan = CoursePlan.model_validate(plan_checkpoint)
    else:
        plan = await _generate_course_plan(
            pdfs=pdfs,
            course_name=course_name,
            instructions=instructions,
            language=language,
            ai_model=ai_model,
        )
    if checkpoint:
        await checkpoint("planning", 1, 1, {"plan": plan.model_dump(mode="json")})
    course, chapters_created, activities_created, source_documents_created = await _create_course_records(
        org=org,
        creator_user_id=creator_user_id,
        plan=plan,
        pdfs=pdfs,
        instructions=instructions,
        language=language,
        ai_model=ai_model,
        db_session=db_session,
        checkpoint=checkpoint,
        build_job_uuid=build_job_uuid,
        resume_course_id=resume_course_id,
    )

    chunks_indexed = 0
    indexing_status: Literal["success", "skipped", "failed", "degraded"] = "skipped"
    indexing_error = None
    if auto_index:
        if checkpoint:
            await checkpoint(
                "indexing",
                0,
                0,
                {
                    "course_id": course.id,
                    "chapters": chapters_created,
                    "activities": activities_created,
                    "sources": source_documents_created,
                },
            )
        try:
            index_result = await embed_course_content(course.id, org.id, db_session)
            chunks_indexed = index_result.chunks_indexed
            # A hash-fallback index is not a success — the caller sees
            # "degraded" and the reason rather than a green tick.
            indexing_status = "degraded" if index_result.degraded else "success"
            indexing_error = index_result.degraded_reason
        except EmbeddingUnavailableError as exc:
            logger.error(
                "ai.pdf_course.embeddings_unavailable",
                extra={
                    "integration": "rag",
                    "operation": "index_pdf_course",
                    "error_code": exc.code,
                    "course_id": course.id,
                },
            )
            indexing_status = "failed"
            indexing_error = exc.code
        except Exception as exc:
            error_code = (
                exc.code
                if isinstance(exc, AIProviderError)
                else "rag_indexing_failed"
            )
            logger.warning(
                "ai.pdf_course.indexing_failed",
                extra={
                    "integration": "rag",
                    "operation": "index_pdf_course",
                    "error_code": error_code,
                },
            )
            indexing_status = "failed"
            indexing_error = error_code

    if checkpoint:
        await checkpoint(
            "indexing",
            chunks_indexed,
            chunks_indexed,
            {
                "course_id": course.id,
                "chapters": chapters_created,
                "activities": activities_created,
                "sources": source_documents_created,
                "indexing_status": indexing_status,
                "indexing_code": indexing_error,
                "chunks": chunks_indexed,
            },
        )

    return PDFCourseBuildResponse(
        status="success",
        course_uuid=course.course_uuid,
        course_id=course.id,
        chapters_created=chapters_created,
        activities_created=activities_created,
        source_documents_created=source_documents_created,
        chunks_indexed=chunks_indexed,
        indexing_status=indexing_status,
        indexing_error=indexing_error,
    )
