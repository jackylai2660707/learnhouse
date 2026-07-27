"""
Tests for the assignment retry feature.

Covers:
- retry_assignment_submission service function (success, forbidden, attempt
  limit, wrong status, missing rows, unlimited path).
- create_assignment_submission reuse-of-PENDING-row path triggered by a prior
  retry (and the still-erroring SUBMITTED/GRADED case).
- AssignmentRead / AssignmentUserSubmissionRead exposing the new fields
  ``allow_retries``, ``max_retries`` and ``attempt_number``.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from src.core.events.database import get_db_session
from src.db.courses.assignments import (
    Assignment,
    AssignmentRead,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionRead,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.certifications import CertificateUser, Certifications
from src.db.trail_runs import TrailRun
from src.db.trail_steps import TrailStep
from src.db.trails import Trail
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.routers.courses.assignments import router as assignments_router
from src.security.auth import get_current_user
from src.services.courses.activities.assignments import (
    create_assignment_submission,
    retry_assignment_submission,
)


# ---------------------------------------------------------------------------
# Helper fixtures local to this module
# ---------------------------------------------------------------------------


@pytest.fixture
async def assignment(db, org, course, chapter, activity, regular_user):
    """Assignment row with retries allowed by default."""
    now = str(datetime.now())
    usergroup = UserGroup(
        name="Retry Class",
        description="Class used by assignment retry tests",
        org_id=org.id,
        usergroup_uuid="usergroup_retry_test",
        creation_date=now,
        update_date=now,
    )
    db.add(usergroup)
    await db.commit()
    await db.refresh(usergroup)
    db.add(
        UserGroupUser(
            usergroup_id=usergroup.id,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=now,
            update_date=now,
        )
    )
    await db.commit()

    a = Assignment(
        id=1,
        title="Retryable",
        description="Assignment used by retry tests",
        due_date="2030-01-01",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=False,
        anti_copy_paste=False,
        show_correct_answers=False,
        allow_retries=True,
        max_retries=3,
        target_usergroup_ids=[usergroup.id],
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_retry_test",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


@pytest.fixture
async def assignment_task(db, org, course, chapter, activity, assignment):
    """One task hung off the assignment so retry has something to wipe."""
    t = AssignmentTask(
        id=1,
        title="Task 1",
        description="A task",
        hint="",
        reference_file=None,
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={"prompt": "x"},
        max_grade_value=100,
        assignment_id=assignment.id,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_task_uuid="assignmenttask_1",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _make_user_submission(
    db,
    assignment_id,
    user_id,
    *,
    status=AssignmentUserSubmissionStatus.GRADED,
    grade=80,
    attempt_number=1,
    overall_feedback="Good job",
    uuid="aus_retry_test",
):
    sub = AssignmentUserSubmission(
        user_id=user_id,
        assignment_id=assignment_id,
        grade=grade,
        submission_status=status,
        overall_feedback=overall_feedback,
        attempt_number=attempt_number,
        assignmentusersubmission_uuid=uuid,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def _make_task_submission(db, task, user_id, *, grade=50, uuid="ats_retry_test"):
    ts = AssignmentTaskSubmission(
        assignment_task_submission_uuid=uuid,
        task_submission={"answer": "x"},
        grade=grade,
        task_submission_grade_feedback="ok",
        assignment_type=task.assignment_type,
        user_id=user_id,
        activity_id=task.activity_id,
        course_id=task.course_id,
        chapter_id=task.chapter_id,
        assignment_task_id=task.id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(ts)
    await db.commit()
    await db.refresh(ts)
    return ts


async def _make_trail_artifacts(db, org_id, course_id, activity_id, user_id, *, complete=True):
    trail = Trail(
        org_id=org_id,
        user_id=user_id,
        trail_uuid="trail_retry",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(trail)
    await db.commit()
    await db.refresh(trail)
    trail_run = TrailRun(
        trail_id=trail.id,
        course_id=course_id,
        org_id=org_id,
        user_id=user_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(trail_run)
    await db.commit()
    await db.refresh(trail_run)
    step = TrailStep(
        complete=complete,
        teacher_verified=True,
        grade="A",
        trailrun_id=trail_run.id,
        trail_id=trail.id,
        activity_id=activity_id,
        course_id=course_id,
        org_id=org_id,
        user_id=user_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return trail, trail_run, step


async def _make_certificate(db, course, user_id):
    cert = Certifications(
        certification_uuid="cert_retry",
        course_id=course.id,
        config={},
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(cert)
    await db.commit()
    await db.refresh(cert)
    cert_user = CertificateUser(
        user_id=user_id,
        certification_id=cert.id,
        user_certification_uuid="certuser_retry",
        created_at=str(datetime.now()),
        updated_at=str(datetime.now()),
    )
    db.add(cert_user)
    await db.commit()
    await db.refresh(cert_user)
    return cert, cert_user


# ---------------------------------------------------------------------------
# retry_assignment_submission service-level tests
# ---------------------------------------------------------------------------


class TestRetryAssignmentSubmissionService:
    async def test_retry_success_preserves_answers_and_resets_scores_and_trailstep(
        self, db, regular_user, mock_request, org, course, activity, assignment, assignment_task
    ):
        user_id = regular_user.id
        submission = await _make_user_submission(db, assignment.id, user_id)
        task_sub = await _make_task_submission(db, assignment_task, user_id)
        _, _, step = await _make_trail_artifacts(
            db, org.id, course.id, activity.id, user_id, complete=True
        )
        cert, cert_user = await _make_certificate(db, course, user_id)

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            result = await retry_assignment_submission(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        assert result["message"] == "已開放修改答案，可重新提交"
        assert result["attempt_number"] == 2
        assert result["max_retries"] == 3
        assert result["submission"]["submission_status"] == AssignmentUserSubmissionStatus.PENDING.value

        await db.refresh(submission)
        assert submission.submission_status == AssignmentUserSubmissionStatus.PENDING
        assert submission.grade == 0
        assert submission.overall_feedback is None
        assert submission.attempt_number == 2

        preserved_task_sub = (await db.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.id == task_sub.id
            )
        )).scalars().first()
        assert preserved_task_sub is not None
        assert preserved_task_sub.task_submission == {"answer": "x"}
        assert preserved_task_sub.grade == 0
        assert preserved_task_sub.task_submission_grade_feedback == ""

        await db.refresh(step)
        assert step.complete is False
        assert step.teacher_verified is False
        assert step.grade == ""

        leftover_cert = (await db.execute(
            select(CertificateUser).where(CertificateUser.id == cert_user.id)
        )).scalars().first()
        assert leftover_cert is None

    async def test_retry_forbidden_when_allow_retries_false(
        self, db, regular_user, mock_request, assignment
    ):
        assignment.allow_retries = False
        db.add(assignment)
        await db.commit()
        await _make_user_submission(db, assignment.id, regular_user.id)

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await retry_assignment_submission(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 403
        assert "這份作業未開放重做" in exc_info.value.detail

    async def test_retry_forbidden_when_attempt_limit_reached(
        self, db, regular_user, mock_request, assignment
    ):
        assignment.max_retries = 3
        db.add(assignment)
        await db.commit()
        await _make_user_submission(
            db,
            assignment.id,
            regular_user.id,
            attempt_number=3,
        )

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await retry_assignment_submission(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 403
        assert "沒有剩餘重做次數" in exc_info.value.detail

    async def test_retry_rejects_non_graded_submission(
        self, db, regular_user, mock_request, assignment
    ):
        await _make_user_submission(
            db,
            assignment.id,
            regular_user.id,
            status=AssignmentUserSubmissionStatus.SUBMITTED,
        )

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await retry_assignment_submission(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 400
        assert "只有已批改的提交可以重做" in exc_info.value.detail

    async def test_retry_returns_404_when_no_submission_exists(
        self, db, regular_user, mock_request, assignment
    ):
        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await retry_assignment_submission(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 404
        assert "找不到學生提交記錄" in exc_info.value.detail

    async def test_retry_returns_404_when_assignment_missing(
        self, db, regular_user, mock_request
    ):
        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await retry_assignment_submission(
                    mock_request, "assignment_does_not_exist", regular_user, db
                )

        assert exc_info.value.status_code == 404
        assert "Assignment not found" in exc_info.value.detail

    async def test_retry_returns_404_when_course_missing(
        self, db, regular_user, mock_request, org, chapter, activity
    ):
        """Orphan assignment whose course_id points at a non-existent row.
        Exercises the defensive 'Course not found' 404 inside the retry
        service — unlikely in production but the branch should still be
        covered to keep patch coverage above the codecov target."""
        orphan = Assignment(
            id=999,
            title="Orphan",
            description="",
            due_date="2030-01-01",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            allow_retries=True,
            max_retries=0,
            org_id=org.id,
            course_id=4242,  # No course row with this id.
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_orphan_course",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(orphan)
        await db.commit()

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await retry_assignment_submission(
                    mock_request, orphan.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 404
        assert "Course not found" in exc_info.value.detail

    async def test_retry_unlimited_when_max_retries_zero(
        self, db, regular_user, mock_request, assignment
    ):
        assignment.max_retries = 0
        db.add(assignment)
        await db.commit()
        submission = await _make_user_submission(
            db,
            assignment.id,
            regular_user.id,
            attempt_number=42,
        )

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            result = await retry_assignment_submission(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        assert result["attempt_number"] == 43
        assert result["max_retries"] == 0
        await db.refresh(submission)
        assert submission.submission_status == AssignmentUserSubmissionStatus.PENDING
        assert submission.attempt_number == 43


# ---------------------------------------------------------------------------
# create_assignment_submission retry/reuse path
# ---------------------------------------------------------------------------


class TestCreateAssignmentSubmissionRetryPath:
    async def test_reuses_pending_row_after_retry(
        self,
        db,
        regular_user,
        mock_request,
        org,
        course,
        activity,
        assignment,
        assignment_task,
    ):
        original_uuid = "aus_reuse_after_retry"
        submission = await _make_user_submission(
            db,
            assignment.id,
            regular_user.id,
            status=AssignmentUserSubmissionStatus.PENDING,
            grade=0,
            overall_feedback=None,
            attempt_number=2,
            uuid=original_uuid,
        )
        _, _, step = await _make_trail_artifacts(
            db, org.id, course.id, activity.id, regular_user.id, complete=False
        )
        await _make_task_submission(
            db, assignment_task, regular_user.id, uuid="ats_retry_resubmit"
        )
        original_id = submission.id

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments.track",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments.dispatch_webhooks",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments."
            "check_course_completion_and_create_certificate",
            new_callable=AsyncMock,
        ):
            result = await create_assignment_submission(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        assert result.id == original_id
        assert result.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
        assert result.grade == 0
        assert result.attempt_number == 2

        await db.refresh(step)
        assert step.complete is True

        rows = (await db.execute(
            select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.assignment_id == assignment.id,
                AssignmentUserSubmission.user_id == regular_user.id,
            )
        )).scalars().all()
        assert len(rows) == 1
        # Same row reused: original uuid preserved on the persisted record.
        assert rows[0].assignmentusersubmission_uuid == original_uuid

    async def test_rejects_when_existing_row_is_submitted(
        self, db, regular_user, mock_request, assignment
    ):
        await _make_user_submission(
            db,
            assignment.id,
            regular_user.id,
            status=AssignmentUserSubmissionStatus.SUBMITTED,
        )

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_assignment_submission(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 400
        assert "學生已提交這份作業" in exc_info.value.detail

    async def test_rejects_when_existing_row_is_graded(
        self, db, regular_user, mock_request, assignment
    ):
        await _make_user_submission(
            db,
            assignment.id,
            regular_user.id,
            status=AssignmentUserSubmissionStatus.GRADED,
        )

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_assignment_submission(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc_info.value.status_code == 400

    async def test_creates_fresh_row_on_first_submission(
        self,
        db,
        regular_user,
        mock_request,
        org,
        course,
        activity,
        assignment,
        assignment_task,
    ):
        """When no AssignmentUserSubmission row exists yet, the service hits
        the else branch and creates a fresh row with attempt_number=1. The
        first-submission path also marks the (existing) trail step complete
        via the new else branch that keeps the reuse path consistent."""
        _, _, step = await _make_trail_artifacts(
            db, org.id, course.id, activity.id, regular_user.id, complete=False
        )
        await _make_task_submission(
            db, assignment_task, regular_user.id, uuid="ats_first_submit"
        )

        with patch(
            "src.services.courses.activities.assignments.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments.track",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments.dispatch_webhooks",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments."
            "check_course_completion_and_create_certificate",
            new_callable=AsyncMock,
        ):
            result = await create_assignment_submission(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        assert result.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
        assert result.grade == 0
        assert result.attempt_number == 1

        await db.refresh(step)
        assert step.complete is True

        rows = (await db.execute(
            select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.assignment_id == assignment.id,
                AssignmentUserSubmission.user_id == regular_user.id,
            )
        )).scalars().all()
        assert len(rows) == 1
        # A brand-new uuid was generated (not the reuse path).
        assert rows[0].assignmentusersubmission_uuid.startswith(
            "assignmentusersubmission_"
        )


# ---------------------------------------------------------------------------
# Schema field coverage
# ---------------------------------------------------------------------------


class TestRetrySchemaFields:
    def test_assignment_read_exposes_retry_fields(self):
        read = AssignmentRead(
            id=10,
            assignment_uuid="a_uuid",
            title="T",
            description="D",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            allow_retries=True,
            max_retries=5,
            org_id=1,
            course_id=1,
            chapter_id=1,
            activity_id=1,
        )

        dumped = read.model_dump()
        assert dumped["allow_retries"] is True
        assert dumped["max_retries"] == 5

    def test_assignment_user_submission_read_exposes_attempt_number(self):
        read = AssignmentUserSubmissionRead(
            id=1,
            user_id=1,
            assignment_id=1,
            grade=0,
            submission_status=AssignmentUserSubmissionStatus.PENDING,
            attempt_number=4,
            creation_date="2024-01-01",
            update_date="2024-01-01",
        )
        dumped = read.model_dump()
        assert dumped["attempt_number"] == 4
        assert dumped["submission_status"] == AssignmentUserSubmissionStatus.PENDING.value


# ---------------------------------------------------------------------------
# Router-level test for the new endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db, regular_user):
    app = FastAPI()
    app.include_router(assignments_router, prefix="/api/v1/assignments")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: regular_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestRetryAssignmentRouter:
    async def test_retry_endpoint_returns_200_and_new_attempt(self, client):
        expected = {
            "message": "已開放修改答案，可重新提交",
            "attempt_number": 2,
            "max_retries": 3,
            "submission": {"submission_status": "PENDING"},
        }
        with patch(
            "src.routers.courses.assignments.retry_assignment_submission",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_retry:
            response = await client.post(
                "/api/v1/assignments/assignment_retry_test/submissions/me/retry"
            )

        assert response.status_code == 200
        assert response.json() == expected
        mock_retry.assert_awaited_once()


class TestSchoolOperationsSummaryRouter:
    async def test_operations_summary_forwards_validated_filters(self, client):
        expected = {
            "scope": {
                "org_id": 1,
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "timezone": "Asia/Macau",
            },
            "metrics": {"record_count": 0},
            "privacy": {"aggregate_only": True},
        }
        with patch(
            "src.routers.courses.assignments.read_school_operations_summary",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_summary:
            response = await client.get(
                "/api/v1/assignments/org/1/operations-summary",
                params={
                    "start_date": "2026-07-13",
                    "end_date": "2026-07-19",
                    "course_id": 4,
                    "usergroup_id": 8,
                    "subject": "數學",
                    "education_stage": "小學",
                    "grade_level": "小四",
                    "school_year": "2026/2027",
                    "term": "第一學期",
                    "include_self_tests": "false",
                },
            )

        assert response.status_code == 200
        assert response.json() == expected
        kwargs = mock_summary.await_args.kwargs
        assert kwargs["start_date"].isoformat() == "2026-07-13"
        assert kwargs["end_date"].isoformat() == "2026-07-19"
        assert kwargs["course_id"] == 4
        assert kwargs["usergroup_id"] == 8
        assert kwargs["subject"] == "數學"
        assert kwargs["education_stage"] == "小學"
        assert kwargs["grade_level"] == "小四"
        assert kwargs["school_year"] == "2026/2027"
        assert kwargs["term"] == "第一學期"
        assert kwargs["include_self_tests"] is False

    async def test_operations_summary_csv_returns_aggregate_attachment(self, client):
        payload = {
            "scope": {
                "org_id": 1,
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
            }
        }
        with patch(
            "src.routers.courses.assignments.read_school_operations_summary",
            new_callable=AsyncMock,
            return_value=payload,
        ), patch(
            "src.routers.courses.assignments.school_operations_summary_to_csv",
            return_value="\ufeff欄位,數值\r\n學習記錄,3\r\n",
        ), patch(
            "src.routers.courses.assignments.school_operations_summary_csv_filename",
            return_value="learnhouse-operations-summary.csv",
        ):
            response = await client.get(
                "/api/v1/assignments/org/1/operations-summary.csv"
            )

        assert response.status_code == 200
        assert response.text.startswith("\ufeff欄位,數值")
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == (
            'attachment; filename="learnhouse-operations-summary.csv"'
        )
