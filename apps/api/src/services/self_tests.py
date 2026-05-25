import random
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.db.organizations import Organization
from src.db.question_bank import QuestionBankItem, QuestionBankVisibilityEnum
from src.db.self_tests import (
    ReviewSelfTestAttemptRequest,
    SelfTestAttempt,
    SelfTestAttemptQuestion,
    SelfTestAttemptQuestionRead,
    SelfTestAttemptRead,
    SelfTestAttemptStatus,
    StartSelfTestRequest,
    SubmitSelfTestRequest,
)
from src.db.users import PublicUser, User
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import require_org_membership, require_org_role_permission
from src.services.courses.activities.assignments import _server_verified_task_grade
from src.services.question_bank import AUTO_GRADABLE_BANK_TYPES


def _now() -> str:
    return str(datetime.now())


def _sanitize_contents(assignment_type: AssignmentTaskTypeEnum, contents: dict, reveal_answers: bool) -> dict:
    data = dict(contents or {})
    if reveal_answers:
        return data

    if assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        data.pop("correct_answers", None)
        data.pop("accepted_answers", None)
        data.pop("explanation", None)
    elif assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER:
        data.pop("correct_value", None)
        data.pop("tolerance", None)
        data.pop("explanation", None)
    elif assignment_type == AssignmentTaskTypeEnum.QUIZ:
        questions = []
        for question in data.get("questions") or []:
            clean_question = dict(question or {})
            clean_options = []
            for option in clean_question.get("options") or []:
                clean_option = dict(option or {})
                clean_option.pop("assigned_right_answer", None)
                clean_options.append(clean_option)
            clean_question["options"] = clean_options
            questions.append(clean_question)
        data["questions"] = questions
    elif assignment_type == AssignmentTaskTypeEnum.FORM:
        questions = []
        for question in data.get("questions") or []:
            clean_question = dict(question or {})
            clean_blanks = []
            for blank in clean_question.get("blanks") or []:
                clean_blank = dict(blank or {})
                clean_blank.pop("correctAnswer", None)
                clean_blank.pop("correct_answer", None)
                clean_blanks.append(clean_blank)
            clean_question["blanks"] = clean_blanks
            questions.append(clean_question)
        data["questions"] = questions
    elif assignment_type == AssignmentTaskTypeEnum.CODE:
        data.pop("solution_code", None)
        data.pop("solutionCode", None)
        data.pop("show_solution_after_submit", None)
        data.pop("showSolutionAfterSubmit", None)
        clean_cases = []
        for case in data.get("test_cases") or []:
            clean_case = dict(case or {})
            clean_case.pop("expectedStdout", None)
            clean_case.pop("expected_stdout", None)
            clean_cases.append(clean_case)
        data["test_cases"] = clean_cases
        clean_hidden_cases = []
        for case in data.get("hidden_test_cases") or data.get("hiddenTestCases") or []:
            clean_case = dict(case or {})
            clean_case.pop("expectedStdout", None)
            clean_case.pop("expected_stdout", None)
            clean_hidden_cases.append(clean_case)
        if "hidden_test_cases" in data:
            data["hidden_test_cases"] = clean_hidden_cases
        if "hiddenTestCases" in data:
            data["hiddenTestCases"] = clean_hidden_cases
    return data


def _question_read(question: SelfTestAttemptQuestion, reveal_answers: bool) -> SelfTestAttemptQuestionRead:
    payload = question.model_dump()
    payload["contents"] = _sanitize_contents(question.assignment_type, question.contents or {}, reveal_answers)
    if not reveal_answers:
        payload["answer"] = {}
        payload["grade"] = 0
        payload["feedback"] = ""
    return SelfTestAttemptQuestionRead.model_validate(payload)


async def _attempt_read(
    attempt: SelfTestAttempt,
    db_session: AsyncSession,
    reveal_answers: bool,
) -> SelfTestAttemptRead:
    questions = (
        await db_session.execute(
            select(SelfTestAttemptQuestion)
            .where(SelfTestAttemptQuestion.attempt_id == attempt.id)
            .order_by(SelfTestAttemptQuestion.display_order.asc(), SelfTestAttemptQuestion.id.asc())
        )
    ).scalars().all()
    user = (await db_session.execute(select(User).where(User.id == attempt.user_id))).scalars().first()
    payload = attempt.model_dump()
    if user:
        payload["user_name"] = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username
        payload["user_email"] = user.email
    payload["questions"] = [_question_read(question, reveal_answers) for question in questions]
    return SelfTestAttemptRead.model_validate(payload)


async def _get_org_or_404(org_id: int, db_session: AsyncSession) -> Organization:
    org = (await db_session.execute(select(Organization).where(Organization.id == org_id))).scalars().first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


async def _get_attempt_or_404(attempt_uuid: str, db_session: AsyncSession) -> SelfTestAttempt:
    attempt = (
        await db_session.execute(select(SelfTestAttempt).where(SelfTestAttempt.attempt_uuid == attempt_uuid))
    ).scalars().first()
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Self-test attempt not found")
    return attempt


async def _ensure_teacher(user_id: int, org_id: int, db_session: AsyncSession) -> None:
    await require_org_role_permission(user_id, org_id, db_session, "dashboard", "action_access")


def _normalize_tags(tags: list[str] | None) -> list[str]:
    return [tag.strip() for tag in tags or [] if str(tag).strip()][:20]


def _allowed_types(types: list[AssignmentTaskTypeEnum] | None) -> list[AssignmentTaskTypeEnum]:
    if not types:
        return [
            AssignmentTaskTypeEnum.QUIZ,
            AssignmentTaskTypeEnum.FORM,
            AssignmentTaskTypeEnum.CODE,
            AssignmentTaskTypeEnum.SHORT_ANSWER,
            AssignmentTaskTypeEnum.NUMBER_ANSWER,
        ]
    allowed = []
    for item in types:
        if item.value not in AUTO_GRADABLE_BANK_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Self-tests only support auto-gradable question types")
        allowed.append(item)
    return allowed


async def start_self_test(
    request_body: StartSelfTestRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> SelfTestAttemptRead:
    await _get_org_or_404(request_body.org_id, db_session)
    user_id = resolve_acting_user_id(current_user)
    await require_org_membership(user_id, request_body.org_id, db_session)

    question_count = max(1, min(int(request_body.question_count or 5), 50))
    tags = _normalize_tags(request_body.tags)
    allowed_types = _allowed_types(request_body.assignment_types)
    statement = select(QuestionBankItem).where(
        QuestionBankItem.org_id == request_body.org_id,
        QuestionBankItem.visibility == QuestionBankVisibilityEnum.ORG,
        QuestionBankItem.assignment_type.in_(allowed_types),
    )
    if request_body.category_id is not None:
        statement = statement.where(QuestionBankItem.category_id == request_body.category_id)

    candidates = list((await db_session.execute(statement)).scalars().all())
    if tags:
        lowered = {tag.lower() for tag in tags}
        candidates = [
            item for item in candidates
            if lowered.intersection({str(tag).lower() for tag in (item.tags or [])})
        ]

    if not candidates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No shared question bank items are available for this self-test")

    selected = random.sample(candidates, min(question_count, len(candidates)))
    attempt = SelfTestAttempt(
        attempt_uuid=f"selftest_{uuid4()}",
        org_id=request_body.org_id,
        user_id=user_id,
        category_id=request_body.category_id,
        tags=tags,
        question_count=len(selected),
        status=SelfTestAttemptStatus.STARTED,
        score=0,
        max_score=len(selected) * 100,
        percentage=0,
        creation_date=_now(),
        update_date=_now(),
    )
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)

    for index, item in enumerate(selected):
        item.usage_count = int(item.usage_count or 0) + 1
        db_session.add(item)
        db_session.add(
            SelfTestAttemptQuestion(
                attempt_question_uuid=f"selftestquestion_{uuid4()}",
                attempt_id=attempt.id,
                question_bank_item_id=item.id,
                question_bank_item_uuid=item.item_uuid,
                title=item.title,
                description=item.description,
                hint=item.hint,
                assignment_type=item.assignment_type,
                contents=item.contents or {},
                answer={},
                grade=0,
                max_grade=100,
                feedback="",
                display_order=index,
            )
        )
    await db_session.commit()
    await db_session.refresh(attempt)
    return await _attempt_read(attempt, db_session, reveal_answers=False)


async def submit_self_test(
    attempt_uuid: str,
    request_body: SubmitSelfTestRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> SelfTestAttemptRead:
    attempt = await _get_attempt_or_404(attempt_uuid, db_session)
    user_id = resolve_acting_user_id(current_user)
    if attempt.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only submit your own self-test")
    await require_org_membership(user_id, attempt.org_id, db_session)
    if attempt.status != SelfTestAttemptStatus.STARTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This self-test has already been submitted")

    questions = (
        await db_session.execute(select(SelfTestAttemptQuestion).where(SelfTestAttemptQuestion.attempt_id == attempt.id))
    ).scalars().all()
    by_uuid = {question.attempt_question_uuid: question for question in questions}
    answers = {answer.attempt_question_uuid: answer.answer for answer in request_body.answers}
    total = 0
    max_score = 0
    for question in questions:
        question.answer = answers.get(question.attempt_question_uuid, {})
        task = SimpleNamespace(
            assignment_type=question.assignment_type,
            contents=question.contents or {},
            max_grade_value=question.max_grade,
            assignment_task_uuid=question.attempt_question_uuid,
        )
        submission = SimpleNamespace(task_submission=question.answer)
        verified = await _server_verified_task_grade(task, submission)
        question.grade = int(verified if verified is not None else 0)
        question.feedback = "Auto-graded"
        total += question.grade
        max_score += int(question.max_grade or 0)
        db_session.add(question)

    unknown = set(answers.keys()) - set(by_uuid.keys())
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Some answers do not belong to this self-test")

    attempt.score = total
    attempt.max_score = max_score
    attempt.percentage = round((total / max_score) * 100, 2) if max_score else 0
    attempt.status = SelfTestAttemptStatus.SUBMITTED
    attempt.submitted_at = _now()
    attempt.update_date = _now()
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)
    return await _attempt_read(attempt, db_session, reveal_answers=True)


async def list_my_self_test_attempts(
    org_id: int,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> list[SelfTestAttemptRead]:
    await _get_org_or_404(org_id, db_session)
    user_id = resolve_acting_user_id(current_user)
    await require_org_membership(user_id, org_id, db_session)
    attempts = (
        await db_session.execute(
            select(SelfTestAttempt)
            .where(SelfTestAttempt.org_id == org_id, SelfTestAttempt.user_id == user_id)
            .order_by(SelfTestAttempt.creation_date.desc(), SelfTestAttempt.id.desc())
        )
    ).scalars().all()
    return [await _attempt_read(attempt, db_session, reveal_answers=attempt.status != SelfTestAttemptStatus.STARTED) for attempt in attempts]


async def list_org_self_test_attempts(
    org_id: int,
    current_user: PublicUser,
    db_session: AsyncSession,
    user_id: int | None = None,
) -> list[SelfTestAttemptRead]:
    await _get_org_or_404(org_id, db_session)
    acting_user_id = resolve_acting_user_id(current_user)
    await _ensure_teacher(acting_user_id, org_id, db_session)
    statement = select(SelfTestAttempt).where(SelfTestAttempt.org_id == org_id)
    if user_id is not None:
        statement = statement.where(SelfTestAttempt.user_id == user_id)
    attempts = (await db_session.execute(statement.order_by(SelfTestAttempt.creation_date.desc(), SelfTestAttempt.id.desc()))).scalars().all()
    return [await _attempt_read(attempt, db_session, reveal_answers=True) for attempt in attempts]


async def read_self_test_attempt(
    attempt_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> SelfTestAttemptRead:
    attempt = await _get_attempt_or_404(attempt_uuid, db_session)
    user_id = resolve_acting_user_id(current_user)
    if attempt.user_id != user_id:
        await _ensure_teacher(user_id, attempt.org_id, db_session)
        return await _attempt_read(attempt, db_session, reveal_answers=True)
    await require_org_membership(user_id, attempt.org_id, db_session)
    return await _attempt_read(attempt, db_session, reveal_answers=attempt.status != SelfTestAttemptStatus.STARTED)


async def review_self_test_attempt(
    attempt_uuid: str,
    request_body: ReviewSelfTestAttemptRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> SelfTestAttemptRead:
    attempt = await _get_attempt_or_404(attempt_uuid, db_session)
    teacher_id = resolve_acting_user_id(current_user)
    await _ensure_teacher(teacher_id, attempt.org_id, db_session)
    if attempt.status == SelfTestAttemptStatus.STARTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot review an unsubmitted self-test")

    if request_body.teacher_score is not None:
        attempt.teacher_score = max(0, min(int(request_body.teacher_score), int(attempt.max_score or 0)))
    attempt.teacher_feedback = request_body.teacher_feedback
    attempt.counts_for_grade = request_body.counts_for_grade
    attempt.reviewed_by_user_id = teacher_id
    attempt.reviewed_at = _now()
    attempt.update_date = _now()
    attempt.status = SelfTestAttemptStatus.REVIEWED
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)
    return await _attempt_read(attempt, db_session, reveal_answers=True)
