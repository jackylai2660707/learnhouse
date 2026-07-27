"""Server-side authorization scopes for RAG retrieval."""

from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import and_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course
from src.db.users import APITokenUser, AnonymousUser, PublicUser
from src.security.auth import resolve_acting_user_id
from src.security.rbac import AccessAction, AccessContext, ResourceAccessChecker
from src.services.courses.locks import batch_accessible_restricted_uuids


@dataclass(frozen=True)
class RAGRetrievalScope:
    """Server-authorized course and activity IDs for one RAG request."""

    course_ids: list[int]
    activity_ids: list[int]


def _lock_value(value) -> str:
    return str(getattr(value, "value", value) or "public").lower()


async def resolve_rag_retrieval_scope(
    request: Request,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    org_id: int,
    db_session: AsyncSession,
    *,
    requested_course: Course | None = None,
) -> RAGRetrievalScope:
    """Resolve normal course READ plus per-activity visibility.

    Course authors/admins/users with UPDATE access receive draft activities in
    their own course scope. Other authenticated users receive published
    activities only, with the same course/chapter/activity usergroup locks used
    by ordinary course reads.
    """
    if requested_course is not None:
        courses = [requested_course]
    else:
        courses = (
            await db_session.execute(select(Course).where(Course.org_id == org_id))
        ).scalars().all()

    checker = ResourceAccessChecker(request, db_session, current_user)
    readable_courses: list[Course] = []
    draft_course_ids: set[int] = set()
    for course in courses:
        read_decision = await checker.check_access(
            course.course_uuid,
            AccessAction.READ,
            AccessContext.PUBLIC_VIEW,
        )
        update_decision = await checker.check_access(
            course.course_uuid,
            AccessAction.UPDATE,
            AccessContext.DASHBOARD,
        )
        # Broad roles historically grant READ on a public but unpublished
        # course. Only actual course updaters may turn that into draft context.
        allowed = read_decision.allowed and (course.published or update_decision.allowed)
        if not allowed:
            if requested_course is not None:
                raise HTTPException(status_code=403, detail=read_decision.reason)
            continue
        if course.id is None:
            continue
        readable_courses.append(course)
        if update_decision.allowed:
            draft_course_ids.add(course.id)

    course_ids = [course.id for course in readable_courses if course.id is not None]
    if not course_ids:
        return RAGRetrievalScope(course_ids=[], activity_ids=[])

    activity_rows = (
        await db_session.execute(
            select(Activity, Chapter, CourseChapter)
            .outerjoin(
                ChapterActivity,
                and_(
                    ChapterActivity.activity_id == Activity.id,
                    ChapterActivity.course_id == Activity.course_id,
                    ChapterActivity.org_id == Activity.org_id,
                ),
            )
            .outerjoin(
                Chapter,
                and_(
                    Chapter.id == ChapterActivity.chapter_id,
                    Chapter.course_id == Activity.course_id,
                    Chapter.org_id == Activity.org_id,
                ),
            )
            .outerjoin(
                CourseChapter,
                and_(
                    CourseChapter.chapter_id == Chapter.id,
                    CourseChapter.course_id == Activity.course_id,
                    CourseChapter.org_id == Activity.org_id,
                ),
            )
            .where(
                Activity.course_id.in_(course_ids),
                Activity.org_id == org_id,
            )
        )
    ).all()

    check_uuids = [course.course_uuid for course in readable_courses]
    for activity, chapter, course_chapter in activity_rows:
        if _lock_value(activity.lock_type) == "restricted":
            check_uuids.append(activity.activity_uuid)
        if (
            chapter is not None
            and course_chapter is not None
            and _lock_value(chapter.lock_type) == "restricted"
        ):
            check_uuids.append(chapter.chapter_uuid)

    accessible_restricted = await batch_accessible_restricted_uuids(
        resolve_acting_user_id(current_user),
        check_uuids,
        db_session,
    )
    course_uuid_by_id = {
        course.id: course.course_uuid
        for course in readable_courses
        if course.id is not None
    }

    # An activity may temporarily appear in more than one chapter during an
    # edit. It is visible if at least one ordinary navigation path is unlocked.
    visible_by_activity: dict[int, bool] = {}
    for activity, chapter, course_chapter in activity_rows:
        if activity.id is None:
            continue
        if activity.course_id in draft_course_ids:
            visible_by_activity[activity.id] = True
            continue
        if not activity.published:
            visible_by_activity.setdefault(activity.id, False)
            continue

        # Student-visible material must be reachable through the same complete
        # navigation chain as the course UI. An orphan activity, a dangling
        # ChapterActivity, or a cross-course/org link is never implicitly
        # "unlocked" merely because an outer join produced no chapter row.
        if chapter is None or course_chapter is None:
            visible_by_activity.setdefault(activity.id, False)
            continue

        course_grant = (
            course_uuid_by_id.get(activity.course_id) in accessible_restricted
        )
        activity_unlocked = (
            course_grant
            or _lock_value(activity.lock_type) != "restricted"
            or activity.activity_uuid in accessible_restricted
        )
        chapter_unlocked = (
            course_grant
            or _lock_value(chapter.lock_type) != "restricted"
            or chapter.chapter_uuid in accessible_restricted
        )
        visible_by_activity[activity.id] = (
            visible_by_activity.get(activity.id, False)
            or (activity_unlocked and chapter_unlocked)
        )

    return RAGRetrievalScope(
        course_ids=course_ids,
        activity_ids=[
            activity_id
            for activity_id, visible in visible_by_activity.items()
            if visible
        ],
    )
