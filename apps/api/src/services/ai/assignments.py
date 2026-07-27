import json
import logging
import asyncio
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
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
from src.services.ai.assignment_config import (
    _config_read_error_message,
    assignment_ai_configuration_error,
    assignment_generation_model,
)
from src.services.ai.base import get_gemini_client
from src.services.ai.schemas.assignments import (
    GeneratedAssignmentTaskDraft,
    GenerateAssignmentTasksRequest,
    GenerateAssignmentTasksResponse,
)
from src.services.courses.activities.assignments import (
    _assignment_has_locked_submissions,
    create_assignment_task,
    SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS,
)
from src.services.question_bank import create_question_bank_item
from src.db.question_bank import QuestionBankItemCreate, QuestionBankItemRead
from src.services.security.rate_limiting import enforce_ai_rate_limit

logger = logging.getLogger(__name__)

SIMPLE_AI_ASSIGNMENT_TYPES = {
    AssignmentTaskTypeEnum.QUIZ,
    AssignmentTaskTypeEnum.FORM,
    AssignmentTaskTypeEnum.SHORT_ANSWER,
}
SIMPLE_AI_ASSIGNMENT_TYPE_VALUES = ("QUIZ", "FORM", "SHORT_ANSWER")
SIMPLE_AI_MAX_QUIZ_OPTIONS = 4
SIMPLE_AI_MAX_SHORT_ANSWER_KEYS = 3
SIMPLE_AI_MAX_QUESTION_TEXT_LENGTH = 220
SIMPLE_AI_MAX_OPTION_TEXT_LENGTH = 120
SIMPLE_AI_MAX_FILL_BLANK_ANSWER_LENGTH = 40
SIMPLE_AI_MAX_SHORT_ANSWER_LENGTH = 40
SIMPLE_AI_SIMPLIFIED_TO_TRADITIONAL_PHRASES = (
    ("澳门", "澳門"),
    ("选择题", "選擇題"),
    ("选择", "選擇"),
    ("填空题", "填空題"),
    ("问答题", "問答題"),
    ("简答题", "簡答題"),
    ("短问答", "短問答"),
    ("为什么", "為甚麼"),
    ("什么", "甚麼"),
    ("学生", "學生"),
    ("学习", "學習"),
    ("练习", "練習"),
    ("课题", "課題"),
    ("课程", "課程"),
    ("课后", "課後"),
    ("测验", "測驗"),
    ("正确答案", "正確答案"),
    ("参考答案", "參考答案"),
)
SIMPLE_AI_SIMPLIFIED_TO_TRADITIONAL_CHARS = str.maketrans(
    {
        "这": "這",
        "个": "個",
        "们": "們",
        "学": "學",
        "习": "習",
        "题": "題",
        "选": "選",
        "择": "擇",
        "对": "對",
        "错": "錯",
        "误": "誤",
        "确": "確",
        "读": "讀",
        "写": "寫",
        "识": "識",
        "说": "說",
        "为": "為",
        "于": "於",
        "么": "麼",
        "国": "國",
        "语": "語",
        "号": "號",
        "数": "數",
        "较": "較",
        "时": "時",
        "间": "間",
        "节": "節",
        "课": "課",
        "后": "後",
        "练": "練",
        "测": "測",
        "验": "驗",
        "图": "圖",
        "发": "發",
        "现": "現",
        "电": "電",
        "脑": "腦",
        "关": "關",
        "键": "鍵",
        "词": "詞",
        "义": "義",
        "内": "內",
        "实": "實",
        "质": "質",
        "计": "計",
        "应": "應",
        "当": "當",
        "会": "會",
        "开": "開",
        "门": "門",
        "见": "見",
        "气": "氣",
        "体": "體",
        "变": "變",
        "过": "過",
        "钟": "鐘",
        "线": "線",
        "点": "點",
        "与": "與",
        "简": "簡",
        "单": "單",
        "项": "項",
        "类": "類",
        "区": "區",
        "东": "東",
        "广": "廣",
        "湾": "灣",
        "岛": "島",
        "积": "積",
        "极": "極",
        "热": "熱",
        "压": "壓",
        "长": "長",
        "团": "團",
        "队": "隊",
    }
)
SIMPLE_AI_ASSIGNMENT_TYPE_ALIASES = {
    "CHOICE": "QUIZ",
    "MCQ": "QUIZ",
    "MULTIPLE_CHOICE": "QUIZ",
    "MULTIPLECHOICE": "QUIZ",
    "選擇題": "QUIZ",
    "單選題": "QUIZ",
    "多項選擇題": "QUIZ",
    "FILL_BLANK": "FORM",
    "FILL_IN_BLANK": "FORM",
    "FILL_IN_THE_BLANK": "FORM",
    "FILLINTHEBLANK": "FORM",
    "BLANK": "FORM",
    "填空題": "FORM",
    "填充題": "FORM",
    "QA": "SHORT_ANSWER",
    "Q&A": "SHORT_ANSWER",
    "QUESTION_ANSWER": "SHORT_ANSWER",
    "SHORTANSWER": "SHORT_ANSWER",
    "問答": "SHORT_ANSWER",
    "問答題": "SHORT_ANSWER",
    "簡答": "SHORT_ANSWER",
    "簡答題": "SHORT_ANSWER",
    "短問答": "SHORT_ANSWER",
}


def _assignment_ai_configuration_error() -> str | None:
    try:
        return assignment_ai_configuration_error(get_learnhouse_config())
    except Exception as exc:
        return _config_read_error_message(exc)


def _assignment_generation_model() -> str:
    try:
        return assignment_generation_model(get_learnhouse_config())
    except Exception:
        return ""


def _refund_reserved_ai_credit(org_id: int, amount: int = 1) -> None:
    try:
        refund_ai_credit(org_id, amount)
    except Exception:
        logger.exception("Failed to refund reserved AI credit for org_id=%s", org_id)


def _clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip() or fallback


def _to_traditional_chinese_text(value: str) -> str:
    text = value
    for simplified, traditional in SIMPLE_AI_SIMPLIFIED_TO_TRADITIONAL_PHRASES:
        text = text.replace(simplified, traditional)
    return text.translate(SIMPLE_AI_SIMPLIFIED_TO_TRADITIONAL_CHARS)


def _normalize_generated_task_language(value: Any) -> Any:
    if isinstance(value, str):
        return _to_traditional_chinese_text(value)
    if isinstance(value, list):
        return [_normalize_generated_task_language(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_generated_task_language(item)
            for key, item in value.items()
        }
    return value


def _coerce_simple_bool(value: Any) -> bool:
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


def _meaningful_generation_context_residue(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    generic_values = {
        "作業",
        "新作業",
        "未命名作業",
        "課程",
        "新課程",
        "未命名課程",
        "練習",
        "課後練習",
        "小測驗",
        "測驗",
        "本課內容",
        "根據本課內容",
        "course",
        "homework",
        "assignment",
        "test course",
        "a test course",
        "test assignment",
    }
    if text.lower() in generic_values:
        return ""

    residue = text
    for phrase in (
        "根據本課內容",
        "根據以上內容",
        "本課內容",
        "生成",
        "出題",
        "題目",
        "作業",
        "課程",
        "練習",
        "基礎",
        "簡單",
        "選擇題",
        "填空題",
        "短問答",
        "問答題",
        "自動批改",
        "學生",
        "請",
        "為",
        "包含",
        "只包含",
        "答案",
        "清楚",
    ):
        residue = residue.replace(phrase, "")
    residue = re.sub(r"[0-9０-９一二三四五六七八九十]+\s*題", "", residue)
    residue = re.sub(r"[0-9０-９一二三四五六七八九十]+", "", residue)
    residue = re.sub(r"[\s，。；：！？、,.!?;:()（）【】《》「」『』\"'`]+", "", residue)
    return residue


def _specific_generation_context_value(value: Any) -> str:
    text = _clean_text(value)
    if len(_meaningful_generation_context_residue(text)) < 2:
        return ""
    return text


def _has_specific_generation_context(request_body: GenerateAssignmentTasksRequest) -> bool:
    candidates: list[Any] = [
        request_body.assignment_title,
        request_body.assignment_description,
        request_body.unit,
        request_body.prompt,
        *(request_body.learning_objectives or []),
    ]
    return any(len(_meaningful_generation_context_residue(candidate)) >= 2 for candidate in candidates)


def _simple_question_types(question_types: list[str] | None) -> list[str]:
    selected: list[str] = []
    for question_type in question_types or []:
        value = _normalize_simple_assignment_type_value(question_type)
        if value in SIMPLE_AI_ASSIGNMENT_TYPE_VALUES and value not in selected:
            selected.append(value)
    return selected or list(SIMPLE_AI_ASSIGNMENT_TYPE_VALUES)


def _normalize_simple_assignment_type_value(value: Any) -> str:
    raw_value = _clean_text(getattr(value, "value", value)).upper()
    normalized = re.sub(r"[\s\-]+", "_", raw_value)
    return SIMPLE_AI_ASSIGNMENT_TYPE_ALIASES.get(
        normalized,
        SIMPLE_AI_ASSIGNMENT_TYPE_ALIASES.get(raw_value, normalized),
    )


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
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_text = _clean_text(
            question.get("questionText") or question.get("question")
        )[:SIMPLE_AI_MAX_QUESTION_TEXT_LENGTH]
        options = question.get("options")
        if not question_text or not isinstance(options, list):
            continue

        question_correct_answer = _clean_text(
            question.get("correctAnswer")
            or question.get("correct_answer")
            or question.get("answer")
            or question.get("correctOption")
            or question.get("correct_option")
        )
        normalized_options = []
        for option in options:
            if not isinstance(option, dict):
                continue
            text = _clean_text(
                option.get("text") or option.get("label") or option.get("answer")
            )[:SIMPLE_AI_MAX_OPTION_TEXT_LENGTH]
            if not text:
                continue
            option_marker = (
                option.get("assigned_right_answer")
                if "assigned_right_answer" in option
                else option.get("correct")
                if "correct" in option
                else option.get("is_correct")
                if "is_correct" in option
                else option.get("isCorrect")
            )
            is_right_answer = _coerce_simple_bool(option_marker)
            if question_correct_answer:
                option_identifiers = {
                    text,
                    _clean_text(option.get("label")),
                    _clean_text(option.get("value")),
                    _clean_text(option.get("id")),
                }
                if question_correct_answer in option_identifiers:
                    is_right_answer = True
            normalized_options.append(
                {
                    "optionUUID": option.get("optionUUID") or _short_id("option"),
                    "text": text,
                    "fileID": "",
                    "type": "text",
                    "assigned_right_answer": is_right_answer,
                }
            )

        if len(normalized_options) < 2:
            continue
        correct_count = sum(1 for option in normalized_options if option["assigned_right_answer"])
        if correct_count != 1:
            raise ValueError("QUIZ task needs exactly one correct option")
        if len(normalized_options) > SIMPLE_AI_MAX_QUIZ_OPTIONS:
            correct_index = next(
                index
                for index, option in enumerate(normalized_options)
                if option["assigned_right_answer"]
            )
            if correct_index >= SIMPLE_AI_MAX_QUIZ_OPTIONS:
                normalized_options = (
                    normalized_options[: SIMPLE_AI_MAX_QUIZ_OPTIONS - 1]
                    + [normalized_options[correct_index]]
                )
            else:
                normalized_options = normalized_options[:SIMPLE_AI_MAX_QUIZ_OPTIONS]

        normalized_questions.append(
            {
                "questionText": question_text,
                "questionUUID": question.get("questionUUID") or _short_id("question"),
                "options": normalized_options,
            }
        )
        break

    if not normalized_questions:
        raise ValueError("QUIZ task needs at least one valid question with two options")
    return {"questions": normalized_questions}


def _normalize_form_contents(contents: dict) -> dict:
    questions = contents.get("questions")
    if not isinstance(questions, list):
        questions = []
    normalized_questions = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_text = _clean_text(
            question.get("questionText") or question.get("question")
        )[:SIMPLE_AI_MAX_QUESTION_TEXT_LENGTH]
        blanks = question.get("blanks")
        if not question_text or not isinstance(blanks, list):
            question_answer = _clean_text(
                question.get("correctAnswer")
                or question.get("correct_answer")
                or question.get("answer")
            )
            blanks = [{"correctAnswer": question_answer}] if question_text and question_answer else []
        if not question_text or not blanks:
            continue
        normalized_blank = None
        for blank in blanks:
            if not isinstance(blank, dict):
                continue
            correct_answer = _clean_text(blank.get("correctAnswer") or blank.get("answer"))
            if not correct_answer:
                continue
            if (
                len(correct_answer) > SIMPLE_AI_MAX_FILL_BLANK_ANSWER_LENGTH
                or "\n" in correct_answer
                or "。" in correct_answer
            ):
                continue
            normalized_blank = {
                "blankUUID": blank.get("blankUUID") or _short_id("blank"),
                "placeholder": _clean_text(blank.get("placeholder"), "填寫答案")[:40],
                "correctAnswer": correct_answer,
                "hint": _clean_text(blank.get("hint"))[:120],
            }
            break
        if normalized_blank:
            normalized_questions.append(
                {
                    "questionText": question_text,
                    "questionUUID": question.get("questionUUID") or _short_id("question"),
                    "blanks": [normalized_blank],
                }
            )
            break
    if not normalized_questions:
        raise ValueError("FORM task needs at least one valid blank")
    return {"questions": normalized_questions}


def _normalize_short_answer_contents(contents: dict) -> dict:
    prompt = _clean_text(contents.get("prompt") or contents.get("question"))[
        :SIMPLE_AI_MAX_QUESTION_TEXT_LENGTH
    ]
    answers = (
        contents.get("correct_answers")
        or contents.get("accepted_answers")
        or contents.get("correctAnswer")
        or contents.get("correct_answer")
        or contents.get("answer")
        or []
    )
    if isinstance(answers, str):
        answers = [answers]
    answers = list(dict.fromkeys(_clean_text(answer) for answer in answers if _clean_text(answer)))
    match_mode = "case_insensitive"
    if not prompt or not answers:
        raise ValueError("SHORT_ANSWER task needs a prompt and accepted answers")
    subjective_markers = (
        "分析",
        "討論",
        "評價",
        "感想",
        "作文",
        "短文",
        "段落",
        "總結",
        "解釋",
        "請說明",
        "說明原因",
        "主要原因",
        "為甚麼",
        "為什麼",
        "為何",
        "如何",
        "怎樣",
        "請比較",
        "比較一下",
        "請舉例",
        "舉例說明",
        "請列出",
        "列出",
        "請描述",
        "描述",
        "說出",
        "說明",
        "談談",
        "分享",
        "看法",
        "觀點",
        "建議",
        "證明",
        "推論",
        "反思",
        "創作",
        "設計",
        "原因",
        "影響",
        "優點",
        "缺點",
    )
    if any(marker in prompt for marker in subjective_markers):
        raise ValueError("SHORT_ANSWER task must be a simple factual Q&A")
    if any(
        len(answer) > SIMPLE_AI_MAX_SHORT_ANSWER_LENGTH
        or "\n" in answer
        or "。" in answer
        for answer in answers
    ):
        raise ValueError("SHORT_ANSWER accepted answers must stay short and auto-gradable")
    return {
        "prompt": prompt,
        "correct_answers": answers[:SIMPLE_AI_MAX_SHORT_ANSWER_KEYS],
        "match_mode": match_mode,
        "explanation": _clean_text(contents.get("explanation")),
    }


def _generated_task_student_prompt(assignment_type: AssignmentTaskTypeEnum, contents: dict) -> str:
    if assignment_type in {AssignmentTaskTypeEnum.QUIZ, AssignmentTaskTypeEnum.FORM}:
        questions = contents.get("questions") if isinstance(contents, dict) else None
        if isinstance(questions, list) and questions:
            first_question = questions[0]
            if isinstance(first_question, dict):
                return _clean_text(first_question.get("questionText"))[:2000]
    if assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        return _clean_text(contents.get("prompt") if isinstance(contents, dict) else "")[:2000]
    return ""


def _generated_task_title(raw_title: Any, student_prompt: str) -> str:
    title = _clean_text(raw_title)
    generic_titles = {
        "AI 自動生成題目",
        "自動生成題目",
        "AI 題目",
        "題目",
        "練習題",
        "新題目",
        "Generated question",
        "AI generated question",
    }
    if not title or title in generic_titles:
        title = student_prompt or "AI 自動生成題目"
    return title[:160]


def normalize_generated_task(
    raw_task: dict,
    allowed_type_values: set[str] | None = None,
) -> GeneratedAssignmentTaskDraft:
    raw_task = _normalize_generated_task_language(raw_task)
    assignment_type = AssignmentTaskTypeEnum(
        _normalize_simple_assignment_type_value(raw_task.get("assignment_type"))
    )
    if assignment_type not in SIMPLE_AI_ASSIGNMENT_TYPES:
        raise ValueError(f"Unsupported assignment_type: {assignment_type}")
    if allowed_type_values is not None and assignment_type.value not in allowed_type_values:
        raise ValueError(f"Unsupported simple AI assignment_type: {assignment_type.value}")

    contents = raw_task.get("contents") or {}
    if not isinstance(contents, dict):
        raise ValueError("contents must be an object")

    if assignment_type == AssignmentTaskTypeEnum.QUIZ:
        contents = _normalize_quiz_contents(contents)
    elif assignment_type == AssignmentTaskTypeEnum.FORM:
        contents = _normalize_form_contents(contents)
    elif assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        contents = _normalize_short_answer_contents(contents)

    student_prompt = _generated_task_student_prompt(assignment_type, contents)
    return GeneratedAssignmentTaskDraft(
        title=_generated_task_title(raw_task.get("title"), student_prompt),
        description=(
            _clean_text(raw_task.get("description"))
            or student_prompt
        )[:2000],
        hint=_clean_text(raw_task.get("hint"))[:1000],
        assignment_type=assignment_type,
        contents=contents,
    )


def _is_fallback_simple_ai_task_draft(draft: GeneratedAssignmentTaskDraft) -> bool:
    text = " ".join(
        [
            _clean_text(draft.title),
            _clean_text(draft.description),
            _clean_text(draft.hint),
            json.dumps(draft.contents or {}, ensure_ascii=False),
        ]
    )
    return (
        "AI 備用題" in text
        or "AI 備用" in text
        or "請老師檢查後再發布" in text
        or "這份練習的主題是" in text
        or "這份練習主要學習哪個主題" in text
        or "這份練習主要圍繞哪個主題" in text
    )


def _normalize_generated_task_drafts(
    raw_tasks: list,
    question_types: list[str],
    requested_count: int,
    request_context: GenerateAssignmentTasksRequest | None = None,
) -> tuple[list[GeneratedAssignmentTaskDraft], list[str]]:
    drafts: list[GeneratedAssignmentTaskDraft] = []
    warnings: list[str] = []
    skipped_count = 0
    allowed_type_values = set(question_types)

    for raw_task in raw_tasks:
        if len(drafts) >= requested_count:
            break
        try:
            drafts.append(normalize_generated_task(raw_task, allowed_type_values))
        except Exception:
            skipped_count += 1
            logger.warning("Skipped invalid AI generated assignment task", exc_info=True)

    if skipped_count:
        warnings.append(f"已略過 {skipped_count} 題格式不完整、無法自動批改的題目。")

    missing_count = max(0, requested_count - len(drafts))
    if missing_count:
        warnings.append(
            f"AI 本次少了 {missing_count} 題可自動批改的題目，"
            f"系統只建立已通過檢查的 {len(drafts)} 題。"
            "請補充課題後再生成，或用題庫/手動新增。"
        )

    return drafts, warnings


def _format_learning_objectives(objectives: list[str]) -> str:
    clean = [str(item).strip() for item in objectives or [] if str(item).strip()]
    if not clean:
        return "未提供"
    return "\n".join(f"- {item}" for item in clean[:10])


def _task_schema_instructions(question_types: list[str]) -> str:
    schema_lines = {
        "QUIZ": "- QUIZ: contents.questions[].questionText; options[].text; options[].assigned_right_answer. At least 2 options and exactly one correct option.",
        "FORM": "- FORM: contents.questions[].questionText; blanks[].placeholder; blanks[].correctAnswer; blanks[].hint.",
        "SHORT_ANSWER": "- SHORT_ANSWER: contents.prompt; contents.correct_answers string[]; contents.match_mode must be case_insensitive; contents.explanation. Use only short factual keywords, names, terms, numbers, symbols, or phrases that can be auto-checked. Provide at most 3 accepted answers.",
    }
    allowed = [item for item in question_types if item in schema_lines]
    return "\n".join(schema_lines[item] for item in allowed)


def _build_assignment_generation_prompt(request_body: GenerateAssignmentTasksRequest) -> str:
    question_types = _simple_question_types(request_body.question_types)
    teacher_brief = _clean_text(
        request_body.prompt,
        "請根據以上科目、年級、單元與學習目標，生成一份簡單可自動批改的基礎作業。",
    )
    return f"""You are generating teacher-owned homework questions for LearnHouse.

Return ONLY valid JSON. The questions will be saved directly as auto-gradable assignment tasks.

Language: use Traditional Chinese for Macau schools for all titles, instructions, hints, feedback-oriented text, and Chinese content. If the teacher brief uses Simplified Chinese, rewrite teacher-facing and student-facing Chinese into Traditional Chinese. Only keep a target-language word or phrase when the subject itself requires it, such as English vocabulary.
Assignment title: {request_body.assignment_title or "未提供"}
Assignment description: {request_body.assignment_description or "未提供"}
Subject: {request_body.subject or "未提供"}
Education stage: {request_body.education_stage or "未提供"}
Grade level: {request_body.grade_level or "未提供"}
Course unit: {request_body.unit or "未提供"}
Learning objectives:
{_format_learning_objectives(request_body.learning_objectives)}
Difficulty: beginner only. This school pilot intentionally avoids advanced, essay, coding, dedicated numeric-answer task types, file-upload, and rubric-heavy tasks in AI one-click homework. Numeric answers are allowed when they fit a simple FORM fill-in-the-blank or SHORT_ANSWER question.
Number of tasks: {request_body.count}
Allowed assignment_type values: {", ".join(question_types)}
Use images: no

Teacher brief:
{teacher_brief}

Required JSON shape:
{{
  "tasks": [
    {{
      "assignment_type": "QUIZ",
      "title": "澳門地理選擇題",
      "description": "請選出正確答案。",
      "hint": "留意地圖上的位置。",
      "contents": {{
        "questions": [
          {{
            "questionText": "澳門特別行政區位於哪個國家？",
            "options": [
              {{"text": "中國", "assigned_right_answer": true}},
              {{"text": "日本", "assigned_right_answer": false}},
              {{"text": "韓國", "assigned_right_answer": false}},
              {{"text": "泰國", "assigned_right_answer": false}}
            ]
          }}
        ]
      }}
    }}
  ]
}}

Task content schemas:
{_task_schema_instructions(question_types)}

Quality rules:
- Make every task independently answerable.
- Each task must contain exactly one learner question. Do not pack several small questions inside one task.
- Avoid ambiguous answer keys.
- Keep the homework simple enough for non-technical teachers to review quickly and for primary/secondary students to submit without extra instructions.
- Do not include file upload, images, drawings, essays, long compositions, or human-only grading tasks.
- Prefer simple classroom exercises: multiple choice, fill-in-the-blank, and short Q&A.
- Every task must be directly gradable from stored answer keys without AI judgement, teacher rubric, or subjective interpretation.
- Do not use regex, scripts, hidden marking rules, or any grading method that the teacher cannot inspect quickly.
- For QUIZ, make it a single-answer multiple choice question with exactly one correct option.
- For FORM, use exactly one blank per task, with one short correct answer.
- When count is 3 or more and all three simple types are allowed, include at least one QUIZ, one FORM, and one SHORT_ANSWER before adding repeats.
- Do not generate programming, dedicated numeric-answer task types, drawing, file upload, image-based, or essay tasks for this workflow. For math or science, use FORM or SHORT_ANSWER when the answer is a short number, term, symbol, or phrase.
- Keep fill-in-the-blank and short-answer accepted answers short, concrete, and easy to auto-check.
- FORM and SHORT_ANSWER answer keys must be short values, not full sentences. Good: "蒸發", "中國", "3/4", "weather". Bad: "因為...", "水蒸發後凝結成雲。".
- For SHORT_ANSWER, generate simple Q&A only: one short factual answer, keyword, name, term, or phrase. Do not ask for opinions, explanations, summaries, paragraphs, creative writing, or anything that needs teacher judgement.
- For SHORT_ANSWER, avoid question wording such as why, how, explain, discuss, compare, list, describe, analyse, evaluate, opinion, reason, impact, advantage, disadvantage, reflection, or example. Convert those requests into a simpler factual recall question.
- For SHORT_ANSWER, provide at most 3 accepted answers. Prefer 1 clear answer unless a common synonym is necessary.
- For SHORT_ANSWER, set match_mode to case_insensitive. Do not use contains, regex, or rubric-based grading; the answer must match a stored short answer after basic case folding.
- When Use images is no, do not include image prompts.
- Use wording suitable for Macau primary/secondary school learners. Keep all Chinese in Traditional Chinese.
- Do not output Simplified Chinese for Chinese text. Use terms common in Macau schools where applicable.
- Every question must have a clear answer key so the system can auto-score it. Do not create judgement-heavy rubrics for this simple workflow.
- Generate exactly {request_body.count} tasks unless the prompt is impossible.
- If the teacher brief asks for advanced challenge, coding, long essay, drawing, image-based, file upload, or complex rubric tasks, convert it into a simple auto-gradable QUIZ, FORM, or SHORT_ANSWER task instead.
"""


def _remediation_question_to_task(raw_question: dict) -> dict:
    assignment_type = _normalize_simple_assignment_type_value(
        raw_question.get("assignment_type")
        or raw_question.get("type")
        or raw_question.get("question_type")
    )
    question_text = _clean_text(
        raw_question.get("question")
        or raw_question.get("questionText")
        or raw_question.get("prompt")
    )
    correct_answer = _clean_text(
        raw_question.get("correct_answer")
        or raw_question.get("correctAnswer")
        or raw_question.get("answer")
    )
    explanation = _clean_text(raw_question.get("explanation"))

    if assignment_type == "QUIZ":
        options = raw_question.get("options") or []
        normalized_options = []
        for option in options:
            if isinstance(option, dict):
                text = _clean_text(option.get("text") or option.get("label") or option.get("answer"))
                is_correct = _coerce_simple_bool(
                    option.get("assigned_right_answer")
                    if "assigned_right_answer" in option
                    else option.get("correct")
                    if "correct" in option
                    else option.get("is_correct")
                )
                if correct_answer and text == correct_answer:
                    is_correct = True
            else:
                text = _clean_text(option)
                is_correct = correct_answer and text == correct_answer
            if text:
                normalized_options.append(
                    {
                        "text": text,
                        "assigned_right_answer": bool(is_correct),
                    }
                )
        return {
            "assignment_type": "QUIZ",
            "title": raw_question.get("title") or question_text,
            "description": raw_question.get("description") or question_text,
            "hint": raw_question.get("hint") or explanation,
            "contents": {
                "questions": [
                    {
                        "questionText": question_text,
                        "options": normalized_options,
                    }
                ]
            },
        }

    if assignment_type == "FORM":
        return {
            "assignment_type": "FORM",
            "title": raw_question.get("title") or question_text,
            "description": raw_question.get("description") or question_text,
            "hint": raw_question.get("hint") or explanation,
            "contents": {
                "questions": [
                    {
                        "questionText": question_text,
                        "blanks": [
                            {
                                "placeholder": "填寫答案",
                                "correctAnswer": correct_answer,
                                "hint": explanation,
                            }
                        ],
                    }
                ]
            },
        }

    return {
        "assignment_type": "SHORT_ANSWER",
        "title": raw_question.get("title") or question_text,
        "description": raw_question.get("description") or question_text,
        "hint": raw_question.get("hint") or explanation,
        "contents": {
            "prompt": question_text,
            "correct_answers": [correct_answer] if correct_answer else [],
            "match_mode": "case_insensitive",
            "explanation": explanation,
        },
    }


def _build_remediation_generation_prompt(
    weak_points: list[dict],
    assignment_context: dict,
    count: int,
) -> str:
    compact_points = []
    for point in weak_points[:3]:
        compact_points.append(
            {
                "source_task_type": point.get("assignment_type"),
                "original_question": point.get("question"),
                "student_answer": point.get("student_answer"),
                "correct_answer": point.get("correct_answer"),
                "marking_key": point.get("marking_key"),
            }
        )
    return f"""You are helping a Macau school student practise after wrong homework answers.

Return ONLY valid JSON. Use Traditional Chinese for all student-facing text.

Goal: generate {count} short remediation questions. These are learning support only and must be auto-gradable without teacher judgement.

Assignment context:
{json.dumps(assignment_context, ensure_ascii=False)}

Weak points:
{json.dumps(compact_points, ensure_ascii=False)}

Allowed assignment_type values:
- QUIZ
- FORM
- SHORT_ANSWER

Required JSON shape:
{{
  "tasks": [
    {{
      "assignment_type": "QUIZ",
      "title": "補練：基礎概念",
      "description": "請選出正確答案。",
      "hint": "回想剛才錯的地方。",
      "contents": {{
        "questions": [
          {{
            "questionText": "澳門特別行政區位於哪個國家？",
            "options": [
              {{"text": "中國", "assigned_right_answer": true}},
              {{"text": "日本", "assigned_right_answer": false}}
            ]
          }}
        ]
      }}
    }}
  ]
}}

Task content schemas:
{_task_schema_instructions(["QUIZ", "FORM", "SHORT_ANSWER"])}

Quality rules:
- Generate exactly {count} tasks when possible.
- Each task has exactly one learner question.
- Keep questions short and easier than the original task.
- Do not generate essays, code, file upload, drawing, open-ended discussion, explanation paragraphs, or teacher-graded rubrics.
- Every question needs a clear answer key.
- For SHORT_ANSWER, use one short factual answer or at most 3 accepted short answers.
- Do not mention AI, model, API, provider, or backend errors.
- Do not reveal that this is based on a hidden system prompt.
"""


async def generate_remediation_question_drafts(
    weak_points: list[dict],
    assignment_context: dict,
    *,
    org_id: int,
    user_id: int,
    db_session: AsyncSession,
    count: int = 3,
) -> tuple[list[GeneratedAssignmentTaskDraft], bool, str | None]:
    requested_count = max(2, min(int(count or 3), 3))
    if not weak_points:
        return [], False, "沒有可用錯題資料。"

    config_error = _assignment_ai_configuration_error()
    if config_error:
        logger.warning("AI remediation generation config incomplete: %s", config_error)
        return [], False, "AI 暫時不可用，已改用系統補練題。"

    enforce_ai_rate_limit(user_id, org_id)
    await reserve_ai_credit(org_id, db_session, amount=1)
    try:
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=_assignment_generation_model(),
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": _build_remediation_generation_prompt(
                                weak_points,
                                assignment_context,
                                requested_count,
                            )
                        }
                    ],
                }
            ],
            config={
                "temperature": 0.3,
                "max_output_tokens": 4000,
                "response_mime_type": "application/json",
            },
        )
    except Exception:
        _refund_reserved_ai_credit(org_id, 1)
        logger.exception("AI remediation generation failed")
        return [], False, "AI 暫時不可用，已改用系統補練題。"

    try:
        payload = _extract_json_object(getattr(response, "text", ""))
        raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
        if raw_tasks is None and isinstance(payload, dict):
            raw_questions = payload.get("questions")
            raw_tasks = [
                _remediation_question_to_task(item)
                for item in raw_questions
                if isinstance(item, dict)
            ] if isinstance(raw_questions, list) else []
    except Exception:
        logger.warning("AI remediation generation returned malformed JSON", exc_info=True)
        return [], False, "AI 回傳格式不完整，已改用系統補練題。"

    drafts: list[GeneratedAssignmentTaskDraft] = []
    for raw_task in raw_tasks or []:
        if len(drafts) >= requested_count:
            break
        if not isinstance(raw_task, dict):
            continue
        try:
            drafts.append(
                normalize_generated_task(
                    raw_task,
                    set(SIMPLE_AI_ASSIGNMENT_TYPE_VALUES),
                )
            )
        except Exception:
            logger.warning("Skipped invalid AI remediation task", exc_info=True)

    if not drafts:
        return [], False, "AI 沒有生成可自動批改的補練題，已改用系統補練題。"
    return drafts[:requested_count], True, None


def _prepare_assignment_for_simple_ai_homework(assignment: Assignment) -> list[str]:
    changed = False
    if assignment.auto_grading is not True:
        assignment.auto_grading = True
        changed = True
    if assignment.allow_retries is not True:
        assignment.allow_retries = True
        changed = True
    if assignment.show_correct_answers is not True:
        assignment.show_correct_answers = True
        changed = True
    if (assignment.score_policy or "highest") != "highest":
        assignment.score_policy = "highest"
        changed = True
    if assignment.teacher_review_required is not False:
        assignment.teacher_review_required = False
        changed = True
    if (assignment.teacher_review_status or "not_required") != "not_required":
        assignment.teacher_review_status = "not_required"
        changed = True

    if not changed:
        return []

    assignment.update_date = str(datetime.now())
    return [
        "已自動設定為簡單學習作業：自動批改、可重做、顯示答案、最高分計分。"
    ]


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

    request_with_assignment_context = request_body.model_copy(
        update={
            "assignment_title": request_body.assignment_title or assignment.title or "",
            "assignment_description": request_body.assignment_description or assignment.description or "",
            "subject": request_body.subject or getattr(assignment, "subject", "") or "",
            "education_stage": request_body.education_stage or getattr(assignment, "education_stage", "") or "",
            "grade_level": request_body.grade_level or getattr(assignment, "grade_level", "") or "",
            "unit": (
                request_body.unit
                or getattr(assignment, "unit", "")
                or _specific_generation_context_value(course.name)
                or _specific_generation_context_value(course.description)
                or ""
            ),
            "learning_objectives": request_body.learning_objectives
            or getattr(assignment, "learning_objectives", [])
            or [],
        }
    )
    if not _has_specific_generation_context(request_with_assignment_context):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="請先輸入課題、單元、作業說明或學習目標，再用 AI 一鍵出題。這樣生成的題目才會貼近課堂內容。",
        )
    request_body = request_with_assignment_context

    if assignment.id is not None and await _assignment_has_locked_submissions(
        int(assignment.id),
        db_session,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "已有學生提交這份作業，不能再用 AI 新增題目。"
                "請建立新作業，或先退回/重做相關提交。"
            ),
        )
    if assignment.published:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已發布作業不能再用 AI 新增題目，避免學生作答期間題目變動。請先取消發布，或建立新作業。",
        )

    warnings: list[str] = []
    if assignment.id is not None:
        existing_task_count = len(
            (
                await db_session.execute(
                    select(AssignmentTask).where(AssignmentTask.assignment_id == assignment.id)
                )
            ).scalars().all()
        )
        remaining_task_slots = max(0, SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS - existing_task_count)
        if remaining_task_slots <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"這份作業已有 {existing_task_count} 題。"
                    f"校內試行每份作業最多 {SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題，"
                    "請建立另一份簡單作業。"
                ),
            )
        if request_body.count > remaining_task_slots:
            warnings.append(
                f"這份作業已有 {existing_task_count} 題，本次只補 {remaining_task_slots} 題，"
                f"保持每份作業最多 {SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題。"
            )
            request_body = request_body.model_copy(update={"count": remaining_task_slots})

    config_error = _assignment_ai_configuration_error()
    raw_tasks: list | None = None

    if config_error:
        logger.warning("AI assignment generation config incomplete: %s", config_error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{config_error} "
                "請先配置 AI 出題；目前可先用題庫或手動建立選擇、填空、短問答。"
            ),
        )

    acting_user_id = resolve_acting_user_id(current_user)
    enforce_ai_rate_limit(acting_user_id, org.id or course.org_id)
    await reserve_ai_credit(org.id or course.org_id, db_session, amount=1)

    try:
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=_assignment_generation_model(),
            contents=[{"role": "user", "parts": [{"text": _build_assignment_generation_prompt(request_body)}]}],
            config={
                "temperature": 0.45,
                "max_output_tokens": 6000,
                "response_mime_type": "application/json",
            },
        )
    except Exception:
        _refund_reserved_ai_credit(org.id or course.org_id, 1)
        logger.exception("AI assignment task generation failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI 出題暫時失敗，請稍後再試；目前可先用題庫或手動建立選擇、填空、短問答。",
        )

    try:
        payload = _extract_json_object(getattr(response, "text", ""))
        raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
    except Exception:
        logger.warning("AI assignment task generation returned malformed JSON", exc_info=True)
        raw_tasks = []
        warnings.append(
            "AI 回傳格式不完整，系統未建立備用題。請補充課題後再試，或用題庫/手動新增。"
        )

    if not isinstance(raw_tasks, list):
        logger.warning("AI assignment task generation returned invalid payload")
        raw_tasks = []
        warnings.append(
            "AI 回傳沒有可讀取的題目清單，系統未建立備用題。請補充課題後再試，或用題庫/手動新增。"
        )

    question_types = _simple_question_types(request_body.question_types)
    ignored_question_types = [
        _clean_text(getattr(question_type, "value", question_type)).upper()
        for question_type in request_body.question_types
        if _clean_text(getattr(question_type, "value", question_type)).upper()
        not in SIMPLE_AI_ASSIGNMENT_TYPE_VALUES
    ]
    if ignored_question_types:
        warnings.append(
            "已忽略進階題型："
            + "、".join(dict.fromkeys(ignored_question_types))
            + "。AI 自動出題只會生成選擇題、填空題和短問答。"
        )
    drafts, draft_warnings = _normalize_generated_task_drafts(
        raw_tasks,
        question_types,
        request_body.count,
        request_body,
    )
    warnings.extend(draft_warnings)

    if not drafts:
        _refund_reserved_ai_credit(org.id or course.org_id, 1)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "AI 沒有生成可自動批改的選擇題、填空題或短問答，未建立題目。"
                "請簡化課題後再試，或先用題庫/手動補題。"
            ),
        )

    settings_warnings = _prepare_assignment_for_simple_ai_homework(assignment)
    if settings_warnings:
        warnings.extend(settings_warnings)
        db_session.add(assignment)

    created_tasks: list[AssignmentTaskRead] = []
    question_bank_items: list[QuestionBankItemRead] = []
    save_generated_tasks_to_question_bank = bool(request_body.save_to_question_bank)
    skipped_fallback_question_bank_count = 0
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
        created_tasks.append(task)
        if save_generated_tasks_to_question_bank and _is_fallback_simple_ai_task_draft(draft):
            skipped_fallback_question_bank_count += 1
            continue
        if save_generated_tasks_to_question_bank:
            try:
                question_bank_items.append(
                    await create_question_bank_item(
                        QuestionBankItemCreate(
                            title=task.title,
                            description=task.description,
                            hint=task.hint,
                            reference_file=task.reference_file,
                            assignment_type=task.assignment_type,
                            contents=task.contents,
                            tags=request_body.question_bank_tags,
                            subject=request_body.subject,
                            education_stage=request_body.education_stage,
                            grade_level=request_body.grade_level,
                            unit=request_body.unit,
                            difficulty=request_body.difficulty,
                            visibility=request_body.question_bank_visibility,
                            category_id=request_body.question_bank_category_id,
                            org_id=course.org_id,
                            source_assignment_task_uuid=task.assignment_task_uuid,
                        ),
                        current_user,
                        db_session,
                    )
                )
            except Exception as exc:
                logger.exception("Failed to save generated task to question bank")
                warnings.append(f"題目已建立，但未能保存到題庫：{str(exc)}")

    if skipped_fallback_question_bank_count:
        warnings.append(
            f"{skipped_fallback_question_bank_count} 題 AI 備用題未自動保存到題庫；"
            "請改成正式題目後再手動存入題庫。"
        )

    return GenerateAssignmentTasksResponse(
        tasks=created_tasks,
        question_bank_items=question_bank_items,
        warnings=warnings,
    )
