import json

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.db.question_bank import QuestionBankItem


SIMPLE_SELF_TEST_TYPES = (
    AssignmentTaskTypeEnum.QUIZ,
    AssignmentTaskTypeEnum.FORM,
    AssignmentTaskTypeEnum.SHORT_ANSWER,
)


def _has_text(value) -> bool:
    return bool(str(value or "").strip())


def _coerce_answer_bool(value) -> bool:
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


def _contains_ai_fallback_starter_text(item: QuestionBankItem) -> bool:
    text = " ".join(
        [
            str(item.title or "").strip(),
            str(item.description or "").strip(),
            str(getattr(item, "hint", "") or "").strip(),
            json.dumps(item.contents or {}, ensure_ascii=False, default=str),
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


def is_self_test_ready_item(item: QuestionBankItem) -> bool:
    if _contains_ai_fallback_starter_text(item):
        return False

    contents = item.contents or {}
    if not isinstance(contents, dict):
        return False

    if item.assignment_type == AssignmentTaskTypeEnum.QUIZ:
        questions = contents.get("questions") or []
        if not questions:
            return False
        for question in questions:
            if not isinstance(question, dict):
                return False
            if not _has_text(question.get("questionText") or question.get("question")):
                return False
            if not _has_text(question.get("questionUUID")):
                return False
            options = question.get("options") or []
            if len(options) < 2:
                return False
            correct_count = 0
            for option in options:
                if not isinstance(option, dict):
                    return False
                if not _has_text(option.get("optionUUID")):
                    return False
                if not _has_text(option.get("text") or option.get("option") or option.get("label")):
                    return False
                if _coerce_answer_bool(option.get("assigned_right_answer")):
                    correct_count += 1
            if correct_count != 1:
                return False
        return True

    if item.assignment_type == AssignmentTaskTypeEnum.FORM:
        questions = contents.get("questions") or []
        if not questions:
            return False
        for question in questions:
            if not isinstance(question, dict):
                return False
            if not _has_text(question.get("questionText") or question.get("question")):
                return False
            if not _has_text(question.get("questionUUID")):
                return False
            blanks = question.get("blanks") or []
            if not blanks:
                return False
            for blank in blanks:
                if not isinstance(blank, dict):
                    return False
                if not _has_text(blank.get("blankUUID")):
                    return False
                if not _has_text(blank.get("correctAnswer") or blank.get("correct_answer") or blank.get("answer")):
                    return False
        return True

    if item.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        match_mode = contents.get("match_mode") or "case_insensitive"
        if match_mode not in {"exact", "case_insensitive"}:
            return False
        answers = contents.get("correct_answers") or contents.get("accepted_answers") or []
        if isinstance(answers, str):
            answers = [answers]
        return (
            _has_text(contents.get("prompt") or contents.get("question"))
            and isinstance(answers, list)
            and any(_has_text(answer) for answer in answers)
        )

    return False
