import random
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.db.organizations import Organization
from src.db.question_bank import QuestionBankItem, QuestionBankVisibilityEnum
from src.db.self_tests import (
    ReviewSelfTestAttemptRequest,
    SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT,
    SIMPLE_SELF_TEST_MAX_QUESTION_COUNT,
    SelfTestAttempt,
    SelfTestAttemptQuestion,
    SelfTestAttemptQuestionRead,
    SelfTestAttemptRead,
    SelfTestAttemptStatus,
    StartSelfTestRequest,
    SubmitSelfTestRequest,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import require_org_membership, require_org_role_permission
from src.services.courses.activities.assignments import (
    _server_verified_task_feedback,
    _server_verified_task_grade,
)
from src.services.self_test_readiness import (
    SIMPLE_SELF_TEST_TYPES,
    is_self_test_ready_item,
)


def _now() -> str:
    return str(datetime.now())


def _drop_answer_key_fields(data: dict) -> dict:
    clean = dict(data or {})
    for key in (
        "assigned_right_answer",
        "correct",
        "is_correct",
        "isCorrect",
        "correctAnswer",
        "correct_answer",
        "correctOption",
        "correct_option",
        "answer",
    ):
        clean.pop(key, None)
    return clean


def _sanitize_contents(assignment_type: AssignmentTaskTypeEnum, contents: dict, reveal_answers: bool) -> dict:
    data = dict(contents or {})
    if reveal_answers:
        return data

    if assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        data.pop("correct_answers", None)
        data.pop("accepted_answers", None)
        data.pop("correctAnswer", None)
        data.pop("correct_answer", None)
        data.pop("answer", None)
        data.pop("explanation", None)
    elif assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER:
        data.pop("correct_value", None)
        data.pop("tolerance", None)
        data.pop("explanation", None)
    elif assignment_type == AssignmentTaskTypeEnum.QUIZ:
        questions = []
        for question in data.get("questions") or []:
            clean_question = _drop_answer_key_fields(question or {})
            clean_options = []
            for option in clean_question.get("options") or []:
                clean_option = _drop_answer_key_fields(option or {})
                clean_options.append(clean_option)
            clean_question["options"] = clean_options
            questions.append(clean_question)
        data["questions"] = questions
    elif assignment_type == AssignmentTaskTypeEnum.FORM:
        questions = []
        for question in data.get("questions") or []:
            clean_question = _drop_answer_key_fields(question or {})
            clean_blanks = []
            for blank in clean_question.get("blanks") or []:
                clean_blank = _drop_answer_key_fields(blank or {})
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
        return list(SIMPLE_SELF_TEST_TYPES)
    allowed = []
    for item in types:
        if item not in SIMPLE_SELF_TEST_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="自測只支援簡單題型：選擇題、填空題、短問答。",
            )
        allowed.append(item)
    return allowed


def _submitted_answer_value(answer: dict, *, question_uuid: str, option_uuid: str | None = None, blank_uuid: str | None = None):
    submissions = answer.get("submissions") if isinstance(answer, dict) else None
    if not isinstance(submissions, list):
        return None
    for submission in submissions:
        if not isinstance(submission, dict):
            continue
        if submission.get("questionUUID") != question_uuid:
            continue
        if option_uuid is not None and submission.get("optionUUID") != option_uuid:
            continue
        if blank_uuid is not None and submission.get("blankUUID") != blank_uuid:
            continue
        return submission.get("answer")
    return None


def _coerce_simple_answer_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "correct", "right", "是", "對", "正確"}:
            return True
        if normalized in {"false", "0", "no", "n", "incorrect", "wrong", "否", "錯", "錯誤", ""}:
            return False
    return False


def _is_answer_complete(question: SelfTestAttemptQuestion, answer: dict) -> bool:
    if not isinstance(answer, dict):
        return False
    contents = question.contents or {}

    if question.assignment_type == AssignmentTaskTypeEnum.QUIZ:
        quiz_questions = contents.get("questions") or []
        if not quiz_questions:
            return False
        for quiz_question in quiz_questions:
            question_uuid = quiz_question.get("questionUUID")
            options = quiz_question.get("options") or []
            if not question_uuid or not options:
                return False
            has_selection = any(
                _coerce_simple_answer_bool(
                    _submitted_answer_value(
                        answer,
                        question_uuid=question_uuid,
                        option_uuid=option.get("optionUUID"),
                    )
                )
                for option in options
                if option.get("optionUUID")
            )
            if not has_selection:
                return False
        return True

    if question.assignment_type == AssignmentTaskTypeEnum.FORM:
        form_questions = contents.get("questions") or []
        if not form_questions:
            return False
        for form_question in form_questions:
            question_uuid = form_question.get("questionUUID")
            blanks = form_question.get("blanks") or []
            if not question_uuid or not blanks:
                return False
            for blank in blanks:
                blank_uuid = blank.get("blankUUID")
                if not blank_uuid:
                    return False
                value = _submitted_answer_value(
                    answer,
                    question_uuid=question_uuid,
                    blank_uuid=blank_uuid,
                )
                if not str(value or "").strip():
                    return False
        return True

    if question.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        return bool(str(answer.get("answer") or "").strip())

    return False


def _select_self_test_items(
    candidates: list[QuestionBankItem],
    question_count: int,
) -> list[QuestionBankItem]:
    selected_count = min(question_count, len(candidates))
    if selected_count <= 0:
        return []

    return sorted(
        candidates,
        key=lambda item: (int(item.usage_count or 0), random.random()),
    )[:selected_count]


async def start_self_test(
    request_body: StartSelfTestRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> SelfTestAttemptRead:
    await _get_org_or_404(request_body.org_id, db_session)
    user_id = resolve_acting_user_id(current_user)
    await require_org_membership(user_id, request_body.org_id, db_session)

    existing_attempt = (
        await db_session.execute(
            select(SelfTestAttempt)
            .where(
                SelfTestAttempt.org_id == request_body.org_id,
                SelfTestAttempt.user_id == user_id,
                SelfTestAttempt.status == SelfTestAttemptStatus.STARTED,
            )
            .order_by(SelfTestAttempt.creation_date.desc(), SelfTestAttempt.id.desc())
        )
    ).scalars().first()
    if existing_attempt:
        return await _attempt_read(existing_attempt, db_session, reveal_answers=False)

    question_count = max(
        1,
        min(
            int(request_body.question_count or SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT),
            SIMPLE_SELF_TEST_MAX_QUESTION_COUNT,
        ),
    )
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
    candidates = [item for item in candidates if is_self_test_ready_item(item)]

    if not candidates:
        detail = (
            "找不到符合練習範圍的題目。可以清除練習範圍，或請老師在題庫加入相關標籤的選擇題、填空題或短問答。"
            if tags or request_body.category_id is not None
            else "暫時沒有可用的共享題庫題目。請老師先在題庫加入內容完整的選擇題、填空題或短問答。"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

    selected = _select_self_test_items(candidates, question_count)
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="你只能提交自己的自測。")
    await require_org_membership(user_id, attempt.org_id, db_session)
    if attempt.status != SelfTestAttemptStatus.STARTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="這份自測已經提交。")

    questions = (
        await db_session.execute(select(SelfTestAttemptQuestion).where(SelfTestAttemptQuestion.attempt_id == attempt.id))
    ).scalars().all()
    by_uuid = {question.attempt_question_uuid: question for question in questions}
    answers = {answer.attempt_question_uuid: answer.answer for answer in request_body.answers}
    unknown = set(answers.keys()) - set(by_uuid.keys())
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部分答案不屬於這份自測。")

    missing_answers = [
        question.attempt_question_uuid
        for question in questions
        if not _is_answer_complete(question, answers.get(question.attempt_question_uuid, {}))
    ]
    if missing_answers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"請先完成所有題目再提交。尚有 {len(missing_answers)} 題未作答。",
        )

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
        question.feedback = _server_verified_task_feedback(
            question.grade,
            int(question.max_grade or 0),
        )
        total += question.grade
        max_score += int(question.max_grade or 0)
        db_session.add(question)

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


async def discard_started_self_test(
    attempt_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> dict:
    attempt = await _get_attempt_or_404(attempt_uuid, db_session)
    user_id = resolve_acting_user_id(current_user)
    if attempt.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="你只能放棄自己的自測。")
    await require_org_membership(user_id, attempt.org_id, db_session)
    if attempt.status != SelfTestAttemptStatus.STARTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="已提交的自測記錄不能刪除。")

    questions = (
        await db_session.execute(
            select(SelfTestAttemptQuestion).where(SelfTestAttemptQuestion.attempt_id == attempt.id)
        )
    ).scalars().all()
    item_ids = {
        question.question_bank_item_id
        for question in questions
        if question.question_bank_item_id is not None
    }
    if item_ids:
        items = (
            await db_session.execute(
                select(QuestionBankItem).where(QuestionBankItem.id.in_(item_ids))
            )
        ).scalars().all()
        for item in items:
            item.usage_count = max(0, int(item.usage_count or 0) - 1)
            item.update_date = _now()
            db_session.add(item)

    await db_session.execute(
        delete(SelfTestAttemptQuestion).where(SelfTestAttemptQuestion.attempt_id == attempt.id)
    )
    await db_session.delete(attempt)
    await db_session.commit()
    return {"ok": True}


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
    statement = (
        select(SelfTestAttempt)
        .join(UserOrganization, UserOrganization.user_id == SelfTestAttempt.user_id)  # type: ignore
        .where(
            SelfTestAttempt.org_id == org_id,
            UserOrganization.org_id == org_id,
        )
    )
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未提交的自測不能確認評分。")

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
