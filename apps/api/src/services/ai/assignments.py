import json
import logging
import math
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskCreate,
    AssignmentTaskRead,
    AssignmentTaskTypeEnum,
)
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.security.auth import resolve_acting_user_id
from src.security.features_utils.usage import refund_ai_credit, reserve_ai_credit
from src.security.rbac import AccessAction, check_resource_access
from src.services.ai.base import generate_openai_compatible_image, get_gemini_client
from src.services.ai.schemas.assignments import (
    GeneratedAssignmentTaskDraft,
    GenerateAssignmentTasksRequest,
    GenerateAssignmentTasksResponse,
)
from src.services.courses.activities.assignments import create_assignment_task
from src.services.security.rate_limiting import enforce_ai_rate_limit
from src.services.utils.upload_content import upload_content

logger = logging.getLogger(__name__)

AUTO_GRADABLE_ASSIGNMENT_TYPES = {
    AssignmentTaskTypeEnum.QUIZ,
    AssignmentTaskTypeEnum.FORM,
    AssignmentTaskTypeEnum.CODE,
    AssignmentTaskTypeEnum.SHORT_ANSWER,
    AssignmentTaskTypeEnum.NUMBER_ANSWER,
}


def _clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip() or fallback


def _extract_json_object(raw: str) -> dict:
    cleaned = (raw or "").strip()
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        if end != -1:
            cleaned = cleaned[start:end].strip()
    elif "```" in cleaned:
        start = cleaned.find("```") + 3
        end = cleaned.find("```", start)
        if end != -1:
            cleaned = cleaned[start:end].strip()

    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        if start != -1:
            cleaned = cleaned[start:]
    if not cleaned.endswith("}"):
        end = cleaned.rfind("}")
        if end != -1:
            cleaned = cleaned[: end + 1]
    return json.loads(cleaned)


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def _normalize_quiz_contents(contents: dict) -> dict:
    questions = contents.get("questions")
    if not isinstance(questions, list):
        questions = []

    normalized_questions = []
    for question in questions[:6]:
        if not isinstance(question, dict):
            continue
        question_text = _clean_text(question.get("questionText") or question.get("question"))
        options = question.get("options")
        if not question_text or not isinstance(options, list):
            continue

        normalized_options = []
        for option in options[:5]:
            if not isinstance(option, dict):
                continue
            text = _clean_text(option.get("text") or option.get("label") or option.get("answer"))
            if not text:
                continue
            normalized_options.append(
                {
                    "optionUUID": option.get("optionUUID") or _short_id("option"),
                    "text": text,
                    "fileID": "",
                    "type": "text",
                    "assigned_right_answer": bool(
                        option.get("assigned_right_answer", option.get("correct", False))
                    ),
                }
            )

        if len(normalized_options) < 2:
            continue
        if not any(option["assigned_right_answer"] for option in normalized_options):
            normalized_options[0]["assigned_right_answer"] = True

        normalized_questions.append(
            {
                "questionText": question_text,
                "questionUUID": question.get("questionUUID") or _short_id("question"),
                "options": normalized_options,
            }
        )

    if not normalized_questions:
        raise ValueError("QUIZ task needs at least one valid question with two options")
    return {"questions": normalized_questions}


def _normalize_form_contents(contents: dict) -> dict:
    questions = contents.get("questions")
    if not isinstance(questions, list):
        questions = []
    normalized_questions = []
    for question in questions[:6]:
        if not isinstance(question, dict):
            continue
        question_text = _clean_text(question.get("questionText") or question.get("question"))
        blanks = question.get("blanks")
        if not question_text or not isinstance(blanks, list):
            continue
        normalized_blanks = []
        for blank in blanks[:6]:
            if not isinstance(blank, dict):
                continue
            correct_answer = _clean_text(blank.get("correctAnswer") or blank.get("answer"))
            if not correct_answer:
                continue
            normalized_blanks.append(
                {
                    "blankUUID": blank.get("blankUUID") or _short_id("blank"),
                    "placeholder": _clean_text(blank.get("placeholder"), "Enter answer"),
                    "correctAnswer": correct_answer,
                    "hint": _clean_text(blank.get("hint")),
                }
            )
        if normalized_blanks:
            normalized_questions.append(
                {
                    "questionText": question_text,
                    "questionUUID": question.get("questionUUID") or _short_id("question"),
                    "blanks": normalized_blanks,
                }
            )
    if not normalized_questions:
        raise ValueError("FORM task needs at least one valid blank")
    return {"questions": normalized_questions}


def _normalize_short_answer_contents(contents: dict) -> dict:
    prompt = _clean_text(contents.get("prompt") or contents.get("question"))
    answers = contents.get("correct_answers") or contents.get("accepted_answers") or []
    if isinstance(answers, str):
        answers = [answers]
    answers = [_clean_text(answer) for answer in answers if _clean_text(answer)]
    match_mode = contents.get("match_mode") or "case_insensitive"
    if match_mode not in {"exact", "case_insensitive", "contains", "regex"}:
        match_mode = "case_insensitive"
    if not prompt or not answers:
        raise ValueError("SHORT_ANSWER task needs a prompt and accepted answers")
    return {
        "prompt": prompt,
        "correct_answers": answers[:8],
        "match_mode": match_mode,
        "explanation": _clean_text(contents.get("explanation")),
    }


def _normalize_number_answer_contents(contents: dict) -> dict:
    prompt = _clean_text(contents.get("prompt") or contents.get("question"))
    if not prompt:
        raise ValueError("NUMBER_ANSWER task needs a prompt")
    try:
        correct_value = float(contents.get("correct_value"))
        tolerance = abs(float(contents.get("tolerance", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("NUMBER_ANSWER task needs numeric correct_value and tolerance") from exc
    if not math.isfinite(correct_value) or not math.isfinite(tolerance):
        raise ValueError("NUMBER_ANSWER values must be finite")
    return {
        "prompt": prompt,
        "correct_value": correct_value,
        "tolerance": tolerance,
        "unit": _clean_text(contents.get("unit")),
        "explanation": _clean_text(contents.get("explanation")),
    }


def _normalize_code_contents(contents: dict) -> dict:
    test_cases = contents.get("test_cases")
    if not isinstance(test_cases, list):
        test_cases = []
    normalized_cases = []
    for index, test_case in enumerate(test_cases[:10]):
        if not isinstance(test_case, dict):
            continue
        expected = test_case.get("expectedStdout")
        if expected is None:
            expected = test_case.get("expected_stdout")
        normalized_cases.append(
            {
                "id": test_case.get("id") or _short_id("tc"),
                "label": _clean_text(test_case.get("label"), f"Test {index + 1}"),
                "stdin": _clean_text(test_case.get("stdin")),
                "expectedStdout": _clean_text(expected),
                "hidden": bool(test_case.get("hidden", index > 1)),
                "weight": max(1, int(test_case.get("weight") or 1)),
            }
        )
    if not normalized_cases:
        raise ValueError("CODE task needs at least one test case")

    grading_mode = contents.get("grading_mode") or "equal_weight"
    if grading_mode not in {"equal_weight", "binary", "custom_weights"}:
        grading_mode = "equal_weight"
    return {
        "language_id": int(contents.get("language_id") or 71),
        "starter_code": _clean_text(contents.get("starter_code"), "# Write your code here\n"),
        "solution_code": _clean_text(contents.get("solution_code")),
        "grading_mode": grading_mode,
        "test_cases": normalized_cases,
        "allow_student_run": bool(contents.get("allow_student_run", True)),
        "show_test_details_on_fail": bool(contents.get("show_test_details_on_fail", True)),
        "show_hidden_test_count": bool(contents.get("show_hidden_test_count", True)),
        "require_passing_to_submit": bool(contents.get("require_passing_to_submit", False)),
        "show_solution_after_submit": bool(contents.get("show_solution_after_submit", False)),
    }


def normalize_generated_task(raw_task: dict) -> GeneratedAssignmentTaskDraft:
    assignment_type = AssignmentTaskTypeEnum(raw_task.get("assignment_type"))
    if assignment_type not in AUTO_GRADABLE_ASSIGNMENT_TYPES:
        raise ValueError(f"Unsupported assignment_type: {assignment_type}")

    contents = raw_task.get("contents") or {}
    if not isinstance(contents, dict):
        raise ValueError("contents must be an object")

    if assignment_type == AssignmentTaskTypeEnum.QUIZ:
        contents = _normalize_quiz_contents(contents)
    elif assignment_type == AssignmentTaskTypeEnum.FORM:
        contents = _normalize_form_contents(contents)
    elif assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        contents = _normalize_short_answer_contents(contents)
    elif assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER:
        contents = _normalize_number_answer_contents(contents)
    elif assignment_type == AssignmentTaskTypeEnum.CODE:
        contents = _normalize_code_contents(contents)

    return GeneratedAssignmentTaskDraft(
        title=_clean_text(raw_task.get("title"), "AI generated question")[:160],
        description=_clean_text(raw_task.get("description"))[:2000],
        hint=_clean_text(raw_task.get("hint"))[:1000],
        assignment_type=assignment_type,
        contents=contents,
        image_prompt=_clean_text(raw_task.get("image_prompt")) or None,
    )


def _build_assignment_generation_prompt(request_body: GenerateAssignmentTasksRequest) -> str:
    return f"""You are generating teacher-owned homework questions for LearnHouse.

Return ONLY valid JSON. The questions will be saved directly as auto-gradable assignment tasks.

Language for learner-facing text: {request_body.language}
Difficulty: {request_body.difficulty}
Number of tasks: {request_body.count}
Allowed assignment_type values: {", ".join(request_body.question_types)}
Use images: {"yes, include image_prompt when a visual would materially improve the question" if request_body.include_images else "no"}

Teacher brief:
{request_body.prompt}

Required JSON shape:
{{
  "tasks": [
    {{
      "assignment_type": "QUIZ",
      "title": "Short teacher-facing title",
      "description": "Student prompt or instructions",
      "hint": "Optional hint",
      "image_prompt": "Optional image generation prompt, only when useful",
      "contents": {{
        "questions": [
          {{
            "questionText": "Question text",
            "options": [
              {{"text": "Option A", "assigned_right_answer": true}},
              {{"text": "Option B", "assigned_right_answer": false}},
              {{"text": "Option C", "assigned_right_answer": false}},
              {{"text": "Option D", "assigned_right_answer": false}}
            ]
          }}
        ]
      }}
    }}
  ]
}}

Task content schemas:
- QUIZ: contents.questions[].questionText; options[].text; options[].assigned_right_answer. At least 2 options and at least one correct option.
- FORM: contents.questions[].questionText; blanks[].placeholder; blanks[].correctAnswer; blanks[].hint.
- SHORT_ANSWER: contents.prompt; contents.correct_answers string[]; contents.match_mode one of exact, case_insensitive, contains, regex; contents.explanation.
- NUMBER_ANSWER: contents.prompt; contents.correct_value number; contents.tolerance number; contents.unit; contents.explanation.
- CODE: Python by default with contents.language_id=71; starter_code; solution_code; grading_mode; test_cases with stdin and expectedStdout. Include 2-5 tests, with at least one hidden test.

Quality rules:
- Make every task independently answerable.
- Avoid ambiguous answer keys.
- For CODE, expectedStdout must exactly match stdout after normal trailing-whitespace normalization.
- Do not include file upload, essays, or human-only grading tasks.
- Generate exactly {request_body.count} tasks unless the prompt is impossible.
"""


async def _attach_ai_reference_image(
    assignment: Assignment,
    course: Course,
    org: Organization,
    task: AssignmentTaskRead,
    image_prompt: str,
    db_session: AsyncSession,
) -> AssignmentTaskRead:
    await reserve_ai_credit(org.id or 0, db_session, amount=3)
    try:
        image_bytes, image_format, _ = generate_openai_compatible_image(
            image_prompt,
            size="1024x1024",
            quality="medium",
        )
    except Exception:
        refund_ai_credit(org.id or 0, 3)
        raise

    if image_format == "jpeg":
        image_format = "jpg"
    if image_format not in {"png", "jpg", "webp"}:
        image_format = "png"

    file_name = f"{uuid4()}_ai_question_reference.{image_format}"
    task_row = (
        await db_session.execute(
            select(AssignmentTask).where(
                AssignmentTask.assignment_task_uuid == task.assignment_task_uuid
            )
        )
    ).scalars().first()
    if not task_row:
        raise HTTPException(status_code=404, detail="Assignment task not found")

    activity = (
        await db_session.execute(select(Activity).where(Activity.id == assignment.activity_id))
    ).scalars().first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    directory = (
        f"courses/{course.course_uuid}/activities/{activity.activity_uuid}"
        f"/assignments/{assignment.assignment_uuid}/tasks/{task.assignment_task_uuid}"
    )
    await upload_content(
        directory=directory,
        type_of_dir="orgs",
        uuid=org.org_uuid,
        file_binary=image_bytes,
        file_and_format=file_name,
        allowed_formats=["png", "jpg", "webp"],
    )

    task_row.reference_file = file_name
    task_row.update_date = str(datetime.now())
    db_session.add(task_row)
    await db_session.commit()
    await db_session.refresh(task_row)
    return AssignmentTaskRead.model_validate(task_row)


async def generate_assignment_tasks(
    request: Request,
    request_body: GenerateAssignmentTasksRequest,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> GenerateAssignmentTasksResponse:
    assignment = (
        await db_session.execute(
            select(Assignment).where(Assignment.assignment_uuid == request_body.assignment_uuid)
        )
    ).scalars().first()
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")

    course = (
        await db_session.execute(select(Course).where(Course.id == assignment.course_id))
    ).scalars().first()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    org = (
        await db_session.execute(select(Organization).where(Organization.id == course.org_id))
    ).scalars().first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    await check_resource_access(
        request,
        db_session,
        current_user,
        course.course_uuid,
        AccessAction.UPDATE,
    )

    acting_user_id = resolve_acting_user_id(current_user)
    enforce_ai_rate_limit(acting_user_id, org.id or course.org_id)
    await reserve_ai_credit(org.id or course.org_id, db_session, amount=1)

    try:
        response = get_gemini_client().models.generate_content(
            model="gemini-2.0-flash",
            contents=[{"role": "user", "parts": [{"text": _build_assignment_generation_prompt(request_body)}]}],
            config={
                "temperature": 0.45,
                "max_output_tokens": 6000,
                "response_mime_type": "application/json",
            },
        )
        payload = _extract_json_object(response.text)
    except Exception as exc:
        refund_ai_credit(org.id or course.org_id, 1)
        logger.exception("AI assignment task generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Assignment task generation failed: {str(exc)}",
        ) from exc

    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(raw_tasks, list):
        raise HTTPException(status_code=502, detail="AI returned no task list")

    warnings: list[str] = []
    drafts: list[GeneratedAssignmentTaskDraft] = []
    for raw_task in raw_tasks[: request_body.count]:
        try:
            drafts.append(normalize_generated_task(raw_task))
        except Exception as exc:
            warnings.append(f"Skipped invalid task: {str(exc)}")

    if not drafts:
        raise HTTPException(status_code=502, detail="AI did not return any valid auto-gradable tasks")

    created_tasks: list[AssignmentTaskRead] = []
    for draft in drafts:
        task = await create_assignment_task(
            request,
            assignment.assignment_uuid,
            AssignmentTaskCreate(
                title=draft.title,
                description=draft.description,
                hint=draft.hint,
                reference_file="",
                assignment_type=draft.assignment_type,
                contents=draft.contents,
                max_grade_value=100,
            ),
            current_user,
            db_session,
        )
        if request_body.include_images and draft.image_prompt:
            try:
                task = await _attach_ai_reference_image(
                    assignment,
                    course,
                    org,
                    task,
                    draft.image_prompt,
                    db_session,
                )
            except Exception as exc:
                logger.exception("Failed to attach AI generated reference image")
                warnings.append(f"Image was not attached to {draft.title}: {str(exc)}")
        created_tasks.append(task)

    return GenerateAssignmentTasksResponse(tasks=created_tasks, warnings=warnings)
