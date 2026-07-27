"""Tests for src/services/courses/activities/assignments.py (CRUD operations).

Covers the newly-async CRUD functions that were migrated from sync SQLModel
Session to AsyncSession in this PR:
  create_assignment, read_assignment, read_assignment_from_activity_uuid,
  update_assignment, delete_assignment, delete_assignment_from_activity_uuid,
  create_assignment_task, read_assignment_tasks, read_assignment_task,
  update_assignment_task, delete_assignment_task,
  handle_assignment_task_submission, read_assignment_submissions,
  read_user_assignment_submissions, read_user_assignment_submissions_me,
  update_assignment_submission, delete_assignment_submission,
  grade_assignment_submission, get_grade_assignment_submission,
  mark_activity_as_done_for_user, get_assignments_from_course,
  _block_api_tokens.
"""

import csv
import io
import json
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.courses.assignments import (
    Assignment,
    AssignmentCreate,
    AssignmentRead,
    AssignmentTask,
    AssignmentTaskCreate,
    AssignmentTaskRead,
    AssignmentTaskSubmission,
    AssignmentTaskSubmissionRead,
    AssignmentTaskSubmissionUpdate,
    AssignmentTaskTypeEnum,
    AssignmentUpdate,
    AssignmentUserSubmission,
    AssignmentUserSubmissionCreate,
    AssignmentUserSubmissionRead,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.certifications import CertificateUser, Certifications
from src.db.courses.courses import Course
from src.db.question_bank import QuestionBankItem, QuestionBankVisibilityEnum
from src.db.trail_runs import TrailRun
from src.db.trail_steps import TrailStep
from src.db.trails import Trail
from src.db.usergroup_user import UserGroupUser
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroups import UserGroup
from src.db.user_organizations import UserOrganization
from src.db.users import APITokenUser, User
from src.db.self_tests import SelfTestAttempt, SelfTestAttemptStatus
from src.services.courses.activities.assignments import (
    _block_api_tokens,
    _apply_grade_and_finalize,
    _build_tasks_breakdown,
    _server_verified_task_feedback,
    _server_verified_task_grade,
    assignment_gradebook_csv_filename,
    assignment_gradebook_to_csv,
    school_operations_summary_csv_filename,
    school_operations_summary_to_csv,
    compute_assignment_grade,
    create_my_assignment_remediation,
    create_assignment,
    create_assignment_submission,
    create_assignment_task,
    delete_assignment,
    delete_assignment_from_activity_uuid,
    delete_assignment_submission,
    delete_assignment_task,
    delete_assignment_task_submission,
    get_assignments_from_course,
    get_grade_assignment_submission,
    grade_assignment_submission,
    handle_assignment_task_submission,
    mark_activity_as_done_for_user,
    put_assignment_task_reference_file,
    put_assignment_task_submission_file,
    read_assignment,
    read_assignment_gradebook,
    read_assignment_gradebook_summary,
    read_school_operations_summary,
    read_assignment_from_activity_uuid,
    read_my_assignment_remediation,
    read_assignment_submissions,
    read_assignment_task,
    read_assignment_task_submissions,
    read_assignment_tasks,
    read_my_assignment_queue,
    read_user_assignment_submissions,
    read_user_assignment_submissions_me,
    read_user_assignment_task_submissions,
    read_user_assignment_task_submissions_me,
    read_user_assignment_task_submissions_me_batch,
    read_teacher_assignment_workbench,
    retry_assignment_submission,
    submit_my_assignment_remediation,
    update_assignment,
    update_assignment_submission,
    update_assignment_task,
    update_assignment_task_submission,
)


def _gradebook_csv_detail_rows(csv_text: str) -> list[dict]:
    detail_text = csv_text.lstrip("\ufeff").split("\r\n\r\n", 1)[-1]
    return list(csv.DictReader(io.StringIO(detail_text)))

# ---------------------------------------------------------------------------
# Module-level patches applied to all tests
# ---------------------------------------------------------------------------

_PATCH_RBAC = "src.services.courses.activities.assignments.check_resource_access"
_PATCH_LIMITS = "src.services.courses.activities.assignments.check_limits_with_usage"
_PATCH_INCREASE = "src.services.courses.activities.assignments.increase_feature_usage"
_PATCH_DECREASE = "src.services.courses.activities.assignments.decrease_feature_usage"
_PATCH_AUTH_ROLES = (
    "src.services.courses.activities.assignments.authorization_verify_based_on_roles"
)
_PATCH_DISPATCH = "src.services.courses.activities.assignments.dispatch_webhooks"
_PATCH_TRACK = "src.services.courses.activities.assignments.track"
_PATCH_CERT = (
    "src.services.courses.activities.assignments."
    "check_course_completion_and_create_certificate"
)
_PATCH_UPLOAD_SUBMISSION_FILE = (
    "src.services.courses.activities.assignments.upload_submission_file"
)


def _ready_self_test_bank_item(org_id: int, title: str) -> QuestionBankItem:
    slug = title.lower().replace(" ", "_")
    return QuestionBankItem(
        item_uuid=f"questionbank_{slug}",
        title=title,
        description="澳門特別行政區位於哪個國家？",
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionText": "澳門特別行政區位於哪個國家？",
                    "questionUUID": f"question_{slug}",
                    "options": [
                        {
                            "optionUUID": f"option_{slug}_china",
                            "text": "中國",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": f"option_{slug}_japan",
                            "text": "日本",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        },
        tags=["self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _incomplete_self_test_bank_item(org_id: int, title: str) -> QuestionBankItem:
    slug = title.lower().replace(" ", "_")
    return QuestionBankItem(
        item_uuid=f"questionbank_bad_{slug}",
        title=title,
        description="Missing option UUIDs should not count as self-test ready.",
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionText": "澳門特別行政區位於哪個國家？",
                    "questionUUID": f"question_bad_{slug}",
                    "options": [
                        {"text": "中國", "assigned_right_answer": True},
                        {"text": "日本", "assigned_right_answer": False},
                    ],
                }
            ]
        },
        tags=["self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


def _fallback_self_test_bank_item(org_id: int, title: str) -> QuestionBankItem:
    slug = title.lower().replace(" ", "_")
    return QuestionBankItem(
        item_uuid=f"questionbank_fallback_{slug}",
        title=title,
        description="AI 備用題，請老師檢查後再發布。",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={
            "prompt": "這份練習主要學習哪個主題？",
            "correct_answers": ["本課主題"],
            "match_mode": "case_insensitive",
        },
        tags=["self-test"],
        difficulty="beginner",
        visibility=QuestionBankVisibilityEnum.ORG,
        org_id=org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def assignment(db, org, course, chapter, activity):
    a = Assignment(
        id=10,
        title="Test Assignment",
        description="An assignment for testing",
        due_date="2030-01-01",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=False,
        anti_copy_paste=False,
        show_correct_answers=False,
        allow_retries=False,
        max_retries=0,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_test",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


@pytest.fixture
async def assignment_task(db, org, course, chapter, activity, assignment):
    t = AssignmentTask(
        id=20,
        title="Test Task",
        description="A task for testing",
        hint="",
        reference_file=None,
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        contents={"prompt": "What is 2+2?", "correct_answers": ["4"], "match_mode": "exact"},
        max_grade_value=100,
        assignment_id=assignment.id,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_task_uuid="assignmenttask_test",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest.fixture
async def user_submission(db, assignment, regular_user):
    sub = AssignmentUserSubmission(
        id=30,
        user_id=regular_user.id,
        assignment_id=assignment.id,
        grade=80,
        submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
        attempt_number=1,
        assignmentusersubmission_uuid="aus_test",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


@pytest.fixture
async def graded_submission(db, assignment, regular_user):
    sub = AssignmentUserSubmission(
        id=31,
        user_id=regular_user.id,
        assignment_id=assignment.id,
        grade=85,
        submission_status=AssignmentUserSubmissionStatus.GRADED,
        overall_feedback="Good work",
        attempt_number=1,
        assignmentusersubmission_uuid="aus_graded_test",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


@pytest.fixture
async def task_submission(db, assignment_task, regular_user):
    ts = AssignmentTaskSubmission(
        id=40,
        assignment_task_submission_uuid="ats_test",
        task_submission={"answer": "4"},
        grade=100,
        task_submission_grade_feedback="Correct",
        assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        user_id=regular_user.id,
        activity_id=assignment_task.activity_id,
        course_id=assignment_task.course_id,
        chapter_id=assignment_task.chapter_id,
        assignment_task_id=assignment_task.id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(ts)
    await db.commit()
    await db.refresh(ts)
    return ts


def _fake_essay_ai_client(score: int = 82):
    class _FakeModels:
        def generate_content(self, **kwargs):
            class _Response:
                text = f"""
                {{
                  "score": {score},
                  "summary": "文章切題，結構基本清晰。",
                  "overall_feedback": "能回應題目，例子可再具體一點。",
                  "criteria": [
                    {{"key": "content", "label": "內容切題", "score": 86, "comment": "內容能扣題。"}},
                    {{"key": "structure", "label": "結構組織", "score": 80, "comment": "段落尚算清楚。"}},
                    {{"key": "language", "label": "語言表達", "score": 78, "comment": "句子可更流暢。"}},
                    {{"key": "mechanics", "label": "錯別字與標點", "score": 84, "comment": "標點大致正確。"}},
                    {{"key": "creativity", "label": "創意與思考", "score": 82, "comment": "有個人想法。"}}
                  ],
                  "strengths": ["能回應題目", "段落清楚"],
                  "improvements": ["例子要更具體", "減少重複用詞"],
                  "next_steps": ["先列提綱再寫", "每段加入一個細節"],
                  "teacher_notes": "建議老師覆核是否切合班級程度。",
                  "confidence": "high"
                }}
                """
            return _Response()

    class _FakeClient:
        models = _FakeModels()

    return _FakeClient()


def _fake_gradebook_summary_ai_client():
    class _FakeModels:
        def generate_content(self, **kwargs):
            class _Response:
                text = """
                {
                  "title": "班級學情摘要",
                  "summary": "本班提交情況穩定，但仍有學生需要跟進低分和補強。",
                  "key_points": ["提交率良好", "低分學生需要補練", "老師可先處理待跟進名單"],
                  "suggested_actions": ["安排 2 題同類補練", "下堂課先回顧常錯點", "複製摘要給主任備查"],
                  "principal_brief": "本班已有穩定學習記錄，下一步會針對低分學生安排補強。"
                }
                """
            return _Response()

    class _FakeClient:
        models = _FakeModels()

    return _FakeClient()


async def _make_task_submission_for_user(db, assignment_task, user_id, uuid="ats_submit_test"):
    ts = AssignmentTaskSubmission(
        assignment_task_submission_uuid=uuid,
        task_submission={"answer": "4"},
        grade=0,
        task_submission_grade_feedback="",
        assignment_type=assignment_task.assignment_type,
        user_id=user_id,
        activity_id=assignment_task.activity_id,
        course_id=assignment_task.course_id,
        chapter_id=assignment_task.chapter_id,
        assignment_task_id=assignment_task.id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(ts)
    await db.commit()
    await db.refresh(ts)
    return ts


async def _target_assignment_to_group(
    db,
    org,
    assignment,
    group_id,
    user=None,
    uuid="usergroup_assignment_read_guard",
):
    group = UserGroup(
        id=group_id,
        org_id=org.id,
        name="小四甲",
        description="",
        usergroup_uuid=uuid,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    assignment.target_usergroup_ids = [group_id]
    db.add(group)
    if user:
        db.add(
            UserGroupUser(
                usergroup_id=group_id,
                user_id=user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return group


async def _make_trail(db, org_id, course_id, activity_id, user_id):
    trail = Trail(
        org_id=org_id,
        user_id=user_id,
        trail_uuid="trail_assign_test",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(trail)
    await db.commit()
    await db.refresh(trail)
    run = TrailRun(
        trail_id=trail.id,
        course_id=course_id,
        org_id=org_id,
        user_id=user_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    step = TrailStep(
        complete=False,
        teacher_verified=False,
        grade="",
        trailrun_id=run.id,
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
    return trail, run, step


def _remediation_answer_for_question(question: dict, answer: str = "4") -> dict:
    assignment_type = question["assignment_type"]
    contents = question.get("contents") or {}
    if assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER.value:
        return {"question_uuid": question["question_uuid"], "answer": {"answer": answer}}

    first_question = (contents.get("questions") or [{}])[0]
    if assignment_type == AssignmentTaskTypeEnum.FORM.value:
        first_blank = (first_question.get("blanks") or [{}])[0]
        return {
            "question_uuid": question["question_uuid"],
            "answer": {
                "submissions": [
                    {
                        "questionUUID": first_question.get("questionUUID"),
                        "blankUUID": first_blank.get("blankUUID"),
                        "answer": answer,
                    }
                ]
            },
        }

    if assignment_type == AssignmentTaskTypeEnum.QUIZ.value:
        options = first_question.get("options") or []
        correct_option = next(
            (option for option in options if option.get("assigned_right_answer")),
            options[0] if options else {},
        )
        return {
            "question_uuid": question["question_uuid"],
            "answer": {
                "submissions": [
                    {
                        "questionUUID": first_question.get("questionUUID"),
                        "optionUUID": option.get("optionUUID"),
                        "answer": option.get("optionUUID") == correct_option.get("optionUUID"),
                    }
                    for option in options
                ]
            },
        }

    raise AssertionError(f"unsupported remediation question type: {assignment_type}")


class TestSimpleServerVerifiedAutoGrading:
    def test_grade_display_uses_traditional_chinese_for_school_ui(self):
        passed = compute_assignment_grade(80, 100, GradingTypeEnum.PASS_FAIL)
        failed = compute_assignment_grade(30, 100, GradingTypeEnum.PASS_FAIL)

        assert passed["display_grade"] == "通過"
        assert failed["display_grade"] == "未通過"
        assert passed["points_summary"] == "80/100 分"

    async def test_server_verified_essay_grading_stores_rich_report(
        self, monkeypatch, assignment_task, task_submission
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.ESSAY
        assignment_task.contents = {"prompt": "以「我的校園」為題寫一篇短文。"}
        task_submission.assignment_type = AssignmentTaskTypeEnum.ESSAY
        task_submission.task_submission = {"essay": "我的校園很美麗。每天早上，我都會和同學一起學習。"}

        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_configuration_error",
            lambda: None,
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.get_gemini_client",
            lambda: _fake_essay_ai_client(score=82),
        )

        grade = await _server_verified_task_grade(assignment_task, task_submission)

        assert grade == 82
        report = json.loads(task_submission.task_submission_grade_feedback)
        assert report["type"] == "ai_essay_grading"
        assert report["score"] == 82
        assert report["needs_teacher_review"] is True
        assert [criterion["key"] for criterion in report["criteria"]] == [
            "content",
            "structure",
            "language",
            "mechanics",
            "creativity",
        ]

    async def test_auto_graded_essay_submission_stays_pending_teacher_review(
        self, monkeypatch, db, assignment, course, assignment_task, task_submission, user_submission, regular_user
    ):
        assignment.auto_grading = True
        assignment.teacher_review_required = True
        assignment_task.assignment_type = AssignmentTaskTypeEnum.ESSAY
        assignment_task.contents = {"prompt": "以「一次難忘的活動」為題寫一篇短文。"}
        task_submission.assignment_type = AssignmentTaskTypeEnum.ESSAY
        task_submission.task_submission = {"essay": "今天學校舉行活動，我和同學合作完成任務，覺得很難忘。"}
        task_submission.grade = 0
        user_submission.submission_status = AssignmentUserSubmissionStatus.SUBMITTED
        user_submission.grade = 0
        db.add(assignment)
        db.add(assignment_task)
        db.add(task_submission)
        db.add(user_submission)
        await db.commit()

        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_configuration_error",
            lambda: None,
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.get_gemini_client",
            lambda: _fake_essay_ai_client(score=88),
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.enforce_ai_rate_limit",
            lambda user_id, org_id: None,
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.reserve_ai_credit",
            AsyncMock(),
        )

        with patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await _apply_grade_and_finalize(
                assignment=assignment,
                course=course,
                user_id=regular_user.id,
                assignment_user_submission=user_submission,
                db_session=db,
                auto_graded=True,
            )

        assert result["display_grade"] == "88/100"
        assert result["teacher_review_status"] == "pending"
        assert user_submission.submission_status == AssignmentUserSubmissionStatus.GRADED
        assert user_submission.teacher_review_status == "pending"
        assert json.loads(task_submission.task_submission_grade_feedback)["score"] == 88

    def test_task_breakdown_uses_traditional_chinese_points_summary(self):
        task = AssignmentTask(
            id=20,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            assignment_task_uuid="assignmenttask_points_summary",
            description="請寫出答案",
            max_grade_value=100,
        )
        task_submission = AssignmentTaskSubmission(
            assignment_task_id=20,
            task_submission={"answer": "中文"},
            grade=80,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
        )

        rows = _build_tasks_breakdown(
            [task],
            {20: task_submission},
            passing_threshold=50,
        )

        assert rows[0]["points_summary"] == "80/100 分"

    async def test_simple_ai_task_types_are_server_verified(self):
        quiz_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "澳門特別行政區位於哪個國家？",
                        "options": [
                            {"optionUUID": "o1", "text": "中國", "assigned_right_answer": True},
                            {"optionUUID": "o2", "text": "日本", "assigned_right_answer": False},
                            {"optionUUID": "o3", "text": "法國", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        )
        quiz_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "optionUUID": "o1", "answer": True},
                    {"questionUUID": "q1", "optionUUID": "o2", "answer": False},
                    {"questionUUID": "q1", "optionUUID": "o3", "answer": False},
                ]
            },
        )

        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "澳門的官方語言之一是____。",
                        "blanks": [
                            {"blankUUID": "b1", "placeholder": "填寫答案", "correctAnswer": "中文"},
                        ],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": " 中文 "},
                ]
            },
        )

        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "請寫出澳門的一種官方語言。",
                "correct_answers": ["中文", "葡文"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "中文"},
        )

        assert await _server_verified_task_grade(quiz_task, quiz_submission) == 100
        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_server_verified_grading_accepts_legacy_simple_answer_key_aliases(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "澳門的官方語言之一是____。",
                        "blanks": [
                            {"blankUUID": "b1", "placeholder": "填寫答案", "correct_answer": "中文"},
                        ],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": "中文"},
                ]
            },
        )

        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "請寫出澳門所屬的國家。",
                "accepted_answers": ["中國"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "中國"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_single_answer_quiz_wrong_choice_gets_zero_for_simple_workflow(self):
        quiz_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "澳門特別行政區位於哪個國家？",
                        "options": [
                            {"optionUUID": "o1", "text": "中國", "assigned_right_answer": True},
                            {"optionUUID": "o2", "text": "日本", "assigned_right_answer": False},
                            {"optionUUID": "o3", "text": "法國", "assigned_right_answer": False},
                            {"optionUUID": "o4", "text": "加拿大", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        )
        quiz_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "optionUUID": "o1", "answer": False},
                    {"questionUUID": "q1", "optionUUID": "o2", "answer": True},
                    {"questionUUID": "q1", "optionUUID": "o3", "answer": False},
                    {"questionUUID": "q1", "optionUUID": "o4", "answer": False},
                ]
            },
        )

        assert await _server_verified_task_grade(quiz_task, quiz_submission) == 0

    async def test_quiz_auto_grading_treats_string_false_as_not_selected(self):
        quiz_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "澳門特別行政區位於哪個國家？",
                        "options": [
                            {"optionUUID": "o1", "text": "中國", "assigned_right_answer": "true"},
                            {"optionUUID": "o2", "text": "日本", "assigned_right_answer": "false"},
                        ],
                    }
                ]
            },
        )
        quiz_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "optionUUID": "o1", "answer": "true"},
                    {"questionUUID": "q1", "optionUUID": "o2", "answer": "false"},
                ]
            },
        )

        assert await _server_verified_task_grade(quiz_task, quiz_submission) == 100

    async def test_simple_ai_text_grading_tolerates_common_student_formatting(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "CSS 中用來設定顏色的屬性是____。",
                        "blanks": [
                            {"blankUUID": "b1", "placeholder": "填寫答案", "correctAnswer": "color"},
                        ],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": " ＣＯＬＯＲ。 "},
                ]
            },
        )

        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "請寫出澳門所屬的國家。",
                "correct_answers": ["中國"],
                "match_mode": "contains",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "答案是：中國。"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_simple_ai_text_grading_tolerates_fullwidth_numbers_and_symbols(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "本學年是____年。",
                        "blanks": [
                            {"blankUUID": "b1", "placeholder": "填寫年份", "correctAnswer": "2026"},
                        ],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": "２０２６。"},
                ]
            },
        )

        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "HTML 標題標籤其中一個是甚麼？",
                "correct_answers": ["<h1>"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "＜Ｈ１＞"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_simple_ai_text_grading_tolerates_common_simplified_chinese_input(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "questionText": "本課介紹的城市是____。",
                        "blanks": [
                            {"blankUUID": "b1", "placeholder": "填寫答案", "correctAnswer": "澳門"},
                        ],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": " 澳门 "},
                ]
            },
        )

        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "請寫出澳門所屬的國家。",
                "correct_answers": ["中國"],
                "match_mode": "contains",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "答案是：中国。"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_simple_ai_text_grading_tolerates_common_china_name_simplified_input(self):
        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "中國的全稱是甚麼？",
                "correct_answers": ["中華人民共和國"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "中华人民共和国"},
        )

        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_simple_text_grading_accepts_common_china_name_equivalent_answers(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "blanks": [{"blankUUID": "b1", "correctAnswer": "中國"}],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": "中華人民共和國"},
                ]
            },
        )
        short_answer_short_key_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "澳門屬於哪個國家？",
                "correct_answers": ["中國"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_short_key_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "中華人民共和國"},
        )
        short_answer_full_key_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "澳門屬於哪個國家？",
                "correct_answers": ["中華人民共和國"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_full_key_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "中國"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(
            short_answer_short_key_task,
            short_answer_short_key_submission,
        ) == 100
        assert await _server_verified_task_grade(
            short_answer_full_key_task,
            short_answer_full_key_submission,
        ) == 100

    async def test_simple_text_grading_accepts_common_language_name_equivalent_answers(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "blanks": [{"blankUUID": "b1", "correctAnswer": "葡文"}],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": "葡萄牙語"},
                ]
            },
        )
        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "English 可以稱為甚麼？",
                "correct_answers": ["英文"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "英語"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_simple_text_answer_grading_ignores_spaces_between_chinese_characters(self):
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "blanks": [{"blankUUID": "b1", "correctAnswer": "澳門"}],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": "澳 門"},
                ]
            },
        )
        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "澳門在哪個國家？",
                "correct_answers": ["中國"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "中 國"},
        )

        assert await _server_verified_task_grade(form_task, form_submission) == 100
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 100

    async def test_simple_ai_task_types_give_zero_for_wrong_answers(self):
        quiz_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "options": [
                            {"optionUUID": "o1", "text": "正確答案", "assigned_right_answer": True},
                            {"optionUUID": "o2", "text": "錯誤答案", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        )
        quiz_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "optionUUID": "o1", "answer": False},
                    {"questionUUID": "q1", "optionUUID": "o2", "answer": True},
                ]
            },
        )
        form_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            max_grade_value=100,
            contents={
                "questions": [
                    {
                        "questionUUID": "q1",
                        "blanks": [{"blankUUID": "b1", "correctAnswer": "中文"}],
                    }
                ]
            },
        )
        form_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.FORM,
            task_submission={
                "submissions": [
                    {"questionUUID": "q1", "blankUUID": "b1", "answer": "英文"},
                ]
            },
        )
        short_answer_task = AssignmentTask(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            max_grade_value=100,
            contents={
                "prompt": "請寫出澳門的一種官方語言。",
                "correct_answers": ["中文", "葡文"],
                "match_mode": "case_insensitive",
            },
        )
        short_answer_submission = AssignmentTaskSubmission(
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            task_submission={"answer": "英文"},
        )

        assert await _server_verified_task_grade(quiz_task, quiz_submission) == 0
        assert await _server_verified_task_grade(form_task, form_submission) == 0
        assert await _server_verified_task_grade(short_answer_task, short_answer_submission) == 0

    async def test_auto_grading_writes_traditional_chinese_task_feedback(
        self,
        db,
        assignment,
        course,
        assignment_task,
        task_submission,
        user_submission,
        regular_user,
    ):
        assignment.auto_grading = True
        assignment.score_policy = "latest"
        task_submission.task_submission = {"answer": "wrong"}
        task_submission.grade = 0
        task_submission.task_submission_grade_feedback = ""
        user_submission.grade = 0
        db.add(assignment)
        db.add(task_submission)
        db.add(user_submission)
        await db.commit()

        with patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await _apply_grade_and_finalize(
                assignment=assignment,
                course=course,
                user_id=regular_user.id,
                assignment_user_submission=user_submission,
                db_session=db,
                auto_graded=True,
            )

        await db.refresh(task_submission)

        assert result["grade"] == 0
        assert task_submission.task_submission_grade_feedback == "答案未符合參考答案，請查看提示或參考答案後再試。"
        assert result["tasks"][0]["feedback"] == "答案未符合參考答案，請查看提示或參考答案後再試。"

    def test_server_verified_task_feedback_distinguishes_partial_credit(self):
        assert _server_verified_task_feedback(100, 100) == "答案正確。"
        assert _server_verified_task_feedback(50, 100) == "部分答案正確，仍有內容未符合參考答案。"
        assert _server_verified_task_feedback(0, 100) == "答案未符合參考答案，請查看提示或參考答案後再試。"

    async def test_auto_grading_feedback_mentions_retry_when_learning_retry_is_available(
        self,
        db,
        assignment,
        course,
        assignment_task,
        task_submission,
        user_submission,
        regular_user,
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.max_retries = 0
        assignment.score_policy = "highest"
        task_submission.task_submission = {"answer": "wrong"}
        task_submission.grade = 0
        task_submission.task_submission_grade_feedback = ""
        user_submission.grade = 0
        user_submission.attempt_number = 1
        db.add(assignment)
        db.add(task_submission)
        db.add(user_submission)
        await db.commit()

        with patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await _apply_grade_and_finalize(
                assignment=assignment,
                course=course,
                user_id=regular_user.id,
                assignment_user_submission=user_submission,
                db_session=db,
                auto_graded=True,
            )

        await db.refresh(task_submission)

        assert result["grade"] == 0
        assert "可以按「重做」改完再提交" in task_submission.task_submission_grade_feedback
        assert "系統會保留最高分" in task_submission.task_submission_grade_feedback
        assert "可以按「重做」改完再提交" in result["tasks"][0]["feedback"]


class TestAssignmentRemediationPractice:
    async def _prepare_wrong_graded_short_answer(
        self,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        task_submission,
        regular_user,
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            group_id=9871,
            user=regular_user,
            uuid="usergroup_remediation_practice",
        )
        assignment.auto_grading = True
        assignment.show_correct_answers = True
        assignment.allow_retries = True
        assignment.score_policy = "highest"
        assignment_task.contents = {
            "prompt": "What is 2+2?",
            "correct_answers": ["4"],
            "match_mode": "case_insensitive",
        }
        task_submission.task_submission = {"answer": "3"}
        task_submission.grade = 0
        task_submission.task_submission_grade_feedback = "答案未符合參考答案，請查看提示或參考答案後再試。"
        graded_submission.grade = 0
        graded_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
        db.add(assignment)
        db.add(assignment_task)
        db.add(task_submission)
        db.add(graded_submission)
        await db.commit()
        await db.refresh(assignment)
        await db.refresh(graded_submission)
        return assignment, graded_submission

    async def test_student_sees_available_remediation_after_wrong_graded_answer(
        self,
        mock_request,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        task_submission,
        regular_user,
    ):
        await self._prepare_wrong_graded_short_answer(
            db,
            org,
            assignment,
            assignment_task,
            graded_submission,
            task_submission,
            regular_user,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            payload = await read_my_assignment_remediation(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )

        assert payload["status"] == "available"
        assert payload["eligible"] is True
        assert payload["weak_point_count"] == 1
        assert payload["practice"] is None

    async def test_create_remediation_uses_safe_fallback_when_ai_unavailable(
        self,
        monkeypatch,
        mock_request,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        task_submission,
        regular_user,
    ):
        await self._prepare_wrong_graded_short_answer(
            db,
            org,
            assignment,
            assignment_task,
            graded_submission,
            task_submission,
            regular_user,
        )
        monkeypatch.setattr(
            "src.services.ai.assignments._assignment_ai_configuration_error",
            lambda: "AI 未配置",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            payload = await create_my_assignment_remediation(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )

        practice = payload["practice"]
        assert payload["status"] == "generated"
        assert payload["eligible"] is True
        assert len(practice["questions"]) >= 2
        assert {question["assignment_type"] for question in practice["questions"]}.issubset(
            {
                AssignmentTaskTypeEnum.FORM.value,
                AssignmentTaskTypeEnum.SHORT_ANSWER.value,
            }
        )
        assert practice["feedback"]["ai_used"] is False
        assert "系統補練題" in practice["feedback"]["message"]
        assert "correct_answers" not in json.dumps(practice["questions"], ensure_ascii=False)

    async def test_submit_remediation_auto_grades_and_preserves_original_assignment_score(
        self,
        monkeypatch,
        mock_request,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        task_submission,
        regular_user,
    ):
        await self._prepare_wrong_graded_short_answer(
            db,
            org,
            assignment,
            assignment_task,
            graded_submission,
            task_submission,
            regular_user,
        )
        monkeypatch.setattr(
            "src.services.ai.assignments._assignment_ai_configuration_error",
            lambda: "AI 未配置",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            created = await create_my_assignment_remediation(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )
            answers = [
                _remediation_answer_for_question(question, "4")
                for question in created["practice"]["questions"]
            ]
            submitted = await submit_my_assignment_remediation(
                mock_request,
                assignment.assignment_uuid,
                answers,
                regular_user,
                db,
            )

        await db.refresh(graded_submission)
        assert submitted["status"] == "completed"
        assert submitted["practice"]["score"] == submitted["practice"]["max_score"]
        assert submitted["practice"]["feedback"]["does_not_change_assignment_grade"] is True
        assert graded_submission.grade == 0
        assert graded_submission.submission_status == AssignmentUserSubmissionStatus.GRADED

    async def test_gradebook_and_submission_rows_show_remediation_status(
        self,
        monkeypatch,
        mock_request,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        task_submission,
        regular_user,
        admin_user,
    ):
        await self._prepare_wrong_graded_short_answer(
            db,
            org,
            assignment,
            assignment_task,
            graded_submission,
            task_submission,
            regular_user,
        )
        monkeypatch.setattr(
            "src.services.ai.assignments._assignment_ai_configuration_error",
            lambda: "AI 未配置",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            created = await create_my_assignment_remediation(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )
            answers = [
                _remediation_answer_for_question(question, "4")
                for question in created["practice"]["questions"]
            ]
            await submit_my_assignment_remediation(
                mock_request,
                assignment.assignment_uuid,
                answers,
                regular_user,
                db,
            )
            gradebook = await read_assignment_gradebook(org.id, admin_user, db)
            submissions = await read_assignment_submissions(
                mock_request,
                assignment.assignment_uuid,
                admin_user,
                db,
            )

        gradebook_row = next(
            row
            for row in gradebook["rows"]
            if row["source_type"] == "assignment"
            and row["assignment_uuid"] == assignment.assignment_uuid
            and row["student_id"] == regular_user.id
        )
        submission_row = next(row for row in submissions if row["user_id"] == regular_user.id)

        assert gradebook_row["remediation"]["status"] == "completed"
        assert gradebook_row["remediation"]["label"] == "已完成"
        assert gradebook_row["remediation"]["score"] == gradebook_row["remediation"]["max_score"]
        assert submission_row["remediation"]["status"] == "completed"


# ---------------------------------------------------------------------------
# _block_api_tokens
# ---------------------------------------------------------------------------


class TestBlockApiTokens:
    def test_raises_403_for_api_token_user(self):
        token_user = APITokenUser(id=1, org_id=1)
        with pytest.raises(HTTPException) as exc:
            _block_api_tokens(token_user)
        assert exc.value.status_code == 403

    def test_passes_for_public_user(self, regular_user):
        _block_api_tokens(regular_user)  # should not raise


# ---------------------------------------------------------------------------
# create_assignment
# ---------------------------------------------------------------------------


class TestCreateAssignment:
    async def test_raises_404_when_course_not_found(
        self, mock_request, db, org, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="T",
            description="D",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=9999,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment(mock_request, obj, admin_user, db)
        assert exc.value.status_code == 404

    async def test_creates_assignment_successfully(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="New Assignment",
            description="Desc",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            result = await create_assignment(mock_request, obj, admin_user, db)
        assert isinstance(result, AssignmentRead)
        assert result.title == "New Assignment"
        assert result.auto_grading is True
        assert result.show_correct_answers is True
        assert result.allow_retries is True
        assert result.score_policy == "highest"

    async def test_create_assignment_trims_title(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="  今日課堂   3 題練習  ",
            description="Desc",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            result = await create_assignment(mock_request, obj, admin_user, db)

        assert result.title == "今日課堂 3 題練習"

    async def test_rejects_creating_assignment_with_blank_title(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="   ",
            description="Desc",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment(mock_request, obj, admin_user, db)

        assert exc.value.status_code == 400
        assert "請輸入作業名稱" in exc.value.detail

    async def test_create_assignment_normalizes_null_learning_platform_defaults(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="Null Defaults Assignment",
            description="Desc",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=None,
            anti_copy_paste=None,
            show_correct_answers=None,
            allow_retries=None,
            max_retries=-5,
            teacher_review_required=None,
            teacher_review_status="unknown",
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            result = await create_assignment(mock_request, obj, admin_user, db)

        assert result.auto_grading is True
        assert result.anti_copy_paste is False
        assert result.show_correct_answers is True
        assert result.allow_retries is True
        assert result.max_retries == 0
        assert result.teacher_review_required is False
        assert result.teacher_review_status == "not_required"

    async def test_rejects_creating_assignment_with_past_due_date(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="Past Due Assignment",
            description="Desc",
            due_date="2000-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment(mock_request, obj, admin_user, db)

        assert exc.value.status_code == 400
        assert "截止日期設定為今天或之後" in exc.value.detail

    async def test_rejects_creating_assignment_with_invalid_due_date(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="Invalid Due Date Assignment",
            description="Desc",
            due_date="不是日期",
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment(mock_request, obj, admin_user, db)

        assert exc.value.status_code == 400
        assert "有效的截止日期" in exc.value.detail

    async def test_rejects_creating_published_assignment_without_tasks(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="Published too early",
            description="Desc",
            due_date="2030-01-01",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment(mock_request, obj, admin_user, db)

        assert exc.value.status_code == 400
        assert "建立作業時請先保存為草稿" in exc.value.detail

    async def test_invalid_score_policy_defaults_to_highest_for_learning_platform(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        obj = AssignmentCreate(
            title="Retry-friendly Assignment",
            description="Desc",
            due_date="2030-01-01",
            grading_type=GradingTypeEnum.NUMERIC,
            score_policy="invalid",
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_LIMITS, new_callable=AsyncMock), \
             patch(_PATCH_INCREASE, new_callable=AsyncMock):
            result = await create_assignment(mock_request, obj, admin_user, db)

        assert result.score_policy == "highest"


# ---------------------------------------------------------------------------
# read_assignment
# ---------------------------------------------------------------------------


class TestReadAssignment:
    async def test_raises_404_when_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_returns_assignment_read(
        self, mock_request, db, assignment, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment(
                mock_request, assignment.assignment_uuid, admin_user, db
            )
        assert isinstance(result, AssignmentRead)
        assert result.assignment_uuid == assignment.assignment_uuid

    async def test_legacy_null_list_metadata_is_serialized_without_mutating_orm_row(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.learning_objectives = None
        assignment.target_usergroup_ids = None
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        assert result.learning_objectives == []
        assert result.target_usergroup_ids == []
        assert assignment.learning_objectives is None
        assert assignment.target_usergroup_ids is None

    async def test_rejects_student_outside_target_usergroup(
        self, mock_request, db, org, assignment, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4043,
            uuid="usergroup_read_assignment_guard",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "不在發布名單內" in exc.value.detail

    async def test_rejects_student_when_assignment_has_no_target_usergroup(
        self, mock_request, db, assignment, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "尚未設定發布班級/群組" in exc.value.detail

    async def test_rejects_student_when_assignment_course_is_unpublished(
        self, mock_request, db, org, assignment, course, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4046,
            user=regular_user,
            uuid="usergroup_read_assignment_hidden_course",
        )
        course.published = False
        db.add(course)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "課程或活動尚未發布" in exc.value.detail

    async def test_allows_student_inside_target_usergroup(
        self, mock_request, db, org, assignment, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4044,
            user=regular_user,
            uuid="usergroup_read_assignment_allowed",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        assert result.assignment_uuid == assignment.assignment_uuid

    async def test_rejects_student_reading_unpublished_assignment(
        self, mock_request, db, assignment, regular_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "尚未發布" in exc.value.detail


# ---------------------------------------------------------------------------
# read_assignment_from_activity_uuid
# ---------------------------------------------------------------------------


class TestReadAssignmentFromActivityUuid:
    async def test_raises_404_when_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_from_activity_uuid(
                    mock_request, "bad-uuid", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_returns_assignment_for_activity(
        self, mock_request, db, assignment, activity, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment_from_activity_uuid(
                mock_request, activity.activity_uuid, admin_user, db
            )
        assert isinstance(result, AssignmentRead)
        assert result.activity_uuid == activity.activity_uuid

    async def test_activity_read_serializes_legacy_json_null_metadata(
        self, mock_request, db, assignment, activity, admin_user
    ):
        # JSON literal null deserializes as Python None, the same runtime shape
        # as SQL NULL before the h10 repair migration is applied.
        assignment.learning_objectives = None
        assignment.target_usergroup_ids = None
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment_from_activity_uuid(
                mock_request, activity.activity_uuid, admin_user, db
            )

        assert result.learning_objectives == []
        assert result.target_usergroup_ids == []

    async def test_rejects_student_outside_target_usergroup(
        self, mock_request, db, org, assignment, activity, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4045,
            uuid="usergroup_read_activity_assignment_guard",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_from_activity_uuid(
                    mock_request, activity.activity_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "不在發布名單內" in exc.value.detail

    async def test_rejects_student_when_activity_assignment_has_no_target_usergroup(
        self, mock_request, db, assignment, activity, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_from_activity_uuid(
                    mock_request, activity.activity_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "尚未設定發布班級/群組" in exc.value.detail

    async def test_rejects_student_when_activity_is_unpublished(
        self, mock_request, db, org, assignment, activity, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4047,
            user=regular_user,
            uuid="usergroup_read_assignment_hidden_activity",
        )
        activity.published = False
        db.add(activity)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_from_activity_uuid(
                    mock_request, activity.activity_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "課程或活動尚未發布" in exc.value.detail


# ---------------------------------------------------------------------------
# update_assignment
# ---------------------------------------------------------------------------


class TestUpdateAssignment:
    async def test_raises_404_when_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request, "nonexistent", AssignmentUpdate(title="X"), admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_updates_assignment_title(
        self, mock_request, db, assignment, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(title="  Updated   Title  "),
                admin_user,
                db,
            )
        assert result.title == "Updated Title"

    async def test_rejects_updating_assignment_to_blank_title(
        self, mock_request, db, assignment, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(title="   "),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "請輸入作業名稱" in exc.value.detail

    async def test_rejects_unpublishing_assignment_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        assignment.published = True
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=False),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "不能取消發布" in exc.value.detail
        assert "成績表" in exc.value.detail

    async def test_rejects_grading_policy_update_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(grading_type=GradingTypeEnum.PERCENTAGE),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "不能修改評分制或計分策略" in exc.value.detail

    async def test_allows_update_with_unchanged_grading_policy_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(
                    title="Updated Title",
                    grading_type=assignment.grading_type,
                    score_policy=assignment.score_policy,
                ),
                admin_user,
                db,
            )

        assert result.title == "Updated Title"
        assert result.grading_type == assignment.grading_type
        assert result.score_policy == assignment.score_policy

    async def test_rejects_due_date_update_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        assignment.due_date = "2030-01-01"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(due_date="2030-01-02"),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "不能修改截止日期" in exc.value.detail
        assert "成績表失真" in exc.value.detail

    async def test_allows_unchanged_due_date_update_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        assignment.due_date = "2030-01-01"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(title="保留截止日期", due_date="2030-01-01"),
                admin_user,
                db,
            )

        assert result.title == "保留截止日期"
        assert result.due_date == "2030-01-01"

    async def test_rejects_target_usergroup_update_after_student_submission(
        self, mock_request, db, org, assignment, user_submission, admin_user, regular_user
    ):
        group = UserGroup(
            id=4040,
            org_id=org.id,
            name="小六甲",
            description="",
            usergroup_uuid="usergroup_locked_target_before",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        new_group = UserGroup(
            id=4041,
            org_id=org.id,
            name="小六乙",
            description="",
            usergroup_uuid="usergroup_locked_target_after",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=4040,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [4040]
        db.add(group)
        db.add(new_group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(target_usergroup_ids=[4041]),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "不能修改發布班級/群組" in exc.value.detail
        assert "成績表和未提交名單失真" in exc.value.detail

    async def test_allows_unchanged_target_usergroup_update_after_student_submission(
        self, mock_request, db, org, assignment, user_submission, admin_user, regular_user
    ):
        group = UserGroup(
            id=4042,
            org_id=org.id,
            name="小六丙",
            description="",
            usergroup_uuid="usergroup_locked_target_unchanged",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=4042,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [4042]
        db.add(group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(title="Same Target", target_usergroup_ids=[4042, 4042]),
                admin_user,
                db,
            )

        assert result.title == "Same Target"
        assert result.target_usergroup_ids == [4042]

    async def test_rejects_publishing_assignment_without_tasks(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先新增至少一題" in exc.value.detail

    async def test_allows_publishing_assignment_with_tasks(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.auto_grading = False
        assignment.allow_retries = False
        assignment.show_correct_answers = False
        assignment.score_policy = "latest"
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(
                    published=True,
                    auto_grading=False,
                    allow_retries=False,
                    show_correct_answers=False,
                    score_policy="latest",
                    teacher_review_required=True,
                    teacher_review_status="pending",
                ),
                admin_user,
                db,
            )

        assert result.published is True
        assert result.auto_grading is True
        assert result.allow_retries is True
        assert result.show_correct_answers is True
        assert result.score_policy == "highest"
        assert result.teacher_review_required is False
        assert result.teacher_review_status == "not_required"

    async def test_publishing_simple_assignment_forces_no_teacher_review(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.teacher_review_required = False
        assignment.teacher_review_status = "not_required"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True, teacher_review_required=True),
                admin_user,
                db,
            )

        assert result.published is True
        assert result.auto_grading is True
        assert result.teacher_review_required is False
        assert result.teacher_review_status == "not_required"

    async def test_rejects_publishing_ai_fallback_starter_task(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.description = "AI 備用題，請老師檢查後再發布：請確認本題的學習主題。"
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "AI 備用題" in exc.value.detail
        assert "正式題目" in exc.value.detail

    async def test_rejects_publishing_ai_fallback_starter_contents_even_without_label(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.title = "澳門地理選擇題"
        assignment_task.description = "請選出本課重點。"
        assignment_task.hint = ""
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionText": "這份練習主要圍繞哪個主題？",
                    "questionUUID": "question_ai_fallback_publish_guard",
                    "options": [
                        {
                            "optionUUID": "option_ai_fallback_topic",
                            "text": "澳門地理",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_ai_fallback_activity",
                            "text": "課外活動",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "AI 備用題" in exc.value.detail
        assert "正式題目" in exc.value.detail

    async def test_rejects_publishing_assignment_with_past_due_date(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment.due_date = "2000-01-01"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "截止日期設定為今天或之後" in exc.value.detail
        assert "一收到作業就逾期" in exc.value.detail

    async def test_rejects_publishing_assignment_without_valid_due_date(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment.due_date = "不是日期"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "設定有效的截止日期" in exc.value.detail
        assert "未交、逾期和快截止" in exc.value.detail

    async def test_rejects_moving_published_assignment_due_date_to_past(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = True
        assignment.due_date = "2030-01-01"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(due_date="2000-01-01"),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "更新已發布作業" in exc.value.detail
        assert "截止日期設定為今天或之後" in exc.value.detail

    async def test_rejects_moving_published_assignment_due_date_to_invalid_value(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = True
        assignment.due_date = "2030-01-01"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(due_date="不是日期"),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "設定有效的截止日期" in exc.value.detail

    async def test_allows_non_due_date_update_on_published_past_due_assignment(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = True
        assignment.due_date = "2000-01-01"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(title="保留歷史作業"),
                admin_user,
                db,
            )

        assert result.title == "保留歷史作業"
        assert result.due_date == "2000-01-01"

    async def test_rejects_publishing_assignment_without_target_when_org_has_usergroups(
        self, mock_request, db, org, assignment, assignment_task, admin_user
    ):
        group = UserGroup(
            id=4010,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_publish_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.published = False
        assignment.target_usergroup_ids = []
        db.add(group)
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "請先指定班級/群組" in exc.value.detail
        assert "避免誤發給全部學生" in exc.value.detail

    async def test_rejects_publishing_assignment_without_any_students(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "請先新增學生帳號" in exc.value.detail
        assert "沒有學生會收到這份作業" in exc.value.detail

    async def test_rejects_publishing_assignment_with_unknown_target_usergroup(
        self, mock_request, db, org, assignment, assignment_task, admin_user
    ):
        group = UserGroup(
            id=4011,
            org_id=org.id,
            name="小四乙",
            description="",
            usergroup_uuid="usergroup_publish_known",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.published = False
        assignment.target_usergroup_ids = [999999]
        db.add(group)
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "班級不存在或不屬於本校" in exc.value.detail
        assert "999999" in exc.value.detail

    async def test_rejects_publishing_assignment_to_empty_target_usergroup(
        self, mock_request, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        group = UserGroup(
            id=4013,
            org_id=org.id,
            name="空班級",
            description="",
            usergroup_uuid="usergroup_empty_publish_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.published = False
        assignment.target_usergroup_ids = [4013]
        db.add(group)
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "請先把學生加入所選班級/群組" in exc.value.detail
        assert "沒有學生會收到這份作業" in exc.value.detail

    async def test_rejects_clearing_targets_on_published_assignment_when_org_has_usergroups(
        self, mock_request, db, org, assignment, assignment_task, admin_user
    ):
        group = UserGroup(
            id=4012,
            org_id=org.id,
            name="小五甲",
            description="",
            usergroup_uuid="usergroup_clear_target_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.published = True
        assignment.target_usergroup_ids = [4012]
        db.add(group)
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(target_usergroup_ids=[]),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "請先指定班級/群組" in exc.value.detail

    async def test_rejects_publishing_assignment_with_more_than_three_tasks(
        self, mock_request, db, org, course, chapter, activity, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        for index in range(3):
            db.add(
                AssignmentTask(
                    title=f"Extra Task {index + 1}",
                    description="Desc",
                    hint="",
                    reference_file=None,
                    assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
                    contents={
                        "prompt": f"What is item {index + 1}?",
                        "correct_answers": [str(index + 1)],
                        "match_mode": "exact",
                    },
                    max_grade_value=100,
                    assignment_id=assignment.id,
                    org_id=org.id,
                    course_id=course.id,
                    chapter_id=chapter.id,
                    activity_id=activity.id,
                    assignment_task_uuid=f"assignmenttask_extra_publish_{index + 1}",
                    creation_date=str(datetime.now()),
                    update_date=str(datetime.now()),
                )
            )
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "最多 3 題" in exc.value.detail
        assert "拆成多份簡單作業" in exc.value.detail

    async def test_rejects_publishing_assignment_with_empty_simple_task(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.contents = {}
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "短問答題目" in exc.value.detail

    async def test_rejects_publishing_assignment_with_incomplete_quiz_options(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_incomplete_quiz",
                    "questionText": "澳門位於哪個國家？",
                    "options": [
                        {
                            "optionUUID": "o_correct",
                            "text": "中國",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "o_blank",
                            "text": "",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "每個選項都需要文字" in exc.value.detail

    async def test_rejects_publishing_form_with_long_blank_answer(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.FORM
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_long_blank_answer",
                    "questionText": "請填寫水循環中的一個階段。",
                    "blanks": [
                        {
                            "blankUUID": "blank_long_answer",
                            "placeholder": "填寫答案",
                            "correctAnswer": "水蒸發後遇冷凝結成雲，最後再以下雨方式回到地面。",
                        }
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "填空答案太長" in exc.value.detail

    async def test_rejects_publishing_quiz_without_correct_answer(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_missing_correct_quiz",
                    "questionText": "澳門位於哪個國家？",
                    "options": [
                        {
                            "optionUUID": "o_china",
                            "text": "中國",
                            "assigned_right_answer": False,
                        },
                        {
                            "optionUUID": "o_japan",
                            "text": "日本",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "剛好 1 個正確答案" in exc.value.detail

    async def test_rejects_publishing_quiz_with_multiple_correct_answers(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_multiple_correct_quiz",
                    "questionText": "下列哪些是澳門官方語言？",
                    "options": [
                        {
                            "optionUUID": "o_zh",
                            "text": "中文",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "o_pt",
                            "text": "葡文",
                            "assigned_right_answer": True,
                        },
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "剛好 1 個正確答案" in exc.value.detail

    async def test_allows_publishing_quiz_with_string_boolean_answer_keys(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.due_date = "2099-01-01"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_string_boolean_quiz",
                    "questionText": "澳門位於哪個地區？",
                    "options": [
                        {
                            "optionUUID": "o_west",
                            "text": "珠江口西側",
                            "assigned_right_answer": "true",
                        },
                        {
                            "optionUUID": "o_north",
                            "text": "長江口北側",
                            "assigned_right_answer": "false",
                        },
                        {
                            "optionUUID": "o_east",
                            "text": "台灣海峽東側",
                            "assigned_right_answer": "0",
                        },
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            updated = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True),
                admin_user,
                db,
            )

        assert updated.published is True

    async def test_rejects_publishing_assignment_with_incomplete_form_blank_answer(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.FORM
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_incomplete_form",
                    "questionText": "澳門的官方語言之一是____。",
                    "blanks": [
                        {
                            "blankUUID": "blank_missing_answer",
                            "placeholder": "填寫答案",
                            "correctAnswer": " ",
                        }
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "每個填空都需要正確答案" in exc.value.detail

    async def test_rejects_publishing_short_answer_without_answer_key(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        assignment_task.contents = {
            "prompt": "澳門位於哪個國家？",
            "correct_answers": [" "],
            "match_mode": "case_insensitive",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "短問答可接受答案" in exc.value.detail

    async def test_allows_publishing_simple_tasks_with_legacy_answer_key_aliases(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.FORM
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_alias_form",
                    "questionText": "澳門的官方語言之一是____。",
                    "blanks": [
                        {
                            "blankUUID": "blank_alias_answer",
                            "placeholder": "填寫答案",
                            "correct_answer": "中文",
                        }
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True),
                admin_user,
                db,
            )

        assert result.published is True

    async def test_allows_publishing_quiz_with_legacy_option_text_aliases(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "q_alias_quiz",
                    "questionText": "澳門位於哪個國家？",
                    "options": [
                        {
                            "optionUUID": "o_china",
                            "label": "中國",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "o_japan",
                            "option": "日本",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True),
                admin_user,
                db,
            )

        assert result.published is True

    async def test_allows_publishing_short_answer_with_accepted_answers_alias(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        assignment_task.contents = {
            "prompt": "請寫出澳門所屬的國家。",
            "accepted_answers": ["中國"],
            "match_mode": "case_insensitive",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True),
                admin_user,
                db,
            )

        assert result.published is True

    async def test_allows_publishing_essay_task_for_school_pilot(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.ESSAY
        assignment_task.contents = {
            "prompt": "以「一次難忘的校園活動」為題，寫一篇短文。",
            "min_words": "150",
            "max_words": "400",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True),
                admin_user,
                db,
            )

        assert result.published is True

    async def test_rejects_publishing_subjective_short_answer_for_simple_pilot(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        assignment_task.contents = {
            "prompt": "請解釋水循環的過程。",
            "correct_answers": ["水蒸發後凝結，再以降水回到地面。"],
            "match_mode": "case_insensitive",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "主觀問答題" in exc.value.detail
        assert "直接自動批改" in exc.value.detail

    async def test_rejects_publishing_complex_short_answer_match_mode_for_simple_pilot(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        assignment_task.contents = {
            "prompt": "澳門的簡稱是甚麼？",
            "correct_answers": ["澳門"],
            "match_mode": "regex",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "太容易誤判" in exc.value.detail

    async def test_rejects_publishing_contains_short_answer_match_mode_for_simple_pilot(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        assignment_task.contents = {
            "prompt": "澳門位於哪個國家？",
            "correct_answers": ["中國"],
            "match_mode": "contains",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "太容易誤判" in exc.value.detail

    async def test_allows_publishing_factual_short_answer_with_comparison_topic_word(
        self, mock_request, db, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        assignment_task.contents = {
            "prompt": "分數比較常用的符號之一是甚麼？",
            "correct_answers": ["大於號"],
            "match_mode": "case_insensitive",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(published=True),
                admin_user,
                db,
            )

        assert result.published is True
        assert result.auto_grading is True

    async def test_rejects_publishing_non_simple_task_type_for_school_pilot(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "language_id": 71,
            "starter_code": "name = input()\n",
            "solution_code": "print(name)\n",
            "test_cases": [
                {"stdin": "Ada\n", "expectedStdout": "Ada\n"},
            ],
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(published=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "發布作業前請先完成題目內容" in exc.value.detail
        assert "程式題" in exc.value.detail
        assert "選擇題、填空題、短問答和作文題" in exc.value.detail

    async def test_rejects_auto_grading_when_assignment_has_manual_task(
        self, mock_request, db, assignment, admin_user
    ):
        file_task = AssignmentTask(
            title="Upload worksheet",
            description="Upload a file",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
            contents={},
            max_grade_value=100,
            assignment_id=assignment.id,
            org_id=assignment.org_id,
            course_id=assignment.course_id,
            chapter_id=assignment.chapter_id,
            activity_id=assignment.activity_id,
            assignment_task_uuid="assignmenttask_file_manual_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.auto_grading = False
        db.add(assignment)
        db.add(file_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment(
                    mock_request,
                    assignment.assignment_uuid,
                    AssignmentUpdate(auto_grading=True),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "不能啟用自動批改" in exc.value.detail

    async def test_updates_school_pilot_metadata(
        self, mock_request, db, org, assignment, admin_user, regular_user
    ):
        group = UserGroup(
            id=50,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_metadata_update",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=50,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(group)
        db.add(membership)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(
                    subject="數學",
                    education_stage="primary",
                    grade_level="小四",
                    school_year="2026-2027",
                    term="第一學期",
                    unit="分數",
                    learning_objectives=["能比較分數大小", "能完成基礎練習"],
                    target_usergroup_ids=[50, 50],
                    score_policy="highest",
                    teacher_review_required=True,
                    teacher_review_status="pending",
                ),
                admin_user,
                db,
            )
        assert result.subject == "數學"
        assert result.education_stage == "primary"
        assert result.grade_level == "小四"
        assert result.learning_objectives == ["能比較分數大小", "能完成基礎練習"]
        assert result.target_usergroup_ids == [50]
        assert result.score_policy == "highest"
        assert result.teacher_review_required is True

    async def test_update_assignment_repairs_null_learning_platform_defaults(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.auto_grading = None
        assignment.anti_copy_paste = None
        assignment.show_correct_answers = None
        assignment.allow_retries = None
        assignment.max_retries = -3
        assignment.teacher_review_required = None
        assignment.teacher_review_status = "unknown"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(title="Repaired Defaults"),
                admin_user,
                db,
            )

        assert result.title == "Repaired Defaults"
        assert result.auto_grading is True
        assert result.anti_copy_paste is False
        assert result.show_correct_answers is True
        assert result.allow_retries is True
        assert result.max_retries == 0
        assert result.teacher_review_required is False
        assert result.teacher_review_status == "not_required"

    async def test_invalid_updated_score_policy_defaults_to_highest(
        self, mock_request, db, assignment, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment(
                mock_request,
                assignment.assignment_uuid,
                AssignmentUpdate(score_policy="not-a-policy"),
                admin_user,
                db,
            )

        assert result.score_policy == "highest"


# ---------------------------------------------------------------------------
# School pilot workbench / gradebook
# ---------------------------------------------------------------------------


class TestSchoolPilotGradebook:
    async def test_my_assignment_queue_lists_student_targeted_pending_assignment(
        self, db, org, assignment, assignment_task, regular_user
    ):
        group = UserGroup(
            id=49,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_student_queue",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=49,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.title = "分數比較 3 題練習"
        assignment.subject = "數學"
        assignment.grade_level = "小四"
        assignment.unit = "分數"
        assignment.target_usergroup_ids = [49]
        assignment.score_policy = "latest"
        db.add(group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 1
        assert payload["summary"]["overdue_count"] == 0
        assert payload["summary"]["self_test_bank_ready"] is False
        assert payload["summary"]["self_test_ready_question_count"] == 0
        assert payload["summary"]["self_test_min_question_count"] == 3
        row = payload["assignments"][0]
        assert row["assignment_uuid"] == assignment.assignment_uuid
        assert row["assignment_title"] == "分數比較 3 題練習"
        assert row["course_uuid"] == "course_test"
        assert row["activity_uuid"] == "activity_test"
        assert row["task_count"] == 1
        assert row["max_grade"] == 100
        assert row["score_policy"] == "latest"
        assert row["submission_status"] == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
        assert row["needs_action"] is True

    async def test_my_assignment_queue_exposes_self_test_readiness_for_student_home(
        self, db, org, assignment, regular_user
    ):
        group = UserGroup(
            id=45,
            org_id=org.id,
            name="小四自測",
            description="",
            usergroup_uuid="usergroup_student_queue_self_test_ready",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=45,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [45]
        db.add(group)
        db.add(membership)
        db.add(assignment)
        for index in range(3):
            db.add(_ready_self_test_bank_item(org.id, f"學生首頁自測題 {index + 1}"))
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["self_test_bank_ready"] is True
        assert payload["summary"]["self_test_ready_question_count"] == 3
        assert payload["summary"]["self_test_min_question_count"] == 3

    async def test_my_assignment_queue_excludes_students_outside_target_group(
        self, db, org, assignment, regular_user
    ):
        group = UserGroup(
            id=48,
            org_id=org.id,
            name="小四乙",
            description="",
            usergroup_uuid="usergroup_student_queue_other",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [48]
        db.add(group)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_hides_published_assignment_without_tasks(
        self, db, org, assignment, regular_user
    ):
        group = UserGroup(
            id=43,
            org_id=org.id,
            name="小四空題",
            description="",
            usergroup_uuid="usergroup_student_queue_no_tasks",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=43,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [43]
        db.add(group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_returns_current_and_best_grade_for_latest_policy(
        self, db, org, assignment, assignment_task, regular_user
    ):
        group = UserGroup(
            id=44,
            org_id=org.id,
            name="小五甲",
            description="",
            usergroup_uuid="usergroup_student_queue_scored",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=44,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [44]
        assignment.allow_retries = True
        assignment.score_policy = "latest"
        submission = AssignmentUserSubmission(
            assignment_id=assignment.id,
            user_id=regular_user.id,
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            grade=60,
            best_grade=90,
            best_attempt_number=1,
            attempt_number=2,
            teacher_review_status="not_required",
            assignmentusersubmission_uuid="assignmentusersubmission_queue_scored",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(group)
        db.add(membership)
        db.add(assignment)
        db.add(submission)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        row = payload["assignments"][0]
        assert row["score_policy"] == "latest"
        assert row["grade"] == 60
        assert row["best_grade"] == 90
        assert row["max_grade"] == 100
        assert row["can_retry"] is True
        assert row["max_retries"] == 0
        assert payload["summary"]["can_retry_count"] == 1

    async def test_my_assignment_queue_hides_retry_when_attempt_cap_reached(
        self, db, org, assignment, assignment_task, regular_user
    ):
        group = UserGroup(
            id=441,
            org_id=org.id,
            name="小五重做上限",
            description="",
            usergroup_uuid="usergroup_student_queue_retry_cap",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=441,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [441]
        assignment.allow_retries = True
        assignment.max_retries = 2
        submission = AssignmentUserSubmission(
            assignment_id=assignment.id,
            user_id=regular_user.id,
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            grade=60,
            best_grade=80,
            best_attempt_number=1,
            attempt_number=2,
            teacher_review_status="not_required",
            assignmentusersubmission_uuid="assignmentusersubmission_queue_retry_cap",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(group)
        db.add(membership)
        db.add(assignment)
        db.add(submission)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        row = payload["assignments"][0]
        assert row["can_retry"] is False
        assert row["max_retries"] == 2
        assert payload["summary"]["can_retry_count"] == 0

    async def test_my_assignment_queue_uses_latest_attempt_when_duplicate_submission_rows_exist(
        self, db, org, assignment, assignment_task, regular_user
    ):
        group = UserGroup(
            id=42,
            org_id=org.id,
            name="小五重做",
            description="",
            usergroup_uuid="usergroup_student_queue_duplicate_submission",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=42,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        earlier_time = str(datetime.now() - timedelta(days=1))
        later_time = str(datetime.now())
        assignment.target_usergroup_ids = [42]
        old_submission = AssignmentUserSubmission(
            assignment_id=assignment.id,
            user_id=regular_user.id,
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            grade=40,
            best_grade=40,
            best_attempt_number=1,
            attempt_number=1,
            teacher_review_status="not_required",
            assignmentusersubmission_uuid="assignmentusersubmission_queue_duplicate_old",
            creation_date=earlier_time,
            update_date=earlier_time,
        )
        latest_submission = AssignmentUserSubmission(
            assignment_id=assignment.id,
            user_id=regular_user.id,
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            grade=90,
            best_grade=90,
            best_attempt_number=2,
            attempt_number=2,
            teacher_review_status="not_required",
            assignmentusersubmission_uuid="assignmentusersubmission_queue_duplicate_latest",
            creation_date=later_time,
            update_date=later_time,
        )
        db.add(group)
        db.add(membership)
        db.add(assignment)
        db.add(old_submission)
        db.add(latest_submission)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        row = payload["assignments"][0]
        assert row["submission_status"] == AssignmentUserSubmissionStatus.GRADED.value
        assert row["attempt_number"] == 2
        assert row["grade"] == 90
        assert row["best_grade"] == 90
        assert payload["summary"]["graded_count"] == 1

    async def test_my_assignment_queue_hides_published_assignment_without_target_group(
        self, db, org, assignment, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_hides_assignment_with_missing_target_group(
        self, db, org, assignment, regular_user
    ):
        assignment.target_usergroup_ids = [99948]
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_hides_assignments_whose_activity_is_unpublished(
        self, db, org, assignment, activity, regular_user
    ):
        activity.published = False
        db.add(activity)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_hides_assignments_whose_course_is_unpublished(
        self, db, org, assignment, course, regular_user
    ):
        course.published = False
        db.add(course)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_hides_private_course_locked_to_another_group(
        self, db, org, assignment, assignment_task, course, regular_user
    ):
        course.public = False
        locked_group = UserGroup(
            id=47,
            org_id=org.id,
            name="中一甲",
            description="",
            usergroup_uuid="usergroup_course_locked_other",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(locked_group)
        db.add(
            UserGroupResource(
                usergroup_id=47,
                resource_uuid=course.course_uuid,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(course)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_my_assignment_queue_includes_private_course_when_student_has_course_group_access(
        self, db, org, assignment, assignment_task, course, regular_user
    ):
        course.public = False
        assignment.target_usergroup_ids = [46]
        course_group = UserGroup(
            id=46,
            org_id=org.id,
            name="中一乙",
            description="",
            usergroup_uuid="usergroup_course_locked_member",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(course_group)
        db.add(
            UserGroupResource(
                usergroup_id=46,
                resource_uuid=course.course_uuid,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=46,
                user_id=regular_user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(course)
        db.add(assignment)
        await db.commit()

        payload = await read_my_assignment_queue(org.id, regular_user, db)

        assert payload["summary"]["todo_count"] == 1
        assert payload["assignments"][0]["assignment_uuid"] == assignment.assignment_uuid

    async def test_my_assignment_queue_returns_empty_for_dashboard_users(
        self, db, org, assignment, assignment_task, admin_user
    ):
        payload = await read_my_assignment_queue(org.id, admin_user, db)

        assert payload["summary"]["todo_count"] == 0
        assert payload["assignments"] == []

    async def test_ready_assignment_with_unpublished_activity_counts_as_needing_setup(
        self, db, org, assignment, assignment_task, activity, admin_user, regular_user
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = [45]
        assignment.due_date = str((datetime.now().date() + timedelta(days=3)).isoformat())
        activity.published = False
        db.add(
            UserGroup(
                id=45,
                org_id=org.id,
                name="小四丁",
                description="",
                usergroup_uuid="usergroup_ready_assignment_hidden_activity",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=45,
                user_id=regular_user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(assignment)
        db.add(activity)
        await db.commit()

        workbench = await read_teacher_assignment_workbench(org.id, admin_user, db)
        gradebook = await read_assignment_gradebook(org.id, admin_user, db)

        assert workbench["summary"]["published"] == 1
        assert workbench["summary"]["simple_pilot_ready_assignments"] == 0
        assert workbench["summary"]["published_needing_setup"] == 1
        assert workbench["summary"]["published_hidden_from_students"] == 1
        assert workbench["summary"]["expected_unsubmitted"] == 0
        assert workbench["summary"]["attention_count"] == 0
        assert workbench["summary"]["due_soon"] == 0
        assert workbench["missing_submissions"] == []
        assert workbench["due_soon_assignments"] == []
        setup_action = next(
            action
            for action in workbench["ai_actions"]
            if action["title"] == "整理已發布作業"
        )
        assert "學生暫時看不到" in setup_action["description"]
        assert gradebook["summary"]["published"] == 1
        assert gradebook["summary"]["simple_pilot_ready_assignments"] == 0
        assert gradebook["summary"]["published_needing_setup"] == 1
        assert gradebook["summary"]["published_hidden_from_students"] == 1
        assert gradebook["summary"]["unsubmitted"] == 0
        assert gradebook["summary"]["attention_count"] == 0
        assert gradebook["summary"]["actionable_rows"] == 0
        assert gradebook["summary"]["visible_assignment_rows"] == 0
        assert len(gradebook["rows"]) == 1
        assert gradebook["rows"][0]["visible_to_students"] is False
        assert gradebook["rows"][0]["counts_for_grade"] is False
        assert "學生暫時看不到" in gradebook["rows"][0]["attention_reason"]

    async def test_ready_assignment_with_unpublished_course_counts_as_needing_setup(
        self, db, org, assignment, assignment_task, course, admin_user, regular_user
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = [44]
        assignment.due_date = str((datetime.now().date() + timedelta(days=3)).isoformat())
        course.published = False
        db.add(
            UserGroup(
                id=44,
                org_id=org.id,
                name="小四戊",
                description="",
                usergroup_uuid="usergroup_ready_assignment_hidden_course",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=44,
                user_id=regular_user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(assignment)
        db.add(course)
        await db.commit()

        workbench = await read_teacher_assignment_workbench(org.id, admin_user, db)
        gradebook = await read_assignment_gradebook(org.id, admin_user, db)

        assert workbench["summary"]["published"] == 1
        assert workbench["summary"]["simple_pilot_ready_assignments"] == 0
        assert workbench["summary"]["published_needing_setup"] == 1
        assert workbench["summary"]["published_hidden_from_students"] == 1
        assert workbench["summary"]["expected_unsubmitted"] == 0
        assert workbench["summary"]["attention_count"] == 0
        assert workbench["summary"]["due_soon"] == 0
        assert workbench["missing_submissions"] == []
        assert workbench["due_soon_assignments"] == []
        setup_action = next(
            action
            for action in workbench["ai_actions"]
            if action["title"] == "整理已發布作業"
        )
        assert "學生暫時看不到" in setup_action["description"]
        assert gradebook["summary"]["published"] == 1
        assert gradebook["summary"]["simple_pilot_ready_assignments"] == 0
        assert gradebook["summary"]["published_needing_setup"] == 1
        assert gradebook["summary"]["published_hidden_from_students"] == 1
        assert gradebook["summary"]["unsubmitted"] == 0
        assert gradebook["summary"]["attention_count"] == 0
        assert gradebook["summary"]["actionable_rows"] == 0
        assert gradebook["summary"]["visible_assignment_rows"] == 0
        assert len(gradebook["rows"]) == 1
        assert gradebook["rows"][0]["visible_to_students"] is False
        assert gradebook["rows"][0]["counts_for_grade"] is False
        assert "學生暫時看不到" in gradebook["rows"][0]["attention_reason"]

    async def test_gradebook_lists_target_group_submission(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        group = UserGroup(
            id=50,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_p4a",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=50,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.subject = "數學"
        assignment.grade_level = "小四"
        assignment.unit = "分數"
        assignment.target_usergroup_ids = [50]
        db.add(group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        payload = await read_assignment_gradebook(
            org.id,
            admin_user,
            db,
            usergroup_id=50,
        )

        assert payload["summary"]["total_rows"] == 1
        row = payload["rows"][0]
        assert row["student_email"] == regular_user.email
        assert row["assignment_title"] == assignment.title
        assert row["subject"] == "數學"
        assert row["target_usergroups"] == ["小四甲"]
        assert row["submission_status"] == AssignmentUserSubmissionStatus.SUBMITTED.value

    async def test_gradebook_usergroup_filter_reports_scoped_learner_count(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        group = UserGroup(
            id=51,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_p4a_scope",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        other_student = User(
            id=601,
            username="other_student",
            email="other-student@example.com",
            first_name="Other",
            last_name="Student",
            role_id=4,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(group)
        db.add(other_student)
        db.add(
            UserOrganization(
                user_id=other_student.id,
                org_id=org.id,
                role_id=4,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=51,
                user_id=regular_user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        assignment.target_usergroup_ids = [51]
        db.add(assignment)
        await db.commit()

        payload = await read_assignment_gradebook(
            org.id,
            admin_user,
            db,
            usergroup_id=51,
        )

        assert payload["summary"]["learner_count"] == 1
        assert payload["summary"]["active_student_count"] == 1
        assert payload["summary"]["engaged_student_count"] == 1
        assert payload["summary"]["engagement_rate"] == 100.0
        assert payload["summary"]["participation_rate"] == 100.0
        assert len(payload["rows"]) == 1
        assert payload["rows"][0]["student_email"] == regular_user.email

    async def test_gradebook_marks_expected_student_unsubmitted(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            51,
            regular_user,
            "usergroup_gradebook_unsubmitted",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["learner_count"] == 1
        assert payload["summary"]["active_student_count"] == 1
        assert payload["summary"]["engaged_student_count"] == 0
        assert payload["summary"]["engagement_rate"] == 0.0
        assert payload["summary"]["unsubmitted"] == 1
        assert payload["summary"]["student_count"] == 1
        assert payload["summary"]["attention_count"] == 1
        assert payload["summary"]["submission_rate"] == 0.0
        assert payload["summary"]["participation_records"] == 0
        assert payload["summary"]["participation_rate"] == 0.0
        assert payload["summary"]["average_score"] is None
        row = payload["rows"][0]
        assert row["student_email"] == regular_user.email
        assert row["submission_status"] == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
        assert row["attention_reason"] == "未提交"

    async def test_gradebook_reports_imported_learners_before_first_assignment(
        self, db, org, admin_user, regular_user
    ):
        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["total_rows"] == 0
        assert payload["summary"]["learner_count"] == 1
        assert payload["summary"]["active_student_count"] == 0
        assert payload["summary"]["engaged_student_count"] == 0
        assert payload["summary"]["engagement_rate"] == 0.0
        assert payload["summary"]["student_count"] == 0

    async def test_gradebook_summary_reports_pilot_metrics_for_principal_view(
        self, db, org, assignment, assignment_task, graded_submission, admin_user, regular_user
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        graded_submission.attempt_number = 2
        graded_submission.best_grade = 90
        graded_submission.best_attempt_number = 1
        db.add(assignment)
        db.add(graded_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            52,
            regular_user,
            "usergroup_gradebook_principal_metrics",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["total_rows"] == 1
        assert payload["summary"]["student_count"] == 1
        assert payload["summary"]["engaged_student_count"] == 1
        assert payload["summary"]["engagement_rate"] == 100.0
        assert payload["summary"]["submitted"] == 1
        assert payload["summary"]["graded"] == 1
        assert payload["summary"]["unsubmitted"] == 0
        assert payload["summary"]["attention_count"] == 0
        assert payload["summary"]["retry_records"] == 1
        assert payload["summary"]["submission_rate"] == 100.0
        assert payload["summary"]["participation_records"] == 1
        assert payload["summary"]["participation_rate"] == 100.0
        assert payload["summary"]["average_score"] == 85.0
        assert payload["summary"]["passing_score_records"] == 1
        assert payload["summary"]["needs_practice_records"] == 0
        assert payload["summary"]["pilot_status"] == "showcase_ready"
        assert payload["summary"]["pilot_status_label"] == "可展示"
        assert "1/1 名學生" in payload["summary"]["principal_evidence_summary"]
        assert "1 條學習記錄" in payload["summary"]["principal_evidence_summary"]
        assert "1 條已完成批改" in payload["summary"]["principal_evidence_summary"]
        assert payload["rows"][0]["attempt_number"] == 2
        assert payload["rows"][0]["best_grade"] == 90
        assert payload["rows"][0]["best_attempt_number"] == 1
        assert payload["rows"][0]["auto_grading"] is True
        assert payload["rows"][0]["allow_retries"] is True
        assert payload["rows"][0]["show_correct_answers"] is True

        csv_text = assignment_gradebook_to_csv(payload)
        assert "校內試行狀態,可展示" in csv_text
        assert "校長展示摘要" in csv_text
        assert "已有 1/1 名學生產生 1 條學習記錄，1 條已完成批改。" in csv_text
        assert "建議下一步" in csv_text
        assert "最高分" in csv_text
        assert "達標記錄（60% 以上）" in csv_text
        assert "需補強記錄（低於 60%）" in csv_text
        assert "90/100" in csv_text
        assert "自動批改" in csv_text
        assert "可重做" in csv_text
        assert "顯示參考答案" in csv_text
        assert ",是,是,是," in csv_text

    async def test_gradebook_summary_counts_low_scores_as_needing_practice(
        self, db, org, assignment, assignment_task, graded_submission, admin_user, regular_user
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        graded_submission.grade = 50
        graded_submission.best_grade = 50
        db.add(assignment)
        db.add(graded_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            53,
            regular_user,
            "usergroup_gradebook_low_score",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["scored_records"] == 1
        assert payload["summary"]["average_score"] == 50.0
        assert payload["summary"]["passing_score_records"] == 0
        assert payload["summary"]["needs_practice_records"] == 1
        assert payload["summary"]["pilot_status"] == "active_needs_followup"
        assert payload["summary"]["pilot_status_label"] == "已使用，需跟進"

    async def test_gradebook_class_summary_falls_back_without_ai_config(
        self, monkeypatch, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            531,
            regular_user,
            "usergroup_gradebook_summary_fallback",
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_configuration_error",
            lambda: "AI 出題尚未配置完整：請設定測試端點。",
        )

        payload = await read_assignment_gradebook_summary(org.id, admin_user, db)

        assert payload["ai_used"] is False
        assert payload["ai_message"] == "AI 暫時不可用，已使用系統統計產生摘要。"
        assert payload["requires_attention"] is True
        assert payload["facts"]["metrics"]["unsubmitted"] == 1
        assert payload["facts"]["metrics"]["submission_rate"] == 0.0
        assert payload["facts"]["follow_up_students"][0]["student_email"] == regular_user.email
        assert "未提交" in payload["facts"]["follow_up_students"][0]["reasons"]
        assert payload["narrative"]["title"] == "班級學情摘要"
        assert "提交率 0%" in payload["narrative"]["summary"]
        assert payload["narrative"]["suggested_actions"]

    async def test_gradebook_class_summary_uses_ai_narrative_with_gradebook_facts(
        self, monkeypatch, db, org, assignment, assignment_task, graded_submission, admin_user, regular_user
    ):
        graded_submission.grade = 50
        graded_submission.best_grade = 50
        db.add(graded_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            532,
            regular_user,
            "usergroup_gradebook_summary_ai",
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_configuration_error",
            lambda: None,
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.get_gemini_client",
            lambda: _fake_gradebook_summary_ai_client(),
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.enforce_ai_rate_limit",
            lambda user_id, org_id: None,
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.reserve_ai_credit",
            AsyncMock(),
        )

        payload = await read_assignment_gradebook_summary(org.id, admin_user, db)

        assert payload["ai_used"] is True
        assert payload["ai_message"] is None
        assert payload["facts"]["metrics"]["low_score_records"] == 1
        assert payload["facts"]["metrics"]["average_score"] == 50.0
        assert payload["facts"]["weak_areas"]
        assert payload["narrative"]["summary"] == "本班提交情況穩定，但仍有學生需要跟進低分和補強。"
        assert "安排 2 題同類補練" in payload["narrative"]["suggested_actions"]

    async def test_gradebook_class_summary_falls_back_when_ai_rate_limit_fails(
        self, monkeypatch, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            533,
            regular_user,
            "usergroup_gradebook_summary_rate_limit",
        )
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_configuration_error",
            lambda: None,
        )

        def _raise_rate_limit(user_id, org_id):
            raise HTTPException(status_code=429, detail="rate limit")

        monkeypatch.setattr(
            "src.services.courses.activities.assignments.enforce_ai_rate_limit",
            _raise_rate_limit,
        )

        payload = await read_assignment_gradebook_summary(org.id, admin_user, db)

        assert payload["ai_used"] is False
        assert payload["ai_message"] == "AI 暫時不可用，已使用系統統計產生摘要。"
        assert payload["facts"]["metrics"]["unsubmitted"] == 1
        assert payload["narrative"]["suggested_actions"]

    async def test_gradebook_treats_unscored_self_tests_as_usage_evidence_without_affecting_average(
        self, db, org, assignment, regular_user, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        db.add(
            SelfTestAttempt(
                id=701,
                attempt_uuid="selftest_gradebook_auto_evidence",
                org_id=org.id,
                user_id=regular_user.id,
                question_count=5,
                status=SelfTestAttemptStatus.SUBMITTED,
                score=400,
                max_score=500,
                percentage=80,
                counts_for_grade=False,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
                submitted_at=str(datetime.now()),
            )
        )
        await db.commit()

        payload = await read_assignment_gradebook(
            org.id,
            admin_user,
            db,
            include_self_tests=True,
        )

        assert payload["summary"]["total_rows"] == 1
        assert payload["summary"]["self_test_rows"] == 1
        assert payload["summary"]["engaged_student_count"] == 1
        assert payload["summary"]["engagement_rate"] == 100.0
        assert payload["summary"]["submitted"] == 1
        assert payload["summary"]["participation_records"] == 1
        assert payload["summary"]["participation_rate"] == 100.0
        assert payload["summary"]["graded"] == 1
        assert payload["summary"]["scored_records"] == 0
        assert payload["summary"]["passing_score_records"] == 0
        assert payload["summary"]["needs_practice_records"] == 0
        assert payload["summary"]["pending_review"] == 0
        assert payload["summary"]["attention_count"] == 0
        assert payload["summary"]["average_score"] is None
        row = payload["rows"][0]
        assert row["source_type"] == "self_test"
        assert row["counts_for_grade"] is False
        assert row["teacher_review_status"] == "not_required"
        assert row["submission_status"] == SelfTestAttemptStatus.SUBMITTED.value
        assert row["grade_display"]["display_grade"] == "80%"

    async def test_gradebook_counts_teacher_selected_self_tests_in_average(
        self, db, org, assignment, regular_user, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        db.add(
            SelfTestAttempt(
                id=702,
                attempt_uuid="selftest_gradebook_counted_evidence",
                org_id=org.id,
                user_id=regular_user.id,
                question_count=5,
                status=SelfTestAttemptStatus.SUBMITTED,
                score=450,
                max_score=500,
                percentage=90,
                counts_for_grade=True,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
                submitted_at=str(datetime.now()),
            )
        )
        await db.commit()

        payload = await read_assignment_gradebook(
            org.id,
            admin_user,
            db,
            include_self_tests=True,
        )

        assert payload["summary"]["total_rows"] == 1
        assert payload["summary"]["self_test_rows"] == 1
        assert payload["summary"]["scored_records"] == 1
        assert payload["summary"]["average_score"] == 90.0
        assert payload["summary"]["passing_score_records"] == 1
        assert payload["summary"]["needs_practice_records"] == 0
        assert payload["rows"][0]["counts_for_grade"] is True

    async def test_gradebook_ignores_unpublished_assignments(
        self, db, org, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["total_rows"] == 0
        assert payload["summary"]["unsubmitted"] == 0
        assert payload["rows"] == []

    async def test_gradebook_csv_uses_traditional_chinese_headers(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        payload = await read_assignment_gradebook(org.id, admin_user, db)
        csv_text = assignment_gradebook_to_csv(payload)

        assert csv_text.startswith("\ufeff摘要項目,摘要數值")
        assert "學生使用率" in csv_text
        assert "平均分（計入評分）" in csv_text
        assert "簡化原則" in csv_text
        assert "\r\n\r\n來源,課程" in csv_text
        assert "學生姓名" in csv_text
        assert "提交狀態" in csv_text
        assert "參與狀態" in csv_text
        assert "跟進原因" in csv_text
        assert "嘗試次數" in csv_text
        assert "最高分" in csv_text
        assert regular_user.email in csv_text
        assert "未提交" in csv_text
        assert "未參與" in csv_text
        assert "NOT_SUBMITTED" not in csv_text
        assert "missing" not in csv_text

    async def test_gradebook_csv_excludes_group_users_without_org_membership(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        group = await _target_assignment_to_group(
            db,
            org,
            assignment,
            63,
            regular_user,
            "usergroup_gradebook_org_scope",
        )
        outsider = User(
            id=963,
            username="outside-org",
            first_name="Outside",
            last_name="Learner",
            email="outside-org@example.com",
            password="hashed_password",
            user_uuid="user_gradebook_outside_org",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(outsider)
        db.add(
            UserGroupUser(
                usergroup_id=group.id,
                user_id=outsider.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        await db.commit()

        payload = await read_assignment_gradebook(org.id, admin_user, db)
        csv_text = assignment_gradebook_to_csv(payload)

        assert [row["student_email"] for row in payload["rows"]] == [regular_user.email]
        assert regular_user.email in csv_text
        assert outsider.email not in csv_text

    async def test_gradebook_csv_treats_string_false_settings_as_disabled(self):
        csv_text = assignment_gradebook_to_csv(
            {
                "summary": {},
                "rows": [
                    {
                        "source_type": "assignment",
                        "assignment_title": "三題簡單作業",
                        "student_name": "測試學生",
                        "student_email": "test@example.com",
                        "submission_status": "GRADED",
                        "teacher_review_status": "not_required",
                        "visible_to_students": True,
                        "auto_grading": "false",
                        "allow_retries": "0",
                        "show_correct_answers": "否",
                        "grade": 80,
                        "max_grade": 100,
                        "grade_display": {"percentage_display": "80%"},
                        "attempt_number": 1,
                        "score_policy": "highest",
                        "late": False,
                        "counts_for_grade": True,
                    }
                ],
            }
        )

        [row] = _gradebook_csv_detail_rows(csv_text)
        assert row["自動批改"] == "否"
        assert row["可重做"] == "否"
        assert row["顯示參考答案"] == "否"

    async def test_gradebook_csv_filename_is_school_admin_friendly(
        self, db, org, assignment, assignment_task, admin_user
    ):
        payload = await read_assignment_gradebook(
            org.id,
            admin_user,
            db,
            course_id=assignment.course_id,
            usergroup_id=12,
            include_self_tests=True,
        )

        filename = assignment_gradebook_csv_filename(
            payload,
            generated_at=datetime(2026, 6, 8, 9, 30, 0),
        )

        assert filename == (
            f"learnhouse-gradebook-org-{org.id}-course-{assignment.course_id}"
            "-group-12-with-self-tests-20260608.csv"
        )
        assert " " not in filename
        assert "/" not in filename
        assert "\\" not in filename

    async def test_gradebook_csv_filename_describes_default_scope(
        self, db, org, assignment, assignment_task, admin_user
    ):
        payload = await read_assignment_gradebook(org.id, admin_user, db)

        filename = assignment_gradebook_csv_filename(
            payload,
            generated_at=datetime(2026, 6, 8, 9, 30, 0),
        )

        assert filename == (
            f"learnhouse-gradebook-org-{org.id}-all-courses-all-groups"
            "-assignments-only-20260608.csv"
        )

    async def test_teacher_workbench_counts_pending_review(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        db.add(assignment)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            54,
            regular_user,
            "usergroup_workbench_pending_review",
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["pending_review"] == 1
        assert payload["summary"]["essay_pending_review"] == 0
        assert payload["pending_review_submissions"][0]["assignment_uuid"] == assignment.assignment_uuid
        assert payload["pending_review_submissions"][0]["has_essay_task"] is False

    async def test_teacher_workbench_counts_essay_pending_review(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.ESSAY
        assignment_task.contents = {
            "prompt": "請寫一篇 200 字短文，介紹你最喜歡的一本書。",
            "rubric": "內容切題、結構清楚、語句通順。",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            154,
            regular_user,
            "usergroup_workbench_essay_pending_review",
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["pending_review"] == 1
        assert payload["summary"]["essay_pending_review"] == 1
        assert payload["pending_review_submissions"][0]["assignment_uuid"] == assignment.assignment_uuid
        assert payload["pending_review_submissions"][0]["has_essay_task"] is True
        assert payload["pending_review_submissions"][0]["task_types"] == ["ESSAY"]

    async def test_teacher_workbench_reports_retry_records(
        self, db, org, assignment, assignment_task, graded_submission, admin_user, regular_user
    ):
        graded_submission.attempt_number = 3
        db.add(graded_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            55,
            regular_user,
            "usergroup_workbench_retry_records",
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["retry_records"] == 1

    async def test_teacher_workbench_does_not_list_retry_in_progress_as_missing(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        user_submission.attempt_number = 2
        db.add(user_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            56,
            regular_user,
            "usergroup_workbench_retry_in_progress",
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["retry_records"] == 1
        assert payload["summary"]["participation_records"] == 1
        assert payload["summary"]["participation_rate"] == 100.0
        assert payload["summary"]["expected_unsubmitted"] == 0
        assert payload["missing_submissions"] == []

    async def test_teacher_workbench_warns_about_published_assignments_without_targets(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = True
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = []
        assignment.due_date = str((datetime.now().date() + timedelta(days=3)).isoformat())
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)
        gradebook_payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["simple_pilot_ready_assignments"] == 0
        assert gradebook_payload["summary"]["simple_pilot_ready_assignments"] == 0
        assert payload["summary"]["published_without_targets"] == 1
        assert payload["summary"]["published_empty_target_usergroups"] == 0
        assert gradebook_payload["summary"]["published_without_targets"] == 1
        assert gradebook_payload["summary"]["published_empty_target_usergroups"] == 0
        assert gradebook_payload["summary"]["unsubmitted"] == 0
        assert gradebook_payload["summary"]["attention_count"] == 0
        assert gradebook_payload["summary"]["actionable_rows"] == 0
        assert gradebook_payload["summary"]["visible_assignment_rows"] == 0
        assert len(gradebook_payload["rows"]) == 1
        assert gradebook_payload["rows"][0]["target_usergroups_valid"] is False
        assert gradebook_payload["rows"][0]["counts_for_grade"] is False
        assert "有效班級/群組" in gradebook_payload["rows"][0]["attention_reason"]
        assert payload["summary"]["expected_unsubmitted"] == 0
        assert payload["summary"]["due_soon"] == 0
        assert payload["missing_submissions"] == []
        assert payload["due_soon_assignments"] == []
        action_titles = [action["title"] for action in payload["ai_actions"]]
        assert action_titles.index("設定發佈班級") < action_titles.index("整理已發布作業")
        assert action_titles.index("設定發佈班級") < action_titles.index("簡單出題")
        target_action = next(
            action
            for action in payload["ai_actions"]
            if action["title"] == "設定發佈班級"
        )
        assert target_action["priority"] == "high"
        assert "未指定班級/群組" in target_action["description"]
        assert "班級已不存在" in target_action["description"]
        assert "重新設定發佈對象" in target_action["description"]
        setup_action = next(
            action
            for action in payload["ai_actions"]
            if action["title"] == "整理已發布作業"
        )
        assert "需要重新設定發佈班級" in setup_action["description"]

    async def test_teacher_workbench_does_not_count_missing_target_group_as_unsubmitted(
        self, db, org, assignment, assignment_task, admin_user
    ):
        assignment.published = True
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = [999_501]
        assignment.due_date = str((datetime.now().date() + timedelta(days=3)).isoformat())
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)
        gradebook_payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["published_without_targets"] == 1
        assert payload["summary"]["published_empty_target_usergroups"] == 0
        assert payload["summary"]["expected_unsubmitted"] == 0
        assert payload["missing_submissions"] == []
        assert gradebook_payload["summary"]["unsubmitted"] == 0
        assert gradebook_payload["rows"] == []

    async def test_teacher_workbench_lists_missing_student_submissions(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.due_date = str((datetime.now().date() + timedelta(days=3)).isoformat())
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            43,
            regular_user,
            "usergroup_workbench_missing_student",
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["expected_unsubmitted"] == 1
        assert payload["summary"]["due_soon"] == 1
        assert len(payload["missing_submissions"]) == 1
        assert len(payload["due_soon_assignments"]) == 1
        missing = payload["missing_submissions"][0]
        assert missing["student_email"] == regular_user.email
        assert missing["assignment_uuid"] == assignment.assignment_uuid
        assert missing["submission_status"] == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
        assert payload["due_soon_assignments"][0]["assignment_uuid"] == assignment.assignment_uuid

    async def test_teacher_workbench_counts_draft_without_missing_students(
        self, db, org, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["drafts"] == 1
        assert payload["summary"]["expected_unsubmitted"] == 0
        assert payload["missing_submissions"] == []

    async def test_teacher_workbench_prompts_student_csv_import_when_no_learners(
        self, db, org, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["learner_count"] == 0
        assert payload["ai_actions"][0]["title"] == "匯入學生帳號"
        import_action = next(
            action for action in payload["ai_actions"]
            if action["title"] == "匯入學生帳號"
        )
        assert import_action["priority"] == "high"
        assert import_action["href"] == "/dash/users/settings/add"
        assert "CSV" in import_action["description"]
        assert "提交、批改和成績記錄" in import_action["description"]

    async def test_teacher_workbench_does_not_prompt_student_import_when_learners_exist(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["learner_count"] == 1
        assert not any(
            action["title"] == "匯入學生帳號"
            for action in payload["ai_actions"]
        )

    async def test_teacher_workbench_counts_unscored_self_tests_as_usage_evidence_without_average(
        self, db, org, assignment, regular_user, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        db.add(
            SelfTestAttempt(
                id=702,
                attempt_uuid="selftest_workbench_student_usage_evidence",
                org_id=org.id,
                user_id=regular_user.id,
                question_count=5,
                status=SelfTestAttemptStatus.SUBMITTED,
                score=450,
                max_score=500,
                percentage=90,
                counts_for_grade=False,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
                submitted_at=str(datetime.now()),
            )
        )
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["student_count"] == 1
        assert payload["summary"]["engaged_student_count"] == 1
        assert payload["summary"]["engagement_rate"] == 100.0
        assert payload["summary"]["self_test_rows"] == 1
        assert payload["summary"]["submitted"] == 1
        assert payload["summary"]["graded"] == 1
        assert payload["summary"]["scored_records"] == 0
        assert payload["summary"]["average_score"] is None
        assert payload["summary"]["pending_review"] == 0
        assert payload["summary"]["expected_unsubmitted"] == 0
        assert payload["pending_review_submissions"] == []
        assert payload["missing_submissions"] == []

    async def test_teacher_workbench_warns_when_self_test_bank_is_empty(
        self, db, org, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["self_test_bank_ready"] is False
        assert payload["summary"]["self_test_ready_question_count"] == 0
        assert payload["summary"]["self_test_min_question_count"] == 3
        self_test_action = next(
            action for action in payload["ai_actions"]
            if action["title"] == "準備自測題庫"
        )
        assert self_test_action["priority"] == "normal"
        assert "加分項" in self_test_action["description"]
        assert "不影響先跑通作業、提交和批改" in self_test_action["description"]

    async def test_teacher_workbench_requires_three_ready_self_test_bank_items_for_ready_status(
        self, db, org, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        db.add(_ready_self_test_bank_item(org.id, "Ready self test item"))
        db.add(_incomplete_self_test_bank_item(org.id, "Broken self test item"))
        db.add(_fallback_self_test_bank_item(org.id, "AI 備用短問答 1"))
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["self_test_bank_ready"] is False
        assert payload["summary"]["self_test_ready_question_count"] == 1
        assert payload["summary"]["self_test_min_question_count"] == 3
        assert not any(
            action["title"] == "準備自測題庫"
            for action in payload["ai_actions"]
        )
        supplement_action = next(
            action for action in payload["ai_actions"]
            if action["title"] == "補充自測題庫"
        )
        assert supplement_action["priority"] == "normal"
        assert "目前只有 1 題" in supplement_action["description"]

    async def test_teacher_workbench_marks_self_test_bank_ready_after_three_ready_items(
        self, db, org, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        db.add(_ready_self_test_bank_item(org.id, "Ready self test item 1"))
        db.add(_ready_self_test_bank_item(org.id, "Ready self test item 2"))
        db.add(_ready_self_test_bank_item(org.id, "Ready self test item 3"))
        db.add(_incomplete_self_test_bank_item(org.id, "Broken self test item"))
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["self_test_bank_ready"] is True
        assert payload["summary"]["self_test_ready_question_count"] == 3
        assert payload["summary"]["self_test_min_question_count"] == 3
        assert not any(
            action["title"] in {"準備自測題庫", "補充自測題庫"}
            for action in payload["ai_actions"]
        )

    async def test_teacher_workbench_counts_only_published_ready_simple_auto_graded_assignments(
        self, db, org, course, chapter, activity, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = [501]
        ready_group = UserGroup(
            id=501,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_ready_pilot_assignment",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        draft_assignment = Assignment(
            id=210,
            title="Draft simple assignment",
            description="Draft should not count in principal demo metrics",
            due_date="2030-01-02",
            published=False,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_draft_simple",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        draft_task = AssignmentTask(
            id=211,
            title="Draft simple task",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "澳門在哪個國家？", "correct_answers": ["中國"]},
            max_grade_value=100,
            assignment_id=210,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_draft_simple",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        manual_assignment = Assignment(
            id=212,
            title="Published manual assignment",
            description="Published but not simple auto-gradable",
            due_date="2030-01-03",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_published_manual",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        manual_task = AssignmentTask(
            id=213,
            title="Upload worksheet",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
            contents={},
            max_grade_value=100,
            assignment_id=212,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_published_manual",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        code_assignment = Assignment(
            id=214,
            title="Published code assignment",
            description="Published and auto-gradable, but not simple pilot-ready",
            due_date="2030-01-04",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_published_code",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        code_task = AssignmentTask(
            id=215,
            title="Run Python",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.CODE,
            contents={"language_id": 71, "test_cases": []},
            max_grade_value=100,
            assignment_id=214,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_published_code",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        incomplete_simple_assignment = Assignment(
            id=216,
            title="Published incomplete simple assignment",
            description="Published simple type but missing answer key",
            due_date="2030-01-05",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_published_incomplete_simple",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        incomplete_simple_task = AssignmentTask(
            id=217,
            title="Incomplete short answer",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "澳門在哪個國家？"},
            max_grade_value=100,
            assignment_id=216,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_published_incomplete_simple",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        no_retry_assignment = Assignment(
            id=218,
            title="Published simple assignment without learning retry settings",
            description="Published simple task but students cannot retry or review answers",
            due_date="2030-01-06",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            allow_retries=False,
            show_correct_answers=False,
            score_policy="latest",
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_published_no_retry",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        no_retry_task = AssignmentTask(
            id=219,
            title="Complete simple task without retry settings",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "澳門在哪個國家？", "correct_answers": ["中國"]},
            max_grade_value=100,
            assignment_id=218,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_published_no_retry",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        invalid_target_assignment = Assignment(
            id=220,
            title="Published simple assignment with invalid target",
            description="Published simple task but target group does not exist",
            due_date="2030-01-07",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            allow_retries=True,
            show_correct_answers=True,
            score_policy="highest",
            target_usergroup_ids=[999501],
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_published_invalid_target",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        invalid_target_task = AssignmentTask(
            id=221,
            title="Complete simple task with invalid target",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "澳門在哪個國家？", "correct_answers": ["中國"]},
            max_grade_value=100,
            assignment_id=220,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_published_invalid_target",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        invalid_due_assignment = Assignment(
            id=222,
            title="Published simple assignment with invalid due date",
            description="Published simple task but due date is invalid",
            due_date="不是日期",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True,
            allow_retries=True,
            show_correct_answers=True,
            score_policy="highest",
            target_usergroup_ids=[501],
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_published_invalid_due_date",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        invalid_due_task = AssignmentTask(
            id=223,
            title="Complete simple task with invalid due date",
            description="",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "澳門在哪個國家？", "correct_answers": ["中國"]},
            max_grade_value=100,
            assignment_id=222,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_published_invalid_due_date",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(ready_group)
        db.add(
            UserGroupUser(
                usergroup_id=501,
                user_id=regular_user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(assignment)
        db.add(draft_assignment)
        db.add(draft_task)
        db.add(manual_assignment)
        db.add(manual_task)
        db.add(code_assignment)
        db.add(code_task)
        db.add(incomplete_simple_assignment)
        db.add(incomplete_simple_task)
        db.add(no_retry_assignment)
        db.add(no_retry_task)
        db.add(invalid_target_assignment)
        db.add(invalid_target_task)
        db.add(invalid_due_assignment)
        db.add(invalid_due_task)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["published"] == 7
        assert payload["summary"]["drafts"] == 1
        assert payload["summary"]["auto_graded_assignments"] == 1
        assert payload["summary"]["simple_auto_graded_assignments"] == 1
        assert payload["summary"]["simple_pilot_ready_assignments"] == 1
        assert payload["summary"]["published_needing_setup"] == 6
        assert payload["summary"]["published_without_targets"] == 5
        assert payload["summary"]["published_empty_target_usergroups"] == 0
        assert payload["summary"]["published_hidden_from_students"] == 0
        assert payload["summary"]["pilot_status"] == "setup_needed"
        assert payload["summary"]["pilot_status_label"] == "需要整理"
        assert "需重設班級" in payload["summary"]["pilot_status_detail"]
        gradebook_payload = await read_assignment_gradebook(org.id, admin_user, db)
        assert gradebook_payload["summary"]["published"] == 7
        assert gradebook_payload["summary"]["auto_graded_assignments"] == 1
        assert gradebook_payload["summary"]["simple_auto_graded_assignments"] == 1
        assert gradebook_payload["summary"]["simple_pilot_ready_assignments"] == 1
        assert gradebook_payload["summary"]["published_needing_setup"] == 6
        assert gradebook_payload["summary"]["published_without_targets"] == 5
        assert gradebook_payload["summary"]["published_empty_target_usergroups"] == 0
        assert gradebook_payload["summary"]["published_hidden_from_students"] == 0
        assert gradebook_payload["summary"]["pilot_status"] == "setup_needed"
        assert gradebook_payload["summary"]["pilot_status_label"] == "需要整理"
        setup_action = next(
            action
            for action in payload["ai_actions"]
            if action["title"] == "整理已發布作業"
        )
        assert setup_action["priority"] == "high"
        assert "6 份已發布作業" in setup_action["description"]
        assert "有效截止日期" in setup_action["description"]
        assert "選擇、填空、短問答" in setup_action["description"]
        assert "可重做" in setup_action["description"]
        assert "最高分計分" in setup_action["description"]

    async def test_teacher_workbench_does_not_count_empty_target_usergroup_as_ready(
        self, db, org, assignment, assignment_task, admin_user, regular_user
    ):
        assignment.published = True
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = [503]
        assignment.due_date = str((datetime.now().date() + timedelta(days=3)).isoformat())
        db.add(
            UserGroup(
                id=503,
                org_id=org.id,
                name="小四空班",
                description="",
                usergroup_uuid="usergroup_empty_pilot_assignment",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(assignment)
        await db.commit()

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)
        gradebook_payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["simple_pilot_ready_assignments"] == 0
        assert payload["summary"]["published_needing_setup"] == 1
        assert payload["summary"]["published_without_targets"] == 1
        assert payload["summary"]["published_empty_target_usergroups"] == 1
        assert payload["summary"]["expected_unsubmitted"] == 0
        assert payload["summary"]["due_soon"] == 0
        assert "班級沒有學生" in payload["summary"]["pilot_status_detail"]
        assert payload["missing_submissions"] == []
        assert payload["due_soon_assignments"] == []
        target_action = next(
            action
            for action in payload["ai_actions"]
            if action["title"] == "設定發佈班級"
        )
        assert "班級沒有學生" in target_action["description"]
        assert "把學生加入班級" in target_action["description"]
        assert gradebook_payload["summary"]["simple_pilot_ready_assignments"] == 0
        assert gradebook_payload["summary"]["published_needing_setup"] == 1
        assert gradebook_payload["summary"]["published_without_targets"] == 1
        assert gradebook_payload["summary"]["published_empty_target_usergroups"] == 1
        assert gradebook_payload["summary"]["actionable_rows"] == 0
        assert gradebook_payload["rows"] == []
        csv_text = assignment_gradebook_to_csv(gradebook_payload)
        assert "班級需重設或需加學生作業" in csv_text
        assert "班級沒有學生作業" in csv_text

    async def test_teacher_workbench_exposes_ai_assignment_configuration_status(
        self, db, org, assignment, assignment_task, admin_user, monkeypatch
    ):
        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_status",
            lambda: {
                "ready": False,
                "provider": "openai_compatible",
                "model": "",
                "message": "AI 出題尚未配置完整：請設定 LEARNHOUSE_OPENAI_MODEL。",
            },
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        assert payload["summary"]["ai_assignment_ready"] is False
        assert payload["summary"]["ai_assignment_provider"] == "openai_compatible"
        assert payload["summary"]["ai_assignment_model"] == ""
        assert "LEARNHOUSE_OPENAI_MODEL" in payload["summary"]["ai_assignment_message"]
        ai_action = next(
            action for action in payload["ai_actions"]
            if action["title"] == "簡單出題"
        )
        assert ai_action["priority"] == "high"
        assert "備用" not in ai_action["description"]
        assert "如要使用 AI 出題再配置端點" in ai_action["description"]
        assert "正式試行前請先配置好端點" not in ai_action["description"]
        assert "題庫" in ai_action["description"]
        assert "手動建立" in ai_action["description"]

    async def test_teacher_workbench_deprioritizes_ai_configuration_after_simple_assignment_exists(
        self, db, org, assignment, assignment_task, admin_user, regular_user, monkeypatch
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.target_usergroup_ids = [502]
        db.add(
            UserGroup(
                id=502,
                org_id=org.id,
                name="小四乙",
                description="",
                usergroup_uuid="usergroup_ready_pilot_assignment_ai_deprioritized",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=502,
                user_id=regular_user.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(assignment)
        await db.commit()

        monkeypatch.setattr(
            "src.services.courses.activities.assignments.assignment_ai_status",
            lambda: {
                "ready": False,
                "provider": "openai_compatible",
                "model": "",
                "message": "AI 出題尚未配置完整：請設定 LEARNHOUSE_OPENAI_MODEL。",
            },
        )

        payload = await read_teacher_assignment_workbench(org.id, admin_user, db)

        ai_action = next(
            action for action in payload["ai_actions"]
            if action["title"] == "簡單出題"
        )
        assert payload["summary"]["simple_auto_graded_assignments"] == 1
        assert payload["summary"]["simple_pilot_ready_assignments"] == 1
        assert ai_action["title"] == "簡單出題"
        assert ai_action["priority"] == "normal"
        assert "核心作業閉環已有校內試行就緒作業" in ai_action["description"]
        assert "正式試行前請先配置好端點" not in ai_action["description"]

    async def test_gradebook_uses_submission_review_status_per_student(
        self, db, org, assignment, assignment_task, user_submission, admin_user
    ):
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        user_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
        user_submission.teacher_review_status = "confirmed"
        db.add(assignment)
        db.add(user_submission)
        await db.commit()

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["pending_review"] == 0
        assert payload["rows"][0]["teacher_review_status"] == "confirmed"

    async def test_gradebook_marks_graded_submission_late_when_submitted_after_due_date(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        assignment.due_date = "2000-01-01"
        user_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
        db.add(assignment)
        db.add(user_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            57,
            regular_user,
            "usergroup_gradebook_late_submission",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["late"] == 1
        assert payload["rows"][0]["late"] is True

    async def test_gradebook_uses_latest_attempt_when_duplicate_submission_rows_exist(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        earlier_time = str(datetime.now() - timedelta(days=1))
        later_time = str(datetime.now())
        user_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
        user_submission.grade = 20
        user_submission.attempt_number = 1
        user_submission.best_grade = 20
        user_submission.best_attempt_number = 1
        user_submission.update_date = earlier_time
        duplicate_submission = AssignmentUserSubmission(
            user_id=regular_user.id,
            assignment_id=assignment.id,
            grade=90,
            assignmentusersubmission_uuid="assignmentusersubmission_duplicate_latest_gradebook",
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            teacher_review_status="not_required",
            attempt_number=2,
            best_grade=90,
            best_attempt_number=2,
            creation_date=later_time,
            update_date=later_time,
        )
        db.add(assignment)
        db.add(user_submission)
        db.add(duplicate_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            59,
            regular_user,
            "usergroup_gradebook_duplicate_submission",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        row = payload["rows"][0]
        assert row["grade"] == 90
        assert row["attempt_number"] == 2
        assert row["best_grade"] == 90
        assert payload["summary"]["average_score"] == 90.0

    async def test_gradebook_does_not_count_retry_in_progress_as_unsubmitted_or_pending_review(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        user_submission.teacher_review_status = "pending"
        user_submission.grade = 0
        user_submission.attempt_number = 2
        user_submission.best_grade = 80
        user_submission.best_attempt_number = 1
        db.add(assignment)
        db.add(user_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            58,
            regular_user,
            "usergroup_gradebook_retry_in_progress",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["pending_review"] == 0
        assert payload["summary"]["unsubmitted"] == 0
        assert payload["summary"]["retry_in_progress"] == 1
        assert payload["summary"]["submission_rate"] == 0.0
        assert payload["summary"]["participation_records"] == 1
        assert payload["summary"]["participation_rate"] == 100.0
        assert payload["summary"]["engagement_rate"] == 100.0
        assert payload["summary"]["attention_count"] == 0
        assert payload["summary"]["average_score"] is None
        assert payload["rows"][0]["best_grade"] == 80
        assert payload["rows"][0]["best_attempt_number"] == 1
        assert payload["rows"][0]["teacher_review_status"] == "missing"
        assert payload["rows"][0]["attention_reason"] == ""

        csv_text = assignment_gradebook_to_csv(payload)
        assert "重做中" in csv_text
        assert "等待重新提交" in csv_text
        assert "80/100" in csv_text
        csv_rows = _gradebook_csv_detail_rows(csv_text)
        assert csv_rows[0]["分數"] == "重做中"
        assert csv_rows[0]["參與狀態"] == "重做中"
        assert csv_rows[0]["最高分"] == "80/100"

    async def test_gradebook_counts_overdue_retry_in_progress_as_attention(
        self, db, org, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        assignment.due_date = "2000-01-01"
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        db.add(assignment)
        db.add(user_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            59,
            regular_user,
            "usergroup_gradebook_overdue_retry",
        )

        payload = await read_assignment_gradebook(org.id, admin_user, db)

        assert payload["summary"]["attention_count"] == 1
        assert payload["summary"]["unsubmitted"] == 0
        assert payload["summary"]["retry_in_progress"] == 1
        assert payload["rows"][0]["late"] is True
        assert payload["rows"][0]["attention_reason"] == "重做中，已逾期"

    async def test_school_operations_summary_defaults_to_macau_week_and_suppresses_small_cohort(
        self,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        admin_user,
        regular_user,
    ):
        assignment.due_date = "2026-07-15"
        assignment.subject = "數學"
        assignment.auto_grading = True
        graded_submission.creation_date = "2026-07-14 08:00:00"
        graded_submission.update_date = "2026-07-14 08:10:00"
        db.add(assignment)
        db.add(graded_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            590,
            regular_user,
            "usergroup_operations_small_cohort",
        )

        payload = await read_school_operations_summary(
            org.id,
            admin_user,
            db,
            include_self_tests=False,
            today=date(2026, 7, 15),
        )

        assert payload["scope"]["start_date"] == "2026-07-13"
        assert payload["scope"]["end_date"] == "2026-07-19"
        assert payload["scope"]["timezone"] == "Asia/Macau"
        assert payload["metrics"]["learner_count"] == 1
        assert payload["metrics"]["record_count"] == 1
        assert payload["metrics"]["graded"] == 1
        assert payload["metrics"]["auto_graded_records"] == 1
        assert payload["metrics"]["submission_rate"] is None
        assert payload["metrics"]["average_score"] is None
        assert payload["privacy"]["suppressed"] is True
        assert payload["workload"]["estimated_teacher_minutes_saved"] == 2
        assert payload["workload"]["ai_usage_tracking"] == "not_recorded"
        serialized = json.dumps(payload, ensure_ascii=False)
        assert regular_user.email not in serialized
        assert "Regular User" not in serialized
        assert "Good work" not in serialized

        csv_text = school_operations_summary_to_csv(payload)
        assert csv_text.startswith("\ufeff欄位,數值")
        assert "已隱藏小群組敏感指標" in csv_text
        assert regular_user.email not in csv_text
        assert "Good work" not in csv_text
        filename = school_operations_summary_csv_filename(payload)
        assert filename == (
            "learnhouse-operations-summary-org-1-2026-07-13-2026-07-19.csv"
        )

    async def test_school_operations_summary_reveals_aggregate_metrics_at_privacy_threshold(
        self,
        db,
        org,
        assignment,
        assignment_task,
        graded_submission,
        admin_user,
        regular_user,
        user_role,
    ):
        assignment.due_date = "2026-07-16"
        assignment.subject = "數學"
        assignment.education_stage = "小學"
        assignment.grade_level = "小四"
        assignment.school_year = "2026/2027"
        assignment.term = "第一學期"
        assignment.auto_grading = True
        graded_submission.creation_date = "2026-07-14 09:00:00"
        graded_submission.update_date = "2026-07-14 09:05:00"
        db.add(assignment)
        db.add(graded_submission)
        await db.commit()
        group = await _target_assignment_to_group(
            db,
            org,
            assignment,
            591,
            regular_user,
            "usergroup_operations_threshold",
        )
        for offset in range(4):
            user_id = 710 + offset
            learner = User(
                id=user_id,
                username=f"operations-learner-{offset}",
                first_name="測試",
                last_name=f"學生{offset}",
                email=f"operations-learner-{offset}@example.com",
                password="hashed_password",
                user_uuid=f"user_operations_learner_{offset}",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
            db.add(learner)
            db.add(
                UserOrganization(
                    user_id=user_id,
                    org_id=org.id,
                    role_id=user_role.id,
                    creation_date=str(datetime.now()),
                    update_date=str(datetime.now()),
                )
            )
            db.add(
                UserGroupUser(
                    usergroup_id=group.id,
                    user_id=user_id,
                    org_id=org.id,
                    creation_date=str(datetime.now()),
                    update_date=str(datetime.now()),
                )
            )
        await db.commit()

        payload = await read_school_operations_summary(
            org.id,
            admin_user,
            db,
            start_date=date(2026, 7, 13),
            end_date=date(2026, 7, 19),
            course_id=assignment.course_id,
            usergroup_id=group.id,
            subject=" 數學 ",
            education_stage="小學",
            grade_level="小四",
            school_year="2026/2027",
            term="第一學期",
            include_self_tests=False,
        )

        assert payload["metrics"]["learner_count"] == 5
        assert payload["metrics"]["record_count"] == 1
        assert payload["metrics"]["expected_records"] == 5
        assert payload["metrics"]["engaged_student_count"] == 1
        assert payload["metrics"]["engagement_rate"] == 20.0
        assert payload["metrics"]["submitted"] == 1
        assert payload["metrics"]["submission_rate"] == 20.0
        assert payload["metrics"]["graded"] == 1
        assert payload["metrics"]["unsubmitted"] == 4
        assert payload["metrics"]["average_score"] == 85.0
        assert payload["metrics"]["low_score_records"] == 0
        assert payload["privacy"]["suppressed"] is False
        assert payload["scope"]["subject"] == "數學"
        assert payload["available_filters"]["subjects"] == ["數學"]
        assert payload["available_filters"]["education_stages"] == ["小學"]
        assert payload["available_filters"]["grade_levels"] == ["小四"]
        assert payload["available_filters"]["school_years"] == ["2026/2027"]
        assert payload["available_filters"]["terms"] == ["第一學期"]
        assert payload["summary"]["status"] == "active_needs_followup"
        assert "提交率 20%" in payload["summary"]["principal_brief"]

        empty_period = await read_school_operations_summary(
            org.id,
            admin_user,
            db,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 7),
            usergroup_id=group.id,
            include_self_tests=False,
        )
        assert empty_period["metrics"]["learner_count"] == 5
        assert empty_period["metrics"]["record_count"] == 0
        assert empty_period["summary"]["status"] == "no_activity"

    async def test_school_operations_summary_rejects_unauthorized_and_foreign_filters(
        self,
        db,
        org,
        other_org,
        course,
        admin_user,
        regular_user,
    ):
        token_user = APITokenUser(id=admin_user.id, org_id=org.id)
        with pytest.raises(HTTPException) as token_error:
            await read_school_operations_summary(org.id, token_user, db)
        assert token_error.value.status_code == 403

        with pytest.raises(HTTPException) as role_error:
            await read_school_operations_summary(org.id, regular_user, db)
        assert role_error.value.status_code == 403

        foreign_course = Course(
            id=902,
            name="Other Org Course",
            description="",
            public=True,
            published=True,
            open_to_contributors=False,
            org_id=other_org.id,
            course_uuid="course_operations_other_org",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        foreign_group = UserGroup(
            id=903,
            org_id=other_org.id,
            name="Other Org Group",
            description="",
            usergroup_uuid="usergroup_operations_other_org",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(foreign_course)
        db.add(foreign_group)
        await db.commit()

        with pytest.raises(HTTPException) as course_error:
            await read_school_operations_summary(
                org.id,
                admin_user,
                db,
                course_id=foreign_course.id,
            )
        assert course_error.value.status_code == 404
        assert course_error.value.detail == "找不到指定課程。"

        with pytest.raises(HTTPException) as group_error:
            await read_school_operations_summary(
                org.id,
                admin_user,
                db,
                usergroup_id=foreign_group.id,
            )
        assert group_error.value.status_code == 404
        assert group_error.value.detail == "找不到指定班級/群組。"

        with pytest.raises(HTTPException) as range_error:
            await read_school_operations_summary(
                org.id,
                admin_user,
                db,
                start_date=date(2025, 1, 1),
                end_date=date(2026, 1, 2),
            )
        assert range_error.value.status_code == 400
        assert "最多可查看 366 日" in range_error.value.detail


# ---------------------------------------------------------------------------
# delete_assignment
# ---------------------------------------------------------------------------


class TestDeleteAssignment:
    async def test_raises_404_when_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_deletes_assignment(
        self, mock_request, db, assignment, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            result = await delete_assignment(
                mock_request, assignment.assignment_uuid, admin_user, db
            )
        assert result["message"] == "Assignment deleted"
        remaining = (await db.execute(
            select(Assignment).where(Assignment.id == assignment.id)
        )).scalars().first()
        assert remaining is None

    async def test_rejects_deleting_assignment_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment(
                    mock_request, assignment.assignment_uuid, admin_user, db
                )

        assert exc.value.status_code == 400
        assert "不能刪除" in exc.value.detail
        assert "提交記錄" in exc.value.detail

        remaining = (await db.execute(
            select(Assignment).where(Assignment.id == assignment.id)
        )).scalars().first()
        assert remaining is not None


# ---------------------------------------------------------------------------
# delete_assignment_from_activity_uuid
# ---------------------------------------------------------------------------


class TestDeleteAssignmentFromActivityUuid:
    async def test_raises_404_when_activity_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_from_activity_uuid(
                    mock_request, "nonexistent-activity", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, activity, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_from_activity_uuid(
                    mock_request, activity.activity_uuid, admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_deletes_assignment_by_activity_uuid(
        self, mock_request, db, assignment, activity, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            result = await delete_assignment_from_activity_uuid(
                mock_request, activity.activity_uuid, admin_user, db
            )
        assert result["message"] == "Assignment deleted"

    async def test_rejects_deleting_assignment_by_activity_uuid_after_student_submission(
        self, mock_request, db, assignment, activity, user_submission, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DECREASE, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_from_activity_uuid(
                    mock_request, activity.activity_uuid, admin_user, db
                )

        assert exc.value.status_code == 400
        assert "不能刪除" in exc.value.detail
        assert "提交記錄" in exc.value.detail

        remaining = (await db.execute(
            select(Assignment).where(Assignment.id == assignment.id)
        )).scalars().first()
        assert remaining is not None


# ---------------------------------------------------------------------------
# create_assignment_task
# ---------------------------------------------------------------------------


class TestCreateAssignmentTask:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user
    ):
        obj = AssignmentTaskCreate(
            title="T",
            description="D",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={},
            max_grade_value=10,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(mock_request, "nonexistent", obj, admin_user, db)
        assert exc.value.status_code == 404

    async def test_creates_task_successfully(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="New Task",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await create_assignment_task(
                mock_request, assignment.assignment_uuid, obj, admin_user, db
            )
        assert isinstance(result, AssignmentTaskRead)
        assert result.title == "New Task"

    async def test_create_assignment_task_trims_title(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="  第一題   分數比較  ",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await create_assignment_task(
                mock_request, assignment.assignment_uuid, obj, admin_user, db
            )

        assert result.title == "第一題 分數比較"

    async def test_rejects_creating_assignment_task_with_blank_title(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="   ",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(
                    mock_request, assignment.assignment_uuid, obj, admin_user, db
                )

        assert exc.value.status_code == 400
        assert "請輸入題目名稱" in exc.value.detail

    async def test_rejects_creating_assignment_task_with_non_positive_max_grade(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="Invalid Max Grade",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=-1,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(
                    mock_request, assignment.assignment_uuid, obj, admin_user, db
                )

        assert exc.value.status_code == 400
        assert "題目分值必須大於 0" in exc.value.detail

    async def test_rejects_creating_fourth_task_for_simple_pilot(
        self, mock_request, db, org, course, chapter, activity, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        for index in range(2):
            db.add(
                AssignmentTask(
                    title=f"Existing Task {index + 1}",
                    description="Desc",
                    hint="",
                    reference_file=None,
                    assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
                    contents={
                        "prompt": f"What is item {index + 1}?",
                        "correct_answers": [str(index + 1)],
                        "match_mode": "exact",
                    },
                    max_grade_value=100,
                    assignment_id=assignment.id,
                    org_id=org.id,
                    course_id=course.id,
                    chapter_id=chapter.id,
                    activity_id=activity.id,
                    assignment_task_uuid=f"assignmenttask_existing_limit_{index + 1}",
                    creation_date=str(datetime.now()),
                    update_date=str(datetime.now()),
                )
            )
        await db.commit()

        obj = AssignmentTaskCreate(
            title="Fourth Task",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(
                    mock_request,
                    assignment.assignment_uuid,
                    obj,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "最多 3 題" in exc.value.detail
        assert "另一份簡單作業" in exc.value.detail

    async def test_rejects_creating_task_after_student_submission(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        obj = AssignmentTaskCreate(
            title="Late New Task",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(
                    mock_request,
                    assignment.assignment_uuid,
                    obj,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已有學生提交這份作業" in exc.value.detail

    async def test_allows_creating_task_when_only_retry_pending_submission_exists(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        assignment.published = False
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        db.add(assignment)
        db.add(user_submission)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="Retry Practice",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await create_assignment_task(
                mock_request,
                assignment.assignment_uuid,
                obj,
                admin_user,
                db,
            )

        assert result.title == "Retry Practice"

    async def test_rejects_creating_task_on_published_assignment(
        self, mock_request, db, assignment, admin_user
    ):
        obj = AssignmentTaskCreate(
            title="Published New Task",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "x", "correct_answers": ["x"], "match_mode": "exact"},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(
                    mock_request,
                    assignment.assignment_uuid,
                    obj,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已發布作業不能新增題目" in exc.value.detail
        assert "避免學生作答期間題目變動" in exc.value.detail

    async def test_allows_creating_incomplete_simple_task_on_draft_assignment(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="Draft Short Answer",
            description="Desc",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={},
            max_grade_value=50,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await create_assignment_task(
                mock_request,
                assignment.assignment_uuid,
                obj,
                admin_user,
                db,
            )

        assert result.title == "Draft Short Answer"
        assert result.contents == {}

    async def test_creating_manual_task_disables_auto_grading_and_requires_review(
        self, mock_request, db, assignment, admin_user
    ):
        assignment.published = False
        assignment.auto_grading = True
        assignment.teacher_review_required = False
        assignment.teacher_review_status = "not_required"
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskCreate(
            title="Upload File",
            description="Upload a worksheet",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
            contents={},
            max_grade_value=100,
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await create_assignment_task(
                mock_request, assignment.assignment_uuid, obj, admin_user, db
            )

        await db.refresh(assignment)
        assert result.assignment_type == AssignmentTaskTypeEnum.FILE_SUBMISSION
        assert assignment.auto_grading is False
        assert assignment.teacher_review_required is True
        assert assignment.teacher_review_status == "pending"


# ---------------------------------------------------------------------------
# read_assignment_tasks
# ---------------------------------------------------------------------------


class TestReadAssignmentTasks:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_tasks(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_returns_task_list(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment_tasks(
                mock_request, assignment.assignment_uuid, admin_user, db
            )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].assignment_task_uuid == assignment_task.assignment_task_uuid

    async def test_student_web_task_payload_redacts_solution_and_hidden_checks(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4048,
            regular_user,
            "usergroup_web_payload_redaction",
        )
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "mode": "web_preview",
            "starter_html": "<main></main>",
            "solution_html": "SOLUTION_HTML_SENTINEL",
            "solution_css": "SOLUTION_CSS_SENTINEL",
            "solution_js": "SOLUTION_JS_SENTINEL",
            "web_checks": [
                {"id": "visible", "pattern": "main", "hidden": False},
                {"id": "hidden", "pattern": "HIDDEN_CHECK_SENTINEL", "hidden": True},
            ],
        }
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False
        ):
            result = await read_assignment_tasks(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        encoded = json.dumps(result[0].contents)
        assert "SOLUTION_" not in encoded
        assert "HIDDEN_CHECK_SENTINEL" not in encoded
        assert [check["id"] for check in result[0].contents["web_checks"]] == ["visible"]

    async def test_student_code_task_list_redacts_solution_and_hidden_tests_without_mutation(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4050,
            regular_user,
            "usergroup_code_payload_redaction",
        )
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "language_id": 71,
            "solution_code": "SOLUTION_SENTINEL",
            "test_cases": [
                {
                    "id": "visible",
                    "stdin": "1",
                    "expectedStdout": "1",
                    "hidden": False,
                },
                {
                    "id": "hidden",
                    "stdin": "SECRET_INPUT",
                    "expectedStdout": "SECRET_OUTPUT",
                    "hidden": True,
                },
            ],
            "show_hidden_test_count": True,
        }
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False
        ):
            result = await read_assignment_tasks(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        encoded = json.dumps(result[0].contents)
        assert "SOLUTION_SENTINEL" not in encoded
        assert "SECRET_INPUT" not in encoded
        assert "SECRET_OUTPUT" not in encoded
        assert [case["id"] for case in result[0].contents["test_cases"]] == ["visible"]
        assert result[0].contents["hidden_test_count"] == 1
        assert assignment_task.contents["solution_code"] == "SOLUTION_SENTINEL"
        assert [case["id"] for case in assignment_task.contents["test_cases"]] == [
            "visible",
            "hidden",
        ]

    async def test_teacher_code_task_list_preserves_solution_and_hidden_tests(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "language_id": 71,
            "solution_code": "TEACHER_SOLUTION_SENTINEL",
            "test_cases": [
                {
                    "id": "hidden",
                    "stdin": "TEACHER_HIDDEN_INPUT",
                    "expectedStdout": "TEACHER_HIDDEN_OUTPUT",
                    "hidden": True,
                }
            ],
        }
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True
        ):
            result = await read_assignment_tasks(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        encoded = json.dumps(result[0].contents)
        assert "TEACHER_SOLUTION_SENTINEL" in encoded
        assert "TEACHER_HIDDEN_INPUT" in encoded
        assert "TEACHER_HIDDEN_OUTPUT" in encoded

    async def test_teacher_web_task_payload_preserves_private_authoring_fields(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "mode": "web_preview",
            "solution_html": "TEACHER_SOLUTION_SENTINEL",
            "web_checks": [
                {"id": "hidden", "pattern": "TEACHER_HIDDEN_SENTINEL", "hidden": True},
            ],
        }
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True
        ):
            result = await read_assignment_tasks(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        assert result[0].contents["solution_html"] == "TEACHER_SOLUTION_SENTINEL"
        assert result[0].contents["web_checks"][0]["pattern"] == "TEACHER_HIDDEN_SENTINEL"

    async def test_rejects_student_outside_target_usergroup(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4046,
            uuid="usergroup_read_tasks_guard",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_tasks(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "不在發布名單內" in exc.value.detail


# ---------------------------------------------------------------------------
# read_assignment_task
# ---------------------------------------------------------------------------


class TestReadAssignmentTask:
    async def test_raises_404_when_task_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_task(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_returns_task(
        self, mock_request, db, assignment_task, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment_task(
                mock_request, assignment_task.assignment_task_uuid, admin_user, db
            )
        assert isinstance(result, AssignmentTaskRead)
        assert result.assignment_task_uuid == assignment_task.assignment_task_uuid

    async def test_student_code_task_read_redacts_hidden_tests_and_respects_count_flag(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4051,
            regular_user,
            "usergroup_code_singular_redaction",
        )
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "language_id": 71,
            "solution_code": "SOLUTION_SENTINEL",
            "test_cases": [
                {
                    "id": "visible",
                    "stdin": "1",
                    "expectedStdout": "1",
                    "hidden": False,
                },
                {
                    "id": "hidden",
                    "stdin": "SECRET_INPUT",
                    "expectedStdout": "SECRET_OUTPUT",
                    "hidden": True,
                },
            ],
            "show_hidden_test_count": False,
        }
        db.add(assignment_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False
        ):
            result = await read_assignment_task(
                mock_request, assignment_task.assignment_task_uuid, regular_user, db
            )

        encoded = json.dumps(result.contents)
        assert "SOLUTION_SENTINEL" not in encoded
        assert "SECRET_INPUT" not in encoded
        assert "SECRET_OUTPUT" not in encoded
        assert [case["id"] for case in result.contents["test_cases"]] == ["visible"]
        assert "hidden_test_count" not in result.contents
        assert len(assignment_task.contents["test_cases"]) == 2

    async def test_rejects_student_outside_target_usergroup(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4047,
            uuid="usergroup_read_task_guard",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_task(
                    mock_request, assignment_task.assignment_task_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "不在發布名單內" in exc.value.detail


# ---------------------------------------------------------------------------
# update_assignment_task
# ---------------------------------------------------------------------------


class TestUpdateAssignmentTask:
    async def test_raises_404_when_task_not_found(
        self, mock_request, db, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task(
                    mock_request, "nonexistent", AssignmentTaskUpdate(title="X"), admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_updates_task_title(
        self, mock_request, db, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment_task(
                mock_request,
                assignment_task.assignment_task_uuid,
                AssignmentTaskUpdate(title="  Updated   Task  "),
                admin_user,
                db,
            )
        assert result.title == "Updated Task"

    async def test_rejects_updating_assignment_task_to_blank_title(
        self, mock_request, db, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    AssignmentTaskUpdate(title="   "),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "請輸入題目名稱" in exc.value.detail

    async def test_rejects_updating_assignment_task_to_non_positive_max_grade(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate
        assignment.published = False
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    AssignmentTaskUpdate(max_grade_value=0),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "題目分值必須大於 0" in exc.value.detail

    async def test_updating_task_to_manual_type_disables_auto_grading(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate

        assignment.published = False
        assignment.auto_grading = True
        assignment.teacher_review_required = False
        assignment.teacher_review_status = "not_required"
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment_task(
                mock_request,
                assignment_task.assignment_task_uuid,
                AssignmentTaskUpdate(
                    assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
                    contents={},
                ),
                admin_user,
                db,
            )

        await db.refresh(assignment)
        assert result.assignment_type == AssignmentTaskTypeEnum.FILE_SUBMISSION
        assert assignment.auto_grading is False
        assert assignment.teacher_review_required is True
        assert assignment.teacher_review_status == "pending"

    async def test_rejects_grading_structure_update_after_student_submission(
        self, mock_request, db, assignment_task, user_submission, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    AssignmentTaskUpdate(contents={"prompt": "Changed question"}),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已有學生提交這份作業" in exc.value.detail

    async def test_allows_non_grading_task_text_update_after_student_submission(
        self, mock_request, db, assignment_task, user_submission, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment_task(
                mock_request,
                assignment_task.assignment_task_uuid,
                AssignmentTaskUpdate(hint="看清楚題目再回答"),
                admin_user,
                db,
            )

        assert result.hint == "看清楚題目再回答"

    async def test_rejects_updating_published_task_grading_structure(
        self, mock_request, db, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    AssignmentTaskUpdate(
                        assignment_type=AssignmentTaskTypeEnum.QUIZ,
                        contents={
                            "questions": [
                                {
                                    "questionUUID": "q_published_incomplete",
                                    "questionText": "澳門位於哪個國家？",
                                    "options": [
                                        {
                                            "optionUUID": "o_correct",
                                            "text": "中國",
                                            "assigned_right_answer": True,
                                        },
                                        {
                                            "optionUUID": "o_blank",
                                            "text": "",
                                            "assigned_right_answer": False,
                                        },
                                    ],
                                }
                            ]
                        },
                    ),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已發布作業不能修改題型、題目內容或分值" in exc.value.detail
        assert "避免學生作答期間題目變動" in exc.value.detail

    async def test_rejects_updating_published_task_to_valid_new_contents(
        self, mock_request, db, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    AssignmentTaskUpdate(
                        contents={
                            "prompt": "What is 5+5?",
                            "correct_answers": ["10"],
                            "match_mode": "exact",
                        },
                    ),
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已發布作業不能修改題型、題目內容或分值" in exc.value.detail

    async def test_allows_updating_draft_task_to_incomplete_simple_contents(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        from src.db.courses.assignments import AssignmentTaskUpdate

        assignment.published = False
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment_task(
                mock_request,
                assignment_task.assignment_task_uuid,
                AssignmentTaskUpdate(contents={}),
                admin_user,
                db,
            )

        assert result.contents == {}


# ---------------------------------------------------------------------------
# delete_assignment_task
# ---------------------------------------------------------------------------


class TestDeleteAssignmentTask:
    async def test_raises_404_when_task_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_task(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_deletes_task(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await delete_assignment_task(
                mock_request, assignment_task.assignment_task_uuid, admin_user, db
            )
        assert result["message"] == "Assignment Task deleted"

    async def test_rejects_deleting_task_from_published_assignment(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = True
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已發布作業不能刪除題目" in exc.value.detail
        assert "避免學生作答期間題目變動" in exc.value.detail

    async def test_rejects_deleting_task_after_student_submission(
        self, mock_request, db, assignment_task, user_submission, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已有學生提交這份作業" in exc.value.detail

    async def test_rejects_deleting_task_from_published_assignment_when_another_task_remains(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        another_task = AssignmentTask(
            title="Second Task",
            description="Another task",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "What is 3+3?", "correct_answers": ["6"], "match_mode": "exact"},
            max_grade_value=100,
            assignment_id=assignment.id,
            org_id=assignment.org_id,
            course_id=assignment.course_id,
            chapter_id=assignment.chapter_id,
            activity_id=assignment.activity_id,
            assignment_task_uuid="assignmenttask_second_delete_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.published = True
        db.add(assignment)
        db.add(another_task)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_task(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已發布作業不能刪除題目" in exc.value.detail

    async def test_allows_deleting_last_task_from_draft_assignment(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await delete_assignment_task(
                mock_request,
                assignment_task.assignment_task_uuid,
                admin_user,
                db,
            )

        assert result["message"] == "Assignment Task deleted"


# ---------------------------------------------------------------------------
# handle_assignment_task_submission
# ---------------------------------------------------------------------------


class TestHandleAssignmentTaskSubmission:
    async def test_raises_404_when_task_not_found(
        self, mock_request, db, regular_user
    ):
        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "x"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await handle_assignment_task_submission(
                    mock_request, "nonexistent", obj, regular_user, db
                )
        assert exc.value.status_code == 404

    async def test_creates_new_submission(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            61,
            regular_user,
            "usergroup_task_submission_create",
        )
        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "hello"},
        )
        # First call: is_instructor check → False
        # Second call: enrollment check → True (user is enrolled)
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            result = await handle_assignment_task_submission(
                mock_request, assignment_task.assignment_task_uuid, obj, regular_user, db
            )
        assert isinstance(result, AssignmentTaskSubmissionRead)

    async def test_web_submission_persists_only_source_fields(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            65,
            regular_user,
            "usergroup_web_submission_canonicalization",
        )
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.contents = {
            "mode": "web_preview",
            "web_checks": [
                {"id": "hidden", "pattern": "SERVER_EXPECTED_SENTINEL", "hidden": True}
            ],
        }
        db.add(assignment_task)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={
                "mode": "web_preview",
                "html_code": "<main>學生作品</main>",
                "css_code": "main {}",
                "js_code": "",
                "expected_stdout": "CLIENT_EXPECTED_SENTINEL",
                "results": [{"passed": True, "expected_stdout": "CLIENT_RESULT_SENTINEL"}],
            },
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]
        ):
            result = await handle_assignment_task_submission(
                mock_request,
                assignment_task.assignment_task_uuid,
                obj,
                regular_user,
                db,
            )

        assert result.task_submission == {
            "mode": "web_preview",
            "html_code": "<main>學生作品</main>",
            "css_code": "main {}",
            "js_code": "",
        }

    async def test_instructor_cannot_override_server_verified_web_grade(
        self, mock_request, db, assignment_task, task_submission, admin_user
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.CODE
        assignment_task.max_grade_value = 100
        assignment_task.contents = {
            "mode": "web_preview",
            "grading_mode": "custom_weights",
            "web_checks": [
                {
                    "id": "html",
                    "target": "html",
                    "match": "contains",
                    "pattern": "SERVER_HTML_SENTINEL",
                    "hidden": True,
                    "weight": 1,
                },
                {
                    "id": "css",
                    "target": "css",
                    "match": "contains",
                    "pattern": "SERVER_CSS_SENTINEL",
                    "hidden": True,
                    "weight": 3,
                },
            ],
        }
        db.add(assignment_task)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            assignment_task_submission_uuid=task_submission.assignment_task_submission_uuid,
            task_submission={
                "mode": "web_preview",
                "html_code": "<main>SERVER_HTML_SENTINEL</main>",
                "css_code": "body { color: red; }",
                "js_code": "",
                "results": [{"id": "css", "passed": True}],
            },
            grade=100,
            task_submission_grade_feedback="CLIENT_GRADE_SENTINEL",
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), patch(
            _PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True
        ):
            result = await handle_assignment_task_submission(
                mock_request,
                assignment_task.assignment_task_uuid,
                obj,
                admin_user,
                db,
            )

        assert result.grade == 25
        assert result.task_submission_grade_feedback == "伺服器自動批改：得分 25/100。"
        assert "results" not in result.task_submission
        assert "CLIENT_GRADE_SENTINEL" not in result.task_submission_grade_feedback

    async def test_rejects_student_saving_answer_when_assignment_is_unpublished(
        self, mock_request, db, assignment, assignment_task, regular_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "hello"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            with pytest.raises(HTTPException) as exc:
                await handle_assignment_task_submission(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    obj,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "尚未發布" in exc.value.detail

    async def test_rejects_student_saving_answer_when_course_is_unpublished(
        self, mock_request, db, org, assignment, assignment_task, course, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            67,
            regular_user,
            "usergroup_task_submission_hidden_course",
        )
        course.published = False
        db.add(course)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "hello"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            with pytest.raises(HTTPException) as exc:
                await handle_assignment_task_submission(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    obj,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 403
        assert "課程或活動尚未發布" in exc.value.detail

    async def test_rejects_student_saving_answer_when_not_in_target_usergroup(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        group = UserGroup(
            id=4030,
            org_id=org.id,
            name="小五甲",
            description="",
            usergroup_uuid="usergroup_save_target_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [4030]
        db.add(group)
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "hello"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            with pytest.raises(HTTPException) as exc:
                await handle_assignment_task_submission(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    obj,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 403
        assert "不在發布名單內" in exc.value.detail

    async def test_allows_student_saving_answer_when_in_target_usergroup(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        group = UserGroup(
            id=4031,
            org_id=org.id,
            name="小五乙",
            description="",
            usergroup_uuid="usergroup_save_target_allowed",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=4031,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [4031]
        db.add(group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "hello"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            result = await handle_assignment_task_submission(
                mock_request,
                assignment_task.assignment_task_uuid,
                obj,
                regular_user,
                db,
            )

        assert isinstance(result, AssignmentTaskSubmissionRead)

    async def test_allows_teacher_saving_answer_when_assignment_is_unpublished(
        self, mock_request, db, assignment, assignment_task, admin_user
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "teacher preview"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await handle_assignment_task_submission(
                mock_request,
                assignment_task.assignment_task_uuid,
                obj,
                admin_user,
                db,
            )

        assert isinstance(result, AssignmentTaskSubmissionRead)

    async def test_rejects_student_editing_answer_after_final_submission(
        self, mock_request, db, org, assignment, assignment_task, task_submission, user_submission, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            62,
            regular_user,
            "usergroup_task_submission_final",
        )
        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "changed after submit"},
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            with pytest.raises(HTTPException) as exc:
                await handle_assignment_task_submission(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    obj,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "已提交" in exc.value.detail
        assert "重做" in exc.value.detail

        await db.refresh(task_submission)
        assert task_submission.task_submission == {"answer": "4"}

    async def test_allows_student_editing_answer_after_retry_pending_state(
        self, mock_request, db, org, assignment, assignment_task, task_submission, user_submission, regular_user
    ):
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        db.add(user_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            63,
            regular_user,
            "usergroup_task_submission_retry",
        )

        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "changed during retry"},
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            result = await handle_assignment_task_submission(
                mock_request,
                assignment_task.assignment_task_uuid,
                obj,
                regular_user,
                db,
            )

        assert isinstance(result, AssignmentTaskSubmissionRead)
        assert result.task_submission == {"answer": "changed during retry"}

    async def test_updates_existing_submission(
        self, mock_request, db, org, assignment, assignment_task, task_submission, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            64,
            regular_user,
            "usergroup_task_submission_update",
        )
        obj = AssignmentTaskSubmissionUpdate(
            task_submission={"answer": "updated"},
        )
        # is_instructor → False, enrollment → True
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[False, True]):
            result = await handle_assignment_task_submission(
                mock_request, assignment_task.assignment_task_uuid, obj, regular_user, db
            )
        assert isinstance(result, AssignmentTaskSubmissionRead)


# ---------------------------------------------------------------------------
# read_assignment_submissions
# ---------------------------------------------------------------------------


class TestReadAssignmentSubmissions:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_submissions(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_returns_submissions_list(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_assignment_submissions(
                mock_request, assignment.assignment_uuid, admin_user, db
            )
        assert isinstance(result, list)
        assert len(result) == 1

    async def test_instructor_list_does_not_create_missing_rows_without_target_group(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_assignment_submissions(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        assert len(result) == 1
        assert result[0]["user_id"] == user_submission.user_id
        assert result[0]["expected_submission"] is True

    async def test_instructor_list_does_not_create_missing_rows_for_missing_target_group(
        self, mock_request, db, assignment, user_submission, admin_user
    ):
        assignment.target_usergroup_ids = [999403]
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_assignment_submissions(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        assert len(result) == 1
        assert result[0]["user_id"] == user_submission.user_id

    async def test_retry_in_progress_submission_keeps_best_score_evidence(
        self, mock_request, db, assignment, assignment_task, user_submission, admin_user
    ):
        assignment.score_policy = "highest"
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        user_submission.grade = 0
        user_submission.attempt_number = 2
        user_submission.best_grade = 80
        user_submission.best_attempt_number = 1
        db.add(assignment)
        db.add(user_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_assignment_submissions(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        row = result[0]
        assert row["submission_status"] == AssignmentUserSubmissionStatus.PENDING.value
        assert row["grade_display"] is None
        assert row["grade"] == 0
        assert row["best_grade"] == 80
        assert row["best_attempt_number"] == 1
        assert row["max_grade"] == 100
        assert row["score_policy"] == "highest"

    async def test_instructor_list_uses_latest_attempt_when_duplicate_submission_rows_exist(
        self, mock_request, db, assignment, assignment_task, user_submission, admin_user, regular_user
    ):
        earlier_time = str(datetime.now() - timedelta(days=1))
        later_time = str(datetime.now())
        user_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
        user_submission.grade = 30
        user_submission.attempt_number = 1
        user_submission.best_grade = 30
        user_submission.best_attempt_number = 1
        user_submission.update_date = earlier_time
        duplicate_submission = AssignmentUserSubmission(
            user_id=regular_user.id,
            assignment_id=assignment.id,
            grade=95,
            assignmentusersubmission_uuid="assignmentusersubmission_duplicate_latest_list",
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            teacher_review_status="not_required",
            attempt_number=2,
            best_grade=95,
            best_attempt_number=2,
            creation_date=later_time,
            update_date=later_time,
        )
        db.add(user_submission)
        db.add(duplicate_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_assignment_submissions(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        assert len(result) == 1
        assert result[0]["grade"] == 95
        assert result[0]["attempt_number"] == 2
        assert result[0]["best_grade"] == 95

    async def test_instructor_list_includes_expected_student_who_has_not_submitted(
        self, mock_request, db, org, assignment, user_submission, admin_user, user_role
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            60,
            None,
            "usergroup_assignment_submissions_missing",
        )
        missing_student = User(
            id=3,
            username="missing",
            first_name="Missing",
            last_name="Student",
            email="missing@test.com",
            password="hashed_password",
            user_uuid="user_missing",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(missing_student)
        await db.commit()
        await db.refresh(missing_student)
        db.add(
            UserOrganization(
                user_id=missing_student.id,
                org_id=org.id,
                role_id=user_role.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=60,
                user_id=missing_student.id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            UserGroupUser(
                usergroup_id=60,
                user_id=user_submission.user_id,
                org_id=org.id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_assignment_submissions(
                mock_request, assignment.assignment_uuid, admin_user, db
            )

        missing_row = next(row for row in result if row["user_id"] == missing_student.id)
        assert missing_row["submission_status"] == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
        assert missing_row["teacher_review_status"] == "missing"
        assert missing_row["grade_display"] is None
        assert missing_row["assignmentusersubmission_uuid"].startswith("missing_")
        assert missing_row["student_name"] == "Missing Student"
        assert missing_row["student_email"] == "missing@test.com"

        submitted_row = next(row for row in result if row["user_id"] == user_submission.user_id)
        assert submitted_row["student_email"] == "regular@test.com"


# ---------------------------------------------------------------------------
# read_user_assignment_submissions
# ---------------------------------------------------------------------------


class TestReadUserAssignmentSubmissions:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_submissions(
                    mock_request, "nonexistent", 1, admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_returns_user_submissions(
        self, mock_request, db, assignment, user_submission, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_user_assignment_submissions(
                mock_request, assignment.assignment_uuid, regular_user.id, admin_user, db
            )
        assert isinstance(result, list)
        assert len(result) == 1

    async def test_user_submission_includes_best_score_display_context(
        self, mock_request, db, org, assignment, assignment_task, user_submission, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4047,
            user=regular_user,
            uuid="usergroup_read_user_submission_context",
        )
        assignment.score_policy = "highest"
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        user_submission.attempt_number = 2
        user_submission.best_grade = 80
        user_submission.best_attempt_number = 1
        db.add(assignment)
        db.add(user_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False):
            result = await read_user_assignment_submissions(
                mock_request,
                assignment.assignment_uuid,
                regular_user.id,
                regular_user,
                db,
            )

        assert result[0]["submission_status"] == AssignmentUserSubmissionStatus.PENDING.value
        assert result[0]["best_grade"] == 80
        assert result[0]["best_attempt_number"] == 1
        assert result[0]["max_grade"] == 100
        assert result[0]["score_policy"] == "highest"

    async def test_rejects_student_when_assignment_has_no_target_usergroup(
        self, mock_request, db, assignment, user_submission, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_submissions(
                    mock_request,
                    assignment.assignment_uuid,
                    regular_user.id,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 403
        assert "尚未設定發布班級/群組" in exc.value.detail

    async def test_rejects_student_when_assignment_course_is_unpublished(
        self, mock_request, db, org, assignment, course, user_submission, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4048,
            user=regular_user,
            uuid="usergroup_read_user_submission_hidden_course",
        )
        course.published = False
        db.add(course)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_submissions(
                    mock_request,
                    assignment.assignment_uuid,
                    regular_user.id,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 403
        assert "課程或活動尚未發布" in exc.value.detail


# ---------------------------------------------------------------------------
# read_user_assignment_submissions_me
# ---------------------------------------------------------------------------


class TestReadUserAssignmentSubmissionsMe:
    async def test_delegates_to_read_user_submissions(
        self, mock_request, db, org, assignment, user_submission, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4049,
            user=regular_user,
            uuid="usergroup_read_user_submission_me",
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False):
            result = await read_user_assignment_submissions_me(
                mock_request, assignment.assignment_uuid, regular_user, db
            )
        assert isinstance(result, list)

    async def test_rejects_student_when_assignment_has_no_target_usergroup(
        self, mock_request, db, assignment, user_submission, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=False):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_submissions_me(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "尚未設定發布班級/群組" in exc.value.detail


# ---------------------------------------------------------------------------
# update_assignment_submission
# ---------------------------------------------------------------------------


class TestUpdateAssignmentSubmission:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user, regular_user
    ):
        obj = AssignmentUserSubmissionCreate(
            user_id=regular_user.id,
            assignment_id=9999,
            grade=0,
            submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
            attempt_number=1,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_submission(
                    mock_request, regular_user.id, "nonexistent", obj, admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, assignment, admin_user, regular_user
    ):
        obj = AssignmentUserSubmissionCreate(
            user_id=regular_user.id,
            assignment_id=assignment.id,
            grade=0,
            submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
            attempt_number=1,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_submission(
                    mock_request,
                    regular_user.id,
                    assignment.assignment_uuid,
                    obj,
                    admin_user,
                    db,
                )
        assert exc.value.status_code == 404

    async def test_updates_submission(
        self, mock_request, db, assignment, user_submission, admin_user, regular_user
    ):
        obj = AssignmentUserSubmissionCreate(
            user_id=regular_user.id,
            assignment_id=assignment.id,
            grade=90,
            submission_status=AssignmentUserSubmissionStatus.GRADED,
            attempt_number=1,
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await update_assignment_submission(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                obj,
                admin_user,
                db,
            )
        assert isinstance(result, AssignmentUserSubmissionRead)


# ---------------------------------------------------------------------------
# delete_assignment_submission
# ---------------------------------------------------------------------------


class TestDeleteAssignmentSubmission:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_submission(
                    mock_request, regular_user.id, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, assignment, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_submission(
                    mock_request,
                    regular_user.id,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )
        assert exc.value.status_code == 404

    async def test_deletes_submission(
        self, mock_request, db, assignment, user_submission, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await delete_assignment_submission(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert result["message"] == "學生提交記錄已刪除"


# ---------------------------------------------------------------------------
# grade_assignment_submission
# ---------------------------------------------------------------------------


class TestGradeAssignmentSubmission:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await grade_assignment_submission(
                    mock_request, regular_user.id, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, assignment, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await grade_assignment_submission(
                    mock_request,
                    regular_user.id,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )
        assert exc.value.status_code == 404

    async def test_grades_submission(
        self,
        mock_request,
        db,
        assignment,
        assignment_task,
        user_submission,
        task_submission,
        admin_user,
        regular_user,
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_CERT, new_callable=AsyncMock):
            result = await grade_assignment_submission(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert "message" in result
        assert "display_grade" in result

    async def test_teacher_confirms_ai_essay_review(
        self,
        mock_request,
        db,
        assignment,
        assignment_task,
        user_submission,
        task_submission,
        admin_user,
        regular_user,
    ):
        assignment.teacher_review_required = True
        assignment.teacher_review_status = "pending"
        assignment_task.assignment_type = AssignmentTaskTypeEnum.ESSAY
        assignment_task.contents = {"prompt": "以「我的校園」為題寫一篇短文。"}
        task_submission.assignment_type = AssignmentTaskTypeEnum.ESSAY
        task_submission.task_submission = {"essay": "我的校園很美麗。"}
        task_submission.grade = 88
        task_submission.task_submission_grade_feedback = json.dumps(
            {
                "type": "ai_essay_grading",
                "score": 88,
                "overall_feedback": "內容切題，例子可以再具體。",
                "needs_teacher_review": True,
            },
            ensure_ascii=False,
        )
        user_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
        user_submission.teacher_review_status = "pending"
        user_submission.grade = 88
        db.add(assignment)
        db.add(assignment_task)
        db.add(task_submission)
        db.add(user_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_CERT, new_callable=AsyncMock):
            result = await grade_assignment_submission(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                admin_user,
                db,
                overall_feedback="老師確認 AI 建議分數，請繼續補充例子。",
            )

        assert result["teacher_review_status"] == "confirmed"
        assert result["display_grade"] == "88/100"
        assert user_submission.teacher_review_status == "confirmed"
        assert user_submission.overall_feedback == "老師確認 AI 建議分數，請繼續補充例子。"


# ---------------------------------------------------------------------------
# get_grade_assignment_submission
# ---------------------------------------------------------------------------


class TestGetGradeAssignmentSubmission:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await get_grade_assignment_submission(
                    mock_request, regular_user.id, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, assignment, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await get_grade_assignment_submission(
                    mock_request,
                    regular_user.id,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )
        assert exc.value.status_code == 404

    async def test_returns_grade_object(
        self,
        mock_request,
        db,
        assignment,
        assignment_task,
        graded_submission,
        task_submission,
        admin_user,
        regular_user,
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await get_grade_assignment_submission(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert "display_grade" in result
        assert "tasks" in result


# ---------------------------------------------------------------------------
# mark_activity_as_done_for_user
# ---------------------------------------------------------------------------


class TestMarkActivityAsDoneForUser:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await mark_activity_as_done_for_user(
                    mock_request, regular_user.id, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_user_not_enrolled(
        self, mock_request, db, assignment, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await mark_activity_as_done_for_user(
                    mock_request,
                    regular_user.id,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )
        assert exc.value.status_code == 404

    async def test_marks_activity_done(
        self,
        mock_request,
        db,
        org,
        course,
        assignment,
        activity,
        admin_user,
        regular_user,
    ):
        _, _, step = await _make_trail(
            db, org.id, course.id, activity.id, regular_user.id
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_CERT, new_callable=AsyncMock):
            result = await mark_activity_as_done_for_user(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert result["message"] == "Activity marked as done for user"
        await db.refresh(step)
        assert step.complete is True


# ---------------------------------------------------------------------------
# get_assignments_from_course
# ---------------------------------------------------------------------------


class TestGetAssignmentsFromCourse:
    async def test_raises_404_when_course_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await get_assignments_from_course(mock_request, "nonexistent", admin_user, db)
        assert exc.value.status_code == 404

    async def test_returns_assignments_for_course(
        self, mock_request, db, course, assignment, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await get_assignments_from_course(
                mock_request, course.course_uuid, admin_user, db
            )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].assignment_uuid == assignment.assignment_uuid

    async def test_list_read_preserves_nonempty_list_metadata(
        self, mock_request, db, course, assignment, admin_user
    ):
        assignment.learning_objectives = ["能比較分數大小"]
        assignment.target_usergroup_ids = [50]
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await get_assignments_from_course(
                mock_request, course.course_uuid, admin_user, db
            )

        assert result[0].learning_objectives == ["能比較分數大小"]
        assert result[0].target_usergroup_ids == [50]


# ---------------------------------------------------------------------------
# put_assignment_task_reference_file
# ---------------------------------------------------------------------------


class TestPutAssignmentTaskReferenceFile:
    async def test_raises_404_when_task_not_found(self, mock_request, db, admin_user):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await put_assignment_task_reference_file(
                    mock_request, db, "nonexistent_task", admin_user, None
                )
        assert exc.value.status_code == 404

    async def test_updates_task_without_file(
        self, mock_request, db, admin_user, assignment_task
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await put_assignment_task_reference_file(
                mock_request,
                db,
                assignment_task.assignment_task_uuid,
                admin_user,
                None,
            )
        assert result.assignment_task_uuid == assignment_task.assignment_task_uuid


# ---------------------------------------------------------------------------
# put_assignment_task_submission_file
# ---------------------------------------------------------------------------


class TestPutAssignmentTaskSubmissionFile:
    async def test_raises_404_when_task_not_found(self, mock_request, db, admin_user):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await put_assignment_task_submission_file(
                    mock_request, db, "nonexistent_task", admin_user, None
                )
        assert exc.value.status_code == 404

    async def test_returns_none_when_no_file(
        self, mock_request, db, admin_user, assignment_task
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await put_assignment_task_submission_file(
                mock_request,
                db,
                assignment_task.assignment_task_uuid,
                admin_user,
                None,
            )
        assert result is None

    async def test_rejects_student_uploading_file_after_final_submission(
        self, mock_request, db, org, assignment, assignment_task, user_submission, regular_user
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.FILE_SUBMISSION
        db.add(assignment_task)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            65,
            regular_user,
            "usergroup_file_submission_final",
        )
        fake_file = type("FakeUploadFile", (), {"filename": "answer.pdf"})()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[True, False]), \
             patch(_PATCH_UPLOAD_SUBMISSION_FILE, new_callable=AsyncMock) as upload_mock:
            with pytest.raises(HTTPException) as exc:
                await put_assignment_task_submission_file(
                    mock_request,
                    db,
                    assignment_task.assignment_task_uuid,
                    regular_user,
                    fake_file,
                )

        assert exc.value.status_code == 400
        assert "已提交" in exc.value.detail
        assert "重做" in exc.value.detail
        upload_mock.assert_not_called()

    async def test_allows_student_uploading_file_after_retry_pending_state(
        self, mock_request, db, org, assignment, assignment_task, user_submission, regular_user
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.FILE_SUBMISSION
        user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
        db.add(assignment_task)
        db.add(user_submission)
        await db.commit()
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            66,
            regular_user,
            "usergroup_file_submission_retry",
        )
        fake_file = type("FakeUploadFile", (), {"filename": "answer.pdf"})()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, side_effect=[True, False]), \
             patch(_PATCH_UPLOAD_SUBMISSION_FILE, new_callable=AsyncMock, return_value="file_saved.pdf"):
            result = await put_assignment_task_submission_file(
                mock_request,
                db,
                assignment_task.assignment_task_uuid,
                regular_user,
                fake_file,
            )

        assert result == {"file_uuid": "file_saved.pdf"}


# ---------------------------------------------------------------------------
# handle_assignment_task_submission — UUID fallback branch (line 1342)
# ---------------------------------------------------------------------------


class TestHandleAssignmentTaskSubmissionUuidBranch:
    async def test_finds_submission_by_uuid_when_user_task_not_found(
        self, mock_request, db, admin_user, assignment_task, task_submission
    ):
        """Covers line 1342: submission not found by (user, task), found by UUID.

        `task_submission` belongs to `regular_user` (id=2). Calling as `admin_user`
        (id=1) means the first lookup returns None; the UUID fallback finds it."""
        payload = AssignmentTaskSubmissionUpdate(
            assignment_task_submission_uuid=task_submission.assignment_task_submission_uuid,
            task_submission={"answer": "updated"},
        )
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await handle_assignment_task_submission(
                mock_request,
                assignment_task.assignment_task_uuid,
                payload,
                admin_user,
                db,
            )
        assert result is not None


# ---------------------------------------------------------------------------
# read_user_assignment_task_submissions
# ---------------------------------------------------------------------------


class TestReadUserAssignmentTaskSubmissions:
    async def test_raises_404_when_task_not_found(
        self, mock_request, db, admin_user, regular_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions(
                    mock_request, "nonexistent", regular_user.id, admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, admin_user, regular_user, assignment_task
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions(
                    mock_request,
                    assignment_task.assignment_task_uuid,
                    regular_user.id,
                    admin_user,
                    db,
                )
        assert exc.value.status_code == 404

    async def test_returns_submission(
        self, mock_request, db, admin_user, regular_user, assignment_task, task_submission
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await read_user_assignment_task_submissions(
                mock_request,
                assignment_task.assignment_task_uuid,
                regular_user.id,
                admin_user,
                db,
            )
        assert result.assignment_task_submission_uuid == task_submission.assignment_task_submission_uuid


# ---------------------------------------------------------------------------
# read_user_assignment_task_submissions_me_batch
# ---------------------------------------------------------------------------


class TestReadUserAssignmentTaskSubmissionsMeBatch:
    async def test_raises_404_when_assignment_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions_me_batch(
                    mock_request, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_returns_batch_map(
        self, mock_request, db, admin_user, assignment, assignment_task
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_user_assignment_task_submissions_me_batch(
                mock_request, assignment.assignment_uuid, admin_user, db
            )
        assert isinstance(result, dict)
        assert assignment_task.assignment_task_uuid in result

    async def test_rejects_student_when_assignment_has_no_target_usergroup(
        self, mock_request, db, assignment, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions_me_batch(
                    mock_request, assignment.assignment_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "尚未設定發布班級/群組" in exc.value.detail

    async def test_allows_student_when_assignment_is_visible_to_target_group(
        self, mock_request, db, org, assignment, assignment_task, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4050,
            user=regular_user,
            uuid="usergroup_task_submission_batch_visible",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_user_assignment_task_submissions_me_batch(
                mock_request, assignment.assignment_uuid, regular_user, db
            )

        assert isinstance(result, dict)
        assert assignment_task.assignment_task_uuid in result


# ---------------------------------------------------------------------------
# read_user_assignment_task_submissions_me
# ---------------------------------------------------------------------------


class TestReadUserAssignmentTaskSubmissionsMe:
    async def test_raises_404_when_task_not_found(self, mock_request, db, admin_user):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions_me(
                    mock_request, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_returns_none_when_no_submission(
        self, mock_request, db, admin_user, assignment_task
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_user_assignment_task_submissions_me(
                mock_request, assignment_task.assignment_task_uuid, admin_user, db
            )
        assert result is None

    async def test_returns_submission(
        self, mock_request, db, org, assignment, regular_user, assignment_task, task_submission
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4051,
            user=regular_user,
            uuid="usergroup_task_submission_me_visible",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_user_assignment_task_submissions_me(
                mock_request, assignment_task.assignment_task_uuid, regular_user, db
            )
        assert result is not None
        assert result.assignment_task_submission_uuid == task_submission.assignment_task_submission_uuid

    async def test_rejects_student_when_assignment_has_no_target_usergroup(
        self, mock_request, db, assignment, assignment_task, task_submission, regular_user
    ):
        assignment.target_usergroup_ids = []
        db.add(assignment)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions_me(
                    mock_request, assignment_task.assignment_task_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "尚未設定發布班級/群組" in exc.value.detail

    async def test_rejects_student_when_assignment_activity_is_unpublished(
        self, mock_request, db, org, assignment, assignment_task, task_submission, activity, regular_user
    ):
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4052,
            user=regular_user,
            uuid="usergroup_task_submission_me_hidden_activity",
        )
        activity.published = False
        db.add(activity)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_user_assignment_task_submissions_me(
                    mock_request, assignment_task.assignment_task_uuid, regular_user, db
                )

        assert exc.value.status_code == 403
        assert "課程或活動尚未發布" in exc.value.detail


# ---------------------------------------------------------------------------
# read_assignment_task_submissions
# ---------------------------------------------------------------------------


class TestReadAssignmentTaskSubmissions:
    async def test_raises_404_when_task_not_found(self, mock_request, db, admin_user):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await read_assignment_task_submissions(
                    mock_request, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_returns_submissions_list(
        self, mock_request, db, admin_user, assignment_task, task_submission
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await read_assignment_task_submissions(
                mock_request, assignment_task.assignment_task_uuid, admin_user, db
            )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].assignment_task_submission_uuid == task_submission.assignment_task_submission_uuid


# ---------------------------------------------------------------------------
# update_assignment_task_submission
# ---------------------------------------------------------------------------


class TestUpdateAssignmentTaskSubmission:
    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, admin_user
    ):
        payload = AssignmentTaskSubmissionUpdate(task_submission={"answer": "x"})
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_assignment_task_submission(
                    mock_request, "nonexistent_uuid", payload, admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_updates_submission(
        self, mock_request, db, admin_user, assignment_task, task_submission
    ):
        payload = AssignmentTaskSubmissionUpdate(task_submission={"answer": "new_answer"})
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await update_assignment_task_submission(
                mock_request,
                task_submission.assignment_task_submission_uuid,
                payload,
                admin_user,
                db,
            )
        assert result.assignment_task_submission_uuid == task_submission.assignment_task_submission_uuid


# ---------------------------------------------------------------------------
# delete_assignment_task_submission
# ---------------------------------------------------------------------------


class TestDeleteAssignmentTaskSubmission:
    async def test_raises_404_when_submission_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await delete_assignment_task_submission(
                    mock_request, "nonexistent", admin_user, db
                )
        assert exc.value.status_code == 404

    async def test_deletes_submission(
        self, mock_request, db, admin_user, task_submission
    ):
        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            result = await delete_assignment_task_submission(
                mock_request,
                task_submission.assignment_task_submission_uuid,
                admin_user,
                db,
            )
        assert result["message"] == "Assignment Task Submission deleted"


# ---------------------------------------------------------------------------
# create_assignment_submission — new TrailRun / TrailStep branches + auto_grading
# ---------------------------------------------------------------------------

_PATCH_TRAIL_PRESENCE = "src.services.courses.activities.assignments.check_trail_presence"
_PATCH_CERT_CHECK = (
    "src.services.courses.activities.assignments."
    "check_course_completion_and_create_certificate"
)
_PATCH_GRADE_FINALIZE = (
    "src.services.courses.activities.assignments._apply_grade_and_finalize"
)


class TestCreateAssignmentSubmission:
    async def test_rejects_submission_when_assignment_is_unpublished(
        self, mock_request, db, admin_user, assignment, assignment_task
    ):
        assignment.published = False
        db.add(assignment)
        await db.commit()

        await _make_task_submission_for_user(
            db,
            assignment_task,
            admin_user.id,
            uuid="ats_unpublished_submit_test",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "尚未發布" in exc.value.detail

    async def test_rejects_submission_when_assignment_has_no_tasks(
        self, mock_request, db, admin_user, assignment, course
    ):
        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_empty_assignment_submit_test",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "老師正在準備題目" in exc.value.detail

    async def test_rejects_submission_when_no_saved_task_answers(
        self, mock_request, db, admin_user, assignment, assignment_task, course
    ):
        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_no_saved_answers_submit_test",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "儲存本題答案" in exc.value.detail
        assert "Test Task" in exc.value.detail

    async def test_rejects_submission_when_student_is_not_in_target_usergroup(
        self, mock_request, db, org, regular_user, assignment, assignment_task
    ):
        group = UserGroup(
            id=4020,
            org_id=org.id,
            name="小四甲",
            description="",
            usergroup_uuid="usergroup_submit_target_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [4020]
        db.add(group)
        db.add(assignment)
        await db.commit()

        await _make_task_submission_for_user(
            db,
            assignment_task,
            regular_user.id,
            uuid="ats_submit_wrong_target_group_test",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    regular_user,
                    db,
                )

        assert exc.value.status_code == 403
        assert "不在發布名單內" in exc.value.detail

    async def test_allows_submission_when_student_is_in_target_usergroup(
        self, mock_request, db, org, regular_user, assignment, assignment_task, course
    ):
        group = UserGroup(
            id=4021,
            org_id=org.id,
            name="小四乙",
            description="",
            usergroup_uuid="usergroup_submit_target_allowed",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        membership = UserGroupUser(
            usergroup_id=4021,
            user_id=regular_user.id,
            org_id=org.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        assignment.target_usergroup_ids = [4021]
        db.add(group)
        db.add(membership)
        db.add(assignment)
        await db.commit()

        trail = Trail(
            org_id=course.org_id,
            user_id=regular_user.id,
            trail_uuid="trail_target_group_submit_allowed",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)
        await _make_task_submission_for_user(
            db,
            assignment_task,
            regular_user.id,
            uuid="ats_submit_target_group_allowed_test",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )

        assert result.submission_status == AssignmentUserSubmissionStatus.SUBMITTED

    async def test_student_can_retry_wrong_simple_answer_and_gradebook_shows_best_score(
        self,
        mock_request,
        db,
        org,
        regular_user,
        admin_user,
        assignment,
        assignment_task,
        course,
    ):
        assignment.auto_grading = True
        assignment.allow_retries = True
        assignment.show_correct_answers = True
        assignment.score_policy = "highest"
        assignment.max_retries = 0
        await _target_assignment_to_group(
            db,
            org,
            assignment,
            4022,
            regular_user,
            "usergroup_retry_learning_trace",
        )
        assignment_task.contents = {
            "prompt": "澳門位於哪個國家？",
            "correct_answers": ["中國"],
            "match_mode": "case_insensitive",
        }
        db.add(assignment)
        db.add(assignment_task)
        await db.commit()

        trail = Trail(
            org_id=course.org_id,
            user_id=regular_user.id,
            trail_uuid="trail_retry_learning_trace",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)
        task_submission = await _make_task_submission_for_user(
            db,
            assignment_task,
            regular_user.id,
            uuid="ats_retry_learning_trace",
        )
        task_submission.task_submission = {"answer": "日本"}
        task_submission.grade = 0
        db.add(task_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            first_result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )

        assert first_result.submission_status == AssignmentUserSubmissionStatus.GRADED
        assert first_result.grade == 0

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            retry_result = await retry_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )

        assert retry_result["attempt_number"] == 2

        await db.refresh(task_submission)
        task_submission.task_submission = {"answer": "中國"}
        task_submission.grade = 0
        task_submission.task_submission_grade_feedback = ""
        db.add(task_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            second_result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                regular_user,
                db,
            )

        assert second_result.submission_status == AssignmentUserSubmissionStatus.GRADED
        assert second_result.grade == 100
        assert second_result.attempt_number == 2
        assert second_result.best_grade == 100
        assert second_result.best_attempt_number == 2

        gradebook = await read_assignment_gradebook(org.id, admin_user, db)
        row = next(row for row in gradebook["rows"] if row["student_id"] == regular_user.id)

        assert gradebook["summary"]["retry_records"] == 1
        assert gradebook["summary"]["scored_records"] == 1
        assert gradebook["summary"]["average_score"] == 100.0
        assert row["submission_status"] == AssignmentUserSubmissionStatus.GRADED.value
        assert row["attempt_number"] == 2
        assert row["best_grade"] == 100
        assert row["best_attempt_number"] == 2
        assert row["grade"] == 100
        assert row["score_policy"] == "highest"

    async def test_rejects_submission_when_only_some_tasks_are_saved(
        self,
        mock_request,
        db,
        admin_user,
        assignment,
        assignment_task,
        course,
        chapter,
        activity,
    ):
        second_task = AssignmentTask(
            id=21,
            title="Second Task",
            description="Another task for testing",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "What is 3+3?", "correct_answers": ["6"], "match_mode": "exact"},
            max_grade_value=100,
            assignment_id=assignment.id,
            org_id=course.org_id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_task_uuid="assignmenttask_second_submit_guard",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(second_task)
        await db.commit()
        await db.refresh(second_task)

        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_partial_saved_answers_submit_test",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)
        await _make_task_submission_for_user(
            db, assignment_task, admin_user.id, uuid="ats_submit_partial_saved_test"
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "儲存本題答案" in exc.value.detail
        assert "Second Task" in exc.value.detail

    async def test_rejects_submission_when_saved_short_answer_is_blank(
        self, mock_request, db, admin_user, assignment, assignment_task
    ):
        blank_submission = AssignmentTaskSubmission(
            assignment_task_submission_uuid="ats_submit_blank_answer_test",
            task_submission={"answer": "   "},
            grade=0,
            task_submission_grade_feedback="",
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            user_id=admin_user.id,
            activity_id=assignment_task.activity_id,
            course_id=assignment_task.course_id,
            chapter_id=assignment_task.chapter_id,
            assignment_task_id=assignment_task.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(blank_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "尚有 1 題未作答" in exc.value.detail
        assert "Test Task" in exc.value.detail

        saved_user_submission = (
            await db.execute(
                select(AssignmentUserSubmission).where(
                    AssignmentUserSubmission.assignment_id == assignment.id,
                    AssignmentUserSubmission.user_id == admin_user.id,
                )
            )
        ).scalars().first()
        assert saved_user_submission is None

    async def test_rejects_submission_when_saved_quiz_has_no_selected_option(
        self, mock_request, db, admin_user, assignment, assignment_task
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "question_no_choice",
                    "questionText": "澳門位於哪個國家？",
                    "options": [
                        {
                            "optionUUID": "option_china",
                            "text": "中國",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_japan",
                            "text": "日本",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        }
        db.add(assignment_task)
        await db.commit()

        quiz_submission = AssignmentTaskSubmission(
            assignment_task_submission_uuid="ats_submit_quiz_no_choice_test",
            task_submission={
                "submissions": [
                    {
                        "questionUUID": "question_no_choice",
                        "optionUUID": "option_china",
                        "answer": False,
                    },
                    {
                        "questionUUID": "question_no_choice",
                        "optionUUID": "option_japan",
                        "answer": False,
                    },
                ]
            },
            grade=0,
            task_submission_grade_feedback="",
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            user_id=admin_user.id,
            activity_id=assignment_task.activity_id,
            course_id=assignment_task.course_id,
            chapter_id=assignment_task.chapter_id,
            assignment_task_id=assignment_task.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(quiz_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "尚有 1 題未作答" in exc.value.detail
        assert "Test Task" in exc.value.detail

    async def test_rejects_submission_when_saved_quiz_false_string_has_no_selected_option(
        self, mock_request, db, admin_user, assignment, assignment_task
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.QUIZ
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "question_false_string_choice",
                    "questionText": "澳門位於哪個國家？",
                    "options": [
                        {
                            "optionUUID": "option_china_false_string",
                            "text": "中國",
                            "assigned_right_answer": True,
                        },
                        {
                            "optionUUID": "option_japan_false_string",
                            "text": "日本",
                            "assigned_right_answer": False,
                        },
                    ],
                }
            ]
        }
        db.add(assignment_task)
        await db.commit()

        quiz_submission = AssignmentTaskSubmission(
            assignment_task_submission_uuid="ats_submit_quiz_false_string_no_choice_test",
            task_submission={
                "submissions": [
                    {
                        "questionUUID": "question_false_string_choice",
                        "optionUUID": "option_china_false_string",
                        "answer": "false",
                    },
                    {
                        "questionUUID": "question_false_string_choice",
                        "optionUUID": "option_japan_false_string",
                        "answer": "false",
                    },
                ]
            },
            grade=0,
            task_submission_grade_feedback="",
            assignment_type=AssignmentTaskTypeEnum.QUIZ,
            user_id=admin_user.id,
            activity_id=assignment_task.activity_id,
            course_id=assignment_task.course_id,
            chapter_id=assignment_task.chapter_id,
            assignment_task_id=assignment_task.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(quiz_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "尚有 1 題未作答" in exc.value.detail
        assert "Test Task" in exc.value.detail

    async def test_rejects_submission_when_saved_form_blank_is_empty(
        self, mock_request, db, admin_user, assignment, assignment_task
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.FORM
        assignment_task.contents = {
            "questions": [
                {
                    "questionUUID": "question_blank",
                    "questionText": "澳門是中國的 ____ 行政區。",
                    "blanks": [
                        {
                            "blankUUID": "blank_special",
                            "correctAnswer": "特別",
                        }
                    ],
                }
            ]
        }
        db.add(assignment_task)
        await db.commit()

        form_submission = AssignmentTaskSubmission(
            assignment_task_submission_uuid="ats_submit_form_empty_blank_test",
            task_submission={
                "submissions": [
                    {
                        "questionUUID": "question_blank",
                        "blankUUID": "blank_special",
                        "answer": "   ",
                    }
                ]
            },
            grade=0,
            task_submission_grade_feedback="",
            assignment_type=AssignmentTaskTypeEnum.FORM,
            user_id=admin_user.id,
            activity_id=assignment_task.activity_id,
            course_id=assignment_task.course_id,
            chapter_id=assignment_task.chapter_id,
            assignment_task_id=assignment_task.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(form_submission)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    admin_user,
                    db,
                )

        assert exc.value.status_code == 400
        assert "尚有 1 題未作答" in exc.value.detail
        assert "Test Task" in exc.value.detail

    async def test_allows_zero_number_answer_as_complete(
        self, mock_request, db, admin_user, assignment, assignment_task, course
    ):
        assignment_task.assignment_type = AssignmentTaskTypeEnum.NUMBER_ANSWER
        assignment_task.contents = {
            "prompt": "0 加 0 等於多少？",
            "correct_value": 0,
            "tolerance": 0,
        }
        db.add(assignment_task)

        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_zero_number_answer_submit_test",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        zero_submission = AssignmentTaskSubmission(
            assignment_task_submission_uuid="ats_submit_zero_number_answer_test",
            task_submission={"answer": 0},
            grade=0,
            task_submission_grade_feedback="",
            assignment_type=AssignmentTaskTypeEnum.NUMBER_ANSWER,
            user_id=admin_user.id,
            activity_id=assignment_task.activity_id,
            course_id=assignment_task.course_id,
            chapter_id=assignment_task.chapter_id,
            assignment_task_id=assignment_task.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        db.add(zero_submission)
        await db.commit()
        await db.refresh(trail)

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                admin_user,
                db,
            )

        assert result.submission_status == AssignmentUserSubmissionStatus.SUBMITTED

    async def test_creates_trailrun_and_trailstep_when_missing(
        self, mock_request, db, admin_user, assignment, course, activity, assignment_task
    ):
        """Covers lines 1915-1916 (new TrailRun) and 1940-1941 (new TrailStep)."""
        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_submit_test",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)
        await _make_task_submission_for_user(
            db, assignment_task, admin_user.id, uuid="ats_submit_trail_test"
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert result.submission_status == AssignmentUserSubmissionStatus.SUBMITTED

    async def test_marks_submission_late_when_due_date_has_passed(
        self, mock_request, db, admin_user, assignment, course, assignment_task
    ):
        assignment.due_date = "2000-01-01"
        db.add(assignment)
        await db.commit()

        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_late_submit_test",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)
        await _make_task_submission_for_user(
            db,
            assignment_task,
            admin_user.id,
            uuid="ats_submit_late_test",
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock):
            result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                admin_user,
                db,
            )

        assert result.submission_status == AssignmentUserSubmissionStatus.LATE

    async def test_auto_grading_path(
        self, mock_request, db, admin_user, assignment, course, activity, assignment_task
    ):
        """Covers the auto_grading branch (lines 1967-1989)."""
        assignment.auto_grading = True
        db.add(assignment)
        await db.commit()

        trail = Trail(
            org_id=course.org_id,
            user_id=admin_user.id,
            trail_uuid="trail_autograding",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)
        await _make_task_submission_for_user(
            db, assignment_task, admin_user.id, uuid="ats_submit_autograde_test"
        )

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail), \
             patch(_PATCH_CERT, new_callable=AsyncMock), \
             patch(_PATCH_CERT_CHECK, new_callable=AsyncMock), \
             patch(_PATCH_TRACK, new_callable=AsyncMock), \
             patch(_PATCH_DISPATCH, new_callable=AsyncMock), \
             patch(_PATCH_GRADE_FINALIZE, new_callable=AsyncMock):
            result = await create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert result.submission_status == AssignmentUserSubmissionStatus.SUBMITTED


# ---------------------------------------------------------------------------
# delete_assignment_submission — certification revocation branch (lines 2292-2297)
# ---------------------------------------------------------------------------


class TestDeleteAssignmentSubmissionCertRevocation:
    async def test_revokes_certificate_when_present(
        self, mock_request, db, assignment, user_submission, admin_user, regular_user, course
    ):
        """Covers the certification revocation path in delete_assignment_submission."""
        cert = Certifications(
            certification_uuid="cert_test_uuid",
            course_id=course.id,
            config={},
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(cert)
        await db.commit()
        await db.refresh(cert)

        cert_user = CertificateUser(
            user_id=regular_user.id,
            certification_id=cert.id,
            user_certification_uuid="certuser_test_uuid",
            created_at=str(datetime.now()),
            updated_at=str(datetime.now()),
        )
        db.add(cert_user)
        await db.commit()

        with patch(_PATCH_RBAC, new_callable=AsyncMock), \
             patch(_PATCH_AUTH_ROLES, new_callable=AsyncMock, return_value=True):
            result = await delete_assignment_submission(
                mock_request,
                regular_user.id,
                assignment.assignment_uuid,
                admin_user,
                db,
            )
        assert result["message"] == "學生提交記錄已刪除"
