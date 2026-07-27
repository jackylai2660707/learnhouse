import pytest
from sqlmodel import select

from src.db.courses.blocks import Block, BlockTypeEnum
from src.db.courses.chapter_activities import ChapterActivity
from src.services.ai.rag.content_extraction import (
    ContentExtractionIntegrityError,
    extract_all_course_content,
    extract_text_from_pdf,
)


def _minimal_text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def test_extract_text_from_synthetic_pdf():
    extracted = extract_text_from_pdf(
        _minimal_text_pdf("Synthetic water cycle lesson")
    )

    assert "Synthetic water cycle lesson" in extracted


def test_extract_text_from_invalid_pdf_does_not_log_raw_exception(caplog):
    extracted = extract_text_from_pdf(b"provider-internal-secret")

    assert extracted == ""
    assert "provider-internal-secret" not in caplog.text


@pytest.mark.asyncio
async def test_extraction_rejects_caller_org_mismatch(db, course):
    with pytest.raises(ContentExtractionIntegrityError):
        await extract_all_course_content(course.id, course.org_id + 999, db)


@pytest.mark.asyncio
async def test_extraction_rejects_cross_org_activity(db, course, activity):
    activity.org_id = course.org_id + 999
    db.add(activity)
    await db.commit()

    with pytest.raises(ContentExtractionIntegrityError):
        await extract_all_course_content(course.id, course.org_id, db)


@pytest.mark.asyncio
async def test_extraction_rejects_cross_org_chapter(db, course, chapter):
    chapter.org_id = course.org_id + 999
    db.add(chapter)
    await db.commit()

    with pytest.raises(ContentExtractionIntegrityError):
        await extract_all_course_content(course.id, course.org_id, db)


@pytest.mark.asyncio
async def test_extraction_rejects_cross_course_chapter_activity_link(
    db, course, activity
):
    link = (
        await db.execute(
            select(ChapterActivity).where(
                ChapterActivity.activity_id == activity.id
            )
        )
    ).scalars().one()
    link.course_id = course.id + 999
    db.add(link)
    await db.commit()

    with pytest.raises(ContentExtractionIntegrityError):
        await extract_all_course_content(course.id, course.org_id, db)


@pytest.mark.asyncio
async def test_extraction_rejects_cross_org_block(db, course, activity):
    db.add(
        Block(
            block_type=BlockTypeEnum.BLOCK_CUSTOM,
            content={"text": "unsafe tenant block"},
            org_id=course.org_id + 999,
            course_id=course.id,
            activity_id=activity.id,
            block_uuid="block_cross_org_extraction",
            creation_date="test",
            update_date="test",
        )
    )
    await db.commit()

    with pytest.raises(ContentExtractionIntegrityError):
        await extract_all_course_content(course.id, course.org_id, db)
