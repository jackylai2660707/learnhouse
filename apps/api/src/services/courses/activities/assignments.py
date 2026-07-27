import asyncio
import csv
import io
import json
import logging
import math
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException, Request, UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.courses.activities import Activity
from src.db.courses.assignments import (
    Assignment,
    AssignmentCreate,
    AssignmentRead,
    AssignmentRemediationPractice,
    AssignmentTask,
    AssignmentTaskCreate,
    AssignmentTaskRead,
    AssignmentTaskSubmission,
    AssignmentTaskSubmissionCreate,
    AssignmentTaskSubmissionRead,
    AssignmentTaskSubmissionUpdate,
    AssignmentTaskTypeEnum,
    AssignmentTaskUpdate,
    AssignmentUpdate,
    AssignmentUserSubmission,
    AssignmentUserSubmissionCreate,
    AssignmentUserSubmissionRead,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.certifications import CertificateUser, Certifications
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.question_bank import QuestionBankItem, QuestionBankVisibilityEnum
from src.db.roles import Role
from src.db.self_tests import (
    SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT,
    SelfTestAttempt,
    SelfTestAttemptStatus,
)
from src.db.trail_runs import TrailRun
from src.db.trail_steps import TrailStep
from src.db.user_organizations import UserOrganization
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.db.users import AnonymousUser, PublicUser, User, APITokenUser
from src.security.features_utils.usage import (
    check_limits_with_usage,
    decrease_feature_usage,
    increase_feature_usage,
)
from src.security.org_auth import require_org_role_permission
from src.security.rbac import (
    authorization_verify_based_on_roles,
    check_resource_access,
    AccessAction,
)
from src.security.auth import resolve_acting_user_id
from src.security.rbac.constants import ADMIN_OR_MAINTAINER_ROLE_IDS
from src.services.ai.assignment_config import assignment_ai_status
from src.services.ai.assignment_config import (
    assignment_ai_configuration_error,
    assignment_generation_model,
)
from src.services.ai.base import get_gemini_client
from src.services.judge0 import Judge0ServiceError, submit_judge0
from src.services.courses.activities.uploads.sub_file import upload_submission_file
from src.services.courses.activities.uploads.tasks_ref_files import (
    upload_reference_file,
)
from src.services.trail.trail import check_trail_presence
from src.services.courses.certifications import check_course_completion_and_create_certificate
from src.services.self_test_readiness import (
    SIMPLE_SELF_TEST_TYPES,
    is_self_test_ready_item,
)
from src.services.security.rate_limiting import enforce_ai_rate_limit
from src.services.analytics.analytics import track
from src.services.analytics import events as analytics_events
from src.services.webhooks.dispatch import dispatch_webhooks
from src.security.features_utils.usage import refund_ai_credit, reserve_ai_credit

logger = logging.getLogger(__name__)


def _block_api_tokens(current_user: PublicUser | AnonymousUser | APITokenUser) -> None:
    """
    Block API tokens from accessing assignments.

    SECURITY: Assignments contain sensitive user submission data and grades.
    API tokens are not allowed to access this data - only user authentication is permitted.
    """
    if isinstance(current_user, APITokenUser):
        raise HTTPException(
            status_code=403,
            detail="API tokens cannot access assignments. Only user authentication is allowed.",
        )


## > Grade computation

# Default passing threshold as a percentage (0-100). Used for PASS_FAIL,
# NUMERIC, and PERCENTAGE grading types — any type where the pass/fail line
# isn't implied by the display format itself.
DEFAULT_PASSING_THRESHOLD_PERCENTAGE = 50.0

# For ALPHABET (A/B/C/D/F) and GPA_SCALE (0.0-4.0), we use 60% as the passing
# line so the `passed` field stays consistent with the display: any score that
# renders as "F" or "0.0" will also have passed=False.
LETTER_PASSING_THRESHOLD_PERCENTAGE = 60.0

SCHOOL_OPERATIONS_TIMEZONE = "Asia/Macau"
SCHOOL_OPERATIONS_MINIMUM_COHORT_SIZE = 5
SCHOOL_OPERATIONS_MAX_RANGE_DAYS = 366
SCHOOL_OPERATIONS_MINUTES_PER_AUTO_GRADED_RECORD = 2


## > Auto-grading allow-list + server-side verification
##
## IMPORTANT: Not every task type can be graded without a human reviewer.
## This is an EXPLICIT allow-list (not a deny-list) so that when new task
## types are added to AssignmentTaskTypeEnum in the future, they default
## to requiring human review until they're explicitly opted in here.

# Tasks whose grade can be computed without teacher review. FILE_SUBMISSION
# and OTHER are deliberately excluded — files need human eyes, and OTHER is
# a legacy catch-all with no grading logic.
AUTO_GRADABLE_TASK_TYPES = frozenset(
    {
        AssignmentTaskTypeEnum.QUIZ,
        AssignmentTaskTypeEnum.FORM,
        AssignmentTaskTypeEnum.CODE,
        AssignmentTaskTypeEnum.SHORT_ANSWER,
        AssignmentTaskTypeEnum.NUMBER_ANSWER,
        AssignmentTaskTypeEnum.ESSAY,
    }
)

# Keep the visible Macau school pilot metrics stricter than the full backend
# grading engine. CODE and NUMBER_ANSWER can remain available elsewhere, but
# the teacher/principal "simple AI homework" readiness count should only mean
# low-maintenance classroom questions.
SIMPLE_PILOT_AUTO_GRADABLE_TASK_TYPES = frozenset(
    {
        AssignmentTaskTypeEnum.QUIZ,
        AssignmentTaskTypeEnum.FORM,
        AssignmentTaskTypeEnum.SHORT_ANSWER,
    }
)
SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS = 3
SIMPLE_PILOT_MAX_FILL_BLANK_ANSWER_LENGTH = 40
SIMPLE_PILOT_MAX_SHORT_ANSWER_LENGTH = 40
SIMPLE_PILOT_SHORT_ANSWER_MATCH_MODES = frozenset(
    {"exact", "case_insensitive"}
)
SIMPLE_PILOT_SHORT_ANSWER_SUBJECTIVE_MARKERS = (
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
    "請描述",
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
)
SIMPLE_PILOT_AUTO_ANSWER_EXPLANATION_MARKERS = (
    "\n",
    "。",
    "！",
    "？",
    ";",
    "；",
)

# Tasks where the backend independently verifies the student's answer
# against the stored task contents during auto-grading, instead of trusting
# whatever grade the client-side component computed and posted.
#
# CODE is included: on auto-grade, the backend spins up a fresh Judge0
# batch against the student's stored source_code using the teacher's
# configured test_cases + grading_mode. The client-stored grade is
# ignored (students save with grade=0 today), so without server-side
# re-grading CODE tasks silently award zero.
SERVER_VERIFIED_TASK_TYPES = frozenset(
    {
        AssignmentTaskTypeEnum.SHORT_ANSWER,
        AssignmentTaskTypeEnum.NUMBER_ANSWER,
        AssignmentTaskTypeEnum.QUIZ,
        AssignmentTaskTypeEnum.FORM,
        AssignmentTaskTypeEnum.CODE,
        AssignmentTaskTypeEnum.ESSAY,
    }
)

ESSAY_AI_FEEDBACK_TYPE = "ai_essay_grading"
ESSAY_AI_RUBRIC = (
    ("content", "內容切題", "是否回應題目、觀點清楚、例子合適。"),
    ("structure", "結構組織", "開頭、段落、承接、結尾是否清晰。"),
    ("language", "語言表達", "用詞、句式、語氣和表達是否準確。"),
    ("mechanics", "錯別字與標點", "錯別字、標點、格式和基本語法。"),
    ("creativity", "創意與思考", "是否有個人思考、細節和吸引力。"),
)

_SIMPLE_TEXT_ANSWER_EDGE_PUNCTUATION = (
    "\"'`.,;:!?()[]{}<>"
    "，。；：！？、（）【】《》「」『』“”‘’"
    "／/｜|～~"
)
_SIMPLE_TEXT_ANSWER_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"
_SIMPLE_TEXT_ANSWER_EQUIVALENT_GROUPS = (
    frozenset({"中國", "中華人民共和國"}),
    frozenset({"葡文", "葡語", "葡萄牙語"}),
    frozenset({"英文", "英語"}),
)
_SIMPLE_TEXT_ANSWER_SIMPLIFIED_TO_TRADITIONAL = str.maketrans(
    {
        # Small, dependency-free classroom fallback. This is not a full OpenCC
        # conversion; it only covers common Macau school answer vocabulary so
        # one simplified character does not turn an otherwise correct answer
        # into a frustrating zero.
        "门": "門",
        "国": "國",
        "语": "語",
        "学": "學",
        "习": "習",
        "数": "數",
        "课": "課",
        "题": "題",
        "问": "問",
        "认": "認",
        "识": "識",
        "义": "義",
        "词": "詞",
        "组": "組",
        "级": "級",
        "读": "讀",
        "写": "寫",
        "听": "聽",
        "说": "說",
        "书": "書",
        "简": "簡",
        "单": "單",
        "对": "對",
        "错": "錯",
        "体": "體",
        "会": "會",
        "复": "復",
        "线": "線",
        "点": "點",
        "电": "電",
        "话": "話",
        "车": "車",
        "马": "馬",
        "鱼": "魚",
        "鸟": "鳥",
        "风": "風",
        "云": "雲",
        "气": "氣",
        "节": "節",
        "时": "時",
        "间": "間",
        "长": "長",
        "历": "歷",
        "汉": "漢",
        "热": "熱",
        "现": "現",
        "这": "這",
        "个": "個",
        "们": "們",
        "为": "為",
        "与": "與",
        "华": "華",
        "民": "民",
        "共": "共",
        "和": "和",
        "产": "產",
        "业": "業",
        "观": "觀",
        "实": "實",
        "验": "驗",
        "试": "試",
        "证": "證",
        "园": "園",
        "区": "區",
        "东": "東",
        "广": "廣",
        "湾": "灣",
        "岛": "島",
        "桥": "橋",
        "际": "際",
        "币": "幣",
        "纪": "紀",
        "录": "錄",
        "统": "統",
        "计": "計",
        "画": "畫",
        "图": "圖",
        "颜": "顏",
        "关": "關",
        "键": "鍵",
        "练": "練",
        "诗": "詩",
        "乐": "樂",
        "艺": "藝",
        "术": "術",
        "卫": "衛",
        "护": "護",
        "强": "強",
        "难": "難",
        "双": "雙",
        "页": "頁",
        "类": "類",
        "别": "別",
        "动": "動",
        "静": "靜",
        "声": "聲",
        "圆": "圓",
        "边": "邊",
        "积": "積",
        "标": "標",
        "维": "維",
    }
)


def _normalize_simple_text_answer(value, *, case_sensitive: bool = False) -> str:
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value))
    text = text.translate(_SIMPLE_TEXT_ANSWER_SIMPLIFIED_TO_TRADITIONAL)
    for char in _SIMPLE_TEXT_ANSWER_ZERO_WIDTH_CHARS:
        text = text.replace(char, "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(_SIMPLE_TEXT_ANSWER_EDGE_PUNCTUATION).strip()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    if not case_sensitive:
        text = text.casefold()
    return text


def _simple_text_answer_equivalents(normalized_value: str) -> set[str]:
    equivalents = {normalized_value}
    for group in _SIMPLE_TEXT_ANSWER_EQUIVALENT_GROUPS:
        if normalized_value in group:
            equivalents.update(group)
    return equivalents


def _simple_text_answers_equivalent(normalized_answer: str, normalized_expected: str) -> bool:
    return bool(
        _simple_text_answer_equivalents(normalized_answer)
        & _simple_text_answer_equivalents(normalized_expected)
    )


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


def _check_short_answer(answer, accepted, mode) -> bool:
    """
    Server-side mirror of TaskShortAnswerObject.tsx > checkShortAnswer.

    Returns True if the student's trimmed answer matches any of the accepted
    answers under the configured match mode. Anchors regex patterns with
    fullmatch so a pattern like ``hello`` doesn't silently match
    ``hello world``. Invalid regex patterns are treated as non-matches
    (never raise).
    """
    raw_answer = (str(answer) if answer is not None else "").strip()
    if not raw_answer:
        return False
    if not isinstance(accepted, list):
        return False
    match_mode = mode or "case_insensitive"
    normalized_answer = _normalize_simple_text_answer(
        raw_answer,
        case_sensitive=match_mode == "exact",
    )
    if not normalized_answer:
        return False
    for raw in accepted:
        if not isinstance(raw, str):
            continue
        expected = _normalize_simple_text_answer(
            raw,
            case_sensitive=match_mode == "exact",
        )
        if not expected:
            continue
        if match_mode == "exact":
            if normalized_answer == expected:
                return True
        elif match_mode == "case_insensitive":
            if _simple_text_answers_equivalent(normalized_answer, expected):
                return True
        elif match_mode == "contains":
            answer_equivalents = _simple_text_answer_equivalents(normalized_answer)
            expected_equivalents = _simple_text_answer_equivalents(expected)
            if any(
                expected_value in answer_value
                for expected_value in expected_equivalents
                for answer_value in answer_equivalents
            ):
                return True
        elif match_mode == "regex":
            try:
                if re.fullmatch(raw.strip(), raw_answer, re.IGNORECASE):
                    return True
            except re.error:
                # Invalid regex from the teacher — treat as no match
                pass
    return False


def _check_number_answer(answer_raw, correct_value, tolerance) -> bool:
    """
    Server-side mirror of TaskNumberAnswerObject.tsx > checkNumberAnswer.

    Parses the student's answer as a float (accepting comma decimals),
    returns True when ``abs(parsed - correct) <= abs(tolerance)``. Returns
    False for blank / NaN / non-numeric input so students can't earn
    credit for a non-answer.
    """
    if answer_raw is None:
        return False
    cleaned = str(answer_raw).strip().replace(",", ".")
    if not cleaned:
        return False
    try:
        parsed = float(cleaned)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(parsed):
        return False
    try:
        correct = float(correct_value if correct_value is not None else 0)
        tol = abs(float(tolerance if tolerance is not None else 0))
    except (TypeError, ValueError):
        return False
    return abs(parsed - correct) <= tol


def _grade_quiz_task(contents: dict, submission_data: dict, task_max: int) -> int:
    """
    Server-side mirror of TaskQuizObject.tsx > gradeFC.

    Simple school-pilot quizzes are single-answer multiple-choice questions:
    each question has exactly one correct option and earns credit only when
    the learner selects that option and no incorrect options. Older multi-
    answer quizzes keep the legacy per-option scoring so existing content
    does not abruptly change shape.

    Returns a grade in [0, task_max], rounded.
    """
    questions = contents.get("questions") or []
    # Snapshot of submissions stored under task_submission.task_submission
    submissions = submission_data.get("submissions") or []

    # Index student answers by (questionUUID, optionUUID) for O(1) lookup
    answer_by_key: dict = {}
    for sub in submissions:
        if not isinstance(sub, dict):
            continue
        q_uuid = sub.get("questionUUID")
        o_uuid = sub.get("optionUUID")
        if q_uuid and o_uuid:
            answer_by_key[(q_uuid, o_uuid)] = _coerce_simple_answer_bool(sub.get("answer"))

    total_units = 0
    correct_units = 0
    for question in questions:
        if not isinstance(question, dict):
            continue
        q_uuid = question.get("questionUUID")
        options = question.get("options") or []
        correct_option_count = sum(
            1
            for option in options
            if isinstance(option, dict)
            and _coerce_simple_answer_bool(option.get("assigned_right_answer"))
        )
        if q_uuid and options and correct_option_count == 1:
            total_units += 1
            selected_correct = False
            selected_wrong = False
            for option in options:
                if not isinstance(option, dict):
                    continue
                o_uuid = option.get("optionUUID")
                expected = _coerce_simple_answer_bool(option.get("assigned_right_answer"))
                student_answer = answer_by_key.get((q_uuid, o_uuid), False)
                if expected and student_answer:
                    selected_correct = True
                if not expected and student_answer:
                    selected_wrong = True
            if selected_correct and not selected_wrong:
                correct_units += 1
            continue

        for option in options:
            if not isinstance(option, dict):
                continue
            total_units += 1
            o_uuid = option.get("optionUUID")
            expected = _coerce_simple_answer_bool(option.get("assigned_right_answer"))
            student_answer = answer_by_key.get((q_uuid, o_uuid), False)
            if student_answer == expected:
                correct_units += 1

    if total_units == 0 or task_max <= 0:
        return 0
    return round(correct_units / total_units * task_max)


def _grade_form_task(contents: dict, submission_data: dict, task_max: int) -> int:
    """
    Server-side mirror of TaskFormObject.tsx > gradeFC.

    Each blank in each question is worth one point. The comparison normalizes
    common classroom input differences such as full-width characters,
    surrounding punctuation, whitespace, and case.

    Returns a grade in [0, task_max], rounded.
    """
    questions = contents.get("questions") or []
    submissions = submission_data.get("submissions") or []

    # Index student answers by (questionUUID, blankUUID)
    answer_by_key: dict = {}
    for sub in submissions:
        if not isinstance(sub, dict):
            continue
        q_uuid = sub.get("questionUUID")
        b_uuid = sub.get("blankUUID")
        if q_uuid and b_uuid:
            answer_by_key[(q_uuid, b_uuid)] = sub.get("answer")

    total_blanks = 0
    correct_blanks = 0
    for question in questions:
        if not isinstance(question, dict):
            continue
        q_uuid = question.get("questionUUID")
        blanks = question.get("blanks") or []
        for blank in blanks:
            if not isinstance(blank, dict):
                continue
            total_blanks += 1
            b_uuid = blank.get("blankUUID")
            correct_value = _simple_form_blank_answer(blank)
            student_value = answer_by_key.get((q_uuid, b_uuid), "")
            normalized_student_value = _normalize_simple_text_answer(student_value)
            normalized_correct_value = _normalize_simple_text_answer(correct_value)
            if (
                normalized_student_value
                and normalized_correct_value
                and _simple_text_answers_equivalent(
                    normalized_student_value,
                    normalized_correct_value,
                )
            ):
                correct_blanks += 1

    if total_blanks == 0 or task_max <= 0:
        return 0
    return round(correct_blanks / total_blanks * task_max)


def _normalize_code_output(s):
    """
    Normalization for Judge0 stdout comparison. Same logic as the client
    and as the one-off version in code_execution.py — strips trailing
    whitespace per line and drops trailing blank lines so ``print("x")``
    matches ``x`` and Windows line endings don't cause false failures.
    """
    if not s:
        return ""
    lines = [line.rstrip() for line in s.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _safe_positive_weight(value) -> int:
    """Return a usable positive grading weight for untrusted JSON content."""
    try:
        weight = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return weight if weight > 0 else 1


def _student_assignment_task_read(task: AssignmentTask) -> AssignmentTaskRead:
    """Build a student-safe task payload without mutating the ORM entity."""
    result = AssignmentTaskRead.model_validate(task)
    if task.assignment_type != AssignmentTaskTypeEnum.CODE:
        return result

    contents = dict(result.contents or {})
    for key in (
        "solution_code",
        "solutionCode",
        "solution_html",
        "solution_css",
        "solution_js",
    ):
        contents.pop(key, None)

    hidden_count = 0
    for test_key in ("test_cases", "testCases"):
        if test_key not in contents:
            continue
        visible_test_cases: list[dict] = []
        for test_case in contents.get(test_key) or []:
            if not isinstance(test_case, dict):
                continue
            if test_case.get("hidden", False):
                hidden_count += 1
                continue
            visible_test_cases.append(dict(test_case))
        contents[test_key] = visible_test_cases

    # Some legacy authoring payloads stored hidden checks separately. Never
    # expose their bodies even if a task has not yet been normalized.
    for key in ("hidden_test_cases", "hiddenTestCases"):
        legacy_hidden = contents.pop(key, None)
        if isinstance(legacy_hidden, list):
            hidden_count += sum(1 for item in legacy_hidden if isinstance(item, dict))

    if "web_checks" in contents:
        visible_web_checks: list[dict] = []
        for check in contents.get("web_checks") or []:
            if not isinstance(check, dict):
                continue
            if check.get("hidden", False):
                hidden_count += 1
                continue
            visible_web_checks.append(dict(check))
        contents["web_checks"] = visible_web_checks

    if contents.get("show_hidden_test_count", True):
        contents["hidden_test_count"] = hidden_count
    else:
        contents.pop("hidden_test_count", None)
    result.contents = contents
    return result


def _sanitize_code_submission_payload(task: AssignmentTask, payload) -> dict:
    """Persist only student source fields; execution evidence is server-owned."""
    data = payload if isinstance(payload, dict) else {}
    contents = task.contents or {}
    if contents.get("mode") == "web_preview":
        return {
            "mode": "web_preview",
            "html_code": str(data.get("html_code") or ""),
            "css_code": str(data.get("css_code") or ""),
            "js_code": str(data.get("js_code") or ""),
        }
    language_id = data.get("language_id") or contents.get("language_id")
    return {
        "source_code": str(data.get("source_code") or ""),
        "language_id": language_id,
    }


def _grade_web_preview_code_task(task, task_submission):
    """
    Grade HTML/CSS/JS live-preview CODE tasks without Judge0.

    These tasks store separate ``html_code``, ``css_code``, and ``js_code`` in
    the submission and define simple checks in ``contents.web_checks``. This
    keeps beginner web assignments auto-gradable even when Judge0 is only used
    for normal executable-language tasks.
    """
    if task_submission is None:
        return 0

    contents = task.contents or {}
    submission_data = task_submission.task_submission or {}
    checks = contents.get("web_checks") or []
    task_max = int(task.max_grade_value or 0)
    if task_max <= 0 or not checks:
        return 0

    sources = {
        "html": str(submission_data.get("html_code") or ""),
        "css": str(submission_data.get("css_code") or ""),
        "js": str(submission_data.get("js_code") or ""),
    }

    total_weight = 0
    passed_weight = 0
    evaluated: list[bool] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        weight = _safe_positive_weight(check.get("weight"))
        total_weight += weight
        source = sources.get(check.get("target") or "html", "")
        pattern = str(check.get("pattern") or "")
        if not pattern:
            evaluated.append(False)
            continue

        match_mode = check.get("match") or "contains"
        if match_mode == "regex":
            try:
                passed = re.search(pattern, source, re.IGNORECASE | re.MULTILINE) is not None
            except re.error:
                passed = False
        else:
            passed = pattern.lower() in source.lower()

        if passed:
            passed_weight += weight
        evaluated.append(passed)

    if total_weight <= 0:
        return 0
    grading_mode = contents.get("grading_mode") or "equal_weight"
    if grading_mode == "binary":
        return task_max if passed_weight == total_weight else 0
    if grading_mode == "custom_weights":
        return round(passed_weight / total_weight * task_max)
    passed_count = sum(evaluated)
    return round(passed_count / len(evaluated) * task_max) if evaluated else 0


async def _grade_code_task_async(task, task_submission):
    """
    Re-grade a CODE task server-side by running the student's stored
    source code against the teacher's configured test cases via Judge0.

    Grading modes (mirrors TaskCodeObject.tsx > gradeFC):
    - ``binary``: full marks only when every test passes, else 0.
    - ``custom_weights``: ``round(passed_weight / total_weight * max)``.
    - ``equal_weight`` (default): ``round(passed / total * max)``.

    Returns an int grade in [0, max_grade_value]. Returns ``None`` when
    Judge0 isn't configured or reachable — the caller leaves the stored
    grade alone in that case rather than silently zeroing students out.
    """
    if task_submission is None:
        return 0

    contents = task.contents or {}
    if contents.get("mode") == "web_preview":
        return _grade_web_preview_code_task(task, task_submission)

    submission_data = task_submission.task_submission or {}
    source_code = submission_data.get("source_code", "") or ""
    if not source_code.strip():
        # Student hasn't written any code yet → zero, consistent with other types
        return 0

    # Prefer the language_id the student actually submitted with — falls back
    # to the task's configured language if missing.
    language_id = submission_data.get("language_id") or contents.get("language_id")
    if language_id is None:
        return 0

    test_cases = contents.get("test_cases") or []
    if not test_cases:
        return 0

    task_max = int(task.max_grade_value or 0)
    if task_max <= 0:
        return 0

    judge0_cfg = get_learnhouse_config().judge0_config
    if judge0_cfg is None:
        # Judge0 not configured — can't verify; leave the stored grade alone
        logger.warning(
            "judge0.grading.skipped",
            extra={
                "integration": "judge0",
                "operation": "grade_code_task",
                "error_code": "judge0_not_configured",
            },
        )
        return None

    async def run_one(tc):
        stdin = tc.get("stdin") or ""
        # Teacher-configured tests use camelCase `expectedStdout` in the
        # frontend contents schema. Tolerate both spellings.
        expected = tc.get("expectedStdout") or tc.get("expected_stdout") or ""
        try:
            r = await submit_judge0(
                judge0_cfg,
                language_id=int(language_id),
                source_code=source_code,
                stdin=stdin,
            )
        except Judge0ServiceError as exc:
            logger.warning(
                "judge0.grading.failed",
                extra={
                    "integration": "judge0",
                    "operation": "grade_code_task",
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
            )
            return (tc, False, True)
        except Exception:
            logger.warning(
                "judge0.grading.failed",
                extra={
                    "integration": "judge0",
                    "operation": "grade_code_task",
                    "error_code": "judge0_unexpected_error",
                    "retryable": True,
                },
            )
            return (tc, False, True)
        status = r.get("status") or {}
        actual = _normalize_code_output(r.get("stdout"))
        passed = status.get("id") == 3 and actual == _normalize_code_output(expected)
        return (tc, passed, False)

    results = await asyncio.gather(*(run_one(tc) for tc in test_cases))
    if any(infrastructure_failed for _, _, infrastructure_failed in results):
        return None

    passed_count = sum(1 for _, passed, _ in results if passed)
    total_count = len(results)
    grading_mode = contents.get("grading_mode") or "equal_weight"

    if grading_mode == "binary":
        return task_max if total_count > 0 and passed_count == total_count else 0

    if grading_mode == "custom_weights":
        total_weight = sum(_safe_positive_weight(tc.get("weight")) for tc in test_cases)
        passed_weight = sum(
            _safe_positive_weight(tc.get("weight")) for tc, passed, _ in results if passed
        )
        if total_weight <= 0:
            return 0
        return round(passed_weight / total_weight * task_max)

    # equal_weight (default)
    if total_count <= 0:
        return 0
    return round(passed_count / total_count * task_max)


def _extract_ai_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    if not text:
        raise ValueError("empty AI response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _essay_submission_text(task_submission: AssignmentTaskSubmission | None) -> str:
    if task_submission is None:
        return ""
    data = task_submission.task_submission or {}
    if not isinstance(data, dict):
        return ""
    return str(
        data.get("essay")
        or data.get("answer")
        or data.get("text")
        or data.get("content")
        or ""
    ).strip()


def _essay_prompt(contents: dict, task: AssignmentTask) -> str:
    return str(
        contents.get("prompt")
        or contents.get("question")
        or contents.get("title")
        or task.description
        or task.title
        or ""
    ).strip()


def _essay_rubric(contents: dict) -> list[dict]:
    raw = contents.get("rubric")
    if isinstance(raw, list):
        rubric = []
        for index, item in enumerate(raw[:8]):
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or f"criterion_{index + 1}").strip()
            label = str(item.get("label") or item.get("title") or key).strip()
            description = str(item.get("description") or "").strip()
            if key and label:
                rubric.append({
                    "key": key,
                    "label": label,
                    "description": description,
                })
        if rubric:
            return rubric
    return [
        {"key": key, "label": label, "description": description}
        for key, label, description in ESSAY_AI_RUBRIC
    ]


def _clamp_score(value, minimum: int = 0, maximum: int = 100) -> int:
    try:
        score = round(float(value))
    except (TypeError, ValueError):
        return minimum
    return max(min(score, maximum), minimum)


def _normalize_string_list(value, *, limit: int = 5) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and value.strip():
        items = [value]
    else:
        items = []
    normalized = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if text:
            normalized.append(text[:220])
    return normalized[:limit]


def _normalize_essay_ai_payload(payload: dict, task: AssignmentTask, task_max: int) -> tuple[int, dict]:
    contents = task.contents or {}
    rubric = _essay_rubric(contents)
    raw_criteria = payload.get("criteria") or payload.get("rubric_scores") or []
    raw_by_key = {
        str(item.get("key") or item.get("id") or "").strip(): item
        for item in raw_criteria
        if isinstance(item, dict)
    } if isinstance(raw_criteria, list) else {}

    criteria: list[dict] = []
    total_score = 0
    for item in rubric:
        raw_item = raw_by_key.get(item["key"], {})
        score = _clamp_score(raw_item.get("score") if raw_item else None)
        total_score += score
        criteria.append({
            "key": item["key"],
            "label": str(raw_item.get("label") or item["label"])[:60] if raw_item else item["label"],
            "score": score,
            "max_score": 100,
            "comment": re.sub(r"\s+", " ", str((raw_item or {}).get("comment") or "").strip())[:260],
        })

    if criteria:
        average_score = round(total_score / len(criteria))
    else:
        average_score = _clamp_score(payload.get("score") or payload.get("total_score"))

    model_total_score = _clamp_score(payload.get("score") or payload.get("total_score"), 0, 100)
    score_100 = model_total_score if model_total_score > 0 else average_score
    task_grade = round(score_100 / 100 * max(task_max, 0))

    report = {
        "type": ESSAY_AI_FEEDBACK_TYPE,
        "score": score_100,
        "grade": task_grade,
        "max_grade": task_max,
        "criteria": criteria,
        "summary": re.sub(r"\s+", " ", str(payload.get("summary") or "").strip())[:500],
        "overall_feedback": re.sub(r"\s+", " ", str(payload.get("overall_feedback") or payload.get("feedback") or "").strip())[:900],
        "strengths": _normalize_string_list(payload.get("strengths"), limit=4),
        "improvements": _normalize_string_list(payload.get("improvements"), limit=5),
        "next_steps": _normalize_string_list(payload.get("next_steps") or payload.get("actions"), limit=4),
        "teacher_notes": re.sub(r"\s+", " ", str(payload.get("teacher_notes") or "").strip())[:500],
        "confidence": str(payload.get("confidence") or "medium").strip().lower()[:20],
        "needs_teacher_review": True,
    }
    if not report["summary"]:
        report["summary"] = "AI 已根據作文題目、學生文章和評分規準完成初步評分。"
    if not report["overall_feedback"]:
        report["overall_feedback"] = "請老師覆核 AI 建議分數，並按學生程度補充個別回饋。"
    return task_grade, report


def _build_essay_grading_prompt(task: AssignmentTask, task_submission: AssignmentTaskSubmission) -> str:
    contents = task.contents or {}
    prompt = _essay_prompt(contents, task)
    student_text = _essay_submission_text(task_submission)
    rubric = _essay_rubric(contents)
    rubric_text = "\n".join(
        f"- {item['key']}: {item['label']}。{item['description']}"
        for item in rubric
    )
    subject = str(contents.get("subject") or "").strip()
    grade_level = str(contents.get("grade_level") or "").strip()
    min_words = str(contents.get("min_words") or "").strip()
    max_words = str(contents.get("max_words") or "").strip()

    return f"""
你是一位澳門中小學中文作文老師。請用繁體中文，專業但鼓勵式地批改學生作文。

只回傳 JSON，不要 Markdown，不要額外說明。

作文題目：
{prompt}

科目：{subject or "未提供"}
年級：{grade_level or "未提供"}
字數要求：{min_words or "未提供"} 至 {max_words or "未提供"}

評分規準，每項 0-100：
{rubric_text}

學生作文：
{student_text}

回傳格式：
{{
  "score": 0-100,
  "summary": "一句總評",
  "overall_feedback": "給學生看的整體評語，指出表現與方向",
  "criteria": [
    {{"key": "{rubric[0]['key']}", "label": "{rubric[0]['label']}", "score": 0-100, "comment": "本項評語"}}
  ],
  "strengths": ["具體優點 1", "具體優點 2"],
  "improvements": ["需要改善 1", "需要改善 2"],
  "next_steps": ["下一次可做的練習 1", "下一次可做的練習 2"],
  "teacher_notes": "給老師覆核時看的簡短提醒",
  "confidence": "low|medium|high"
}}

要求：
- 分數要符合澳門中小學課堂水平，不要過分嚴苛或過分寬鬆。
- 必須根據學生實際文字評分，不可憑空補內容。
- criteria 必須覆蓋上方每一個 key。
- 不要批改政治、身份、家庭背景，只評作文表現。
- 如果文章太短或離題，要明確指出，仍需給出可改進建議。
""".strip()


async def _grade_essay_task_async(
    task: AssignmentTask,
    task_submission: AssignmentTaskSubmission | None,
    *,
    org_id: int | None = None,
    user_id: int | None = None,
    db_session: AsyncSession | None = None,
) -> int | None:
    if task_submission is None:
        return 0
    student_text = _essay_submission_text(task_submission)
    if not student_text:
        return 0

    config_error = assignment_ai_configuration_error()
    if config_error:
        logger.warning("Essay AI grading unavailable: %s", config_error)
        return None

    if org_id is not None and user_id is not None:
        enforce_ai_rate_limit(user_id, org_id)
    if org_id is not None and db_session is not None:
        await reserve_ai_credit(org_id, db_session, amount=1)

    try:
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=assignment_generation_model(),
            contents=[{"role": "user", "parts": [{"text": _build_essay_grading_prompt(task, task_submission)}]}],
            config={
                "temperature": 0.25,
                "max_output_tokens": 5000,
                "response_mime_type": "application/json",
            },
        )
        payload = _extract_ai_json_object(getattr(response, "text", ""))
        grade, report = _normalize_essay_ai_payload(
            payload,
            task,
            int(task.max_grade_value or 0),
        )
    except Exception:
        if org_id is not None:
            try:
                refund_ai_credit(org_id, 1)
            except Exception:
                logger.exception("Failed to refund AI credit after essay grading failure")
        logger.exception("AI essay grading failed")
        return None

    task_submission.task_submission_grade_feedback = json.dumps(report, ensure_ascii=False)
    return grade


async def _server_verified_task_grade(
    task,
    task_submission,
    *,
    org_id: int | None = None,
    user_id: int | None = None,
    db_session: AsyncSession | None = None,
):
    """
    If this task type is in SERVER_VERIFIED_TASK_TYPES, re-compute its
    grade from the stored task contents + submission data and return it.
    Returns ``None`` for task types we don't verify, or when the CODE
    grader can't reach Judge0 — the caller should fall back to
    ``task_submission.grade`` in both cases.
    """
    if task.assignment_type not in SERVER_VERIFIED_TASK_TYPES:
        return None
    if task_submission is None:
        return 0

    contents = task.contents or {}
    submission_data = task_submission.task_submission or {}
    task_max = int(task.max_grade_value or 0)

    if task.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        passed = _check_short_answer(
            submission_data.get("answer"),
            _simple_short_answer_keys(contents),
            contents.get("match_mode"),
        )
        return task_max if passed else 0

    if task.assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER:
        passed = _check_number_answer(
            submission_data.get("answer"),
            contents.get("correct_value"),
            contents.get("tolerance"),
        )
        return task_max if passed else 0

    if task.assignment_type == AssignmentTaskTypeEnum.QUIZ:
        return _grade_quiz_task(contents, submission_data, task_max)

    if task.assignment_type == AssignmentTaskTypeEnum.FORM:
        return _grade_form_task(contents, submission_data, task_max)

    if task.assignment_type == AssignmentTaskTypeEnum.CODE:
        return await _grade_code_task_async(task, task_submission)

    if task.assignment_type == AssignmentTaskTypeEnum.ESSAY:
        return await _grade_essay_task_async(
            task,
            task_submission,
            org_id=org_id,
            user_id=user_id,
            db_session=db_session,
        )

    return None


def _server_verified_task_feedback(
    verified_grade: int,
    max_grade: int,
    retry_note: str | None = None,
) -> str:
    if max_grade > 0 and verified_grade >= max_grade:
        return "答案正確。"
    if verified_grade > 0:
        feedback = "部分答案正確，仍有內容未符合參考答案。"
    else:
        feedback = "答案未符合參考答案，請查看提示或參考答案後再試。"
    if retry_note:
        feedback = f"{feedback}{retry_note}"
    return feedback


def _assignment_retry_feedback_note(
    assignment: Assignment,
    assignment_user_submission: AssignmentUserSubmission,
) -> str | None:
    if not assignment.allow_retries:
        return None
    current_attempt = int(assignment_user_submission.attempt_number or 1)
    max_attempts = int(assignment.max_retries or 0)
    if max_attempts and current_attempt >= max_attempts:
        return None
    if (assignment.score_policy or "highest") == "highest":
        return " 可以按「重做」改完再提交；系統會保留最高分。"
    return " 可以按「重做」改完再提交；系統會用最新一次成績計分。"


def _submission_has_text(value) -> bool:
    if value is None:
        return False
    return bool(str(value).strip())


def _simple_quiz_option_text(option: dict) -> str:
    return str(option.get("text") or option.get("option") or option.get("label") or "").strip()


def _simple_form_blank_answer(blank: dict) -> str:
    return str(blank.get("correctAnswer") or blank.get("correct_answer") or blank.get("answer") or "").strip()


def _simple_short_answer_keys(contents: dict) -> list:
    answers = contents.get("correct_answers") or contents.get("accepted_answers") or []
    if isinstance(answers, str):
        return [answers]
    return answers if isinstance(answers, list) else []


def _student_task_issue_detail(tasks: list[AssignmentTask], limit: int = 3) -> str:
    titles: list[str] = []
    for task in tasks[:limit]:
        title = re.sub(r"\s+", " ", str(getattr(task, "title", "") or "").strip())
        titles.append((title or "未命名題目")[:40])
    if not titles:
        return ""
    suffix = f" 等 {len(tasks)} 題" if len(tasks) > limit else ""
    return f"：{'、'.join(titles)}{suffix}"


def _clean_assignment_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _require_assignment_title(value: str | None) -> str:
    title = _clean_assignment_title(value)
    if not title:
        raise HTTPException(
            status_code=400,
            detail="請輸入作業名稱。",
        )
    return title


def _clean_assignment_task_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _require_assignment_task_title(value: str | None) -> str:
    title = _clean_assignment_task_title(value)
    if not title:
        raise HTTPException(
            status_code=400,
            detail="請輸入題目名稱。",
        )
    return title


def _require_positive_assignment_task_max_grade(value) -> int:
    try:
        max_grade_value = int(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="題目分值必須大於 0。",
        )
    if max_grade_value <= 0:
        raise HTTPException(
            status_code=400,
            detail="題目分值必須大於 0。",
        )
    return max_grade_value


async def _read_assignment_user_submission(
    assignment_id: int | None,
    user_id: int,
    db_session: AsyncSession,
) -> AssignmentUserSubmission | None:
    if assignment_id is None:
        return None
    return (
        await db_session.execute(
            select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.assignment_id == assignment_id,
                AssignmentUserSubmission.user_id == user_id,
            )
        )
    ).scalars().first()


def _ensure_student_can_change_assignment_evidence(
    final_submission: AssignmentUserSubmission | None,
) -> None:
    if final_submission and final_submission.submission_status not in (
        AssignmentUserSubmissionStatus.PENDING,
        AssignmentUserSubmissionStatus.NOT_SUBMITTED,
    ):
        raise HTTPException(
            status_code=400,
            detail="這份作業已提交，不能直接修改答案。請按「重做」後再修改並重新提交。",
        )


def _submitted_choice_or_blank_value(
    submission_data: dict,
    *,
    question_uuid: str,
    option_uuid: str | None = None,
    blank_uuid: str | None = None,
):
    submissions = submission_data.get("submissions") if isinstance(submission_data, dict) else None
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


def _is_assignment_task_submission_complete(
    task: AssignmentTask,
    task_submission: AssignmentTaskSubmission | None,
) -> bool:
    if task_submission is None:
        return False
    submission_data = task_submission.task_submission or {}
    if not isinstance(submission_data, dict):
        return False
    contents = task.contents or {}

    if task.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        return _submission_has_text(submission_data.get("answer"))

    if task.assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER:
        return _submission_has_text(submission_data.get("answer"))

    if task.assignment_type == AssignmentTaskTypeEnum.ESSAY:
        return _submission_has_text(
            submission_data.get("essay")
            or submission_data.get("answer")
            or submission_data.get("text")
            or submission_data.get("content")
        )

    if task.assignment_type == AssignmentTaskTypeEnum.QUIZ:
        questions = contents.get("questions") or []
        if not questions:
            return False
        for question in questions:
            if not isinstance(question, dict):
                return False
            question_uuid = question.get("questionUUID")
            options = question.get("options") or []
            if not question_uuid or not options:
                return False
            has_selection = any(
                _coerce_simple_answer_bool(
                    _submitted_choice_or_blank_value(
                        submission_data,
                        question_uuid=question_uuid,
                        option_uuid=option.get("optionUUID"),
                    )
                )
                for option in options
                if isinstance(option, dict) and option.get("optionUUID")
            )
            if not has_selection:
                return False
        return True

    if task.assignment_type == AssignmentTaskTypeEnum.FORM:
        questions = contents.get("questions") or []
        if not questions:
            return False
        for question in questions:
            if not isinstance(question, dict):
                return False
            question_uuid = question.get("questionUUID")
            blanks = question.get("blanks") or []
            if not question_uuid or not blanks:
                return False
            for blank in blanks:
                if not isinstance(blank, dict):
                    return False
                blank_uuid = blank.get("blankUUID")
                if not blank_uuid:
                    return False
                value = _submitted_choice_or_blank_value(
                    submission_data,
                    question_uuid=question_uuid,
                    blank_uuid=blank_uuid,
                )
                if not _submission_has_text(value):
                    return False
        return True

    if task.assignment_type == AssignmentTaskTypeEnum.CODE:
        if contents.get("mode") == "web_preview" or submission_data.get("mode") == "web_preview":
            return any(
                _submission_has_text(submission_data.get(field))
                for field in ("html_code", "css_code", "js_code", "source_code")
            )
        return _submission_has_text(submission_data.get("source_code"))

    if task.assignment_type == AssignmentTaskTypeEnum.FILE_SUBMISSION:
        return _submission_has_text(submission_data.get("fileUUID"))

    # Legacy/manual task types are treated as complete once a saved row exists
    # because the system does not know how to validate their answer shape.
    return True


REMEDIATION_PRACTICE_QUESTION_COUNT = 3
REMEDIATION_PRACTICE_MIN_QUESTION_COUNT = 2
REMEDIATION_PRACTICE_STATUS_GENERATED = "generated"
REMEDIATION_PRACTICE_STATUS_COMPLETED = "completed"
REMEDIATION_PRACTICE_TYPES = frozenset(
    {
        AssignmentTaskTypeEnum.QUIZ,
        AssignmentTaskTypeEnum.FORM,
        AssignmentTaskTypeEnum.SHORT_ANSWER,
    }
)


def _remediation_drop_answer_keys(data: dict) -> dict:
    clean = dict(data or {})
    for key in (
        "assigned_right_answer",
        "correct",
        "is_correct",
        "isCorrect",
        "correctAnswer",
        "correct_answer",
        "correct_answers",
        "accepted_answers",
        "answer",
        "explanation",
    ):
        clean.pop(key, None)
    return clean


def _sanitize_remediation_contents(
    assignment_type: AssignmentTaskTypeEnum,
    contents: dict,
    reveal_answers: bool,
) -> dict:
    data = dict(contents or {})
    if reveal_answers:
        return data
    if assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        for key in ("correct_answers", "accepted_answers", "correctAnswer", "correct_answer", "answer", "explanation"):
            data.pop(key, None)
        return data
    if assignment_type == AssignmentTaskTypeEnum.QUIZ:
        questions = []
        for question in data.get("questions") or []:
            if not isinstance(question, dict):
                continue
            clean_question = _remediation_drop_answer_keys(question)
            clean_question["options"] = [
                _remediation_drop_answer_keys(option)
                for option in (question.get("options") or [])
                if isinstance(option, dict)
            ]
            questions.append(clean_question)
        data["questions"] = questions
        return data
    if assignment_type == AssignmentTaskTypeEnum.FORM:
        questions = []
        for question in data.get("questions") or []:
            if not isinstance(question, dict):
                continue
            clean_question = _remediation_drop_answer_keys(question)
            clean_question["blanks"] = [
                _remediation_drop_answer_keys(blank)
                for blank in (question.get("blanks") or [])
                if isinstance(blank, dict)
            ]
            questions.append(clean_question)
        data["questions"] = questions
    return data


def _remediation_question_read(question: dict, reveal_answers: bool = False) -> dict:
    assignment_type = AssignmentTaskTypeEnum(
        getattr(question.get("assignment_type"), "value", question.get("assignment_type"))
    )
    return {
        **question,
        "assignment_type": assignment_type.value,
        "contents": _sanitize_remediation_contents(
            assignment_type,
            question.get("contents") or {},
            reveal_answers,
        ),
    }


def _remediation_practice_payload(
    practice: AssignmentRemediationPractice,
    *,
    reveal_answers: bool,
) -> dict:
    questions = [
        _remediation_question_read(question, reveal_answers=reveal_answers)
        for question in (practice.questions or [])
        if isinstance(question, dict)
    ]
    return {
        "practice_uuid": practice.assignment_remediation_practice_uuid,
        "status": practice.status,
        "questions": questions,
        "answers": practice.answers if reveal_answers else None,
        "score": practice.score,
        "max_score": practice.max_score,
        "feedback": practice.feedback or {},
        "created_at": practice.created_at,
        "updated_at": practice.updated_at,
        "completed_at": practice.completed_at,
    }


def _remediation_summary_for_submission(
    submission: AssignmentUserSubmission | None,
    max_grade: int,
    practice: AssignmentRemediationPractice | None,
) -> dict:
    if practice:
        if practice.status == REMEDIATION_PRACTICE_STATUS_COMPLETED:
            return {
                "status": "completed",
                "label": "已完成",
                "eligible": True,
                "score": practice.score,
                "max_score": practice.max_score,
                "completed_at": practice.completed_at,
            }
        return {
            "status": "generated",
            "label": "待完成",
            "eligible": True,
            "score": practice.score,
            "max_score": practice.max_score,
            "completed_at": None,
        }

    if (
        submission
        and submission.submission_status == AssignmentUserSubmissionStatus.GRADED
        and max_grade > 0
        and int(submission.grade or 0) < max_grade
    ):
        return {
            "status": "not_started",
            "label": "未補練",
            "eligible": True,
            "score": None,
            "max_score": 0,
            "completed_at": None,
        }
    return {
        "status": "not_needed",
        "label": "無需補練",
        "eligible": False,
        "score": None,
        "max_score": 0,
        "completed_at": None,
    }


def _latest_remediation_practice(
    current: AssignmentRemediationPractice | None,
    candidate: AssignmentRemediationPractice,
) -> AssignmentRemediationPractice:
    if current is None:
        return candidate

    def key(practice: AssignmentRemediationPractice) -> tuple[int, float, int]:
        status_rank = 1 if practice.status == REMEDIATION_PRACTICE_STATUS_COMPLETED else 0
        return (
            status_rank,
            _datetime_sort_value(practice.updated_at or practice.created_at),
            int(practice.id or 0),
        )

    return candidate if key(candidate) > key(current) else current


def _remediation_question_text(task: AssignmentTask, contents: dict) -> str:
    if task.assignment_type in {AssignmentTaskTypeEnum.QUIZ, AssignmentTaskTypeEnum.FORM}:
        questions = contents.get("questions") or []
        if questions and isinstance(questions[0], dict):
            text = str(questions[0].get("questionText") or questions[0].get("question") or "").strip()
            if text:
                return text
    if task.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        text = str(contents.get("prompt") or contents.get("question") or "").strip()
        if text:
            return text
    return str(task.description or task.title or "").strip()


def _extract_short_answer_weak_point(
    task: AssignmentTask,
    task_submission: AssignmentTaskSubmission,
) -> dict | None:
    contents = task.contents or {}
    answers = [
        str(answer).strip()
        for answer in _simple_short_answer_keys(contents)
        if str(answer).strip()
    ]
    if not answers:
        return None
    student_answer = str((task_submission.task_submission or {}).get("answer") or "").strip()
    return {
        "assignment_task_uuid": task.assignment_task_uuid,
        "assignment_type": AssignmentTaskTypeEnum.SHORT_ANSWER.value,
        "question": _remediation_question_text(task, contents),
        "student_answer": student_answer,
        "correct_answer": answers[0],
        "marking_key": answers[:3],
    }


def _extract_form_weak_point(
    task: AssignmentTask,
    task_submission: AssignmentTaskSubmission,
) -> dict | None:
    contents = task.contents or {}
    submission_data = task_submission.task_submission or {}
    for question in contents.get("questions") or []:
        if not isinstance(question, dict):
            continue
        question_uuid = question.get("questionUUID")
        question_text = str(question.get("questionText") or question.get("question") or "").strip()
        for blank in question.get("blanks") or []:
            if not isinstance(blank, dict):
                continue
            blank_uuid = blank.get("blankUUID")
            correct_answer = _simple_form_blank_answer(blank)
            if not correct_answer:
                continue
            student_answer = _submitted_choice_or_blank_value(
                submission_data,
                question_uuid=question_uuid,
                blank_uuid=blank_uuid,
            )
            if not _simple_text_answers_equivalent(
                _normalize_simple_text_answer(student_answer),
                _normalize_simple_text_answer(correct_answer),
            ):
                return {
                    "assignment_task_uuid": task.assignment_task_uuid,
                    "assignment_type": AssignmentTaskTypeEnum.FORM.value,
                    "question": question_text or _remediation_question_text(task, contents),
                    "student_answer": str(student_answer or "").strip(),
                    "correct_answer": correct_answer,
                    "marking_key": [correct_answer],
                }
    return None


def _extract_quiz_weak_point(
    task: AssignmentTask,
    task_submission: AssignmentTaskSubmission,
) -> dict | None:
    contents = task.contents or {}
    submission_data = task_submission.task_submission or {}
    for question in contents.get("questions") or []:
        if not isinstance(question, dict):
            continue
        question_uuid = question.get("questionUUID")
        question_text = str(question.get("questionText") or question.get("question") or "").strip()
        correct_option_text = ""
        selected_option_text = ""
        selected_wrong = False
        selected_correct = False
        for option in question.get("options") or []:
            if not isinstance(option, dict):
                continue
            option_uuid = option.get("optionUUID")
            option_text = _simple_quiz_option_text(option)
            is_correct = _coerce_simple_answer_bool(option.get("assigned_right_answer"))
            is_selected = _coerce_simple_answer_bool(
                _submitted_choice_or_blank_value(
                    submission_data,
                    question_uuid=question_uuid,
                    option_uuid=option_uuid,
                )
            )
            if is_correct:
                correct_option_text = option_text
            if is_selected:
                selected_option_text = option_text
                if is_correct:
                    selected_correct = True
                else:
                    selected_wrong = True
        if correct_option_text and (selected_wrong or not selected_correct):
            return {
                "assignment_task_uuid": task.assignment_task_uuid,
                "assignment_type": AssignmentTaskTypeEnum.QUIZ.value,
                "question": question_text or _remediation_question_text(task, contents),
                "student_answer": selected_option_text,
                "correct_answer": correct_option_text,
                "marking_key": [correct_option_text],
            }
    return None


def _extract_remediation_weak_points(
    assignment_tasks: list[AssignmentTask],
    task_submissions_by_task_id: dict[int, AssignmentTaskSubmission],
) -> list[dict]:
    weak_points: list[dict] = []
    for task in sorted(assignment_tasks, key=lambda item: item.id or 0):
        if task.assignment_type not in REMEDIATION_PRACTICE_TYPES or task.id is None:
            continue
        task_submission = task_submissions_by_task_id.get(int(task.id))
        if not task_submission:
            continue
        task_max = int(task.max_grade_value or 0)
        if task_max <= 0 or int(task_submission.grade or 0) >= task_max:
            continue
        weak_point = None
        if task.assignment_type == AssignmentTaskTypeEnum.QUIZ:
            weak_point = _extract_quiz_weak_point(task, task_submission)
        elif task.assignment_type == AssignmentTaskTypeEnum.FORM:
            weak_point = _extract_form_weak_point(task, task_submission)
        elif task.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
            weak_point = _extract_short_answer_weak_point(task, task_submission)
        if weak_point and weak_point.get("question") and weak_point.get("correct_answer"):
            weak_points.append(weak_point)
    return weak_points[:REMEDIATION_PRACTICE_QUESTION_COUNT]


def _fallback_remediation_questions(weak_points: list[dict]) -> list[dict]:
    questions: list[dict] = []
    if not weak_points:
        return questions

    index = 0
    while (
        len(questions) < REMEDIATION_PRACTICE_QUESTION_COUNT
        and index < REMEDIATION_PRACTICE_QUESTION_COUNT * 2
    ):
        weak_point = weak_points[index % len(weak_points)]
        correct_answer = str(weak_point.get("correct_answer") or "").strip()
        source_question = str(weak_point.get("question") or "").strip()
        if not correct_answer:
            index += 1
            continue
        answer_is_short = len(correct_answer) <= 40 and "\n" not in correct_answer and "。" not in correct_answer
        question_uuid = f"remediationquestion_{uuid4()}"
        if len(questions) % 2 == 0 and answer_is_short:
            contents = {
                "questions": [
                    {
                        "questionText": f"補練：{source_question}",
                        "questionUUID": f"question_{uuid4()}",
                        "blanks": [
                            {
                                "blankUUID": f"blank_{uuid4()}",
                                "placeholder": "填寫答案",
                                "correctAnswer": correct_answer,
                                "hint": "想一想剛才正確答案的關鍵詞。",
                            }
                        ],
                    }
                ]
            }
            assignment_type = AssignmentTaskTypeEnum.FORM
        else:
            contents = {
                "prompt": f"補練：{source_question}",
                "correct_answers": [correct_answer],
                "match_mode": "case_insensitive",
                "explanation": "這題是根據你剛才的錯題整理的短練習。",
            }
            assignment_type = AssignmentTaskTypeEnum.SHORT_ANSWER
        questions.append(
            {
                "question_uuid": question_uuid,
                "title": "錯題補練",
                "description": source_question,
                "hint": "先回想原題，再輸入最直接的答案。",
                "assignment_type": assignment_type.value,
                "contents": contents,
                "max_grade": 100,
                "source": "fallback",
                "source_assignment_task_uuid": weak_point.get("assignment_task_uuid"),
            }
        )
        index += 1
    return questions[:REMEDIATION_PRACTICE_QUESTION_COUNT]


def _remediation_questions_from_drafts(
    drafts,
    weak_points: list[dict],
) -> list[dict]:
    questions: list[dict] = []
    for index, draft in enumerate(drafts[:REMEDIATION_PRACTICE_QUESTION_COUNT]):
        weak_point = weak_points[index % len(weak_points)] if weak_points else {}
        questions.append(
            {
                "question_uuid": f"remediationquestion_{uuid4()}",
                "title": draft.title,
                "description": draft.description,
                "hint": draft.hint,
                "assignment_type": draft.assignment_type.value,
                "contents": draft.contents,
                "max_grade": 100,
                "source": "ai",
                "source_assignment_task_uuid": weak_point.get("assignment_task_uuid"),
            }
        )
    return questions


def _remediation_assignment_context(assignment: Assignment) -> dict:
    return {
        "subject": assignment.subject or "未設定科目",
        "education_stage": assignment.education_stage or "",
        "grade_level": assignment.grade_level or "未設定年級",
        "unit": assignment.unit or "",
        "difficulty": "beginner",
        "assignment_title": assignment.title,
        "learning_objectives": _clean_learning_objectives(assignment.learning_objectives),
    }


async def _load_assignment_remediation_context(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
) -> tuple[Assignment, Course, AssignmentUserSubmission | None, list[AssignmentTask], dict[int, AssignmentTaskSubmission]]:
    _block_api_tokens(current_user)
    row = (
        await db_session.execute(
            select(Assignment, Course)
            .join(Course, Course.id == Assignment.course_id)  # type: ignore
            .where(Assignment.assignment_uuid == assignment_uuid)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    assignment, course = row
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    user_id = int(current_user.id)
    submission = await _read_assignment_user_submission(
        int(assignment.id) if assignment.id is not None else None,
        user_id,
        db_session,
    )
    assignment_tasks = (
        await db_session.execute(
            select(AssignmentTask)
            .where(AssignmentTask.assignment_id == assignment.id)
            .order_by(AssignmentTask.id.asc())
        )
    ).scalars().all()
    task_ids = [int(task.id) for task in assignment_tasks if task.id is not None]
    task_submissions_by_task_id: dict[int, AssignmentTaskSubmission] = {}
    if task_ids:
        task_submissions = (
            await db_session.execute(
                select(AssignmentTaskSubmission).where(
                    AssignmentTaskSubmission.user_id == user_id,
                    AssignmentTaskSubmission.assignment_task_id.in_(task_ids),  # type: ignore[attr-defined]
                )
            )
        ).scalars().all()
        task_submissions_by_task_id = {
            int(task_submission.assignment_task_id): task_submission
            for task_submission in task_submissions
        }
    return assignment, course, submission, assignment_tasks, task_submissions_by_task_id


async def _read_existing_remediation_practice(
    assignment_id: int,
    assignment_submission_id: int,
    user_id: int,
    db_session: AsyncSession,
) -> AssignmentRemediationPractice | None:
    practices = (
        await db_session.execute(
            select(AssignmentRemediationPractice).where(
                AssignmentRemediationPractice.assignment_id == assignment_id,
                AssignmentRemediationPractice.assignment_submission_id == assignment_submission_id,
                AssignmentRemediationPractice.user_id == user_id,
            )
        )
    ).scalars().all()
    current = None
    for practice in practices:
        current = _latest_remediation_practice(current, practice)
    return current


async def read_my_assignment_remediation(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
) -> dict:
    assignment, _course, submission, assignment_tasks, task_submissions_by_task_id = await _load_assignment_remediation_context(
        request,
        assignment_uuid,
        current_user,
        db_session,
    )
    if not submission or submission.submission_status != AssignmentUserSubmissionStatus.GRADED:
        return {
            "status": "not_ready",
            "eligible": False,
            "message": "完成並批改作業後，才會顯示補練。",
            "practice": None,
        }

    existing = await _read_existing_remediation_practice(
        int(assignment.id),
        int(submission.id),
        int(current_user.id),
        db_session,
    )
    if existing:
        reveal_answers = existing.status == REMEDIATION_PRACTICE_STATUS_COMPLETED
        return {
            "status": existing.status,
            "eligible": True,
            "message": "已建立補練。",
            "practice": _remediation_practice_payload(
                existing,
                reveal_answers=reveal_answers,
            ),
        }

    weak_points = _extract_remediation_weak_points(
        assignment_tasks,
        task_submissions_by_task_id,
    )
    if not weak_points:
        return {
            "status": "not_needed",
            "eligible": False,
            "message": "這次沒有可自動整理的錯題補練。",
            "practice": None,
        }
    return {
        "status": "available",
        "eligible": True,
        "message": f"已找到 {len(weak_points)} 個可補強重點。",
        "practice": None,
        "weak_point_count": len(weak_points),
    }


async def create_my_assignment_remediation(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
) -> dict:
    assignment, course, submission, assignment_tasks, task_submissions_by_task_id = await _load_assignment_remediation_context(
        request,
        assignment_uuid,
        current_user,
        db_session,
    )
    if not submission or submission.submission_status != AssignmentUserSubmissionStatus.GRADED:
        raise HTTPException(
            status_code=400,
            detail="完成並批改作業後，才可以開始錯題補練。",
        )

    existing = await _read_existing_remediation_practice(
        int(assignment.id),
        int(submission.id),
        int(current_user.id),
        db_session,
    )
    if existing:
        return {
            "status": existing.status,
            "eligible": True,
            "message": "已建立補練。",
            "practice": _remediation_practice_payload(
                existing,
                reveal_answers=existing.status == REMEDIATION_PRACTICE_STATUS_COMPLETED,
            ),
        }

    weak_points = _extract_remediation_weak_points(
        assignment_tasks,
        task_submissions_by_task_id,
    )
    if not weak_points:
        raise HTTPException(
            status_code=400,
            detail="這次沒有可自動整理的錯題補練。",
        )

    ai_used = False
    ai_message = None
    questions: list[dict] = []
    try:
        from src.services.ai.assignments import generate_remediation_question_drafts

        drafts, ai_used, ai_message = await generate_remediation_question_drafts(
            weak_points,
            _remediation_assignment_context(assignment),
            org_id=int(course.org_id),
            user_id=int(current_user.id),
            db_session=db_session,
            count=REMEDIATION_PRACTICE_QUESTION_COUNT,
        )
        questions = _remediation_questions_from_drafts(drafts, weak_points)
    except Exception:
        logger.exception("AI remediation generation wrapper failed")
        ai_message = "AI 暫時不可用，已改用系統補練題。"

    if len(questions) < REMEDIATION_PRACTICE_MIN_QUESTION_COUNT:
        questions = _fallback_remediation_questions(weak_points)
        ai_used = False
        ai_message = ai_message or "AI 暫時不可用，已改用系統補練題。"

    if len(questions) < REMEDIATION_PRACTICE_MIN_QUESTION_COUNT:
        raise HTTPException(
            status_code=400,
            detail="暫時未能從錯題整理出可自動批改的補練題。",
        )

    now = str(datetime.now())
    practice = AssignmentRemediationPractice(
        assignment_remediation_practice_uuid=f"assignmentremediation_{uuid4()}",
        assignment_id=int(assignment.id),
        assignment_submission_id=int(submission.id),
        user_id=int(current_user.id),
        status=REMEDIATION_PRACTICE_STATUS_GENERATED,
        questions=questions[:REMEDIATION_PRACTICE_QUESTION_COUNT],
        answers=None,
        score=None,
        max_score=sum(int(question.get("max_grade") or 0) for question in questions),
        feedback={
            "ai_used": ai_used,
            "message": ai_message,
            "weak_point_count": len(weak_points),
            "does_not_change_assignment_grade": True,
        },
        created_at=now,
        updated_at=now,
    )
    db_session.add(practice)
    await db_session.commit()
    await db_session.refresh(practice)
    return {
        "status": practice.status,
        "eligible": True,
        "message": "補練已準備好。",
        "practice": _remediation_practice_payload(practice, reveal_answers=False),
    }


def _answer_for_remediation_question(answers_by_uuid: dict[str, dict], question: dict) -> dict:
    answer = answers_by_uuid.get(str(question.get("question_uuid") or ""))
    return answer if isinstance(answer, dict) else {}


async def submit_my_assignment_remediation(
    request: Request,
    assignment_uuid: str,
    answers: list[dict],
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
) -> dict:
    assignment, _course, submission, _assignment_tasks, _task_submissions_by_task_id = await _load_assignment_remediation_context(
        request,
        assignment_uuid,
        current_user,
        db_session,
    )
    if not submission:
        raise HTTPException(status_code=400, detail="請先完成原作業，再提交補練。")
    practice = await _read_existing_remediation_practice(
        int(assignment.id),
        int(submission.id),
        int(current_user.id),
        db_session,
    )
    if not practice:
        raise HTTPException(status_code=404, detail="找不到補練記錄。請先按「補練一下」。")
    if practice.status == REMEDIATION_PRACTICE_STATUS_COMPLETED:
        return {
            "status": practice.status,
            "eligible": True,
            "message": "這份補練已完成。",
            "practice": _remediation_practice_payload(practice, reveal_answers=True),
        }

    answers_by_uuid = {
        str(item.get("question_uuid") or ""): item.get("answer") or {}
        for item in answers or []
        if isinstance(item, dict)
    }
    missing_count = 0
    total = 0
    max_score = 0
    saved_answers: list[dict] = []
    feedback_items: list[dict] = []
    for question in practice.questions or []:
        if not isinstance(question, dict):
            continue
        assignment_type = AssignmentTaskTypeEnum(question.get("assignment_type"))
        answer = _answer_for_remediation_question(answers_by_uuid, question)
        task = SimpleNamespace(
            assignment_type=assignment_type,
            contents=question.get("contents") or {},
            max_grade_value=int(question.get("max_grade") or 0),
            assignment_task_uuid=question.get("question_uuid") or "",
        )
        task_submission = SimpleNamespace(task_submission=answer)
        if not _is_assignment_task_submission_complete(task, task_submission):
            missing_count += 1
            continue
        verified = await _server_verified_task_grade(task, task_submission)
        grade = int(verified if verified is not None else 0)
        max_grade = int(question.get("max_grade") or 0)
        total += grade
        max_score += max_grade
        feedback = _server_verified_task_feedback(grade, max_grade)
        saved_answers.append(
            {
                "question_uuid": question.get("question_uuid"),
                "answer": answer,
                "grade": grade,
                "max_grade": max_grade,
                "feedback": feedback,
            }
        )
        feedback_items.append(
            {
                "question_uuid": question.get("question_uuid"),
                "passed": max_grade > 0 and grade >= max_grade,
                "feedback": feedback,
            }
        )

    if missing_count:
        raise HTTPException(
            status_code=400,
            detail=f"請先完成所有補練題再提交。尚有 {missing_count} 題未作答。",
        )

    practice.status = REMEDIATION_PRACTICE_STATUS_COMPLETED
    practice.answers = saved_answers
    practice.score = total
    practice.max_score = max_score
    practice.completed_at = str(datetime.now())
    practice.updated_at = str(datetime.now())
    practice.feedback = {
        **(practice.feedback or {}),
        "items": feedback_items,
        "summary": "補練已完成，原作業分數不會因此改變。",
        "does_not_change_assignment_grade": True,
    }
    db_session.add(practice)
    await db_session.commit()
    await db_session.refresh(practice)
    return {
        "status": practice.status,
        "eligible": True,
        "message": "補練已完成。",
        "practice": _remediation_practice_payload(practice, reveal_answers=True),
    }


def _percentage_to_letter_grade(percentage: float) -> str:
    """Convert a 0-100 percentage into a US-style A/B/C/D/F letter grade."""
    if percentage >= 90:
        return "A"
    if percentage >= 80:
        return "B"
    if percentage >= 70:
        return "C"
    if percentage >= 60:
        return "D"
    return "F"


def _percentage_to_gpa(percentage: float) -> str:
    """Convert a 0-100 percentage into a US 4.0 GPA scale string."""
    if percentage >= 93:
        return "4.0"
    if percentage >= 90:
        return "3.7"
    if percentage >= 87:
        return "3.3"
    if percentage >= 83:
        return "3.0"
    if percentage >= 80:
        return "2.7"
    if percentage >= 77:
        return "2.3"
    if percentage >= 73:
        return "2.0"
    if percentage >= 70:
        return "1.7"
    if percentage >= 67:
        return "1.3"
    if percentage >= 63:
        return "1.0"
    if percentage >= 60:
        return "0.7"
    return "0.0"


def _build_tasks_breakdown(
    assignment_tasks,
    task_submissions_by_task_id: dict,
    passing_threshold: float,
) -> list:
    """
    Build the per-task breakdown array included in grade responses.

    Shared by the grading path (``_apply_grade_and_finalize``) and the read
    path (``get_grade_assignment_submission``) so both sides always agree on
    the numbers. Each row includes the raw grade, the task's max, the
    percentage, and a ``passed`` flag computed against the assignment's
    grading-type-aware passing threshold — that way the student's activity
    view and the teacher's evaluate modal can render consistent pass/fail
    chips without each one re-deriving its own threshold.
    """
    rows = []
    for index, task in enumerate(
        sorted(assignment_tasks, key=lambda t: t.id or 0)
    ):
        ts = task_submissions_by_task_id.get(task.id)
        task_max = int(task.max_grade_value or 0)
        task_raw = int(ts.grade or 0) if ts else 0
        task_percentage = (
            round((task_raw / task_max) * 100.0, 2) if task_max > 0 else 0.0
        )
        task_percentage = max(min(task_percentage, 100.0), 0.0)
        rows.append(
            {
                "index": index + 1,
                "assignment_task_uuid": task.assignment_task_uuid,
                "title": task.title,
                "description": task.description,
                "assignment_type": task.assignment_type,
                "submitted": ts is not None,
                "grade": task_raw,
                "max_grade": task_max,
                "percentage": task_percentage,
                "percentage_display": f"{task_percentage:.0f}%",
                "points_summary": f"{task_raw}/{task_max} 分",
                "passed": task_percentage >= passing_threshold,
                "feedback": ts.task_submission_grade_feedback if ts else None,
            }
        )
    return rows


def compute_assignment_grade(
    raw_grade: int,
    max_grade: int,
    grading_type: GradingTypeEnum | str | None,
    overall_feedback: str | None = None,
) -> dict:
    """
    Build a normalized grade object from a raw grade sum and the configured
    grading type.

    Responsibilities:
    - Clamp raw_grade to [0, max_grade] so a buggy task sum can't report 120/100.
    - Guard against max_grade <= 0 (yields a 0% grade, not a divide-by-zero).
    - Compute a single percentage that every display format derives from.
    - Produce a human-readable `display_grade` (the canonical string the UI
      renders), plus `letter_grade`, `points_summary`, and `percentage_display`
      as secondary formats the UI can show side-by-side without doing math.
    - Flag `passed` using a mode-aware threshold so it stays consistent with
      the display: ALPHABET/GPA_SCALE pass at 60% (D / 0.7), everything else
      passes at 50%.

    The backend intentionally stores only the raw integer sum in
    AssignmentUserSubmission.grade; all formatting is derived on read.
    """
    clamped_max = max(int(max_grade or 0), 0)
    clamped_grade = max(min(int(raw_grade or 0), clamped_max), 0)

    if clamped_max > 0:
        percentage = (clamped_grade / clamped_max) * 100.0
    else:
        percentage = 0.0

    # Round to 2 decimal places for stable display + comparisons
    percentage = round(percentage, 2)

    # Normalize enum value to string so the rest of the function is type-agnostic
    gt_value = (
        grading_type.value
        if isinstance(grading_type, GradingTypeEnum)
        else (grading_type or "NUMERIC")
    )

    # Mode-aware passing threshold — keeps `passed` aligned with the display
    if gt_value in ("ALPHABET", "GPA_SCALE"):
        passing_threshold = LETTER_PASSING_THRESHOLD_PERCENTAGE
    else:
        passing_threshold = DEFAULT_PASSING_THRESHOLD_PERCENTAGE
    passed = percentage >= passing_threshold

    # Secondary formats — always available regardless of grading_type so the
    # UI can render e.g. "B (85/100 · 85%)" without recomputing anything.
    letter_grade = _percentage_to_letter_grade(percentage)
    points_summary = f"{clamped_grade}/{clamped_max} 分"
    percentage_display = f"{percentage:.2f}%"

    if gt_value == "ALPHABET":
        display_grade = letter_grade
    elif gt_value == "PERCENTAGE":
        display_grade = percentage_display
    elif gt_value == "PASS_FAIL":
        display_grade = "通過" if passed else "未通過"
    elif gt_value == "GPA_SCALE":
        display_grade = _percentage_to_gpa(percentage)
    else:
        # NUMERIC and any unknown type: show a canonical "X/100" score so
        # students always see a familiar out-of-100 number regardless of how
        # many tasks the assignment has or what their max_grade_values were.
        display_grade = f"{round(percentage)}/100"

    return {
        "grade": clamped_grade,
        "max_grade": clamped_max,
        "percentage": percentage,
        "display_grade": display_grade,
        "letter_grade": letter_grade,
        "points_summary": points_summary,
        "percentage_display": percentage_display,
        "passed": passed,
        "passing_threshold": passing_threshold,
        "grading_type": gt_value,
        "overall_feedback": overall_feedback,
    }


## > School pilot workbench + gradebook helpers


def _parse_date(value: str | None):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in [text, text[:10]]:
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            continue
    return None


def _datetime_sort_value(value: str | None) -> float:
    if not value:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _latest_assignment_submission(
    current: AssignmentUserSubmission | None,
    candidate: AssignmentUserSubmission,
) -> AssignmentUserSubmission:
    if current is None:
        return candidate

    def selection_key(submission: AssignmentUserSubmission) -> tuple[int, float, int]:
        return (
            int(submission.attempt_number or 0),
            _datetime_sort_value(submission.update_date or submission.creation_date),
            int(submission.id or 0),
        )

    return candidate if selection_key(candidate) > selection_key(current) else current


def _submission_status_for_due_date(assignment: Assignment) -> AssignmentUserSubmissionStatus:
    due_date = _parse_date(assignment.due_date)
    if due_date is not None and due_date < datetime.now().date():
        return AssignmentUserSubmissionStatus.LATE
    return AssignmentUserSubmissionStatus.SUBMITTED


def _validate_new_assignment_due_date(due_date: str | None) -> None:
    parsed_due_date = _parse_date(due_date)
    if parsed_due_date is None:
        raise HTTPException(
            status_code=400,
            detail="建立作業前請設定有效的截止日期。",
        )
    if parsed_due_date < datetime.now().date():
        raise HTTPException(
            status_code=400,
            detail="建立作業時請把截止日期設定為今天或之後，避免學生一收到作業就逾期。",
        )


def _normalize_int_list(value) -> list[int]:
    if not value or not isinstance(value, list):
        return []
    clean: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in seen:
            clean.append(number)
            seen.add(number)
    return clean


def _clean_learning_objectives(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _assignment_read(assignment: Assignment) -> AssignmentRead:
    """Serialize an assignment without repairing the loaded ORM row in place.

    The h10 migration repairs historical NULL/JSON-null values permanently, but
    reads must also tolerate a lagging migration or manually-corrupted row.
    """
    payload = {
        field_name: getattr(assignment, field_name, None)
        for field_name in AssignmentRead.model_fields
    }
    payload["learning_objectives"] = _clean_learning_objectives(
        payload["learning_objectives"]
    )
    payload["target_usergroup_ids"] = _normalize_int_list(
        payload["target_usergroup_ids"]
    )
    return AssignmentRead.model_validate(payload)


def _display_user_name(user: User) -> str:
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full_name or user.username or user.email


def _role_has_dashboard_access(user_org: UserOrganization, role: Role | None) -> bool:
    if user_org.role_id in ADMIN_OR_MAINTAINER_ROLE_IDS:
        return True
    rights = getattr(role, "rights", None)
    if hasattr(rights, "model_dump"):
        rights = rights.model_dump()
    if not isinstance(rights, dict):
        return False
    dashboard = rights.get("dashboard") or {}
    if hasattr(dashboard, "model_dump"):
        dashboard = dashboard.model_dump()
    return isinstance(dashboard, dict) and bool(dashboard.get("action_access", False))


async def _ensure_org_exists(org_id: int, db_session: AsyncSession) -> Organization:
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _read_org_learners(org_id: int, db_session: AsyncSession) -> dict[int, User]:
    rows = (
        await db_session.execute(
            select(User, UserOrganization, Role)
            .join(UserOrganization, UserOrganization.user_id == User.id)  # type: ignore
            .outerjoin(Role, Role.id == UserOrganization.role_id)  # type: ignore
            .where(UserOrganization.org_id == org_id)
            .order_by(User.last_name.asc(), User.first_name.asc(), User.email.asc())
        )
    ).all()
    learners: dict[int, User] = {}
    for user, user_org, role in rows:
        if not _role_has_dashboard_access(user_org, role):
            learners[int(user.id)] = user
    return learners


async def _count_self_test_ready_question_bank_items(
    org_id: int,
    db_session: AsyncSession,
) -> int:
    items = (
        await db_session.execute(
            select(QuestionBankItem).where(
                QuestionBankItem.org_id == org_id,
                QuestionBankItem.visibility == QuestionBankVisibilityEnum.ORG,
                QuestionBankItem.assignment_type.in_(SIMPLE_SELF_TEST_TYPES),
            )
        )
    ).scalars().all()
    return sum(1 for item in items if is_self_test_ready_item(item))


async def _read_usergroup_context(
    org_id: int,
    usergroup_ids: set[int],
    db_session: AsyncSession,
):
    groups_by_id: dict[int, UserGroup] = {}
    members_by_group_id: dict[int, set[int]] = defaultdict(set)
    if not usergroup_ids:
        return groups_by_id, members_by_group_id

    groups = (
        await db_session.execute(
            select(UserGroup).where(
                UserGroup.org_id == org_id,
                UserGroup.id.in_(sorted(usergroup_ids)),
            )
        )
    ).scalars().all()
    groups_by_id = {int(group.id): group for group in groups if group.id is not None}

    memberships = (
        await db_session.execute(
            select(UserGroupUser).where(
                UserGroupUser.org_id == org_id,
                UserGroupUser.usergroup_id.in_(sorted(usergroup_ids)),
            )
        )
    ).scalars().all()
    for membership in memberships:
        members_by_group_id[int(membership.usergroup_id)].add(int(membership.user_id))
    return groups_by_id, members_by_group_id


def _assignment_targets_for_filter(
    assignment: Assignment,
    usergroup_id: int | None,
) -> list[int] | None:
    target_ids = _normalize_int_list(getattr(assignment, "target_usergroup_ids", []))
    if usergroup_id is not None:
        if target_ids and usergroup_id not in target_ids:
            return None
        return [usergroup_id]
    return target_ids


def _expected_user_ids_for_assignment(
    assignment: Assignment,
    learners_by_id: dict[int, User],
    members_by_group_id: dict[int, set[int]],
    usergroup_id: int | None,
) -> list[int]:
    target_ids = _assignment_targets_for_filter(assignment, usergroup_id)
    if target_ids is None:
        return []
    if not target_ids:
        return sorted(learners_by_id.keys())
    expected: set[int] = set()
    for group_id in target_ids:
        expected.update(members_by_group_id.get(group_id, set()))
    return sorted(user_id for user_id in expected if user_id in learners_by_id)


def _submission_status_value(submission: AssignmentUserSubmission | None) -> str:
    if not submission:
        return AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
    return getattr(submission.submission_status, "value", str(submission.submission_status))


def _teacher_review_state(
    assignment: Assignment,
    submission: AssignmentUserSubmission | None,
    all_auto_gradable: bool,
) -> str:
    if not submission:
        return "missing"
    status_value = _submission_status_value(submission)
    if status_value in {
        AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
        AssignmentUserSubmissionStatus.PENDING.value,
    }:
        return "missing"
    needs_review = bool(assignment.teacher_review_required) or not all_auto_gradable
    if status_value in {
        AssignmentUserSubmissionStatus.SUBMITTED.value,
        AssignmentUserSubmissionStatus.LATE.value,
    }:
        return "pending"
    if status_value == AssignmentUserSubmissionStatus.GRADED.value:
        if not needs_review:
            return "not_required"
        submission_review_status = getattr(submission, "teacher_review_status", None)
        if submission_review_status == "confirmed":
            return "confirmed"
        return "pending"
    return "pending" if needs_review else "not_required"


def _gradebook_row_needs_attention(row: dict) -> bool:
    if row.get("source_type") == "self_test":
        return (
            row["teacher_review_status"] == "pending"
            or row["late"] is True
            or row["submission_status"] == SelfTestAttemptStatus.STARTED.value
        )

    if row.get("visible_to_students") is False:
        return False
    if row.get("target_usergroups_valid") is False:
        return False

    return (
        row["teacher_review_status"] == "pending"
        or row["late"] is True
        or row["submission_status"] in {
            AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            AssignmentUserSubmissionStatus.SUBMITTED.value,
            AssignmentUserSubmissionStatus.LATE.value,
        }
    )


def _gradebook_attention_reason(row: dict) -> str:
    status_value = row.get("submission_status")
    review_status = row.get("teacher_review_status")
    is_late = row.get("late") is True

    if row.get("source_type") == "assignment" and row.get("visible_to_students") is False:
        return "課程或活動未發布，學生暫時看不到"
    if row.get("source_type") == "assignment" and row.get("target_usergroups_valid") is False:
        return "尚未設定有效班級/群組"
    if status_value == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value:
        return "逾期未提交" if is_late else "未提交"
    if status_value == AssignmentUserSubmissionStatus.PENDING.value:
        return "重做中，已逾期" if is_late else ""
    if row.get("source_type") == "self_test" and status_value == SelfTestAttemptStatus.STARTED.value:
        return "自測中未提交"
    if review_status == "pending":
        if status_value == AssignmentUserSubmissionStatus.GRADED.value:
            return "已批改，待老師確認"
        if is_late or status_value == AssignmentUserSubmissionStatus.LATE.value:
            return "逾期提交，待老師批改"
        return "已提交，待老師批改"
    if is_late:
        return "逾期"
    return ""


def _gradebook_row_percentage(row: dict) -> float | None:
    status_value = row.get("submission_status")
    if row.get("source_type") == "assignment" and status_value != AssignmentUserSubmissionStatus.GRADED.value:
        return None
    if row.get("source_type") == "self_test" and status_value not in {
        SelfTestAttemptStatus.SUBMITTED.value,
        SelfTestAttemptStatus.REVIEWED.value,
    }:
        return None

    grade_display = row.get("grade_display")
    if isinstance(grade_display, dict):
        percentage = grade_display.get("percentage")
        if isinstance(percentage, (int, float)) and math.isfinite(float(percentage)):
            return float(percentage)

    grade = row.get("grade")
    max_grade = row.get("max_grade")
    if grade is None or grade == "":
        return None
    try:
        grade_value = float(grade)
        max_grade_value = float(max_grade)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(grade_value) or not math.isfinite(max_grade_value) or max_grade_value <= 0:
        return None
    return (grade_value / max_grade_value) * 100


def _summary_int(summary: dict, key: str) -> int:
    try:
        return int(summary.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _build_simple_pilot_status_summary(summary: dict) -> dict:
    learner_count = _summary_int(summary, "learner_count")
    engaged_student_count = _summary_int(summary, "engaged_student_count")
    participation_records = _summary_int(summary, "participation_records")
    graded_records = _summary_int(summary, "graded")
    simple_ready_assignments = _summary_int(summary, "simple_pilot_ready_assignments")
    published_needing_setup = _summary_int(summary, "published_needing_setup")
    published_without_targets = _summary_int(summary, "published_without_targets")
    published_empty_target_usergroups = _summary_int(summary, "published_empty_target_usergroups")
    published_hidden_from_students = _summary_int(summary, "published_hidden_from_students")
    unsubmitted = _summary_int(summary, "unsubmitted")
    needs_practice = _summary_int(summary, "needs_practice_records")
    attention_count = _summary_int(summary, "attention_count")

    evidence_summary = (
        f"已有 {engaged_student_count}/{learner_count} 名學生產生 "
        f"{participation_records} 條學習記錄，{graded_records} 條已完成批改。"
        if learner_count > 0
        else "尚未匯入學生，暫時沒有可展示的學習記錄。"
    )

    if learner_count <= 0:
        return {
            "pilot_status": "not_started",
            "pilot_status_label": "未開始",
            "pilot_status_detail": "尚未匯入學生帳號，校內試行暫時沒有發布對象。",
            "pilot_next_step": "先用 CSV 批量匯入學生，並分配到班級。",
            "principal_evidence_summary": evidence_summary,
        }

    if simple_ready_assignments <= 0:
        if (
            published_without_targets > 0
            or published_hidden_from_students > 0
            or published_needing_setup > 0
        ):
            empty_target_detail = (
                f"其中 {published_empty_target_usergroups} 份班級沒有學生，"
                if published_empty_target_usergroups > 0
                else ""
            )
            return {
                "pilot_status": "setup_needed",
                "pilot_status_label": "需要整理",
                "pilot_status_detail": (
                    "尚未有校內試行就緒的簡單自動批改作業；"
                    f"{published_without_targets} 份需重設班級或加入學生，"
                    f"{empty_target_detail}"
                    f"{published_hidden_from_students} 份學生暫時看不到。"
                ),
                "pilot_next_step": "先整理已發布作業的班級、學生名單、可見狀態和簡單自動批改設定。",
                "principal_evidence_summary": evidence_summary,
            }
        return {
            "pilot_status": "setup_needed",
            "pilot_status_label": "需要準備",
            "pilot_status_detail": "已有學生，但尚未有校內試行就緒的簡單自動批改作業。",
            "pilot_next_step": "建立一份 3 題簡單作業，使用選擇、填空或短問答。",
            "principal_evidence_summary": evidence_summary,
        }

    if (
        published_without_targets > 0
        or published_hidden_from_students > 0
        or published_needing_setup > 0
    ):
        empty_target_detail = (
            f"其中 {published_empty_target_usergroups} 份班級沒有學生，"
            if published_empty_target_usergroups > 0
            else ""
        )
        return {
            "pilot_status": "setup_needed",
            "pilot_status_label": "需要整理",
            "pilot_status_detail": (
                f"已有 {simple_ready_assignments} 份就緒作業，但仍有已發布作業需要整理；"
                f"{published_without_targets} 份需重設班級或加入學生，"
                f"{empty_target_detail}"
                f"{published_hidden_from_students} 份學生暫時看不到。"
            ),
            "pilot_next_step": "先整理已發布作業的班級、學生名單、可見狀態和簡單自動批改設定。",
            "principal_evidence_summary": evidence_summary,
        }

    if engaged_student_count <= 0 or participation_records <= 0:
        return {
            "pilot_status": "waiting_for_students",
            "pilot_status_label": "等待學生使用",
            "pilot_status_detail": (
                f"已有 {simple_ready_assignments} 份就緒作業，"
                "但學生還未產生提交或自測記錄。"
            ),
            "pilot_next_step": "把作業發布給班級，讓學生完成第一份 3 題短練習。",
            "principal_evidence_summary": evidence_summary,
        }

    if attention_count > 0 or unsubmitted > 0 or needs_practice > 0:
        return {
            "pilot_status": "active_needs_followup",
            "pilot_status_label": "已使用，需跟進",
            "pilot_status_detail": (
                f"已有 {engaged_student_count} 名學生產生學習記錄；"
                f"{unsubmitted} 條未提交，{needs_practice} 條需補強。"
            ),
            "pilot_next_step": "先跟進未提交和低分學生，再複製摘要給校長展示使用情況。",
            "principal_evidence_summary": evidence_summary,
        }

    return {
        "pilot_status": "showcase_ready",
        "pilot_status_label": "可展示",
        "pilot_status_detail": (
            f"已有 {engaged_student_count} 名學生產生學習記錄，"
            "核心作業、提交、自動批改和成績記錄閉環已跑通。"
        ),
        "pilot_next_step": "可以把校內試行摘要複製給校長，並持續增加簡單短練習。",
        "principal_evidence_summary": evidence_summary,
    }


def _usergroup_names(target_ids: list[int], groups_by_id: dict[int, UserGroup]) -> list[str]:
    return [groups_by_id[group_id].name for group_id in target_ids if group_id in groups_by_id]


def _normalize_assignment_school_fields(assignment: Assignment) -> None:
    assignment.target_usergroup_ids = _normalize_int_list(assignment.target_usergroup_ids)
    assignment.learning_objectives = _clean_learning_objectives(assignment.learning_objectives)
    assignment.auto_grading = True if assignment.auto_grading is None else _coerce_simple_answer_bool(assignment.auto_grading)
    assignment.anti_copy_paste = False if assignment.anti_copy_paste is None else _coerce_simple_answer_bool(assignment.anti_copy_paste)
    assignment.show_correct_answers = (
        True if assignment.show_correct_answers is None else _coerce_simple_answer_bool(assignment.show_correct_answers)
    )
    assignment.allow_retries = True if assignment.allow_retries is None else _coerce_simple_answer_bool(assignment.allow_retries)
    try:
        assignment.max_retries = max(0, int(assignment.max_retries or 0))
    except (TypeError, ValueError):
        assignment.max_retries = 0
    assignment.teacher_review_required = _coerce_simple_answer_bool(assignment.teacher_review_required)
    if assignment.score_policy not in {"latest", "highest"}:
        assignment.score_policy = "highest"
    if assignment.teacher_review_status not in {"not_required", "pending", "confirmed"}:
        assignment.teacher_review_status = "pending" if assignment.teacher_review_required else "not_required"
    if not assignment.teacher_review_required and assignment.teacher_review_status == "pending":
        assignment.teacher_review_status = "not_required"


async def _has_non_auto_gradable_assignment_tasks(
    assignment_id: int,
    db_session: AsyncSession,
    replacing_task_id: int | None = None,
    replacement_type: AssignmentTaskTypeEnum | None = None,
) -> bool:
    tasks = (
        await db_session.execute(
            select(AssignmentTask).where(AssignmentTask.assignment_id == assignment_id)
        )
    ).scalars().all()
    for task in tasks:
        task_type = (
            replacement_type
            if replacing_task_id is not None and task.id == replacing_task_id and replacement_type is not None
            else task.assignment_type
        )
        if task_type not in AUTO_GRADABLE_TASK_TYPES:
            return True
    return False


def _simple_task_publish_error_for_values(
    title: str | None,
    assignment_type: AssignmentTaskTypeEnum,
    contents_value,
    description: str | None = None,
    hint: str | None = None,
) -> str | None:
    contents = contents_value or {}
    title = title or "未命名題目"
    if (
        assignment_type not in SIMPLE_PILOT_AUTO_GRADABLE_TASK_TYPES
        and assignment_type != AssignmentTaskTypeEnum.ESSAY
    ):
        task_type_labels = {
            AssignmentTaskTypeEnum.FILE_SUBMISSION: "檔案提交",
            AssignmentTaskTypeEnum.CODE: "程式題",
            AssignmentTaskTypeEnum.NUMBER_ANSWER: "數字題",
            AssignmentTaskTypeEnum.ESSAY: "作文題",
            AssignmentTaskTypeEnum.OTHER: "其他題型",
        }
        task_type_label = task_type_labels.get(assignment_type, str(assignment_type))
        return (
            f"{title} 是{task_type_label}。"
            "校內試行發布主要支援選擇題、填空題、短問答和作文題，"
            "這樣學生提交後可以快速批改，老師也更容易查看成績。"
        )
    task_text = " ".join(
        str(value or "")
        for value in (
            title,
            description,
            hint,
            json.dumps(contents, ensure_ascii=False) if isinstance(contents, dict) else contents,
        )
    )
    if (
        "AI 備用題" in task_text
        or "AI 備用" in task_text
        or "請老師檢查後再發布" in task_text
        or "這份練習的主題是" in task_text
        or "這份練習主要學習哪個主題" in task_text
        or "這份練習主要圍繞哪個主題" in task_text
    ):
        return f"{title} 是 AI 備用題，請老師先修改成正式題目後再發布。"
    if not isinstance(contents, dict):
        return f"{title} 的題目內容格式不完整。"

    if assignment_type == AssignmentTaskTypeEnum.QUIZ:
        questions = contents.get("questions") or []
        if not isinstance(questions, list) or not questions:
            return f"{title} 尚未設定選擇題題目和答案。"
        for question in questions:
            if not isinstance(question, dict):
                return f"{title} 的選擇題內容格式不完整。"
            if not _submission_has_text(question.get("questionText") or question.get("question")):
                return f"{title} 尚未設定選擇題題目。"
            options = question.get("options") or []
            if not isinstance(options, list) or len(options) < 2:
                return f"{title} 每題至少需要 2 個選項。"
            if any(
                not isinstance(option, dict)
                or not _submission_has_text(_simple_quiz_option_text(option))
                for option in options
            ):
                return f"{title} 每個選項都需要文字。"
            correct_count = sum(
                1
                for option in options
                if isinstance(option, dict)
                and _coerce_simple_answer_bool(option.get("assigned_right_answer"))
                and _submission_has_text(_simple_quiz_option_text(option))
            )
            if correct_count != 1:
                return f"{title} 每題需要剛好 1 個正確答案。"
        return None

    if assignment_type == AssignmentTaskTypeEnum.FORM:
        questions = contents.get("questions") or []
        if not isinstance(questions, list) or not questions:
            return f"{title} 尚未設定填空題題目和答案。"
        for question in questions:
            if not isinstance(question, dict):
                return f"{title} 的填空題內容格式不完整。"
            if not _submission_has_text(question.get("questionText") or question.get("question")):
                return f"{title} 尚未設定填空題題目。"
            blanks = question.get("blanks") or []
            if not isinstance(blanks, list) or not blanks:
                return f"{title} 至少需要 1 個填空。"
            for blank in blanks:
                if not isinstance(blank, dict):
                    return f"{title} 每個填空都需要正確答案。"
                answer = _simple_form_blank_answer(blank)
                if not _submission_has_text(answer):
                    return f"{title} 每個填空都需要正確答案。"
                answer_text = str(answer).strip()
                if (
                    len(answer_text) > SIMPLE_PILOT_MAX_FILL_BLANK_ANSWER_LENGTH
                    or any(marker in answer_text for marker in SIMPLE_PILOT_AUTO_ANSWER_EXPLANATION_MARKERS)
                ):
                    return f"{title} 的填空答案太長，請改成一個詞語、數字或很短的標準答案。"
        return None

    if assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER:
        answers = _simple_short_answer_keys(contents)
        prompt = contents.get("prompt")
        if not _submission_has_text(prompt):
            return f"{title} 尚未設定短問答題目。"
        if not isinstance(answers, list) or not any(_submission_has_text(answer) for answer in answers):
            return f"{title} 尚未設定短問答可接受答案。"
        prompt_text = str(prompt or "")
        if any(marker in prompt_text for marker in SIMPLE_PILOT_SHORT_ANSWER_SUBJECTIVE_MARKERS):
            return (
                f"{title} 像主觀問答題。校內簡單模式請改成可用關鍵詞、名詞或一句短答案"
                "直接自動批改的題目。"
            )
        clean_answers = [str(answer).strip() for answer in answers if _submission_has_text(answer)]
        if any(
            len(answer) > SIMPLE_PILOT_MAX_SHORT_ANSWER_LENGTH
            or any(marker in answer for marker in SIMPLE_PILOT_AUTO_ANSWER_EXPLANATION_MARKERS)
            for answer in clean_answers
        ):
            return f"{title} 的短問答答案太長，請改成關鍵詞、名詞或一句短答案。"
        match_mode = contents.get("match_mode") or "case_insensitive"
        if match_mode not in SIMPLE_PILOT_SHORT_ANSWER_MATCH_MODES:
            return f"{title} 的短問答批改方式太容易誤判，請使用精確或忽略大小寫。"
        return None

    if assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER:
        if not _submission_has_text(contents.get("prompt")):
            return f"{title} 尚未設定數字題題目。"
        try:
            float(contents.get("correct_value"))
            float(contents.get("tolerance", 0))
        except (TypeError, ValueError):
            return f"{title} 尚未設定可自動批改的數字答案。"
        return None

    if assignment_type == AssignmentTaskTypeEnum.ESSAY:
        essay_prompt = (
            contents.get("prompt")
            or contents.get("question")
            or contents.get("title")
            or description
            or title
        )
        if not _submission_has_text(essay_prompt):
            return f"{title} 尚未設定作文題目。"
        return None

    return None


def _simple_task_publish_error(task: AssignmentTask) -> str | None:
    return _simple_task_publish_error_for_values(
        task.title,
        task.assignment_type,
        task.contents,
        task.description,
        task.hint,
    )


def _is_simple_pilot_auto_gradable_task_ready(task: AssignmentTask) -> bool:
    return (
        task.assignment_type in SIMPLE_PILOT_AUTO_GRADABLE_TASK_TYPES
        and _simple_task_publish_error(task) is None
    )


def _assignment_has_valid_target_usergroups(
    assignment: Assignment,
    groups_by_id: dict[int, UserGroup] | None,
    members_by_group_id: dict[int, set[int]] | None = None,
    learners_by_id: dict[int, User] | None = None,
) -> bool:
    target_ids = _normalize_int_list(assignment.target_usergroup_ids)
    if not target_ids:
        return False
    if groups_by_id is not None and not all(group_id in groups_by_id for group_id in target_ids):
        return False
    if members_by_group_id is None or learners_by_id is None:
        return True
    learner_ids = set(learners_by_id.keys())
    return all(
        bool(members_by_group_id.get(group_id, set()).intersection(learner_ids))
        for group_id in target_ids
    )


def _assignment_empty_target_usergroup_ids(
    assignment: Assignment,
    groups_by_id: dict[int, UserGroup],
    members_by_group_id: dict[int, set[int]],
    learners_by_id: dict[int, User],
) -> list[int]:
    learner_ids = set(learners_by_id.keys())
    return [
        group_id
        for group_id in _normalize_int_list(assignment.target_usergroup_ids)
        if group_id in groups_by_id
        and not members_by_group_id.get(group_id, set()).intersection(learner_ids)
    ]


def _assignment_has_valid_due_date(assignment: Assignment) -> bool:
    return _parse_date(assignment.due_date) is not None


def _is_simple_pilot_assignment_learning_ready(
    assignment: Assignment,
    assignment_tasks: list[AssignmentTask],
    groups_by_id: dict[int, UserGroup] | None = None,
    members_by_group_id: dict[int, set[int]] | None = None,
    learners_by_id: dict[int, User] | None = None,
    course_published: bool = True,
    activity_published: bool = True,
) -> bool:
    return (
        bool(assignment.published)
        and bool(course_published)
        and bool(activity_published)
        and _assignment_has_valid_due_date(assignment)
        and _assignment_has_valid_target_usergroups(
            assignment,
            groups_by_id,
            members_by_group_id,
            learners_by_id,
        )
        and _coerce_simple_answer_bool(assignment.auto_grading)
        and _coerce_simple_answer_bool(assignment.allow_retries)
        and _coerce_simple_answer_bool(assignment.show_correct_answers)
        and (assignment.score_policy or "highest") == "highest"
        and bool(assignment_tasks)
        and all(_is_simple_pilot_auto_gradable_task_ready(task) for task in assignment_tasks)
    )


def _force_teacher_review_for_manual_tasks(assignment: Assignment) -> None:
    assignment.auto_grading = False
    assignment.teacher_review_required = True
    assignment.teacher_review_status = "pending"
    assignment.update_date = str(datetime.now())


def _assignment_update_field_was_provided(assignment_object: AssignmentUpdate, field: str) -> bool:
    fields_set = getattr(
        assignment_object,
        "model_fields_set",
        getattr(assignment_object, "__fields_set__", set()),
    )
    return field in fields_set


def _apply_simple_publish_learning_defaults(
    assignment_object: AssignmentUpdate,
    assignment_tasks: list[AssignmentTask],
) -> None:
    if not assignment_tasks or not all(_is_simple_pilot_auto_gradable_task_ready(task) for task in assignment_tasks):
        return

    defaults = {
        "auto_grading": True,
        "allow_retries": True,
        "show_correct_answers": True,
        "score_policy": "highest",
        "teacher_review_required": False,
        "teacher_review_status": "not_required",
    }
    for field, value in defaults.items():
        setattr(assignment_object, field, value)


async def _assignment_has_locked_submissions(
    assignment_id: int,
    db_session: AsyncSession,
) -> bool:
    submission = (
        await db_session.execute(
            select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.assignment_id == assignment_id,
                AssignmentUserSubmission.submission_status.in_([
                    AssignmentUserSubmissionStatus.SUBMITTED,
                    AssignmentUserSubmissionStatus.LATE,
                    AssignmentUserSubmissionStatus.GRADED,
                ]),
            )
        )
    ).scalars().first()
    return submission is not None


def _task_update_changes_grading_structure(
    assignment_task_object: AssignmentTaskUpdate,
) -> bool:
    return any(
        value is not None
        for value in (
            assignment_task_object.assignment_type,
            assignment_task_object.contents,
            assignment_task_object.max_grade_value,
        )
    )


def _enum_or_text_value(value) -> str:
    return getattr(value, "value", value) or ""


def _assignment_update_changes_grading_policy(
    assignment: Assignment,
    assignment_object: AssignmentUpdate,
) -> bool:
    if assignment_object.grading_type is not None:
        if _enum_or_text_value(assignment_object.grading_type) != _enum_or_text_value(assignment.grading_type):
            return True
    if assignment_object.score_policy is not None:
        if (assignment_object.score_policy or "highest") != (assignment.score_policy or "highest"):
            return True
    return False


def _assignment_update_changes_target_usergroups(
    assignment: Assignment,
    assignment_object: AssignmentUpdate,
) -> bool:
    if assignment_object.target_usergroup_ids is None:
        return False
    return (
        _normalize_int_list(assignment_object.target_usergroup_ids)
        != _normalize_int_list(assignment.target_usergroup_ids)
    )


def _assignment_update_changes_due_date(
    assignment: Assignment,
    assignment_object: AssignmentUpdate,
) -> bool:
    if assignment_object.due_date is None:
        return False
    current_due_date = _parse_date(assignment.due_date)
    next_due_date = _parse_date(assignment_object.due_date)
    if current_due_date is not None or next_due_date is not None:
        return current_due_date != next_due_date
    return str(assignment_object.due_date or "").strip() != str(assignment.due_date or "").strip()


def _assignment_publish_or_due_date_update_needs_due_date_guard(
    assignment: Assignment,
    assignment_object: AssignmentUpdate,
) -> bool:
    if assignment_object.published is True:
        return True
    if assignment_object.due_date is None:
        return False
    if assignment_object.published is False:
        return False
    return bool(assignment.published)


async def _validate_assignment_publish_targets(
    assignment: Assignment,
    assignment_object: AssignmentUpdate,
    db_session: AsyncSession,
) -> None:
    effective_published = (
        assignment_object.published
        if assignment_object.published is not None
        else assignment.published
    )
    if not effective_published:
        return

    target_usergroup_ids = _normalize_int_list(
        assignment_object.target_usergroup_ids
        if assignment_object.target_usergroup_ids is not None
        else assignment.target_usergroup_ids
    )
    learners_by_id = await _read_org_learners(assignment.org_id, db_session)

    if not target_usergroup_ids:
        existing_group = (
            await db_session.execute(
                select(UserGroup.id).where(UserGroup.org_id == assignment.org_id).limit(1)
            )
        ).first()
        if existing_group:
            raise HTTPException(
                status_code=400,
                detail="發布作業前請先指定班級/群組，避免誤發給全部學生。",
            )
        if learners_by_id:
            return
        raise HTTPException(
            status_code=400,
            detail="發布作業前請先新增學生帳號，否則沒有學生會收到這份作業。",
        )

    existing_ids = set(
        (
            await db_session.execute(
                select(UserGroup.id).where(
                    UserGroup.org_id == assignment.org_id,
                    UserGroup.id.in_(target_usergroup_ids),
                )
            )
        ).scalars().all()
    )
    missing_ids = [group_id for group_id in target_usergroup_ids if group_id not in existing_ids]
    if missing_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "發布作業前請確認班級/群組設定，以下班級不存在或不屬於本校："
                + "、".join(str(group_id) for group_id in missing_ids)
                + "。"
            ),
        )

    if not learners_by_id:
        raise HTTPException(
            status_code=400,
            detail="發布作業前請先新增學生帳號，否則沒有學生會收到這份作業。",
        )

    memberships = (
        await db_session.execute(
            select(UserGroupUser).where(
                UserGroupUser.org_id == assignment.org_id,
                UserGroupUser.usergroup_id.in_(target_usergroup_ids),
            )
        )
    ).scalars().all()
    target_learner_ids = {
        int(membership.user_id)
        for membership in memberships
        if int(membership.user_id) in learners_by_id
    }
    if not target_learner_ids:
        raise HTTPException(
            status_code=400,
            detail="發布作業前請先把學生加入所選班級/群組，否則沒有學生會收到這份作業。",
        )


async def _validate_student_can_submit_targeted_assignment(
    assignment: Assignment,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> None:
    target_usergroup_ids = _normalize_int_list(assignment.target_usergroup_ids)

    learners_by_id = await _read_org_learners(assignment.org_id, db_session)
    current_user_id = int(current_user.id or 0)
    if current_user_id not in learners_by_id:
        return

    await _validate_assignment_course_activity_visible_to_students(
        assignment,
        db_session,
    )

    if not target_usergroup_ids:
        raise HTTPException(
            status_code=403,
            detail="這份作業尚未設定發布班級/群組，暫時不能查看或提交。請聯絡老師確認。",
        )

    membership = (
        await db_session.execute(
            select(UserGroupUser.id).where(
                UserGroupUser.org_id == assignment.org_id,
                UserGroupUser.user_id == current_user_id,
                UserGroupUser.usergroup_id.in_(target_usergroup_ids),
            ).limit(1)
        )
    ).first()
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="這份作業只發布給指定班級/群組，你目前不在發布名單內。請聯絡老師確認班級。",
        )


async def _validate_assignment_course_activity_visible_to_students(
    assignment: Assignment,
    db_session: AsyncSession,
) -> None:
    visibility_row = (
        await db_session.execute(
            select(Course.published, Activity.published)
            .join(Activity, Activity.course_id == Course.id)  # type: ignore
            .where(
                Course.id == assignment.course_id,
                Activity.id == assignment.activity_id,
            )
            .limit(1)
        )
    ).first()
    if not visibility_row or not (bool(visibility_row[0]) and bool(visibility_row[1])):
        raise HTTPException(
            status_code=403,
            detail="這份作業所在的課程或活動尚未發布，學生暫時不能查看或提交。",
        )


async def _validate_student_can_read_assignment(
    assignment: Assignment,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
) -> None:
    if isinstance(current_user, AnonymousUser):
        if not assignment.published:
            raise HTTPException(
                status_code=403,
                detail="這份作業尚未發布，暫時不能查看。",
            )
        await _validate_assignment_course_activity_visible_to_students(
            assignment,
            db_session,
        )
        target_usergroup_ids = _normalize_int_list(assignment.target_usergroup_ids)
        if not target_usergroup_ids:
            raise HTTPException(
                status_code=403,
                detail="這份作業尚未設定發布班級/群組，暫時不能查看。",
            )
        if target_usergroup_ids:
            raise HTTPException(
                status_code=403,
                detail="這份作業只發布給指定班級/群組，請登入學生帳號後再查看。",
            )
        return

    learners_by_id = await _read_org_learners(assignment.org_id, db_session)
    if int(current_user.id or 0) not in learners_by_id:
        return

    if not assignment.published:
        raise HTTPException(
            status_code=403,
            detail="這份作業尚未發布，暫時不能查看。",
        )
    await _validate_assignment_course_activity_visible_to_students(
        assignment,
        db_session,
    )
    await _validate_student_can_submit_targeted_assignment(
        assignment,
        current_user,
        db_session,
    )


async def _build_assignment_gradebook_payload(
    org_id: int,
    db_session: AsyncSession,
    course_id: int | None = None,
    usergroup_id: int | None = None,
    include_self_tests: bool = False,
) -> dict:
    await _ensure_org_exists(org_id, db_session)

    assignment_statement = (
        select(Assignment, Course.name, Course.published, Activity.published)
        .join(Course, Course.id == Assignment.course_id)  # type: ignore
        .join(Activity, Activity.id == Assignment.activity_id)  # type: ignore
        .where(Assignment.org_id == org_id, Assignment.published == True)
        .order_by(Assignment.due_date.asc(), Assignment.id.asc())
    )
    if course_id is not None:
        assignment_statement = assignment_statement.where(Assignment.course_id == course_id)
    assignment_rows = (await db_session.execute(assignment_statement)).all()
    assignments = [row[0] for row in assignment_rows]
    course_names_by_assignment_id = {
        int(row[0].id): row[1] for row in assignment_rows if row[0].id is not None
    }
    published_context_by_assignment_id = {
        int(row[0].id): {
            "course_published": bool(row[2]),
            "activity_published": bool(row[3]),
        }
        for row in assignment_rows
        if row[0].id is not None
    }

    learners_by_id = await _read_org_learners(org_id, db_session)
    target_usergroup_ids: set[int] = set()
    if usergroup_id is not None:
        target_usergroup_ids.add(usergroup_id)
    for assignment in assignments:
        target_usergroup_ids.update(_normalize_int_list(assignment.target_usergroup_ids))
    groups_by_id, members_by_group_id = await _read_usergroup_context(
        org_id, target_usergroup_ids, db_session
    )

    assignment_ids = [int(assignment.id) for assignment in assignments if assignment.id is not None]
    tasks_by_assignment_id: dict[int, list[AssignmentTask]] = defaultdict(list)
    submissions_by_assignment_user: dict[tuple[int, int], AssignmentUserSubmission] = {}
    remediation_by_assignment_user_submission: dict[
        tuple[int, int, int],
        AssignmentRemediationPractice,
    ] = {}

    if assignment_ids:
        tasks = (
            await db_session.execute(
                select(AssignmentTask).where(AssignmentTask.assignment_id.in_(assignment_ids))
            )
        ).scalars().all()
        for task in tasks:
            tasks_by_assignment_id[int(task.assignment_id)].append(task)

        submissions = (
            await db_session.execute(
                select(AssignmentUserSubmission).where(
                    AssignmentUserSubmission.assignment_id.in_(assignment_ids)
                )
            )
        ).scalars().all()
        for submission in submissions:
            key = (int(submission.assignment_id), int(submission.user_id))
            submissions_by_assignment_user[key] = _latest_assignment_submission(
                submissions_by_assignment_user.get(key),
                submission,
            )

        remediation_practices = (
            await db_session.execute(
                select(AssignmentRemediationPractice).where(
                    AssignmentRemediationPractice.assignment_id.in_(assignment_ids)
                )
            )
        ).scalars().all()
        for practice in remediation_practices:
            key = (
                int(practice.assignment_id),
                int(practice.user_id),
                int(practice.assignment_submission_id),
            )
            remediation_by_assignment_user_submission[key] = _latest_remediation_practice(
                remediation_by_assignment_user_submission.get(key),
                practice,
            )

    today = datetime.now().date()
    rows: list[dict] = []
    published_assignments = 0
    simple_auto_graded_assignments = 0
    published_needing_setup = 0
    published_without_targets = 0
    published_empty_target_usergroups = 0
    published_hidden_from_students = 0

    for assignment in assignments:
        assignment_id = int(assignment.id or 0)
        visibility_context = published_context_by_assignment_id.get(assignment_id, {})
        visible_to_students = (
            visibility_context.get("course_published", True)
            and visibility_context.get("activity_published", True)
        )
        valid_target_usergroups = _assignment_has_valid_target_usergroups(
            assignment,
            groups_by_id,
            members_by_group_id,
            learners_by_id,
        )
        if not visible_to_students:
            published_hidden_from_students += 1
        if not valid_target_usergroups:
            published_without_targets += 1
        if _assignment_empty_target_usergroup_ids(
            assignment,
            groups_by_id,
            members_by_group_id,
            learners_by_id,
        ):
            published_empty_target_usergroups += 1
        target_ids = _assignment_targets_for_filter(assignment, usergroup_id)
        if target_ids is None:
            continue
        assignment_tasks = tasks_by_assignment_id.get(assignment_id, [])
        published_assignments += 1
        ready_for_simple_auto_grading = _is_simple_pilot_assignment_learning_ready(
            assignment,
            assignment_tasks,
            groups_by_id,
            members_by_group_id,
            learners_by_id,
            **visibility_context,
        )
        if ready_for_simple_auto_grading:
            simple_auto_graded_assignments += 1
        else:
            published_needing_setup += 1

        expected_user_ids = _expected_user_ids_for_assignment(
            assignment, learners_by_id, members_by_group_id, usergroup_id
        )
        max_grade = sum(int(task.max_grade_value or 0) for task in assignment_tasks)
        assignment_task_types = [
            getattr(task.assignment_type, "value", str(task.assignment_type))
            for task in assignment_tasks
        ]
        has_essay_task = AssignmentTaskTypeEnum.ESSAY.value in assignment_task_types
        all_auto_gradable = bool(assignment_tasks) and all(
            task.assignment_type in AUTO_GRADABLE_TASK_TYPES for task in assignment_tasks
        )
        due_date = _parse_date(assignment.due_date)

        for expected_user_id in expected_user_ids:
            user = learners_by_id[expected_user_id]
            submission = submissions_by_assignment_user.get((assignment_id, expected_user_id))
            status_value = _submission_status_value(submission)
            is_missing = status_value in {
                AssignmentUserSubmissionStatus.PENDING.value,
                AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            }
            submitted_date = (
                _parse_date(submission.creation_date or submission.update_date)
                if submission
                else None
            )
            is_late = (
                status_value == AssignmentUserSubmissionStatus.LATE.value
                or (is_missing and due_date is not None and due_date < today)
                or (
                    submission is not None
                    and not is_missing
                    and due_date is not None
                    and submitted_date is not None
                    and submitted_date > due_date
                )
            )
            grade_display = None
            if submission and status_value == AssignmentUserSubmissionStatus.GRADED.value:
                grade_display = compute_assignment_grade(
                    int(submission.grade or 0),
                    max_grade,
                    assignment.grading_type,
                    submission.overall_feedback,
                )
            review_state = _teacher_review_state(
                assignment,
                submission,
                all_auto_gradable,
            )
            remediation_practice = (
                remediation_by_assignment_user_submission.get(
                    (assignment_id, expected_user_id, int(submission.id))
                )
                if submission and submission.id is not None
                else None
            )
            rows.append(
                {
                    "source_type": "assignment",
                    "assignment_id": assignment_id,
                    "assignment_uuid": assignment.assignment_uuid,
                    "assignment_title": assignment.title,
                    "course_id": assignment.course_id,
                    "course_name": course_names_by_assignment_id.get(assignment_id, ""),
                    "subject": assignment.subject,
                    "education_stage": assignment.education_stage,
                    "grade_level": assignment.grade_level,
                    "school_year": assignment.school_year,
                    "term": assignment.term,
                    "unit": assignment.unit,
                    "learning_objectives": _clean_learning_objectives(assignment.learning_objectives),
                    "target_usergroup_ids": target_ids,
                    "target_usergroups_valid": bool(valid_target_usergroups),
                    "target_usergroups": _usergroup_names(target_ids, groups_by_id),
                    "due_date": assignment.due_date,
                    "published": bool(assignment.published),
                    "course_published": bool(visibility_context.get("course_published", True)),
                    "activity_published": bool(visibility_context.get("activity_published", True)),
                    "visible_to_students": bool(visible_to_students),
                    "auto_grading": _coerce_simple_answer_bool(assignment.auto_grading),
                    "allow_retries": _coerce_simple_answer_bool(assignment.allow_retries),
                    "show_correct_answers": _coerce_simple_answer_bool(assignment.show_correct_answers),
                    "score_policy": assignment.score_policy or "highest",
                    "teacher_review_required": bool(assignment.teacher_review_required),
                    "teacher_review_status": review_state,
                    "all_tasks_auto_gradable": all_auto_gradable,
                    "task_types": assignment_task_types,
                    "has_essay_task": has_essay_task,
                    "task_count": len(assignment_tasks),
                    "student_id": expected_user_id,
                    "student_name": _display_user_name(user),
                    "student_email": user.email,
                    "submission_status": status_value,
                    "submitted_at": submission.update_date if submission else None,
                    "attempt_number": int(submission.attempt_number or 0) if submission else 0,
                    "grade": int(submission.grade or 0) if submission else None,
                    "best_grade": int(submission.best_grade or 0) if submission else None,
                    "best_attempt_number": int(submission.best_attempt_number or 0) if submission else 0,
                    "max_grade": max_grade,
                    "grade_display": grade_display,
                    "overall_feedback": submission.overall_feedback if submission else None,
                    "remediation": _remediation_summary_for_submission(
                        submission,
                        max_grade,
                        remediation_practice,
                    ),
                    "late": is_late,
                    "counts_for_grade": bool(visible_to_students and valid_target_usergroups),
                }
            )

    if include_self_tests:
        allowed_user_ids = set(learners_by_id.keys())
        if usergroup_id is not None:
            allowed_user_ids = members_by_group_id.get(usergroup_id, set()).intersection(allowed_user_ids)
        attempts = (
            await db_session.execute(
                select(SelfTestAttempt)
                .where(
                    SelfTestAttempt.org_id == org_id,
                    SelfTestAttempt.status.in_([
                        SelfTestAttemptStatus.SUBMITTED,
                        SelfTestAttemptStatus.REVIEWED,
                    ]),
                )
                .order_by(SelfTestAttempt.update_date.desc(), SelfTestAttempt.id.desc())
            )
        ).scalars().all()
        for attempt in attempts:
            if int(attempt.user_id) not in allowed_user_ids:
                continue
            user = learners_by_id.get(int(attempt.user_id))
            if not user:
                continue
            score = attempt.teacher_score if attempt.teacher_score is not None else attempt.score
            max_score = int(attempt.max_score or 0)
            percentage = round((float(score or 0) / max_score) * 100, 2) if max_score else 0
            rows.append(
                {
                    "source_type": "self_test",
                    "assignment_id": None,
                    "assignment_uuid": attempt.attempt_uuid,
                    "assignment_title": "自測記錄",
                    "course_id": None,
                    "course_name": "",
                    "subject": "",
                    "education_stage": "",
                    "grade_level": "",
                    "school_year": "",
                    "term": "",
                    "unit": "",
                    "learning_objectives": [],
                    "target_usergroup_ids": [],
                    "target_usergroups_valid": True,
                    "target_usergroups": [],
                    "due_date": None,
                    "published": True,
                    "course_published": True,
                    "activity_published": True,
                    "visible_to_students": True,
                    "auto_grading": True,
                    "allow_retries": False,
                    "show_correct_answers": True,
                    "score_policy": "latest",
                    "teacher_review_required": False,
                    "teacher_review_status": "confirmed" if attempt.status == SelfTestAttemptStatus.REVIEWED else "not_required",
                    "all_tasks_auto_gradable": True,
                    "task_count": int(attempt.question_count or 0),
                    "student_id": int(attempt.user_id),
                    "student_name": _display_user_name(user),
                    "student_email": user.email,
                    "submission_status": getattr(attempt.status, "value", str(attempt.status)),
                    "submitted_at": attempt.submitted_at or attempt.update_date,
                    "attempt_number": 1,
                    "grade": int(score or 0),
                    "best_grade": int(score or 0),
                    "best_attempt_number": 1,
                    "max_grade": max_score,
                    "grade_display": {
                        "grade": int(score or 0),
                        "max_grade": max_score,
                        "percentage": percentage,
                        "display_grade": f"{round(percentage)}%",
                        "percentage_display": f"{percentage:.1f}%",
                        "grading_type": "PERCENTAGE",
                    },
                    "overall_feedback": attempt.teacher_feedback,
                    "late": False,
                    "counts_for_grade": bool(attempt.counts_for_grade),
                }
            )

    for row in rows:
        row["attention_reason"] = _gradebook_attention_reason(row)

    learner_count = len(learners_by_id)
    if usergroup_id is not None:
        learner_count = len(
            members_by_group_id.get(usergroup_id, set()).intersection(learners_by_id.keys())
        )

    actionable_rows = [
        row for row in rows
        if row.get("visible_to_students", True) is not False
        and row.get("target_usergroups_valid", True) is not False
    ]
    submitted_rows = [
        row for row in actionable_rows
        if row["submission_status"] not in {
            AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            AssignmentUserSubmissionStatus.PENDING.value,
        }
    ]
    participation_rows = [
        row for row in actionable_rows
        if row["submission_status"] not in {
            AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            SelfTestAttemptStatus.STARTED.value,
        }
    ]
    graded_rows = [
        row for row in actionable_rows
        if row["submission_status"] == AssignmentUserSubmissionStatus.GRADED.value
        or (
            row["source_type"] == "self_test"
            and row["submission_status"] in {
                SelfTestAttemptStatus.SUBMITTED.value,
                SelfTestAttemptStatus.REVIEWED.value,
            }
        )
    ]
    unsubmitted_rows = [
        row for row in actionable_rows
        if row["submission_status"] == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
    ]
    retry_in_progress_rows = [
        row for row in actionable_rows
        if row["submission_status"] == AssignmentUserSubmissionStatus.PENDING.value
    ]
    late_rows = [row for row in actionable_rows if row["late"]]
    pending_review_rows = [
        row for row in actionable_rows if row["teacher_review_status"] == "pending"
    ]
    essay_pending_review_rows = [
        row for row in pending_review_rows if row.get("has_essay_task") is True
    ]
    attention_rows = [row for row in actionable_rows if _gradebook_row_needs_attention(row)]
    retry_rows = [
        row for row in actionable_rows if int(row.get("attempt_number") or 0) > 1
    ]
    scored_rows = [
        row
        for row in rows
        if row.get("counts_for_grade") is not False
        and _gradebook_row_percentage(row) is not None
    ]
    score_percentages = [
        percentage
        for row in scored_rows
        if (percentage := _gradebook_row_percentage(row)) is not None
    ]
    passing_score_rows = [
        row
        for row in scored_rows
        if (percentage := _gradebook_row_percentage(row)) is not None
        and percentage >= 60
    ]
    needs_practice_rows = [
        row
        for row in scored_rows
        if (percentage := _gradebook_row_percentage(row)) is not None
        and percentage < 60
    ]
    average_score = (
        round(sum(score_percentages) / len(score_percentages), 2)
        if score_percentages
        else None
    )
    submission_rate = (
        round((len(submitted_rows) / len(actionable_rows)) * 100, 2)
        if actionable_rows
        else None
    )
    participation_rate = (
        round((len(participation_rows) / len(actionable_rows)) * 100, 2)
        if actionable_rows
        else None
    )
    ai_status = assignment_ai_status()

    active_student_count = len(
        {row["student_id"] for row in actionable_rows if row.get("student_id")}
    )
    engaged_student_count = len(
        {row["student_id"] for row in participation_rows if row.get("student_id")}
    )
    engagement_rate = (
        round((engaged_student_count / learner_count) * 100, 2)
        if learner_count
        else None
    )
    summary = {
        "total_rows": len(rows),
        "actionable_rows": len(actionable_rows),
        "assignment_rows": len([row for row in rows if row["source_type"] == "assignment"]),
        "visible_assignment_rows": len(
            [
                row for row in rows
                if row["source_type"] == "assignment"
                and row.get("visible_to_students", True) is not False
                and row.get("target_usergroups_valid", True) is not False
            ]
        ),
        "self_test_rows": len([row for row in rows if row["source_type"] == "self_test"]),
        "student_count": active_student_count,
        "active_student_count": active_student_count,
        "engaged_student_count": engaged_student_count,
        "learner_count": learner_count,
        "engagement_rate": engagement_rate,
        "submitted": len(submitted_rows),
        "participation_records": len(participation_rows),
        "graded": len(graded_rows),
        "scored_records": len(scored_rows),
        "passing_score_records": len(passing_score_rows),
        "needs_practice_records": len(needs_practice_rows),
        "unsubmitted": len(unsubmitted_rows),
        "retry_in_progress": len(retry_in_progress_rows),
        "late": len(late_rows),
        "pending_review": len(pending_review_rows),
        "essay_pending_review": len(essay_pending_review_rows),
        "attention_count": len(attention_rows),
        "retry_records": len(retry_rows),
        "average_score": average_score,
        "submission_rate": submission_rate,
        "participation_rate": participation_rate,
        "published": published_assignments,
        "auto_graded_assignments": simple_auto_graded_assignments,
        "simple_auto_graded_assignments": simple_auto_graded_assignments,
        "simple_pilot_ready_assignments": simple_auto_graded_assignments,
        "published_needing_setup": published_needing_setup,
        "published_without_targets": published_without_targets,
        "published_empty_target_usergroups": published_empty_target_usergroups,
        "published_hidden_from_students": published_hidden_from_students,
        "ai_assignment_ready": ai_status["ready"],
        "ai_assignment_provider": ai_status["provider"],
        "ai_assignment_model": ai_status["model"],
        "ai_assignment_message": ai_status["message"],
    }
    summary.update(_build_simple_pilot_status_summary(summary))

    return {
        "summary": summary,
        "rows": rows,
        "filters": {
            "org_id": org_id,
            "course_id": course_id,
            "usergroup_id": usergroup_id,
            "include_self_tests": include_self_tests,
        },
    }


def _normalize_school_operations_date_range(
    start_date: date | None,
    end_date: date | None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    macau_today = today or datetime.now(ZoneInfo(SCHOOL_OPERATIONS_TIMEZONE)).date()
    default_start = macau_today - timedelta(days=macau_today.weekday())
    normalized_start = start_date or default_start
    normalized_end = end_date or (normalized_start + timedelta(days=6))
    if normalized_start > normalized_end:
        raise HTTPException(status_code=400, detail="開始日期不可晚於結束日期。")
    if (normalized_end - normalized_start).days + 1 > SCHOOL_OPERATIONS_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"每次最多可查看 {SCHOOL_OPERATIONS_MAX_RANGE_DAYS} 日的營運摘要。",
        )
    return normalized_start, normalized_end


def _school_operations_text_filter(value: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", str(value or "").strip())
    return normalized[:120] or None


def _school_operations_row_matches_text(
    row: dict,
    field: str,
    expected: str | None,
) -> bool:
    if expected is None:
        return True
    return str(row.get(field) or "").strip().casefold() == expected.casefold()


def _school_operations_row_dates(row: dict) -> list[date]:
    if row.get("source_type") == "self_test":
        submitted_at = _parse_date(row.get("submitted_at"))
        return [submitted_at] if submitted_at is not None else []

    dates: list[date] = []
    submitted_at = _parse_date(row.get("submitted_at"))
    due_date = _parse_date(row.get("due_date"))
    if submitted_at is not None:
        dates.append(submitted_at)
    if due_date is not None and due_date not in dates:
        dates.append(due_date)
    return dates


def _school_operations_row_in_range(
    row: dict,
    start_date: date,
    end_date: date,
) -> bool:
    return any(start_date <= record_date <= end_date for record_date in _school_operations_row_dates(row))


def _school_operations_available_filters(rows: list[dict]) -> dict:
    def text_values(field: str) -> list[str]:
        return sorted(
            {
                str(row.get(field) or "").strip()
                for row in rows
                if str(row.get(field) or "").strip()
            },
            key=str.casefold,
        )

    courses: dict[int, str] = {}
    for row in rows:
        course_id = row.get("course_id")
        course_name = str(row.get("course_name") or "").strip()
        if course_id and course_name:
            courses[int(course_id)] = course_name

    return {
        "subjects": text_values("subject"),
        "education_stages": text_values("education_stage"),
        "grade_levels": text_values("grade_level"),
        "school_years": text_values("school_year"),
        "terms": text_values("term"),
        "courses": [
            {"id": course_id, "name": courses[course_id]}
            for course_id in sorted(courses, key=lambda item: courses[item].casefold())
        ],
    }


def _school_operations_metrics(rows: list[dict], learner_count: int) -> tuple[dict, dict]:
    actionable_rows = [
        row
        for row in rows
        if row.get("visible_to_students", True) is not False
        and row.get("target_usergroups_valid", True) is not False
    ]
    participation_rows = [
        row
        for row in actionable_rows
        if row.get("submission_status")
        not in {
            AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            AssignmentUserSubmissionStatus.PENDING.value,
            SelfTestAttemptStatus.STARTED.value,
        }
    ]
    submitted_rows = [
        row
        for row in actionable_rows
        if row.get("submission_status")
        not in {
            AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            AssignmentUserSubmissionStatus.PENDING.value,
        }
    ]
    graded_rows = [
        row
        for row in actionable_rows
        if row.get("submission_status") == AssignmentUserSubmissionStatus.GRADED.value
        or (
            row.get("source_type") == "self_test"
            and row.get("submission_status")
            in {
                SelfTestAttemptStatus.SUBMITTED.value,
                SelfTestAttemptStatus.REVIEWED.value,
            }
        )
    ]
    scored_rows = [
        row
        for row in actionable_rows
        if row.get("counts_for_grade") is not False
        and _gradebook_row_percentage(row) is not None
    ]
    percentages = [
        percentage
        for row in scored_rows
        if (percentage := _gradebook_row_percentage(row)) is not None
    ]
    low_score_rows = [
        row
        for row in scored_rows
        if (percentage := _gradebook_row_percentage(row)) is not None and percentage < 60
    ]
    engaged_student_count = len(
        {int(row.get("student_id")) for row in participation_rows if row.get("student_id")}
    )
    auto_graded_records = len(
        [
            row
            for row in graded_rows
            if bool(row.get("auto_grading")) and bool(row.get("all_tasks_auto_gradable"))
        ]
    )
    cohort_is_small = 0 < learner_count < SCHOOL_OPERATIONS_MINIMUM_COHORT_SIZE
    protected_metrics = {
        "engagement_rate",
        "participation_rate",
        "submission_rate",
        "average_score",
        "passing_score_records",
        "low_score_records",
    }

    def protected(value: object, name: str):
        return None if cohort_is_small and name in protected_metrics else value

    metrics = {
        "learner_count": learner_count,
        "engaged_student_count": engaged_student_count,
        "engagement_rate": protected(
            round((engaged_student_count / learner_count) * 100, 2) if learner_count else None,
            "engagement_rate",
        ),
        "record_count": len(participation_rows),
        "expected_records": len(actionable_rows),
        "participation_records": len(participation_rows),
        "participation_rate": protected(
            round((len(participation_rows) / len(actionable_rows)) * 100, 2)
            if actionable_rows
            else None,
            "participation_rate",
        ),
        "submitted": len(submitted_rows),
        "submission_rate": protected(
            round((len(submitted_rows) / len(actionable_rows)) * 100, 2)
            if actionable_rows
            else None,
            "submission_rate",
        ),
        "graded": len(graded_rows),
        "pending_review": len(
            [row for row in actionable_rows if row.get("teacher_review_status") == "pending"]
        ),
        "unsubmitted": len(
            [
                row
                for row in actionable_rows
                if row.get("submission_status") == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
            ]
        ),
        "late": len([row for row in actionable_rows if row.get("late") is True]),
        "scored_records": len(scored_rows),
        "average_score": protected(
            round(sum(percentages) / len(percentages), 2) if percentages else None,
            "average_score",
        ),
        "passing_score_records": protected(
            len([percentage for percentage in percentages if percentage >= 60]),
            "passing_score_records",
        ),
        "low_score_records": protected(len(low_score_rows), "low_score_records"),
        "auto_graded_records": auto_graded_records,
    }
    privacy = {
        "aggregate_only": True,
        "minimum_cohort_size": SCHOOL_OPERATIONS_MINIMUM_COHORT_SIZE,
        "cohort_size": learner_count,
        "suppressed": cohort_is_small,
        "suppressed_metrics": sorted(protected_metrics) if cohort_is_small else [],
        "excluded_data_categories": [
            "學生身分資料",
            "學生答案",
            "上傳檔名",
            "逐份評語",
            "AI 請求內容",
        ],
    }
    return metrics, privacy


def _school_operations_summary_text(
    metrics: dict,
    privacy: dict,
    start_date: date,
    end_date: date,
) -> dict:
    learner_count = int(metrics.get("learner_count") or 0)
    record_count = int(metrics.get("record_count") or 0)
    engaged_student_count = int(metrics.get("engaged_student_count") or 0)
    pending_review = int(metrics.get("pending_review") or 0)
    unsubmitted = int(metrics.get("unsubmitted") or 0)
    period_label = f"{start_date.isoformat()} 至 {end_date.isoformat()}"

    if learner_count <= 0:
        return {
            "status": "not_started",
            "status_label": "尚未有學生",
            "headline": "目前範圍尚未有可統計的學生。",
            "principal_brief": f"{period_label} 尚未有可統計學生或學習記錄。",
            "next_step": "先匯入學生並分配班級，再發布第一份簡單作業。",
        }
    if record_count <= 0:
        return {
            "status": "no_activity",
            "status_label": "本期尚未使用",
            "headline": f"目前有 {learner_count} 名學生，但指定期間尚未產生學習記錄。",
            "principal_brief": f"{period_label} 有 {learner_count} 名學生，暫未產生作業或自測記錄。",
            "next_step": "發布一份 3 題簡單作業，讓學生完成第一次提交。",
        }

    if privacy.get("suppressed"):
        principal_brief = (
            f"{period_label} 有 {learner_count} 名學生及 {record_count} 條學習記錄；"
            f"系統已完成 {int(metrics.get('auto_graded_records') or 0)} 條自動批改。"
            "因群組人數較少，成績和比率已隱藏。"
        )
    else:
        submission_rate = _format_summary_percent(metrics.get("submission_rate"))
        average_score = _format_summary_score(metrics.get("average_score"))
        principal_brief = (
            f"{period_label} 有 {engaged_student_count}/{learner_count} 名學生產生 "
            f"{record_count} 條學習記錄，提交率 {submission_rate}，平均分 {average_score}；"
            f"系統完成 {int(metrics.get('auto_graded_records') or 0)} 條自動批改。"
        )

    needs_follow_up = pending_review > 0 or unsubmitted > 0
    return {
        "status": "active_needs_followup" if needs_follow_up else "active",
        "status_label": "本期已有使用，需跟進" if needs_follow_up else "本期已有使用",
        "headline": f"已有 {engaged_student_count} 名學生產生 {record_count} 條學習記錄。",
        "principal_brief": principal_brief,
        "next_step": (
            f"先處理 {pending_review} 條待覆核和 {unsubmitted} 條未提交記錄。"
            if needs_follow_up
            else "目前核心作業閉環運作正常，可持續發布短練習。"
        ),
    }


async def _validate_school_operations_resource_filters(
    org_id: int,
    db_session: AsyncSession,
    *,
    course_id: int | None,
    usergroup_id: int | None,
) -> None:
    if course_id is not None:
        course = (
            await db_session.execute(
                select(Course.id).where(Course.id == course_id, Course.org_id == org_id)
            )
        ).first()
        if not course:
            raise HTTPException(status_code=404, detail="找不到指定課程。")
    if usergroup_id is not None:
        usergroup = (
            await db_session.execute(
                select(UserGroup.id).where(UserGroup.id == usergroup_id, UserGroup.org_id == org_id)
            )
        ).first()
        if not usergroup:
            raise HTTPException(status_code=404, detail="找不到指定班級/群組。")


async def read_school_operations_summary(
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    course_id: int | None = None,
    usergroup_id: int | None = None,
    subject: str | None = None,
    education_stage: str | None = None,
    grade_level: str | None = None,
    school_year: str | None = None,
    term: str | None = None,
    include_self_tests: bool = True,
    today: date | None = None,
) -> dict:
    _block_api_tokens(current_user)
    await require_org_role_permission(
        int(current_user.id),
        org_id,
        db_session,
        "dashboard",
        "action_access",
    )
    await _validate_school_operations_resource_filters(
        org_id,
        db_session,
        course_id=course_id,
        usergroup_id=usergroup_id,
    )
    normalized_start, normalized_end = _normalize_school_operations_date_range(
        start_date,
        end_date,
        today=today,
    )
    normalized_filters = {
        "subject": _school_operations_text_filter(subject),
        "education_stage": _school_operations_text_filter(education_stage),
        "grade_level": _school_operations_text_filter(grade_level),
        "school_year": _school_operations_text_filter(school_year),
        "term": _school_operations_text_filter(term),
    }
    payload = await _build_assignment_gradebook_payload(
        org_id=org_id,
        db_session=db_session,
        course_id=course_id,
        usergroup_id=usergroup_id,
        include_self_tests=include_self_tests,
    )
    base_rows = list(payload.get("rows") or [])
    available_filters = _school_operations_available_filters(base_rows)
    metadata_rows = [
        row
        for row in base_rows
        if all(
            _school_operations_row_matches_text(row, field, expected)
            for field, expected in normalized_filters.items()
        )
    ]
    rows = [
        row
        for row in metadata_rows
        if _school_operations_row_in_range(row, normalized_start, normalized_end)
    ]

    has_structural_filter = bool(course_id or any(normalized_filters.values()))
    learner_count = int((payload.get("summary") or {}).get("learner_count") or 0)
    if has_structural_filter:
        learner_count = len(
            {
                int(row.get("student_id"))
                for row in metadata_rows
                if row.get("student_id")
                and row.get("visible_to_students", True) is not False
                and row.get("target_usergroups_valid", True) is not False
            }
        )

    metrics, privacy = _school_operations_metrics(rows, learner_count)
    workload = {
        "estimated_teacher_minutes_saved": (
            int(metrics.get("auto_graded_records") or 0)
            * SCHOOL_OPERATIONS_MINUTES_PER_AUTO_GRADED_RECORD
        ),
        "minutes_per_auto_graded_record": SCHOOL_OPERATIONS_MINUTES_PER_AUTO_GRADED_RECORD,
        "methodology": (
            "每份完成自動批改的簡單作業或自測估算節省 "
            f"{SCHOOL_OPERATIONS_MINUTES_PER_AUTO_GRADED_RECORD} 分鐘人工批改時間。"
        ),
        "ai_assignment_ready": bool((payload.get("summary") or {}).get("ai_assignment_ready")),
        "ai_usage_tracking": "not_recorded",
        "ai_usage_note": "目前不保存逐次 AI 出題審計，因此不把 AI 出題次數列為實際使用量。",
    }
    scope = {
        "org_id": org_id,
        "start_date": normalized_start.isoformat(),
        "end_date": normalized_end.isoformat(),
        "timezone": SCHOOL_OPERATIONS_TIMEZONE,
        "course_id": course_id,
        "usergroup_id": usergroup_id,
        **normalized_filters,
        "include_self_tests": include_self_tests,
        "learner_count": learner_count,
        "record_count": int(metrics.get("record_count") or 0),
    }
    return {
        "scope": scope,
        "metrics": metrics,
        "display": {
            "engagement_rate": _format_summary_percent(metrics.get("engagement_rate")),
            "participation_rate": _format_summary_percent(metrics.get("participation_rate")),
            "submission_rate": _format_summary_percent(metrics.get("submission_rate")),
            "average_score": _format_summary_score(metrics.get("average_score")),
            "estimated_teacher_time_saved": (
                f"約 {workload['estimated_teacher_minutes_saved']} 分鐘"
            ),
        },
        "workload": workload,
        "privacy": privacy,
        "summary": _school_operations_summary_text(
            metrics,
            privacy,
            normalized_start,
            normalized_end,
        ),
        "available_filters": available_filters,
        "generated_at": datetime.now(ZoneInfo(SCHOOL_OPERATIONS_TIMEZONE)).isoformat(),
    }


def school_operations_summary_to_csv(payload: dict) -> str:
    scope = payload.get("scope") or {}
    metrics = payload.get("metrics") or {}
    workload = payload.get("workload") or {}
    privacy = payload.get("privacy") or {}
    summary = payload.get("summary") or {}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["欄位", "數值"])
    rows = [
        ("摘要範圍", f"{scope.get('start_date') or ''} 至 {scope.get('end_date') or ''}"),
        ("時區", scope.get("timezone") or SCHOOL_OPERATIONS_TIMEZONE),
        ("狀態", summary.get("status_label") or ""),
        ("校長/主任摘要", summary.get("principal_brief") or ""),
        ("學生人數", metrics.get("learner_count") or 0),
        ("有學習記錄學生", metrics.get("engaged_student_count") or 0),
        ("學習記錄", metrics.get("record_count") or 0),
        ("應完成記錄", metrics.get("expected_records") or 0),
        ("提交", metrics.get("submitted") or 0),
        ("提交率", "" if metrics.get("submission_rate") is None else f"{metrics['submission_rate']}%"),
        ("已批改", metrics.get("graded") or 0),
        ("待覆核", metrics.get("pending_review") or 0),
        ("未提交", metrics.get("unsubmitted") or 0),
        ("平均分", "" if metrics.get("average_score") is None else f"{metrics['average_score']}%"),
        ("低分記錄", "" if metrics.get("low_score_records") is None else metrics.get("low_score_records")),
        ("自動批改記錄", metrics.get("auto_graded_records") or 0),
        ("估算節省時間", f"約 {workload.get('estimated_teacher_minutes_saved') or 0} 分鐘"),
        ("估算方法", workload.get("methodology") or ""),
        ("私隱保護", "已隱藏小群組敏感指標" if privacy.get("suppressed") else "聚合資料"),
        ("最低群組人數", privacy.get("minimum_cohort_size") or SCHOOL_OPERATIONS_MINIMUM_COHORT_SIZE),
    ]
    for label, value in rows:
        writer.writerow([label, value])
    return "\ufeff" + output.getvalue()


def school_operations_summary_csv_filename(payload: dict) -> str:
    scope = payload.get("scope") or {}
    parts = [
        "learnhouse",
        "operations-summary",
        f"org-{scope.get('org_id') or 'org'}",
        str(scope.get("start_date") or "start"),
        str(scope.get("end_date") or "end"),
    ]
    if scope.get("course_id") is not None:
        parts.append(f"course-{scope['course_id']}")
    if scope.get("usergroup_id") is not None:
        parts.append(f"group-{scope['usergroup_id']}")
    filename = "-".join(
        part for part in (_safe_ascii_filename_part(part) for part in parts) if part
    )
    return f"{filename or 'learnhouse-operations-summary'}.csv"


def _format_summary_percent(value: object) -> str:
    if value is None:
        return "暫無資料"
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return str(value)


def _format_summary_score(value: object) -> str:
    if value is None:
        return "暫無評分"
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return str(value)


def _safe_summary_text(value: object, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return (text or fallback)[:160]


def _gradebook_summary_student_key(row: dict) -> tuple[int, str]:
    return (
        int(row.get("student_id") or 0),
        str(row.get("student_email") or row.get("student_name") or ""),
    )


def _gradebook_summary_student_label(row: dict) -> str:
    name = _safe_summary_text(row.get("student_name"), "未命名學生")
    email = _safe_summary_text(row.get("student_email"))
    return f"{name}（{email}）" if email else name


def _gradebook_summary_row_priority(row: dict) -> tuple[int, str, str]:
    percentage = _gradebook_row_percentage(row)
    if row.get("teacher_review_status") == "pending":
        priority = 0
    elif row.get("submission_status") == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value:
        priority = 1
    elif percentage is not None and percentage < 60:
        priority = 2
    elif row.get("late"):
        priority = 3
    else:
        priority = 4
    return (
        priority,
        str(row.get("due_date") or "9999-12-31"),
        str(row.get("student_name") or ""),
    )


def _build_gradebook_summary_follow_up_students(rows: list[dict], limit: int = 6) -> list[dict]:
    follow_up_by_student: dict[tuple[int, str], dict] = {}
    for row in sorted(rows, key=_gradebook_summary_row_priority):
        reasons: list[str] = []
        percentage = _gradebook_row_percentage(row)
        if row.get("teacher_review_status") == "pending":
            reasons.append("待老師覆核")
        if row.get("submission_status") == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value:
            reasons.append("未提交")
        if percentage is not None and percentage < 60:
            reasons.append(f"低分 {round(percentage)}%")
        if row.get("late"):
            reasons.append("逾期")
        attention_reason = _safe_summary_text(row.get("attention_reason"))
        if attention_reason and attention_reason not in reasons:
            reasons.append(attention_reason)
        if not reasons:
            continue

        key = _gradebook_summary_student_key(row)
        entry = follow_up_by_student.setdefault(
            key,
            {
                "student_id": row.get("student_id"),
                "student_name": _safe_summary_text(row.get("student_name"), "未命名學生"),
                "student_email": _safe_summary_text(row.get("student_email")),
                "reasons": [],
                "records": [],
            },
        )
        for reason in reasons:
            if reason and reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        if len(entry["records"]) < 3:
            entry["records"].append(
                {
                    "title": _safe_summary_text(row.get("assignment_title"), "未命名作業"),
                    "subject": _safe_summary_text(row.get("subject"), "未設定科目"),
                    "unit": _safe_summary_text(row.get("unit")),
                    "score_percentage": percentage,
                    "status": row.get("submission_status"),
                }
            )

    return list(follow_up_by_student.values())[:limit]


def _build_gradebook_summary_weak_areas(rows: list[dict], limit: int = 5) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        percentage = _gradebook_row_percentage(row)
        is_weak = (
            row.get("teacher_review_status") == "pending"
            or row.get("submission_status") == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
            or (percentage is not None and percentage < 60)
        )
        if not is_weak:
            continue
        label_parts = [
            _safe_summary_text(row.get("subject"), "未設定科目"),
            _safe_summary_text(row.get("grade_level")),
            _safe_summary_text(row.get("unit")),
        ]
        label = " · ".join(part for part in label_parts if part)
        if not label:
            label = _safe_summary_text(row.get("assignment_title"), "未命名作業")
        bucket = buckets.setdefault(
            label,
            {
                "label": label,
                "count": 0,
                "students": set(),
                "sample_assignments": [],
                "task_types": set(),
            },
        )
        bucket["count"] += 1
        if row.get("student_id"):
            bucket["students"].add(row.get("student_id"))
        assignment_title = _safe_summary_text(row.get("assignment_title"))
        if assignment_title and assignment_title not in bucket["sample_assignments"]:
            bucket["sample_assignments"].append(assignment_title)
        for task_type in row.get("task_types") or []:
            bucket["task_types"].add(str(task_type))

    weak_areas: list[dict] = []
    for bucket in sorted(
        buckets.values(),
        key=lambda item: (int(item["count"]), len(item["students"])),
        reverse=True,
    )[:limit]:
        weak_areas.append(
            {
                "label": bucket["label"],
                "count": int(bucket["count"]),
                "student_count": len(bucket["students"]),
                "sample_assignments": bucket["sample_assignments"][:3],
                "task_types": sorted(bucket["task_types"]),
            }
        )
    return weak_areas


def _build_gradebook_summary_actions(summary: dict, weak_areas: list[dict]) -> list[str]:
    actions: list[str] = []
    if _summary_int(summary, "unsubmitted") > 0:
        actions.append("先提醒未提交學生，必要時在課堂上預留 5 分鐘補交。")
    if _summary_int(summary, "pending_review") > 0:
        actions.append("先處理待覆核作文或主觀提交，確認最終分數後再匯出成績。")
    if _summary_int(summary, "needs_practice_records") > 0:
        weak_label = weak_areas[0]["label"] if weak_areas else "低分知識點"
        actions.append(f"針對「{weak_label}」安排 2-3 題短補練，讓學生即時重做。")
    if _summary_int(summary, "published_needing_setup") > 0:
        actions.append("整理已發布但不適合正式展示的作業，確認班級、截止日期和自動批改設定。")
    if not actions:
        actions.append("目前班級狀態穩定，可準備下一個小單元的 3 題簡單練習。")
    if len(actions) < 2:
        actions.append("保留成績表摘要，下一堂課前快速檢查是否有新增未交或低分記錄。")
    return actions[:3]


def _build_gradebook_summary_facts(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    rows = [
        row for row in payload.get("rows", [])
        if row.get("visible_to_students", True) is not False
        and row.get("target_usergroups_valid", True) is not False
    ]
    attention_rows = [row for row in rows if _gradebook_row_needs_attention(row)]
    low_score_rows = [
        row for row in rows
        if (percentage := _gradebook_row_percentage(row)) is not None
        and percentage < 60
    ]
    pending_review_rows = [
        row for row in rows if row.get("teacher_review_status") == "pending"
    ]
    unsubmitted_rows = [
        row for row in rows
        if row.get("submission_status") == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
    ]
    weak_areas = _build_gradebook_summary_weak_areas(rows)
    follow_up_students = _build_gradebook_summary_follow_up_students(attention_rows + low_score_rows)
    suggested_actions = _build_gradebook_summary_actions(summary, weak_areas)
    requires_attention = bool(
        unsubmitted_rows
        or low_score_rows
        or pending_review_rows
        or _summary_int(summary, "published_needing_setup") > 0
    )

    return {
        "scope": {
            "org_id": (payload.get("filters") or {}).get("org_id"),
            "course_id": (payload.get("filters") or {}).get("course_id"),
            "usergroup_id": (payload.get("filters") or {}).get("usergroup_id"),
            "include_self_tests": bool((payload.get("filters") or {}).get("include_self_tests")),
            "learner_count": _summary_int(summary, "learner_count"),
            "record_count": _summary_int(summary, "actionable_rows"),
        },
        "metrics": {
            "submission_rate": summary.get("submission_rate"),
            "participation_rate": summary.get("participation_rate"),
            "average_score": summary.get("average_score"),
            "submitted": _summary_int(summary, "submitted"),
            "unsubmitted": _summary_int(summary, "unsubmitted"),
            "pending_review": _summary_int(summary, "pending_review"),
            "essay_pending_review": _summary_int(summary, "essay_pending_review"),
            "low_score_records": _summary_int(summary, "needs_practice_records"),
            "attention_count": _summary_int(summary, "attention_count"),
            "late": _summary_int(summary, "late"),
            "retry_in_progress": _summary_int(summary, "retry_in_progress"),
            "self_test_rows": _summary_int(summary, "self_test_rows"),
            "published_needing_setup": _summary_int(summary, "published_needing_setup"),
        },
        "display": {
            "submission_rate": _format_summary_percent(summary.get("submission_rate")),
            "participation_rate": _format_summary_percent(summary.get("participation_rate")),
            "average_score": _format_summary_score(summary.get("average_score")),
            "pilot_status": summary.get("pilot_status_label") or "",
            "principal_evidence": summary.get("principal_evidence_summary") or "",
        },
        "follow_up_students": follow_up_students,
        "weak_areas": weak_areas,
        "suggested_actions": suggested_actions,
        "requires_attention": requires_attention,
    }


def _build_gradebook_summary_fallback_narrative(facts: dict) -> dict:
    metrics = facts.get("metrics") or {}
    display = facts.get("display") or {}
    scope = facts.get("scope") or {}
    learner_count = int(scope.get("learner_count") or 0)
    record_count = int(scope.get("record_count") or 0)
    summary_text = (
        f"本班共有 {learner_count} 名學生，已有 {record_count} 條可用學習記錄；"
        f"提交率 {display.get('submission_rate')}，平均分 {display.get('average_score')}。"
    )
    key_points = [
        f"未提交：{int(metrics.get('unsubmitted') or 0)} 條。",
        f"待老師覆核：{int(metrics.get('pending_review') or 0)} 條，其中作文 {int(metrics.get('essay_pending_review') or 0)} 條。",
        f"低分需補強：{int(metrics.get('low_score_records') or 0)} 條。",
    ]
    if facts.get("weak_areas"):
        weak = facts["weak_areas"][0]
        key_points.append(
            f"主要需跟進範圍：{weak['label']}，涉及 {weak['count']} 條記錄。"
        )
    principal_brief = (
        f"本週可匯報：{learner_count} 名學生、{record_count} 條學習記錄，"
        f"提交率 {display.get('submission_rate')}，待跟進 {int(metrics.get('attention_count') or 0)} 條。"
    )
    return {
        "title": "班級學情摘要",
        "summary": summary_text,
        "key_points": key_points[:4],
        "suggested_actions": list(facts.get("suggested_actions") or [])[:3],
        "principal_brief": principal_brief,
    }


def _build_gradebook_summary_ai_prompt(facts: dict) -> str:
    compact_facts = {
        "scope": facts.get("scope"),
        "metrics": facts.get("metrics"),
        "display": facts.get("display"),
        "follow_up_students": [
            {
                "student_name": item.get("student_name"),
                "student_email": item.get("student_email"),
                "reasons": item.get("reasons"),
                "records": item.get("records"),
            }
            for item in (facts.get("follow_up_students") or [])[:6]
        ],
        "weak_areas": (facts.get("weak_areas") or [])[:5],
        "suggested_actions": facts.get("suggested_actions") or [],
    }
    return f"""
你是澳門中小學老師的教務助理。請根據成績表統計，生成一份繁體中文班級學情摘要。

要求：
- 只回傳 JSON，不要 Markdown。
- 語氣專業、簡潔、可直接給老師和主任閱讀。
- 不要編造資料；只能根據 facts。
- 不要暴露系統、模型、API、token 或後端錯誤。
- 不要把 AI 分數當作主觀題最終裁判，只提供教學跟進建議。
- suggested_actions 必須是 2-3 條，短而可執行。

facts:
{json.dumps(compact_facts, ensure_ascii=False)}

回傳格式：
{{
  "title": "班級學情摘要",
  "summary": "一段 80 字內總結",
  "key_points": ["重點 1", "重點 2", "重點 3"],
  "suggested_actions": ["建議 1", "建議 2", "建議 3"],
  "principal_brief": "給主任/校長看的 80 字內短報告"
}}
""".strip()


def _normalize_gradebook_summary_narrative(payload: dict, fallback: dict) -> dict:
    if not isinstance(payload, dict):
        return fallback
    title = _safe_summary_text(payload.get("title"), fallback.get("title", "班級學情摘要"))[:60]
    summary = _safe_summary_text(payload.get("summary"), fallback.get("summary", ""))[:260]
    key_points = _normalize_string_list(payload.get("key_points"), limit=4)
    suggested_actions = _normalize_string_list(payload.get("suggested_actions"), limit=3)
    principal_brief = _safe_summary_text(
        payload.get("principal_brief"),
        fallback.get("principal_brief", ""),
    )[:260]
    return {
        "title": title or fallback.get("title", "班級學情摘要"),
        "summary": summary or fallback.get("summary", ""),
        "key_points": key_points or fallback.get("key_points", []),
        "suggested_actions": suggested_actions or fallback.get("suggested_actions", []),
        "principal_brief": principal_brief or fallback.get("principal_brief", ""),
    }


async def _generate_gradebook_summary_narrative(
    facts: dict,
    fallback: dict,
    *,
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
) -> tuple[dict, bool, str | None]:
    config_error = assignment_ai_configuration_error()
    if config_error:
        return fallback, False, "AI 暫時不可用，已使用系統統計產生摘要。"

    credit_reserved = False
    try:
        acting_user_id = resolve_acting_user_id(current_user)
        enforce_ai_rate_limit(acting_user_id, org_id)
        await reserve_ai_credit(org_id, db_session, amount=1)
        credit_reserved = True
        response = await asyncio.to_thread(
            get_gemini_client().models.generate_content,
            model=assignment_generation_model(),
            contents=[{"role": "user", "parts": [{"text": _build_gradebook_summary_ai_prompt(facts)}]}],
            config={
                "temperature": 0.25,
                "max_output_tokens": 2500,
                "response_mime_type": "application/json",
            },
        )
        payload = _extract_ai_json_object(getattr(response, "text", ""))
        return _normalize_gradebook_summary_narrative(payload, fallback), True, None
    except Exception:
        if credit_reserved:
            try:
                refund_ai_credit(org_id, 1)
            except Exception:
                logger.exception("Failed to refund AI credit after gradebook summary failure")
        logger.exception("AI gradebook summary failed")
        return fallback, False, "AI 暫時不可用，已使用系統統計產生摘要。"


async def read_assignment_gradebook_summary(
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    course_id: int | None = None,
    usergroup_id: int | None = None,
    include_self_tests: bool = False,
) -> dict:
    _block_api_tokens(current_user)
    await require_org_role_permission(
        int(current_user.id),
        org_id,
        db_session,
        "dashboard",
        "action_access",
    )
    payload = await _build_assignment_gradebook_payload(
        org_id=org_id,
        db_session=db_session,
        course_id=course_id,
        usergroup_id=usergroup_id,
        include_self_tests=include_self_tests,
    )
    facts = _build_gradebook_summary_facts(payload)
    fallback_narrative = _build_gradebook_summary_fallback_narrative(facts)
    narrative, ai_used, ai_message = await _generate_gradebook_summary_narrative(
        facts,
        fallback_narrative,
        org_id=org_id,
        current_user=current_user,
        db_session=db_session,
    )
    return {
        "facts": facts,
        "narrative": narrative,
        "ai_used": ai_used,
        "ai_message": ai_message,
        "requires_attention": bool(facts.get("requires_attention")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "filters": payload.get("filters") or {},
    }


async def read_teacher_assignment_workbench(
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
) -> dict:
    _block_api_tokens(current_user)
    await require_org_role_permission(
        int(current_user.id),
        org_id,
        db_session,
        "dashboard",
        "action_access",
    )
    payload = await _build_assignment_gradebook_payload(
        org_id=org_id,
        db_session=db_session,
        include_self_tests=True,
    )

    today = datetime.now().date()
    due_limit = today + timedelta(days=7)
    assignment_rows = (
        await db_session.execute(
            select(Assignment, Course.name, Course.published, Activity.published)
            .join(Course, Course.id == Assignment.course_id)  # type: ignore
            .join(Activity, Activity.id == Assignment.activity_id)  # type: ignore
            .where(Assignment.org_id == org_id)
            .order_by(Assignment.due_date.asc(), Assignment.id.asc())
        )
    ).all()
    assignment_ids = [
        int(assignment.id)
        for assignment, _course_name, _course_published, _activity_published in assignment_rows
        if assignment.id is not None
    ]
    tasks_by_assignment_id: dict[int, list[AssignmentTask]] = defaultdict(list)
    if assignment_ids:
        assignment_tasks = (
            await db_session.execute(
                select(AssignmentTask).where(AssignmentTask.assignment_id.in_(assignment_ids))
            )
        ).scalars().all()
        for task in assignment_tasks:
            tasks_by_assignment_id[int(task.assignment_id)].append(task)
    target_usergroup_ids: set[int] = set()
    for assignment, _course_name, _course_published, _activity_published in assignment_rows:
        target_usergroup_ids.update(_normalize_int_list(assignment.target_usergroup_ids))
    groups_by_id, _members_by_group_id = await _read_usergroup_context(
        org_id,
        target_usergroup_ids,
        db_session,
    )
    workbench_learners_by_id = await _read_org_learners(org_id, db_session)

    due_soon_assignments: list[dict] = []
    drafts = 0
    published = 0
    auto_graded = 0
    published_needing_setup = 0
    published_without_targets = 0
    published_empty_target_usergroups = 0
    published_hidden_from_students = 0
    for assignment, course_name, course_published, activity_published in assignment_rows:
        visible_to_students = bool(course_published) and bool(activity_published)
        valid_target_usergroups = _assignment_has_valid_target_usergroups(
            assignment,
            groups_by_id,
            _members_by_group_id,
            workbench_learners_by_id,
        )
        if assignment.published:
            published += 1
            if not visible_to_students:
                published_hidden_from_students += 1
            if not valid_target_usergroups:
                published_without_targets += 1
            if _assignment_empty_target_usergroup_ids(
                assignment,
                groups_by_id,
                _members_by_group_id,
                workbench_learners_by_id,
            ):
                published_empty_target_usergroups += 1
        else:
            drafts += 1

        assignment_tasks = tasks_by_assignment_id.get(int(assignment.id or 0), [])
        ready_for_simple_auto_grading = _is_simple_pilot_assignment_learning_ready(
            assignment,
            assignment_tasks,
            groups_by_id,
            _members_by_group_id,
            workbench_learners_by_id,
            course_published=bool(course_published),
            activity_published=bool(activity_published),
        )
        if ready_for_simple_auto_grading:
            auto_graded += 1
        elif assignment.published:
            published_needing_setup += 1

        due_date = _parse_date(assignment.due_date)
        if (
            assignment.published
            and visible_to_students
            and valid_target_usergroups
            and due_date
            and today <= due_date <= due_limit
        ):
            due_soon_assignments.append(
                {
                    "assignment_uuid": assignment.assignment_uuid,
                    "title": assignment.title,
                    "course_name": course_name,
                    "subject": assignment.subject,
                    "grade_level": assignment.grade_level,
                    "due_date": assignment.due_date,
                    "target_usergroup_ids": _normalize_int_list(assignment.target_usergroup_ids),
                }
            )

    rows = payload["rows"]
    missing_rows = [
        row for row in rows
        if row["submission_status"] == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value
        and row.get("visible_to_students", True) is not False
        and row.get("target_usergroups_valid", True) is not False
        and bool(row.get("target_usergroup_ids"))
    ]
    ai_status = assignment_ai_status()
    self_test_ready_question_count = await _count_self_test_ready_question_bank_items(
        org_id,
        db_session,
    )
    self_test_min_question_count = SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT
    self_test_bank_ready = self_test_ready_question_count >= self_test_min_question_count
    simple_core_ready = auto_graded > 0
    ai_action_description = (
        "根據科目、年級和學習目標生成選擇、填空、短問答等簡單可自動批改題目。"
        if ai_status["ready"]
        else (
            "核心作業閉環已有校內試行就緒作業；AI 出題可之後再配置，"
            "目前可先用題庫或手動建立選擇、填空、短問答。"
            if simple_core_ready
            else "AI 端點尚未配置完整；如要使用 AI 出題再配置端點，目前可先用題庫或手動建立選擇、填空、短問答。"
        )
    )
    ai_action_priority = (
        "high"
        if (not simple_core_ready and (not ai_status["ready"] or drafts > 0))
        else "normal"
    )
    ai_actions = []
    if int(payload["summary"].get("learner_count") or 0) <= 0:
        ai_actions.append(
            {
                "title": "匯入學生帳號",
                "description": (
                    "目前尚未匯入學生帳號。請先用 CSV 批量新增學生，"
                    "再發布作業到班級，校內試行才會有提交、批改和成績記錄。"
                ),
                "href": "/dash/users/settings/add",
                "priority": "high",
            }
        )
    ai_assignment_action = {
        "title": "簡單出題",
        "description": ai_action_description,
        "href": "/dash/assignments",
        "priority": ai_action_priority,
    }
    if published_without_targets > 0:
        empty_class_detail = (
            f"其中 {published_empty_target_usergroups} 份是所選班級沒有學生；"
            if published_empty_target_usergroups > 0
            else ""
        )
        ai_actions.append(
            {
                "title": "設定發佈班級",
                "description": (
                    f"有 {published_without_targets} 份已發布作業未指定班級/群組、班級已不存在，或班級沒有學生；"
                    f"{empty_class_detail}"
                    "成績表可能按錯誤範圍計算，正式試行前建議先把學生加入班級或重新設定發佈對象。"
                ),
                "href": "/dash/assignments",
                "priority": "high",
            }
        )
    if published_needing_setup > 0:
        hidden_from_students_detail = (
            f"其中 {published_hidden_from_students} 份的課程或活動未發布，學生暫時看不到。"
            if published_hidden_from_students > 0
            else ""
        )
        target_detail = (
            f"另有 {published_without_targets} 份需要重新設定發佈班級或把學生加入班級；"
            if published_without_targets > 0
            else ""
        )
        ai_actions.append(
            {
                "title": "整理已發布作業",
                "description": (
                    f"有 {published_needing_setup} 份已發布作業不適合正式展示；"
                    f"{hidden_from_students_detail}"
                    f"{target_detail}"
                    "請確認有效截止日期，改成選擇、填空、短問答，"
                    "並啟用自動批改、可重做、顯示答案和最高分計分，或先取消發布。"
                ),
                "href": "/dash/assignments",
                "priority": "high",
            }
        )
    ai_actions.append(ai_assignment_action)
    if self_test_ready_question_count <= 0:
        ai_actions.append(
            {
                "title": "準備自測題庫",
                "description": (
                    "目前沒有可用的共享自測題目。可用 AI 生成，也可在題庫手動加入"
                    "內容完整的選擇、填空、短問答；這是加分項，不影響先跑通作業、提交和批改。"
                ),
                "href": "/dash/question-bank",
                "priority": "normal",
            }
        )
    elif self_test_ready_question_count < self_test_min_question_count:
        ai_actions.append(
            {
                "title": "補充自測題庫",
                "description": (
                    f"目前只有 {self_test_ready_question_count} 題可用自測題。"
                    f"建議至少準備 {self_test_min_question_count} 題，學生每次可以做完整短練習。"
                ),
                "href": "/dash/question-bank",
                "priority": "normal",
            }
        )
    ai_actions.extend(
        [
            {
                "title": "自動批改",
                "description": "學生提交後立即給分；答錯可重做，老師只需跟進特殊情況。",
                "href": "/dash/gradebook",
                "priority": "high" if payload["summary"]["pending_review"] > 0 else "normal",
            },
            {
                "title": "題庫抽題自測",
                "description": (
                    f"題庫已有 {self_test_ready_question_count} 題可用簡單題。"
                    "學生自測後會保留記錄供老師參考。"
                    if self_test_bank_ready
                    else "先準備共享題庫題目，學生才可以開始自測。"
                ),
                "href": "/dash/self-tests",
                "priority": "normal",
            },
        ]
    )

    summary = {
        **payload["summary"],
        "drafts": drafts,
        "published": published,
        "auto_graded_assignments": auto_graded,
        "simple_auto_graded_assignments": auto_graded,
        "simple_pilot_ready_assignments": auto_graded,
        "published_needing_setup": published_needing_setup,
        "published_without_targets": published_without_targets,
        "published_empty_target_usergroups": published_empty_target_usergroups,
        "published_hidden_from_students": published_hidden_from_students,
        "due_soon": len(due_soon_assignments),
        "expected_unsubmitted": len(missing_rows),
        "essay_pending_review": len([
            row for row in rows
            if row.get("teacher_review_status") == "pending"
            and row.get("has_essay_task") is True
        ]),
        "ai_assignment_ready": ai_status["ready"],
        "ai_assignment_provider": ai_status["provider"],
        "ai_assignment_model": ai_status["model"],
        "ai_assignment_message": ai_status["message"],
        "self_test_bank_ready": self_test_bank_ready,
        "self_test_ready_question_count": self_test_ready_question_count,
        "self_test_min_question_count": self_test_min_question_count,
    }
    summary.update(_build_simple_pilot_status_summary(summary))

    return {
        "summary": summary,
        "due_soon_assignments": due_soon_assignments[:8],
        "pending_review_submissions": [
            row for row in rows if row["teacher_review_status"] == "pending"
        ][:8],
        "missing_submissions": missing_rows[:8],
        "ai_actions": ai_actions,
    }


async def read_assignment_gradebook(
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    course_id: int | None = None,
    usergroup_id: int | None = None,
    include_self_tests: bool = False,
) -> dict:
    _block_api_tokens(current_user)
    await require_org_role_permission(
        int(current_user.id),
        org_id,
        db_session,
        "dashboard",
        "action_access",
    )
    return await _build_assignment_gradebook_payload(
        org_id=org_id,
        db_session=db_session,
        course_id=course_id,
        usergroup_id=usergroup_id,
        include_self_tests=include_self_tests,
    )


async def read_my_assignment_queue(
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    limit: int = 5,
) -> dict:
    _block_api_tokens(current_user)
    await _ensure_org_exists(org_id, db_session)

    current_user_id = int(getattr(current_user, "id", 0) or 0)
    learners_by_id = await _read_org_learners(org_id, db_session)
    self_test_ready_question_count = await _count_self_test_ready_question_bank_items(
        org_id,
        db_session,
    )
    self_test_bank_ready = self_test_ready_question_count >= SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT
    if current_user_id not in learners_by_id:
        return {
            "summary": {
                "todo_count": 0,
                "overdue_count": 0,
                "retry_count": 0,
                "waiting_count": 0,
                "graded_count": 0,
                "self_test_bank_ready": self_test_bank_ready,
                "self_test_ready_question_count": self_test_ready_question_count,
                "self_test_min_question_count": SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT,
            },
            "assignments": [],
        }

    assignment_rows = (
        await db_session.execute(
            select(
                Assignment,
                Course.name,
                Course.course_uuid,
                Course.public,
                Activity.activity_uuid,
            )
            .join(Course, Course.id == Assignment.course_id)  # type: ignore
            .join(Activity, Activity.id == Assignment.activity_id)  # type: ignore
            .where(
                Assignment.org_id == org_id,
                Assignment.published == True,
                Course.published == True,
                Activity.published == True,
            )
            .order_by(Assignment.due_date.asc(), Assignment.id.asc())
        )
    ).all()
    assignments = [row[0] for row in assignment_rows]
    assignment_ids = [int(assignment.id) for assignment in assignments if assignment.id is not None]
    course_uuids = [str(row[2]) for row in assignment_rows if row[2]]

    target_usergroup_ids: set[int] = set()
    for assignment in assignments:
        target_usergroup_ids.update(_normalize_int_list(assignment.target_usergroup_ids))
    groups_by_id, members_by_group_id = await _read_usergroup_context(
        org_id,
        target_usergroup_ids,
        db_session,
    )
    course_access_group_ids_by_uuid: dict[str, set[int]] = defaultdict(set)
    current_user_course_access_group_ids: set[int] = set()
    if course_uuids:
        course_resource_rows = (
            await db_session.execute(
                select(UserGroupResource.resource_uuid, UserGroupResource.usergroup_id).where(
                    UserGroupResource.org_id == org_id,
                    UserGroupResource.resource_uuid.in_(course_uuids),
                )
            )
        ).all()
        for resource_uuid, group_id in course_resource_rows:
            course_access_group_ids_by_uuid[str(resource_uuid)].add(int(group_id))
        all_course_access_group_ids = {
            group_id
            for group_ids in course_access_group_ids_by_uuid.values()
            for group_id in group_ids
        }
        if all_course_access_group_ids:
            membership_rows = (
                await db_session.execute(
                    select(UserGroupUser.usergroup_id).where(
                        UserGroupUser.org_id == org_id,
                        UserGroupUser.user_id == current_user_id,
                        UserGroupUser.usergroup_id.in_(sorted(all_course_access_group_ids)),
                    )
                )
            ).all()
            current_user_course_access_group_ids = {
                int(group_id) for (group_id,) in membership_rows
            }

    submissions_by_assignment_id: dict[int, AssignmentUserSubmission] = {}
    task_counts_by_assignment_id: dict[int, int] = defaultdict(int)
    max_grade_by_assignment_id: dict[int, int] = defaultdict(int)
    if assignment_ids:
        submissions = (
            await db_session.execute(
                select(AssignmentUserSubmission).where(
                    AssignmentUserSubmission.assignment_id.in_(assignment_ids),
                    AssignmentUserSubmission.user_id == current_user_id,
                )
            )
        ).scalars().all()
        for submission in submissions:
            assignment_id = int(submission.assignment_id)
            submissions_by_assignment_id[assignment_id] = _latest_assignment_submission(
                submissions_by_assignment_id.get(assignment_id),
                submission,
            )

        task_rows = (
            await db_session.execute(
                select(AssignmentTask.assignment_id, AssignmentTask.max_grade_value).where(
                    AssignmentTask.assignment_id.in_(assignment_ids)
                )
            )
        ).all()
        for assignment_id, max_grade_value in task_rows:
            task_counts_by_assignment_id[int(assignment_id)] += 1
            max_grade_by_assignment_id[int(assignment_id)] += int(max_grade_value or 0)

    today = datetime.now().date()
    rows: list[dict] = []
    for assignment, course_name, course_uuid, course_public, activity_uuid in assignment_rows:
        assignment_id = int(assignment.id or 0)
        task_count = task_counts_by_assignment_id.get(assignment_id, 0)
        if task_count <= 0:
            continue
        target_ids = _normalize_int_list(assignment.target_usergroup_ids)
        if not target_ids:
            continue
        if not all(group_id in groups_by_id for group_id in target_ids):
            continue
        if not any(
            current_user_id in members_by_group_id.get(group_id, set())
            for group_id in target_ids
        ):
            continue
        course_access_group_ids = course_access_group_ids_by_uuid.get(str(course_uuid), set())
        if (
            not bool(course_public)
            and course_access_group_ids
            and course_access_group_ids.isdisjoint(current_user_course_access_group_ids)
        ):
            continue

        submission = submissions_by_assignment_id.get(assignment_id)
        status_value = _submission_status_value(submission)
        due_date = _parse_date(assignment.due_date)
        is_overdue = (
            due_date is not None
            and due_date < today
            and status_value
            in {
                AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
                AssignmentUserSubmissionStatus.PENDING.value,
            }
        )
        needs_action = status_value in {
            AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            AssignmentUserSubmissionStatus.PENDING.value,
        }
        waiting_for_grade = status_value in {
            AssignmentUserSubmissionStatus.SUBMITTED.value,
            AssignmentUserSubmissionStatus.LATE.value,
        }
        attempt_number = int(submission.attempt_number or 0) if submission else 0
        max_retries = int(assignment.max_retries or 0)
        can_retry = (
            status_value == AssignmentUserSubmissionStatus.GRADED.value
            and _coerce_simple_answer_bool(assignment.allow_retries)
            and (max_retries == 0 or max(1, attempt_number) < max_retries)
        )
        rows.append(
            {
                "assignment_uuid": assignment.assignment_uuid,
                "assignment_title": assignment.title,
                "course_uuid": course_uuid,
                "course_name": course_name,
                "activity_uuid": activity_uuid,
                "subject": assignment.subject,
                "grade_level": assignment.grade_level,
                "unit": assignment.unit,
                "due_date": assignment.due_date,
                "task_count": task_count,
                "submission_status": status_value,
                "attempt_number": attempt_number,
                "grade": int(submission.grade or 0) if submission else None,
                "best_grade": int(submission.best_grade or 0) if submission else None,
                "max_grade": max_grade_by_assignment_id.get(assignment_id, 0),
                "allow_retries": _coerce_simple_answer_bool(assignment.allow_retries),
                "max_retries": max_retries,
                "can_retry": can_retry,
                "score_policy": assignment.score_policy or "highest",
                "show_correct_answers": _coerce_simple_answer_bool(assignment.show_correct_answers),
                "needs_action": needs_action,
                "waiting_for_grade": waiting_for_grade,
                "overdue": is_overdue,
            }
        )

    def queue_sort_key(row: dict) -> tuple[int, str, str]:
        status_value = row.get("submission_status")
        priority = 0
        if row.get("overdue"):
            priority = 0
        elif status_value == AssignmentUserSubmissionStatus.PENDING.value:
            priority = 1
        elif status_value == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value:
            priority = 2
        elif row.get("can_retry"):
            priority = 3
        elif row.get("waiting_for_grade"):
            priority = 4
        else:
            priority = 5
        return (
            priority,
            str(row.get("due_date") or "9999-12-31"),
            str(row.get("assignment_title") or ""),
        )

    rows.sort(key=queue_sort_key)
    todo_rows = [row for row in rows if row["needs_action"]]
    return {
        "summary": {
            "todo_count": len(todo_rows),
            "overdue_count": len([row for row in rows if row["overdue"]]),
            "retry_count": len([
                row for row in rows
                if row["submission_status"] == AssignmentUserSubmissionStatus.PENDING.value
            ]),
            "can_retry_count": len([row for row in rows if row.get("can_retry") is True]),
            "waiting_count": len([row for row in rows if row["waiting_for_grade"]]),
            "graded_count": len([
                row for row in rows
                if row["submission_status"] == AssignmentUserSubmissionStatus.GRADED.value
            ]),
            "self_test_bank_ready": self_test_bank_ready,
            "self_test_ready_question_count": self_test_ready_question_count,
            "self_test_min_question_count": SIMPLE_SELF_TEST_DEFAULT_QUESTION_COUNT,
        },
        "assignments": rows[: max(1, min(int(limit or 5), 10))],
    }


def assignment_gradebook_to_csv(payload: dict) -> str:
    def summary_value(key: str, fallback: object = 0) -> object:
        return (payload.get("summary") or {}).get(key, fallback)

    def percent_label(value: object) -> str:
        if value is None:
            return ""
        try:
            return f"{round(float(value))}%"
        except (TypeError, ValueError):
            return str(value)

    def count_label(key: str) -> str:
        value = summary_value(key)
        return "" if value is None else str(value)

    def score_label(value: object) -> str:
        if value is None:
            return ""
        try:
            return f"{round(float(value))}%"
        except (TypeError, ValueError):
            return str(value)

    submission_status_labels = {
        AssignmentUserSubmissionStatus.NOT_SUBMITTED.value: "未提交",
        AssignmentUserSubmissionStatus.PENDING.value: "重做中",
        AssignmentUserSubmissionStatus.SUBMITTED.value: "已提交",
        AssignmentUserSubmissionStatus.LATE.value: "逾期提交",
        AssignmentUserSubmissionStatus.GRADED.value: "已批改",
        SelfTestAttemptStatus.STARTED.value: "自測中",
        SelfTestAttemptStatus.REVIEWED.value: "已確認",
    }
    review_status_labels = {
        "pending": "待老師確認",
        "confirmed": "已確認",
        "not_required": "系統自動批改",
        "missing": "未提交",
    }

    def review_status_label(row: dict) -> str:
        if (
            row.get("submission_status") == AssignmentUserSubmissionStatus.PENDING.value
            and row.get("teacher_review_status") == "missing"
        ):
            return "等待重新提交"
        return review_status_labels.get(
            row.get("teacher_review_status"), row.get("teacher_review_status") or ""
        )

    def current_score_label(row: dict) -> str:
        if row.get("submission_status") == AssignmentUserSubmissionStatus.PENDING.value:
            return "重做中"
        return "" if row.get("grade") is None else str(row.get("grade"))

    def participation_status_label(row: dict) -> str:
        status_value = row.get("submission_status")
        if status_value == AssignmentUserSubmissionStatus.NOT_SUBMITTED.value:
            return "未參與"
        if status_value == AssignmentUserSubmissionStatus.PENDING.value:
            return "重做中"
        if row.get("source_type") == "self_test":
            if status_value == SelfTestAttemptStatus.STARTED.value:
                return "自測中"
            if status_value in {
                SelfTestAttemptStatus.SUBMITTED.value,
                SelfTestAttemptStatus.REVIEWED.value,
            }:
                return "已完成"
        if status_value in {
            AssignmentUserSubmissionStatus.SUBMITTED.value,
            AssignmentUserSubmissionStatus.LATE.value,
        }:
            return "已提交"
        if status_value == AssignmentUserSubmissionStatus.GRADED.value:
            return "已完成"
        return "已參與"

    output = io.StringIO()
    summary_writer = csv.writer(output)
    summary_writer.writerow(["摘要項目", "摘要數值"])
    summary_writer.writerow(["校內試行狀態", summary_value("pilot_status_label", "")])
    summary_writer.writerow(["校長展示摘要", summary_value("principal_evidence_summary", "")])
    summary_writer.writerow(["建議下一步", summary_value("pilot_next_step", "")])
    summary_writer.writerow(["已匯入學生", count_label("learner_count")])
    summary_writer.writerow(["已有學習記錄學生", count_label("engaged_student_count")])
    summary_writer.writerow(["學生使用率", percent_label(summary_value("engagement_rate", None))])
    summary_writer.writerow(["學習記錄", count_label("actionable_rows")])
    summary_writer.writerow(["提交率", percent_label(summary_value("submission_rate", None))])
    summary_writer.writerow(["平均分（計入評分）", score_label(summary_value("average_score", None))])
    summary_writer.writerow(["計入評分記錄", count_label("scored_records")])
    summary_writer.writerow(["達標記錄（60% 以上）", count_label("passing_score_records")])
    summary_writer.writerow(["需補強記錄（低於 60%）", count_label("needs_practice_records")])
    summary_writer.writerow(["未提交", count_label("unsubmitted")])
    summary_writer.writerow(["待覆核", count_label("pending_review")])
    summary_writer.writerow(["逾期", count_label("late")])
    summary_writer.writerow(["重做中", count_label("retry_in_progress")])
    summary_writer.writerow(["重做/再練習記錄", count_label("retry_records")])
    summary_writer.writerow(["自測記錄", count_label("self_test_rows")])
    summary_writer.writerow(["校內試行就緒作業", count_label("simple_pilot_ready_assignments")])
    summary_writer.writerow(["班級需重設或需加學生作業", count_label("published_without_targets")])
    summary_writer.writerow(["班級沒有學生作業", count_label("published_empty_target_usergroups")])
    summary_writer.writerow(["學生暫時看不到的已發布作業", count_label("published_hidden_from_students")])
    summary_writer.writerow(
        [
            "簡化原則",
            "作業只用選擇、填空、短問答；可用 AI、題庫或手動出題；學生提交後自動批改，答錯可重做，成績取最高分。",
        ]
    )
    summary_writer.writerow([])

    fieldnames = [
        "來源",
        "課程",
        "作業/自測",
        "科目",
        "年級",
        "班級/群組",
        "學生姓名",
        "學生電郵",
        "提交狀態",
        "參與狀態",
        "覆核狀態",
        "跟進原因",
        "學生是否可見",
        "自動批改",
        "可重做",
        "顯示參考答案",
        "提交時間",
        "截止日期",
        "分數",
        "滿分",
        "百分比",
        "嘗試次數",
        "是否重做",
        "最高分",
        "最高分嘗試",
        "計分策略",
        "是否逾期",
        "是否計入評分",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in payload.get("rows", []):
        grade_display = row.get("grade_display") or {}
        attempt_number = int(row.get("attempt_number") or 0)
        best_grade = row.get("best_grade")
        best_attempt_number = row.get("best_attempt_number")
        max_grade = row.get("max_grade")
        score_policy = row.get("score_policy")
        has_best_score = (
            best_grade is not None
            and bool(max_grade)
            and int(best_attempt_number or 0) > 0
        )
        writer.writerow(
            {
                "來源": "自測" if row.get("source_type") == "self_test" else "作業",
                "課程": row.get("course_name") or "",
                "作業/自測": row.get("assignment_title") or "",
                "科目": row.get("subject") or "",
                "年級": row.get("grade_level") or "",
                "班級/群組": "、".join(row.get("target_usergroups") or []),
                "學生姓名": row.get("student_name") or "",
                "學生電郵": row.get("student_email") or "",
                "提交狀態": submission_status_labels.get(
                    row.get("submission_status"), row.get("submission_status") or ""
                ),
                "參與狀態": participation_status_label(row),
                "覆核狀態": review_status_label(row),
                "跟進原因": row.get("attention_reason") or "",
                "學生是否可見": "否" if row.get("visible_to_students") is False else "是",
                "自動批改": "是" if _coerce_simple_answer_bool(row.get("auto_grading")) else "否",
                "可重做": "是" if _coerce_simple_answer_bool(row.get("allow_retries")) else "否",
                "顯示參考答案": "是" if _coerce_simple_answer_bool(row.get("show_correct_answers")) else "否",
                "提交時間": row.get("submitted_at") or "",
                "截止日期": row.get("due_date") or "",
                "分數": current_score_label(row),
                "滿分": row.get("max_grade") or "",
                "百分比": grade_display.get("percentage_display") or "",
                "嘗試次數": attempt_number or "",
                "是否重做": "是" if attempt_number > 1 else "否",
                "最高分": (
                    f"{best_grade}/{max_grade}"
                    if has_best_score
                    else ""
                ),
                "最高分嘗試": best_attempt_number if has_best_score else "",
                "計分策略": (
                    "最高分"
                    if score_policy == "highest"
                    else "最後一次" if score_policy == "latest" else ""
                ),
                "是否逾期": "是" if row.get("late") else "否",
                "是否計入評分": "是" if row.get("counts_for_grade") else "否",
            }
        )
    return "\ufeff" + output.getvalue()


def _safe_ascii_filename_part(value: object) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())).strip("-")


def assignment_gradebook_csv_filename(
    payload: dict,
    generated_at: datetime | None = None,
) -> str:
    filters = payload.get("filters") or {}
    generated_at = generated_at or datetime.utcnow()
    parts = [
        "learnhouse",
        "gradebook",
        f"org-{filters.get('org_id') or 'org'}",
        f"course-{filters.get('course_id')}" if filters.get("course_id") is not None else "all-courses",
        f"group-{filters.get('usergroup_id')}" if filters.get("usergroup_id") is not None else "all-groups",
        "with-self-tests" if filters.get("include_self_tests") else "assignments-only",
        generated_at.strftime("%Y%m%d"),
    ]
    filename = "-".join(
        part for part in (_safe_ascii_filename_part(part) for part in parts) if part
    )
    return f"{filename or 'learnhouse-gradebook'}.csv"


## > Assignments CRUD


async def create_assignment(
    request: Request,
    assignment_object: AssignmentCreate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if org exists
    statement = select(Course).where(Course.id == assignment_object.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.CREATE)

    # Usage check
    await check_limits_with_usage("assignments", course.org_id, db_session)

    if assignment_object.published:
        raise HTTPException(
            status_code=400,
            detail="建立作業時請先保存為草稿，新增題目後再發布。",
        )

    assignment_object.title = _require_assignment_title(assignment_object.title)
    _validate_new_assignment_due_date(assignment_object.due_date)

    # Create Assignment
    assignment = Assignment(**assignment_object.model_dump())
    _normalize_assignment_school_fields(assignment)

    assignment.assignment_uuid = str(f"assignment_{uuid4()}")
    assignment.creation_date = str(datetime.now())
    assignment.update_date = str(datetime.now())
    assignment.org_id = course.org_id

    # Insert Assignment in DB
    db_session.add(assignment)
    await db_session.commit()
    await db_session.refresh(assignment)

    # Feature usage
    await increase_feature_usage("assignments", course.org_id, db_session)

    # return assignment read
    return _assignment_read(assignment)


async def read_assignment(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    statement = (
        select(Assignment, Course.course_uuid, Activity.activity_uuid)
        .join(Course, Course.id == Assignment.course_id)  # type: ignore
        .join(Activity, Activity.id == Assignment.activity_id)  # type: ignore
        .where(Assignment.assignment_uuid == assignment_uuid)
    )
    row = (await db_session.execute(statement)).first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    assignment, course_uuid, activity_uuid = row

    await check_resource_access(request, db_session, current_user, course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    result = _assignment_read(assignment)
    result.course_uuid = course_uuid
    result.activity_uuid = activity_uuid
    return result


async def read_assignment_from_activity_uuid(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    statement = (
        select(Assignment, Course.course_uuid, Activity.activity_uuid)
        .join(Activity, Activity.id == Assignment.activity_id)  # type: ignore
        .join(Course, Course.id == Assignment.course_id)  # type: ignore
        .where(Activity.activity_uuid == activity_uuid)
    )
    row = (await db_session.execute(statement)).first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    assignment, course_uuid, activity_uuid_val = row

    await check_resource_access(request, db_session, current_user, course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    result = _assignment_read(assignment)
    result.course_uuid = course_uuid
    result.activity_uuid = activity_uuid_val
    return result


async def update_assignment(
    request: Request,
    assignment_uuid: str,
    assignment_object: AssignmentUpdate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    if assignment_object.title is not None:
        assignment_object.title = _require_assignment_title(assignment_object.title)

    if (
        assignment.id is not None
        and assignment.published
        and assignment_object.published is False
        and await _assignment_has_locked_submissions(int(assignment.id), db_session)
    ):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能取消發布，避免學生提交記錄和成績表被隱藏。請建立新作業或保留此作業作為評分證據。",
        )

    if (
        assignment.id is not None
        and _assignment_update_changes_grading_policy(assignment, assignment_object)
        and await _assignment_has_locked_submissions(int(assignment.id), db_session)
    ):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能修改評分制或計分策略。請建立新作業，或先退回/重做相關提交。",
        )

    if (
        assignment.id is not None
        and _assignment_update_changes_due_date(assignment, assignment_object)
        and await _assignment_has_locked_submissions(int(assignment.id), db_session)
    ):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能修改截止日期，避免逾期狀態和成績表失真。請建立新作業，或保留原截止日期。",
        )

    if (
        assignment.id is not None
        and _assignment_update_changes_target_usergroups(assignment, assignment_object)
        and await _assignment_has_locked_submissions(int(assignment.id), db_session)
    ):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能修改發布班級/群組，避免成績表和未提交名單失真。請建立新作業。",
        )

    if _assignment_publish_or_due_date_update_needs_due_date_guard(assignment, assignment_object):
        effective_due_date = _parse_date(
            assignment_object.due_date
            if assignment_object.due_date is not None
            else assignment.due_date
        )
        if effective_due_date is None:
            raise HTTPException(
                status_code=400,
                detail="發布或更新已發布作業前，請設定有效的截止日期，方便老師查看未交、逾期和快截止學生。",
            )
        if effective_due_date is not None and effective_due_date < datetime.now().date():
            raise HTTPException(
                status_code=400,
                detail="發布或更新已發布作業前，請把截止日期設定為今天或之後，避免學生一收到作業就逾期。",
            )

    if assignment_object.published is True:
        assignment_tasks = (
            await db_session.execute(
                select(AssignmentTask).where(AssignmentTask.assignment_id == assignment.id)
            )
        ).scalars().all()
        if not assignment_tasks:
            raise HTTPException(
                status_code=400,
                detail="發布作業前請先新增至少一題。建議用 AI、題庫或手動建立選擇、填空或短問答。",
            )
        if len(assignment_tasks) > SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"校內試行建議每份作業最多 {SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題，"
                    "請拆成多份簡單作業，學生更容易完成，老師也更容易查看成績。"
                ),
            )
        for task in assignment_tasks:
            if publish_error := _simple_task_publish_error(task):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"發布作業前請先完成題目內容：{publish_error}"
                        "建議用 AI、題庫或手動建立選擇、填空、短問答或作文題。"
                    ),
                )
        _apply_simple_publish_learning_defaults(assignment_object, assignment_tasks)

    if assignment_object.published is True or assignment_object.target_usergroup_ids is not None:
        await _validate_assignment_publish_targets(assignment, assignment_object, db_session)

    if (
        assignment_object.auto_grading is True
        and assignment.id is not None
        and await _has_non_auto_gradable_assignment_tasks(int(assignment.id), db_session)
    ):
        raise HTTPException(
            status_code=400,
            detail="這份作業包含需要老師覆核的題型，不能啟用自動批改。請只使用選擇、填空、短問答等可自動批改題型。",
        )

    # Update only the fields that were passed in
    for var, value in vars(assignment_object).items():
        if value is not None:
            setattr(assignment, var, value)
    _normalize_assignment_school_fields(assignment)
    assignment.update_date = str(datetime.now())

    # Insert Assignment in DB
    db_session.add(assignment)
    await db_session.commit()
    await db_session.refresh(assignment)

    # return assignment read
    return _assignment_read(assignment)


async def delete_assignment(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.DELETE)

    if assignment.id is not None and await _assignment_has_locked_submissions(int(assignment.id), db_session):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能刪除，避免成績表和提交記錄遺失。請保留作業作為評分證據。",
        )

    # Feature usage
    await decrease_feature_usage("assignments", course.org_id, db_session)

    # Delete Assignment
    await db_session.delete(assignment)
    await db_session.commit()

    return {"message": "Assignment deleted"}


async def delete_assignment_from_activity_uuid(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if activity exists
    statement = select(Activity).where(Activity.activity_uuid == activity_uuid)

    activity = (await db_session.execute(statement)).scalars().first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == activity.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.activity_id == activity.id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.DELETE)

    if assignment.id is not None and await _assignment_has_locked_submissions(int(assignment.id), db_session):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能刪除，避免成績表和提交記錄遺失。請保留作業作為評分證據。",
        )

     # Feature usage
    await decrease_feature_usage("assignments", course.org_id, db_session)

    # Delete Assignment
    await db_session.delete(assignment)

    await db_session.commit()

    return {"message": "Assignment deleted"}


## > Assignments Tasks CRUD


async def create_assignment_task(
    request: Request,
    assignment_uuid: str,
    assignment_task_object: AssignmentTaskCreate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.CREATE)

    if assignment.id is not None and await _assignment_has_locked_submissions(int(assignment.id), db_session):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能新增題目。請建立新作業，或先退回/重做相關提交。",
        )
    if assignment.published:
        raise HTTPException(
            status_code=400,
            detail="已發布作業不能新增題目，避免學生作答期間題目變動。請先取消發布，或建立新作業。",
        )
    if assignment.id is not None:
        existing_task_count = len(
            (
                await db_session.execute(
                    select(AssignmentTask).where(AssignmentTask.assignment_id == assignment.id)
                )
            ).scalars().all()
        )
        if existing_task_count >= SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"校內試行每份作業最多 {SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS} 題。"
                    "請建立另一份簡單作業，讓學生分次完成。"
                ),
            )

    assignment_task_object.title = _require_assignment_task_title(assignment_task_object.title)
    assignment_task_object.max_grade_value = _require_positive_assignment_task_max_grade(
        assignment_task_object.max_grade_value
    )

    # Create Assignment Task
    assignment_task = AssignmentTask(**assignment_task_object.model_dump())

    assignment_task.assignment_task_uuid = str(f"assignmenttask_{uuid4()}")
    assignment_task.creation_date = str(datetime.now())
    assignment_task.update_date = str(datetime.now())
    assignment_task.org_id = course.org_id
    assignment_task.chapter_id = assignment.chapter_id
    assignment_task.activity_id = assignment.activity_id
    assignment_task.assignment_id = assignment.id  # type: ignore
    assignment_task.course_id = assignment.course_id

    if (
        assignment_task.assignment_type not in AUTO_GRADABLE_TASK_TYPES
        or assignment_task.assignment_type == AssignmentTaskTypeEnum.ESSAY
    ):
        _force_teacher_review_for_manual_tasks(assignment)
        db_session.add(assignment)

    # Insert Assignment Task in DB
    db_session.add(assignment_task)
    await db_session.commit()
    await db_session.refresh(assignment_task)

    # return assignment task read
    return AssignmentTaskRead.model_validate(assignment_task)


async def read_assignment_tasks(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Find assignment
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Find assignments tasks for an assignment, most recently created first
    statement = (
        select(AssignmentTask)
        .where(AssignmentTask.assignment_id == assignment.id)
        .order_by(AssignmentTask.creation_date.desc(), AssignmentTask.id.desc())
    )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )

    # return assignment tasks read
    return [
        (
            AssignmentTaskRead.model_validate(assignment_task)
            if is_instructor
            else _student_assignment_task_read(assignment_task)
        )
        for assignment_task in (await db_session.execute(statement)).scalars().all()
    ]


async def read_assignment_task(
    request: Request,
    assignment_task_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Find assignment
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignmenttask = (await db_session.execute(statement)).scalars().first()

    if not assignmenttask:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignmenttask.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )

    # return assignment task read
    return (
        AssignmentTaskRead.model_validate(assignmenttask)
        if is_instructor
        else _student_assignment_task_read(assignmenttask)
    )


async def put_assignment_task_reference_file(
    request: Request,
    db_session: AsyncSession,
    assignment_task_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    reference_file: UploadFile | None = None,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check for activity
    statement = select(Activity).where(Activity.id == assignment.activity_id)
    activity = (await db_session.execute(statement)).scalars().first()

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Get org uuid
    org_statement = select(Organization).where(Organization.id == course.org_id)
    org = (await db_session.execute(org_statement)).scalars().first()

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    # Upload reference file
    if reference_file and reference_file.filename and activity and org:
        name_in_disk = await upload_reference_file(
            reference_file,
            activity.activity_uuid,
            org.org_uuid,
            course.course_uuid,
            assignment.assignment_uuid,
            assignment_task_uuid,
        )
        # Update reference file
        assignment_task.reference_file = name_in_disk

    assignment_task.update_date = str(datetime.now())

    # Insert Assignment Task in DB
    db_session.add(assignment_task)
    await db_session.commit()
    await db_session.refresh(assignment_task)

    # return assignment task read
    return AssignmentTaskRead.model_validate(assignment_task)


async def put_assignment_task_submission_file(
    request: Request,
    db_session: AsyncSession,
    assignment_task_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    sub_file: UploadFile | None = None,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check for activity
    statement = select(Activity).where(Activity.id == assignment.activity_id)
    activity = (await db_session.execute(statement)).scalars().first()

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Get org uuid
    org_statement = select(Organization).where(Organization.id == course.org_id)
    org = (await db_session.execute(org_statement)).scalars().first()

    # RBAC check - only need read permission to submit files
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Check if user is enrolled in the course
    if not await authorization_verify_based_on_roles(request, current_user.id, "read", course.course_uuid, db_session):
        raise HTTPException(
            status_code=403,
            detail="You must be enrolled in this course to submit files"
        )

    # Upload submission file
    if sub_file and sub_file.filename and activity and org:
        is_instructor = await authorization_verify_based_on_roles(
            request,
            current_user.id,
            "update",
            course.course_uuid,
            db_session,
        )
        if not is_instructor:
            if not assignment.published:
                raise HTTPException(
                    status_code=400,
                    detail="這份作業尚未發布，暫時不能上傳答案檔案。",
                )
            await _validate_student_can_submit_targeted_assignment(
                assignment,
                current_user,
                db_session,
            )
            _ensure_student_can_change_assignment_evidence(
                await _read_assignment_user_submission(
                    int(assignment.id) if assignment.id is not None else None,
                    int(current_user.id),
                    db_session,
                )
            )

        name_in_disk = await upload_submission_file(
            sub_file,
            activity.activity_uuid,
            org.org_uuid,
            course.course_uuid,
            assignment.assignment_uuid,
            assignment_task_uuid,
        )

        return {"file_uuid": name_in_disk}


async def update_assignment_task(
    request: Request,
    assignment_task_uuid: str,
    assignment_task_object: AssignmentTaskUpdate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    if (
        assignment.id is not None
        and _task_update_changes_grading_structure(assignment_task_object)
        and await _assignment_has_locked_submissions(int(assignment.id), db_session)
    ):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能再修改題型、題目內容或分值。請建立新作業，或先退回/重做相關提交。",
        )
    if assignment.published and _task_update_changes_grading_structure(assignment_task_object):
        raise HTTPException(
            status_code=400,
            detail="已發布作業不能修改題型、題目內容或分值，避免學生作答期間題目變動。請先取消發布，或建立新作業。",
        )

    if assignment_task_object.title is not None:
        assignment_task_object.title = _require_assignment_task_title(assignment_task_object.title)
    if assignment_task_object.max_grade_value is not None:
        assignment_task_object.max_grade_value = _require_positive_assignment_task_max_grade(
            assignment_task_object.max_grade_value
        )

    projected_type = assignment_task_object.assignment_type or assignment_task.assignment_type

    if (
        assignment.id is not None
        and (
            projected_type == AssignmentTaskTypeEnum.ESSAY
            or await _has_non_auto_gradable_assignment_tasks(
                int(assignment.id),
                db_session,
                replacing_task_id=int(assignment_task.id or 0),
                replacement_type=projected_type,
            )
        )
    ):
        _force_teacher_review_for_manual_tasks(assignment)
        db_session.add(assignment)

    # Update only the fields that were passed in
    for var, value in vars(assignment_task_object).items():
        if value is not None:
            setattr(assignment_task, var, value)
    assignment_task.update_date = str(datetime.now())

    # Insert Assignment Task in DB
    db_session.add(assignment_task)
    await db_session.commit()
    await db_session.refresh(assignment_task)

    # return assignment task read
    return AssignmentTaskRead.model_validate(assignment_task)


async def delete_assignment_task(
    request: Request,
    assignment_task_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.DELETE)

    if assignment.id is not None and await _assignment_has_locked_submissions(int(assignment.id), db_session):
        raise HTTPException(
            status_code=400,
            detail="已有學生提交這份作業，不能刪除題目。請建立新作業，或先退回/重做相關提交。",
        )

    if assignment.published:
        raise HTTPException(
            status_code=400,
            detail="已發布作業不能刪除題目，避免學生作答期間題目變動。請先取消發布，或建立新作業。",
        )

    # Delete Assignment Task
    await db_session.delete(assignment_task)
    await db_session.commit()

    return {"message": "Assignment Task deleted"}


## > Assignments Tasks Submissions CRUD


async def handle_assignment_task_submission(
    request: Request,
    assignment_task_uuid: str,
    assignment_task_submission_object: AssignmentTaskSubmissionUpdate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    assignment_task_submission_uuid = assignment_task_submission_object.assignment_task_submission_uuid
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # SECURITY: Check if user has instructor/admin permissions for grading
    is_instructor = await authorization_verify_based_on_roles(request, current_user.id, "update", course.course_uuid, db_session)

    # For regular users, ensure they can only submit their own work
    if not is_instructor:
        if not assignment.published:
            raise HTTPException(
                status_code=400,
                detail="這份作業尚未發布，暫時不能儲存答案。",
            )

        # Check if user is enrolled in the course
        if not await authorization_verify_based_on_roles(request, current_user.id, "read", course.course_uuid, db_session):
            raise HTTPException(
                status_code=403,
                detail="You must be enrolled in this course to submit assignments"
            )
        await _validate_student_can_submit_targeted_assignment(
            assignment,
            current_user,
            db_session,
        )

        _ensure_student_can_change_assignment_evidence(
            await _read_assignment_user_submission(
                int(assignment.id) if assignment.id is not None else None,
                int(current_user.id),
                db_session,
            )
        )

        # SECURITY: Regular users cannot update grades - only check if actual values are being set
        if (assignment_task_submission_object.grade is not None and assignment_task_submission_object.grade != 0) or \
           (assignment_task_submission_object.task_submission_grade_feedback is not None and assignment_task_submission_object.task_submission_grade_feedback != ""):
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to update grades"
            )

        # Only need read permission for submissions
        await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    else:
        # SECURITY: Instructors/admins need update permission to grade
        await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    if assignment_task.assignment_type == AssignmentTaskTypeEnum.CODE:
        assignment_task_submission_object.task_submission = _sanitize_code_submission_payload(
            assignment_task,
            assignment_task_submission_object.task_submission,
        )
        if is_instructor and (assignment_task.contents or {}).get("mode") == "web_preview":
            # Web-preview tasks are fully server-verifiable. An instructor UI
            # may request grading, but neither its claimed grade nor its local
            # check results are evidence. Recompute from the sanitized source
            # and the private stored checks before any value is persisted.
            verified_grade = _grade_web_preview_code_task(
                assignment_task,
                SimpleNamespace(
                    task_submission=assignment_task_submission_object.task_submission
                ),
            )
            task_max = int(assignment_task.max_grade_value or 0)
            assignment_task_submission_object.grade = verified_grade
            assignment_task_submission_object.task_submission_grade_feedback = (
                f"伺服器自動批改：得分 {verified_grade}/{task_max}。"
            )

    # Try to find existing submission by user_id and assignment_task_id first (for save progress functionality)
    statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_id == assignment_task.id,
        AssignmentTaskSubmission.user_id == current_user.id,
    )
    assignment_task_submission = (await db_session.execute(statement)).scalars().first()

    # If no submission found by user+task, try to find by UUID if provided (for specific submission updates)
    if not assignment_task_submission and assignment_task_submission_uuid:
        statement = select(AssignmentTaskSubmission).where(
            AssignmentTaskSubmission.assignment_task_submission_uuid == assignment_task_submission_uuid
        )
        assignment_task_submission = (await db_session.execute(statement)).scalars().first()

    # If submission exists, update it
    if assignment_task_submission:
        # SECURITY: For regular users, ensure they can only update their own submissions
        if not is_instructor and assignment_task_submission.user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="You can only update your own submissions"
            )

        # Update only the fields that were passed in
        for var, value in vars(assignment_task_submission_object).items():
            if value is not None:
                setattr(assignment_task_submission, var, value)
        assignment_task_submission.update_date = str(datetime.now())

        # Insert Assignment Task Submission in DB
        db_session.add(assignment_task_submission)
        await db_session.commit()
        await db_session.refresh(assignment_task_submission)

    else:
        # Create new Task submission
        current_time = str(datetime.now())

        # Assuming model_dump() returns a dictionary
        model_data = assignment_task_submission_object.model_dump()

        assignment_task_submission = AssignmentTaskSubmission(
            assignment_task_submission_uuid=assignment_task_submission_uuid or f"assignmenttasksubmission_{uuid4()}",
            task_submission=model_data["task_submission"],
            grade=0,  # Always start with 0 for new submissions
            task_submission_grade_feedback="",  # Start with empty feedback
            assignment_task_id=int(assignment_task.id),  # type: ignore
            assignment_type=assignment_task.assignment_type,
            activity_id=assignment.activity_id,
            course_id=assignment.course_id,
            chapter_id=assignment.chapter_id,
            user_id=current_user.id,
            creation_date=current_time,
            update_date=current_time,
        )

        # Insert Assignment Task Submission in DB
        db_session.add(assignment_task_submission)
        await db_session.commit()
        await db_session.refresh(assignment_task_submission)

    # return assignment task submission read
    return AssignmentTaskSubmissionRead.model_validate(assignment_task_submission)


async def read_user_assignment_task_submissions(
    request: Request,
    assignment_task_uuid: str,
    user_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Ownership check: non-instructors may only read their own submissions
    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )
    if not is_instructor and int(user_id) != int(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only view your own submissions",
        )
    if not is_instructor:
        await _validate_student_can_read_assignment(assignment, current_user, db_session)

    # Check if assignment task submission exists
    statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_id == assignment_task.id,
        AssignmentTaskSubmission.user_id == user_id,
    )
    assignment_task_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_task_submission:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task Submission not found",
        )

    # return assignment task submission read
    return AssignmentTaskSubmissionRead.model_validate(assignment_task_submission)


async def read_user_assignment_task_submissions_me_batch(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    """Return a map of {assignment_task_uuid: submission | None} for the
    current user across every task in the assignment, in a single round trip.
    Replaces N per-task /submissions/me calls from the activity view."""
    _block_api_tokens(current_user)

    assignment_row = (await db_session.execute(
        select(Assignment, Course.course_uuid)
        .join(Course, Course.id == Assignment.course_id)  # type: ignore
        .where(Assignment.assignment_uuid == assignment_uuid)
    )).first()

    if not assignment_row:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    assignment, course_uuid = assignment_row

    await check_resource_access(request, db_session, current_user, course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    rows = (await db_session.execute(
        select(AssignmentTask, AssignmentTaskSubmission)
        .outerjoin(
            AssignmentTaskSubmission,
            (AssignmentTaskSubmission.assignment_task_id == AssignmentTask.id)  # type: ignore
            & (AssignmentTaskSubmission.user_id == current_user.id),  # type: ignore
        )
        .where(AssignmentTask.assignment_id == assignment.id)
        # ASC ordering means that if legacy data has multiple submissions per
        # (task,user) — handle_assignment_task_submission is upsert so this
        # shouldn't happen in normal flow — the dict comprehension below
        # overwrites lower ids with higher ones, leaving the most recent
        # submission as the winning value.
        .order_by(AssignmentTaskSubmission.id.asc())  # type: ignore
    )).all()

    return {
        task.assignment_task_uuid: (
            AssignmentTaskSubmissionRead.model_validate(sub) if sub else None
        )
        for task, sub in rows
    }


async def read_user_assignment_task_submissions_me(
    request: Request,
    assignment_task_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
    await _validate_student_can_read_assignment(assignment, current_user, db_session)

    # Check if assignment task submission exists
    statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_id == assignment_task.id,
        AssignmentTaskSubmission.user_id == current_user.id,
    )
    assignment_task_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_task_submission:
        # Return None instead of raising an error for cases where no submission exists yet
        return None

    # return assignment task submission read
    return AssignmentTaskSubmissionRead.model_validate(assignment_task_submission)


async def read_assignment_task_submissions(
    request: Request,
    assignment_task_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
):
    _block_api_tokens(current_user)
    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.assignment_task_uuid == assignment_task_uuid,
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Only instructors may list all submissions for a task
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_id == assignment_task.id
    ).limit(limit).offset(offset)
    submissions = (await db_session.execute(statement)).scalars().all()
    return [AssignmentTaskSubmissionRead.model_validate(s) for s in submissions]


async def update_assignment_task_submission(
    request: Request,
    assignment_task_submission_uuid: str,
    assignment_task_submission_object: AssignmentTaskSubmissionCreate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment task submission exists
    statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_submission_uuid
        == assignment_task_submission_uuid
    )
    assignment_task_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_task_submission:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task Submission not found",
        )

    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.id == assignment_task_submission.assignment_task_id
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Update only the fields that were passed in
    for var, value in vars(assignment_task_submission_object).items():
        if value is not None:
            setattr(assignment_task_submission, var, value)
    assignment_task_submission.update_date = str(datetime.now())

    # Insert Assignment Task Submission in DB
    db_session.add(assignment_task_submission)
    await db_session.commit()
    await db_session.refresh(assignment_task_submission)

    # return assignment task submission read
    return AssignmentTaskSubmissionRead.model_validate(assignment_task_submission)


async def delete_assignment_task_submission(
    request: Request,
    assignment_task_submission_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment task submission exists
    statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_submission_uuid
        == assignment_task_submission_uuid
    )
    assignment_task_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_task_submission:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task Submission not found",
        )

    # Check if assignment task exists
    statement = select(AssignmentTask).where(
        AssignmentTask.id == assignment_task_submission.assignment_task_id
    )
    assignment_task = (await db_session.execute(statement)).scalars().first()

    if not assignment_task:
        raise HTTPException(
            status_code=404,
            detail="Assignment Task not found",
        )

    # Check if assignment exists
    statement = select(Assignment).where(Assignment.id == assignment_task.assignment_id)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.DELETE)

    # Delete Assignment Task Submission
    await db_session.delete(assignment_task_submission)
    await db_session.commit()

    return {"message": "Assignment Task Submission deleted"}


## > Assignments Submissions CRUD


async def create_assignment_submission(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if the submission has already been made. A row in PENDING /
    # NOT_SUBMITTED state means the student previously hit "Try again":
    # retry_assignment_submission left the row in place so the attempt
    # counter survives, but cleared everything else. Treat that as a
    # fresh-submission slot — flip status to SUBMITTED and reuse the row
    # — instead of erroring out on the existing row.
    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.assignment_id == assignment.id,
        AssignmentUserSubmission.user_id == current_user.id,
    )

    assignment_user_submission = (await db_session.execute(statement)).scalars().first()

    if assignment_user_submission:
        reusable_states = (
            AssignmentUserSubmissionStatus.PENDING,
            AssignmentUserSubmissionStatus.NOT_SUBMITTED,
        )
        if assignment_user_submission.submission_status not in reusable_states:
            raise HTTPException(
                status_code=400,
                detail="學生已提交這份作業",
            )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    if not assignment.published:
        raise HTTPException(
            status_code=400,
            detail="這份作業尚未發布，暫時不能提交。",
        )
    await _validate_student_can_submit_targeted_assignment(
        assignment,
        current_user,
        db_session,
    )

    tasks_statement = (
        select(AssignmentTask)
        .where(AssignmentTask.assignment_id == assignment.id)
        .order_by(AssignmentTask.id.asc())
    )
    assignment_tasks = (await db_session.execute(tasks_statement)).scalars().all()
    task_ids = [task.id for task in assignment_tasks if task.id is not None]
    if not assignment_tasks or not task_ids:
        raise HTTPException(
            status_code=400,
            detail="老師正在準備題目，暫時不能提交這份作業。",
        )

    task_submissions_statement = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.user_id == current_user.id,
        AssignmentTaskSubmission.assignment_task_id.in_(task_ids),  # type: ignore[attr-defined]
    )
    task_submissions = (await db_session.execute(task_submissions_statement)).scalars().all()
    task_submissions_by_task_id = {
        task_submission.assignment_task_id: task_submission
        for task_submission in task_submissions
    }
    missing_saved_tasks = [
        task for task in assignment_tasks
        if task.id is None or task.id not in task_submissions_by_task_id
    ]
    if missing_saved_tasks:
        raise HTTPException(
            status_code=400,
            detail=(
                "請先按「儲存本題答案」保存每題答案，再提交批改。"
                f"尚有 {len(missing_saved_tasks)} 題未儲存"
                f"{_student_task_issue_detail(missing_saved_tasks)}。"
            ),
        )

    incomplete_tasks = [
        task for task in assignment_tasks
        if not _is_assignment_task_submission_complete(
            task,
            task_submissions_by_task_id.get(task.id),
        )
    ]
    if incomplete_tasks:
        raise HTTPException(
            status_code=400,
            detail=(
                "請先完成所有題目的答案，再提交批改。"
                f"尚有 {len(incomplete_tasks)} 題未作答"
                f"{_student_task_issue_detail(incomplete_tasks)}。"
            ),
        )

    # Either reuse the existing PENDING row (retry path) or create a fresh
    # submission. On the retry path we keep the original
    # assignmentusersubmission_uuid so external systems that already store a
    # reference don't break. The creation_date IS refreshed to the time of
    # this new attempt — the teacher's submissions list sorts by submitted-at
    # and a stale original date would put a brand-new retry at the bottom
    # next to old submissions.
    submission_status = _submission_status_for_due_date(assignment)
    if assignment_user_submission:
        assignment_user_submission.submission_status = submission_status
        assignment_user_submission.grade = 0
        assignment_user_submission.teacher_review_status = "pending"
        assignment_user_submission.creation_date = str(datetime.now())
        assignment_user_submission.update_date = str(datetime.now())
    else:
        assignment_user_submission = AssignmentUserSubmission(
            user_id=current_user.id,
            assignment_id=assignment.id,  # type: ignore
            grade=0,
            assignmentusersubmission_uuid=str(f"assignmentusersubmission_{uuid4()}"),
            submission_status=submission_status,
            teacher_review_status="pending",
            attempt_number=1,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )

    # Insert Assignment User Submission in DB
    db_session.add(assignment_user_submission)
    await db_session.commit()

    # Track assignment submission. attempt_number lets downstream consumers
    # (analytics, webhooks) tell a retry resubmit from the original — without
    # it, retries would silently double-count.
    submitted_attempt_number = int(assignment_user_submission.attempt_number or 1)
    await track(
        event_name=analytics_events.ASSIGNMENT_SUBMITTED,
        org_id=course.org_id,
        user_id=current_user.id,
        properties={
            "assignment_uuid": assignment_uuid,
            "course_uuid": course.course_uuid,
            "attempt_number": submitted_attempt_number,
        },
    )
    await dispatch_webhooks(
        event_name=analytics_events.ASSIGNMENT_SUBMITTED,
        org_id=course.org_id,
        data={
            "user": {"user_uuid": current_user.user_uuid, "email": current_user.email, "username": current_user.username},
            "assignment": {"assignment_uuid": assignment_uuid},
            "course": {"course_uuid": course.course_uuid, "name": course.name},
            "attempt_number": submitted_attempt_number,
        },
    )

    # User
    statement = select(User).where(User.id == current_user.id)
    user = (await db_session.execute(statement)).scalars().first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Activity
    statement = select(Activity).where(Activity.id == assignment.activity_id)
    activity = (await db_session.execute(statement)).scalars().first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    # Add TrailStep
    trail = await check_trail_presence(
        org_id=course.org_id,
        user_id=user.id,  # type: ignore
        request=request,
        user=user,  # type: ignore
        db_session=db_session,
    )

    statement = select(TrailRun).where(
        TrailRun.trail_id == trail.id,
        TrailRun.course_id == course.id,
        TrailRun.user_id == user.id,
    )
    trailrun = (await db_session.execute(statement)).scalars().first()

    if not trailrun:
        trailrun = TrailRun(
            trail_id=trail.id if trail.id is not None else 0,
            course_id=course.id if course.id is not None else 0,
            org_id=course.org_id,
            user_id=user.id,  # type: ignore
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db_session.add(trailrun)
        await db_session.commit()
        await db_session.refresh(trailrun)

    statement = select(TrailStep).where(
        TrailStep.trailrun_id == trailrun.id,
        TrailStep.activity_id == activity.id,
        TrailStep.user_id == user.id,
    )
    trailstep = (await db_session.execute(statement)).scalars().first()

    if not trailstep:
        trailstep = TrailStep(
            trailrun_id=trailrun.id if trailrun.id is not None else 0,
            activity_id=activity.id if activity.id is not None else 0,
            course_id=course.id if course.id is not None else 0,
            trail_id=trail.id if trail.id is not None else 0,
            org_id=course.org_id,
            complete=True,
            teacher_verified=False,
            grade="",
            user_id=user.id, # type: ignore
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db_session.add(trailstep)
        await db_session.commit()
        await db_session.refresh(trailstep)
    else:
        # Existing trail step — either from prior progress saves, or because
        # the student just hit "Try again" (the retry endpoint flipped it to
        # incomplete). Re-flip it to complete now that the assignment is
        # back in SUBMITTED state. The first-submission branch above sets
        # complete=True; this keeps the reuse path consistent.
        trailstep.complete = True
        trailstep.update_date = str(datetime.now())
        db_session.add(trailstep)
        await db_session.commit()
        await db_session.refresh(trailstep)

    # Auto-grading path: if the teacher enabled auto_grading on this assignment
    # AND every task is in AUTO_GRADABLE_TASK_TYPES (explicit allow-list —
    # FILE_SUBMISSION and OTHER are deliberately excluded), compute the grade
    # now and flip the submission to GRADED. The student's per-task submissions
    # already exist at this point because they were persisted as the student
    # worked through the tasks; we just sum them and run them through the
    # shared grading helper. For SHORT_ANSWER and NUMBER_ANSWER, the helper
    # re-verifies the student's answer server-side so client-side tampering
    # is caught.
    if _coerce_simple_answer_bool(assignment.auto_grading):
        all_auto_gradable = bool(assignment_tasks) and all(
            t.assignment_type in AUTO_GRADABLE_TASK_TYPES for t in assignment_tasks
        )

        if all_auto_gradable:
            await _apply_grade_and_finalize(
                assignment=assignment,
                course=course,
                user_id=int(current_user.id),
                assignment_user_submission=assignment_user_submission,
                db_session=db_session,
                overall_feedback=None,
                auto_graded=True,
            )
            # Ensure trailstep reflects completion (create_assignment_submission
            # above already created it with complete=True, but if one already
            # existed from a previous state we make sure it's marked done).
            trailstep.complete = True
            trailstep.update_date = str(datetime.now())
            db_session.add(trailstep)
            await db_session.commit()

    # Check if all activities in the course are completed and create certificate if so
    if course and course.id and user and user.id:
        await check_course_completion_and_create_certificate(
            request, user.id, course.id, db_session
        )

    # return assignment user submission read
    return AssignmentUserSubmissionRead.model_validate(assignment_user_submission)


async def read_assignment_submissions(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
):
    _block_api_tokens(current_user)
    # Find assignment
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Check if user has instructor/admin privileges on this course
    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )

    # Compute the assignment-level max_grade once so every row can render a
    # formatted display_grade (e.g. "A", "85/100") rather than just the raw
    # integer sum from AssignmentUserSubmission.grade. Without this the
    # submissions list shows "80" while the evaluate modal and the student's
    # own view show "B" / "80/100" — three places, three formats.
    tasks_statement = select(AssignmentTask).where(
        AssignmentTask.assignment_id == assignment.id
    )
    assignment_tasks = (await db_session.execute(tasks_statement)).scalars().all()
    max_grade = sum(int(t.max_grade_value or 0) for t in assignment_tasks)
    all_auto_gradable = bool(assignment_tasks) and all(
        t.assignment_type in AUTO_GRADABLE_TASK_TYPES for t in assignment_tasks
    )
    remediation_by_submission_user: dict[tuple[int, int], AssignmentRemediationPractice] = {}
    remediation_practices = (
        await db_session.execute(
            select(AssignmentRemediationPractice).where(
                AssignmentRemediationPractice.assignment_id == assignment.id
            )
        )
    ).scalars().all()
    for practice in remediation_practices:
        key = (int(practice.assignment_submission_id), int(practice.user_id))
        remediation_by_submission_user[key] = _latest_remediation_practice(
            remediation_by_submission_user.get(key),
            practice,
        )

    def apply_student_fields(row: dict, user: User | None) -> dict:
        if user:
            row["student_name"] = _display_user_name(user)
            row["student_email"] = user.email
            row["student_username"] = user.username
            row["student_user_uuid"] = user.user_uuid
            row["student_avatar_image"] = user.avatar_image
        return row

    def build_submission_row(sub: AssignmentUserSubmission, user: User | None = None) -> dict:
        row = AssignmentUserSubmissionRead.model_validate(sub).model_dump()
        remediation_practice = remediation_by_submission_user.get(
            (int(sub.id), int(sub.user_id))
        )
        row["teacher_review_status"] = _teacher_review_state(
            assignment,
            sub,
            all_auto_gradable,
        )
        if sub.submission_status == AssignmentUserSubmissionStatus.GRADED:
            row["grade_display"] = compute_assignment_grade(
                int(sub.grade or 0),
                max_grade,
                assignment.grading_type,
            )
        else:
            row["grade_display"] = None
        row["max_grade"] = max_grade
        row["score_policy"] = assignment.score_policy or "highest"
        row["remediation"] = _remediation_summary_for_submission(
            sub,
            max_grade,
            remediation_practice,
        )
        row["expected_submission"] = True
        return apply_student_fields(row, user)

    def build_missing_submission_row(user_id: int, user: User | None) -> dict:
        return apply_student_fields({
            "id": None,
            "creation_date": None,
            "update_date": None,
            "assignmentusersubmission_uuid": f"missing_{assignment.assignment_uuid}_{user_id}",
            "submission_status": AssignmentUserSubmissionStatus.NOT_SUBMITTED.value,
            "grade": 0,
            "overall_feedback": None,
            "teacher_review_status": "missing",
            "attempt_number": 0,
            "best_grade": 0,
            "best_attempt_number": 0,
            "user_id": user_id,
            "assignment_id": assignment.id,
            "max_grade": max_grade,
            "score_policy": assignment.score_policy or "highest",
            "grade_display": None,
            "remediation": _remediation_summary_for_submission(None, max_grade, None),
            "expected_submission": True,
        }, user)

    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.assignment_id == assignment.id
    )

    if not is_instructor:
        statement = statement.where(
            AssignmentUserSubmission.user_id == current_user.id
        ).limit(limit).offset(offset)
        return [
            build_submission_row(sub)
            for sub in (await db_session.execute(statement)).scalars().all()
        ]

    submissions = (await db_session.execute(statement)).scalars().all()
    submissions_by_user_id: dict[int, AssignmentUserSubmission] = {}
    for submission in submissions:
        user_id = int(submission.user_id)
        submissions_by_user_id[user_id] = _latest_assignment_submission(
            submissions_by_user_id.get(user_id),
            submission,
        )

    learners_by_id = await _read_org_learners(course.org_id, db_session)
    target_usergroup_ids = set(_normalize_int_list(assignment.target_usergroup_ids))
    groups_by_id, members_by_group_id = await _read_usergroup_context(
        course.org_id,
        target_usergroup_ids,
        db_session,
    )
    if _assignment_has_valid_target_usergroups(assignment, groups_by_id):
        expected_user_ids = _expected_user_ids_for_assignment(
            assignment,
            learners_by_id,
            members_by_group_id,
            None,
        )
    else:
        expected_user_ids = []
    visible_user_ids = sorted(set(expected_user_ids).union(submissions_by_user_id.keys()))

    rows: list[dict] = []
    for user_id in visible_user_ids:
        submission = submissions_by_user_id.get(user_id)
        if submission:
            rows.append(build_submission_row(submission, learners_by_id.get(user_id)))
        elif user_id in learners_by_id:
            rows.append(build_missing_submission_row(user_id, learners_by_id.get(user_id)))

    return rows[offset : offset + limit]


async def read_user_assignment_submissions(
    request: Request,
    assignment_uuid: str,
    user_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Find assignment
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Ownership check: non-instructors may only read their own submissions
    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )
    if not is_instructor and int(user_id) != int(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only view your own submissions",
        )
    if not is_instructor:
        await _validate_student_can_read_assignment(assignment, current_user, db_session)

    # Find assignments tasks for an assignment
    tasks_statement = select(AssignmentTask).where(
        AssignmentTask.assignment_id == assignment.id
    )
    assignment_tasks = (await db_session.execute(tasks_statement)).scalars().all()
    max_grade = sum(int(task.max_grade_value or 0) for task in assignment_tasks)

    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.assignment_id == assignment.id,
        AssignmentUserSubmission.user_id == user_id,
    )

    # return assignment tasks read
    rows = []
    for assignment_user_submission in (await db_session.execute(statement)).scalars().all():
        row = AssignmentUserSubmissionRead.model_validate(assignment_user_submission).model_dump()
        row["max_grade"] = max_grade
        row["score_policy"] = assignment.score_policy or "highest"
        rows.append(row)
    return rows


async def read_user_assignment_submissions_me(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    return await read_user_assignment_submissions(
        request,
        assignment_uuid,
        current_user.id,
        current_user,
        db_session,
    )


async def update_assignment_submission(
    request: Request,
    user_id: int,
    assignment_uuid: str,
    assignment_user_submission_object: AssignmentUserSubmissionCreate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if assignment user submission exists (scoped to this specific assignment)
    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.user_id == user_id,
        AssignmentUserSubmission.assignment_id == assignment.id,
    )
    assignment_user_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_user_submission:
        raise HTTPException(
            status_code=404,
            detail="找不到學生提交記錄",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Check if user is an instructor/admin (has UPDATE permission)
    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )

    if is_instructor:
        # Instructors/admins can update any submission (e.g., for grading)
        await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)
    else:
        # Regular users need READ access and can only update their own submissions
        await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)
        if str(assignment_user_submission.user_id) != str(current_user.id):
            raise HTTPException(
                status_code=403,
                detail="You can only update your own submissions",
            )

    # Update only the fields that were passed in
    for var, value in vars(assignment_user_submission_object).items():
        if value is not None:
            setattr(assignment_user_submission, var, value)
    assignment_user_submission.update_date = str(datetime.now())

    # Insert Assignment User Submission in DB
    db_session.add(assignment_user_submission)
    await db_session.commit()
    await db_session.refresh(assignment_user_submission)

    # return assignment user submission read
    return AssignmentUserSubmissionRead.model_validate(assignment_user_submission)


async def delete_assignment_submission(
    request: Request,
    user_id: int,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if assignment user submission exists
    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.user_id == user_id,
        AssignmentUserSubmission.assignment_id == assignment.id,
    )
    assignment_user_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_user_submission:
        raise HTTPException(
            status_code=404,
            detail="找不到學生提交記錄",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.DELETE)

    # Rejecting a submission means the student is no longer "done" with this
    # activity — reset the TrailStep so the activity is no longer complete,
    # clear the teacher-verification flag, and drop any stored grade string.
    # Leave the per-task AssignmentTaskSubmission rows intact so the student
    # keeps the work they did and can edit + resubmit rather than starting
    # from scratch.
    trailstep_statement = select(TrailStep).where(
        TrailStep.activity_id == assignment.activity_id,
        TrailStep.user_id == user_id,
    )
    trailstep = (await db_session.execute(trailstep_statement)).scalars().first()
    if trailstep:
        trailstep.complete = False
        trailstep.teacher_verified = False
        trailstep.grade = ""
        trailstep.update_date = str(datetime.now())
        db_session.add(trailstep)

    # If a course certificate was already issued to this user (the activity
    # was previously counted as complete and this was the final one), pull it
    # now — the student can't hold a certificate while an assignment that
    # gated it is in a rejected state. A new certificate will be re-issued
    # automatically once the rework is accepted.
    cert_statement = select(Certifications).where(
        Certifications.course_id == course.id
    )
    certification = (await db_session.execute(cert_statement)).scalars().first()
    if certification:
        cert_user_statement = select(CertificateUser).where(
            CertificateUser.user_id == user_id,
            CertificateUser.certification_id == certification.id,
        )
        cert_user = (await db_session.execute(cert_user_statement)).scalars().first()
        if cert_user:
            await db_session.delete(cert_user)

    # Delete Assignment User Submission (so the student can create a new one)
    await db_session.delete(assignment_user_submission)
    await db_session.commit()

    return {"message": "學生提交記錄已刪除"}


async def retry_assignment_submission(
    request: Request,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    """
    Reopen the current user's submission for ``assignment_uuid`` so they can
    correct and submit it again. The teacher must have opted in via ``allow_retries``;
    we additionally bound the number of retries with ``max_retries`` (0
    means unlimited).

    On retry we keep the AssignmentUserSubmission row and per-task answers
    in place so students can correct their previous work instead of starting
    over. Task scores/feedback are cleared, the row is flipped to PENDING
    with grade=0, the matching TrailStep is reset to incomplete, and any
    issued course certificate is revoked. The student then edits the saved
    answers and submits again, at which point ``create_assignment_submission``
    transitions the existing PENDING row to SUBMITTED.
    """
    _block_api_tokens(current_user)

    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()
    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    if not assignment.allow_retries:
        raise HTTPException(
            status_code=403,
            detail="這份作業未開放重做",
        )

    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()
    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Only READ permission is required: the student is rescheduling their
    # own work, not editing the assignment configuration.
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Row-level lock on the user's submission so two concurrent retries can't
    # both read attempt_number=N, pass the cap check, and increment to N+1 —
    # which would let students sneak past max_retries on a double-click. The
    # lock is released on commit/rollback at the end of the function.
    # SQLite (used in tests) ignores FOR UPDATE and falls back to its own
    # locking, which is single-writer anyway.
    statement = (
        select(AssignmentUserSubmission)
        .where(
            AssignmentUserSubmission.user_id == current_user.id,
            AssignmentUserSubmission.assignment_id == assignment.id,
        )
        .with_for_update()
    )
    assignment_user_submission = (await db_session.execute(statement)).scalars().first()
    if not assignment_user_submission:
        raise HTTPException(
            status_code=404,
            detail="找不到學生提交記錄",
        )

    # Only graded submissions are eligible to be retried. Retrying a row
    # that is still SUBMITTED would silently throw away the student's
    # pending work before the teacher even sees it.
    if assignment_user_submission.submission_status != AssignmentUserSubmissionStatus.GRADED:
        raise HTTPException(
            status_code=400,
            detail="只有已批改的提交可以重做",
        )

    # Enforce the attempt cap. max_retries=0 means unlimited; otherwise the
    # current attempt_number must be strictly less than max_retries so the
    # increment below stays within bounds.
    current_attempt = int(assignment_user_submission.attempt_number or 1)
    max_attempts = int(assignment.max_retries or 0)
    if max_attempts and current_attempt >= max_attempts:
        raise HTTPException(
            status_code=403,
            detail="沒有剩餘重做次數",
        )

    # Keep saved answers so retry behaves like learning correction: students
    # can see what they wrote, change the wrong parts, then resubmit. Clear
    # grade/feedback so the pending attempt does not display stale results.
    task_ids_statement = select(AssignmentTask.id).where(
        AssignmentTask.assignment_id == assignment.id
    )
    task_ids = [tid for tid in (await db_session.execute(task_ids_statement)).scalars().all() if tid is not None]
    if task_ids:
        existing_submissions = (await db_session.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.user_id == current_user.id,
                AssignmentTaskSubmission.assignment_task_id.in_(task_ids),  # type: ignore[attr-defined]
            )
        )).scalars().all()
        for ts in existing_submissions:
            ts.grade = 0
            ts.task_submission_grade_feedback = ""
            ts.update_date = str(datetime.now())
            db_session.add(ts)

    # Mark the activity incomplete again so the student's progress bar
    # reflects the in-flight retry rather than the (now stale) previous
    # completion.
    trailstep_statement = select(TrailStep).where(
        TrailStep.activity_id == assignment.activity_id,
        TrailStep.user_id == int(current_user.id),
    )
    trailstep = (await db_session.execute(trailstep_statement)).scalars().first()
    if trailstep:
        trailstep.complete = False
        trailstep.teacher_verified = False
        trailstep.grade = ""
        trailstep.update_date = str(datetime.now())
        db_session.add(trailstep)

    # Revoke any course certificate previously issued. If this assignment
    # was the final activity, a new certificate will be re-issued once the
    # retry attempt is graded and the course is again fully complete.
    cert_statement = select(Certifications).where(
        Certifications.course_id == course.id
    )
    certification = (await db_session.execute(cert_statement)).scalars().first()
    if certification:
        cert_user_statement = select(CertificateUser).where(
            CertificateUser.user_id == int(current_user.id),
            CertificateUser.certification_id == certification.id,
        )
        cert_user = (await db_session.execute(cert_user_statement)).scalars().first()
        if cert_user:
            await db_session.delete(cert_user)

    # Reopen the submission row in place. Keeping the same uuid means
    # downstream consumers (analytics, webhooks) don't see a "new"
    # submission appear; the attempt_number is the canonical signal that
    # this is the next attempt.
    if assignment_user_submission.submission_status == AssignmentUserSubmissionStatus.GRADED:
        previous_grade = int(assignment_user_submission.grade or 0)
        if previous_grade >= int(assignment_user_submission.best_grade or 0):
            assignment_user_submission.best_grade = previous_grade
            assignment_user_submission.best_attempt_number = current_attempt
    assignment_user_submission.submission_status = AssignmentUserSubmissionStatus.PENDING
    assignment_user_submission.grade = 0
    assignment_user_submission.overall_feedback = None
    assignment_user_submission.teacher_review_status = "pending"
    assignment_user_submission.attempt_number = current_attempt + 1
    assignment_user_submission.update_date = str(datetime.now())
    db_session.add(assignment_user_submission)

    await db_session.commit()
    await db_session.refresh(assignment_user_submission)

    return {
        "message": "已開放修改答案，可重新提交",
        "attempt_number": assignment_user_submission.attempt_number,
        "max_retries": max_attempts,
        "submission": AssignmentUserSubmissionRead.model_validate(
            assignment_user_submission
        ).model_dump(),
    }


## > Assignments Submissions Grading


async def _apply_grade_and_finalize(
    assignment: Assignment,
    course: Course,
    user_id: int,
    assignment_user_submission: AssignmentUserSubmission,
    db_session: AsyncSession,
    overall_feedback: str | None = None,
    auto_graded: bool = False,
) -> dict:
    """
    Core grading logic shared by manual and auto-grade flows. Computes the
    final grade from existing per-task submissions, persists it with status
    GRADED, dispatches the webhook, and returns the enriched grade dict
    (including per-task breakdown).

    IMPORTANT: This helper does NO permission checks. Callers must enforce
    access control before calling it. It exists so that both the teacher's
    manual grading endpoint (UPDATE permission) and the student's auto-grade
    path (READ permission, self-grading under teacher-configured auto_grading)
    can share one implementation.
    """
    # Compute max_grade from the current task configuration
    tasks_statement = select(AssignmentTask).where(
        AssignmentTask.assignment_id == assignment.id
    )
    assignment_tasks = (await db_session.execute(tasks_statement)).scalars().all()
    max_grade = 0
    for task in assignment_tasks:
        max_grade += int(task.max_grade_value or 0)
    all_auto_gradable = bool(assignment_tasks) and all(
        task.assignment_type in AUTO_GRADABLE_TASK_TYPES for task in assignment_tasks
    )
    retry_feedback_note = _assignment_retry_feedback_note(
        assignment,
        assignment_user_submission,
    )

    # Load this user's per-task submissions (keyed by task_id so we can build
    # both the sum and the per-task breakdown).
    task_ids = [task.id for task in assignment_tasks if task.id is not None]
    task_submissions_by_task_id: dict = {}
    if task_ids:
        ts_statement = select(AssignmentTaskSubmission).where(
            AssignmentTaskSubmission.user_id == user_id,
            AssignmentTaskSubmission.assignment_task_id.in_(task_ids),  # type: ignore[attr-defined]
        )
        for ts in (await db_session.execute(ts_statement)).scalars().all():
            task_submissions_by_task_id[ts.assignment_task_id] = ts

    # Server-side re-verification for task types where we don't trust the
    # client's computed grade (SHORT_ANSWER, NUMBER_ANSWER). If the
    # verified grade differs from what the client submitted, overwrite it
    # so tampering is caught and future reads see the correct number.
    for task in assignment_tasks:
        ts = task_submissions_by_task_id.get(task.id)
        if task.assignment_type == AssignmentTaskTypeEnum.ESSAY and not auto_graded:
            # Teacher review should confirm or adjust the saved essay score,
            # not re-run AI grading or consume another AI/rate-limit credit.
            verified = int(ts.grade or 0) if ts is not None else None
        else:
            verified = await _server_verified_task_grade(
                task,
                ts,
                org_id=course.org_id,
                user_id=user_id,
                db_session=db_session,
            )
        if verified is None and task.assignment_type == AssignmentTaskTypeEnum.ESSAY and auto_graded:
            raise HTTPException(
                status_code=503,
                detail="作文 AI 批改暫時未能完成，提交已保存。請稍後再試，或由老師在提交記錄中覆核批改。",
            )
        if verified is None or ts is None:
            continue
        if int(ts.grade or 0) != verified:
            ts.grade = verified
            db_session.add(ts)
        existing_feedback = (ts.task_submission_grade_feedback or "").strip()
        if not existing_feedback or existing_feedback.startswith("Server-verified:"):
            ts.task_submission_grade_feedback = _server_verified_task_feedback(
                verified,
                int(task.max_grade_value or 0),
                retry_note=retry_feedback_note,
            )
            db_session.add(ts)

    raw_grade = 0
    for ts in task_submissions_by_task_id.values():
        raw_grade += int(ts.grade or 0)

    # Only overwrite stored feedback when the caller explicitly provided one.
    # Passing None means "leave the existing note alone".
    if overall_feedback is not None:
        assignment_user_submission.overall_feedback = overall_feedback or None

    computed = compute_assignment_grade(
        raw_grade,
        max_grade,
        assignment.grading_type,
        overall_feedback=assignment_user_submission.overall_feedback,
    )

    computed["tasks"] = _build_tasks_breakdown(
        assignment_tasks,
        task_submissions_by_task_id,
        computed["passing_threshold"],
    )

    current_grade = int(computed["grade"] or 0)
    current_attempt = int(assignment_user_submission.attempt_number or 1)
    previous_best_grade = int(assignment_user_submission.best_grade or 0)
    if current_grade >= previous_best_grade:
        assignment_user_submission.best_grade = current_grade
        assignment_user_submission.best_attempt_number = current_attempt

    effective_grade = (
        int(assignment_user_submission.best_grade or 0)
        if assignment.score_policy == "highest"
        else current_grade
    )
    if assignment.score_policy == "highest":
        computed = compute_assignment_grade(
            effective_grade,
            max_grade,
            assignment.grading_type,
            overall_feedback=assignment_user_submission.overall_feedback,
        ) | {"tasks": computed["tasks"]}
    computed["current_attempt_grade"] = current_grade
    computed["best_grade"] = int(assignment_user_submission.best_grade or 0)
    computed["best_attempt_number"] = int(assignment_user_submission.best_attempt_number or 1)
    computed["score_policy"] = assignment.score_policy or "highest"

    # Persist the effective raw grade + flip status to GRADED in a single commit
    assignment_user_submission.grade = effective_grade
    assignment_user_submission.submission_status = AssignmentUserSubmissionStatus.GRADED
    has_essay_task = any(
        task.assignment_type == AssignmentTaskTypeEnum.ESSAY
        for task in assignment_tasks
    )
    needs_teacher_review = bool(assignment.teacher_review_required) or not all_auto_gradable or has_essay_task
    if not needs_teacher_review:
        assignment_user_submission.teacher_review_status = "not_required"
    elif auto_graded:
        assignment_user_submission.teacher_review_status = "pending"
    else:
        assignment_user_submission.teacher_review_status = "confirmed"
    computed["teacher_review_status"] = assignment_user_submission.teacher_review_status
    db_session.add(assignment_user_submission)
    await db_session.commit()
    await db_session.refresh(assignment_user_submission)

    await dispatch_webhooks(
        event_name="assignment_graded",
        org_id=course.org_id,
        data={
            "user_id": user_id,
            "assignment_uuid": assignment.assignment_uuid,
            "course_uuid": course.course_uuid,
            "grade": computed["grade"],
            "max_grade": computed["max_grade"],
            "percentage": computed["percentage"],
            "display_grade": computed["display_grade"],
            "letter_grade": computed["letter_grade"],
            "points_summary": computed["points_summary"],
            "passed": computed["passed"],
            "grading_type": computed["grading_type"],
            "overall_feedback": computed["overall_feedback"],
            "auto_graded": auto_graded,
        },
    )

    return computed


async def grade_assignment_submission(
    request: Request,
    user_id: int,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
    overall_feedback: str | None = None,
):
    _block_api_tokens(current_user)
    # SECURITY: This function should only be accessible by course owners or instructors
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # SECURITY: Require course ownership or instructor role for grading
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    # Check if assignment user submission exists
    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.user_id == user_id,
        AssignmentUserSubmission.assignment_id == assignment.id,
    )
    assignment_user_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_user_submission:
        raise HTTPException(
            status_code=404,
            detail="找不到學生提交記錄",
        )

    computed = await _apply_grade_and_finalize(
        assignment=assignment,
        course=course,
        user_id=user_id,
        assignment_user_submission=assignment_user_submission,
        db_session=db_session,
        overall_feedback=overall_feedback,
        auto_graded=False,
    )

    return {
        "message": f"學生提交已評分：{computed['display_grade']}",
        **computed,
    }


async def get_grade_assignment_submission(
    request: Request,
    user_id: int,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Check if assignment exists
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if course exists
    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Ownership check: non-instructors may only read their own grade
    is_instructor = await authorization_verify_based_on_roles(
        request, current_user.id, "update", course.course_uuid, db_session
    )
    if not is_instructor and str(user_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only view your own grade",
        )

    # Check if assignment user submission exists
    statement = select(AssignmentUserSubmission).where(
        AssignmentUserSubmission.user_id == user_id,
        AssignmentUserSubmission.assignment_id == assignment.id,
    )
    assignment_user_submission = (await db_session.execute(statement)).scalars().first()

    if not assignment_user_submission:
        raise HTTPException(
            status_code=404,
            detail="找不到學生提交記錄",
        )

    # Recompute max_grade from current task configuration. Doing this on read
    # (rather than storing a stale value) means instructor edits to task
    # max_grade_value are reflected immediately.
    tasks_statement = select(AssignmentTask).where(
        AssignmentTask.assignment_id == assignment.id
    )
    assignment_tasks = (await db_session.execute(tasks_statement)).scalars().all()
    max_grade = 0
    for task in assignment_tasks:
        max_grade += int(task.max_grade_value or 0)

    # Load this user's per-task submissions so the response can include a
    # per-task breakdown. The UI uses this to show "Task N · 85%" badges
    # instead of re-fetching one request per task.
    task_ids = [task.id for task in assignment_tasks if task.id is not None]
    task_submissions_by_task_id: dict = {}
    if task_ids:
        ts_statement = select(AssignmentTaskSubmission).where(
            AssignmentTaskSubmission.user_id == user_id,
            AssignmentTaskSubmission.assignment_task_id.in_(task_ids),  # type: ignore[attr-defined]
        )
        for ts in (await db_session.execute(ts_statement)).scalars().all():
            task_submissions_by_task_id[ts.assignment_task_id] = ts

    grade_obj = compute_assignment_grade(
        int(assignment_user_submission.grade or 0),
        max_grade,
        assignment.grading_type,
        overall_feedback=assignment_user_submission.overall_feedback,
    )
    grade_obj["tasks"] = _build_tasks_breakdown(
        assignment_tasks,
        task_submissions_by_task_id,
        grade_obj["passing_threshold"],
    )
    all_auto_gradable = bool(assignment_tasks) and all(
        task.assignment_type in AUTO_GRADABLE_TASK_TYPES for task in assignment_tasks
    )
    grade_obj["teacher_review_status"] = _teacher_review_state(
        assignment,
        assignment_user_submission,
        all_auto_gradable,
    )
    return grade_obj


async def mark_activity_as_done_for_user(
    request: Request,
    user_id: int,
    assignment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # SECURITY: This function should only be accessible by course owners or instructors
    # Get Assignment
    statement = select(Assignment).where(Assignment.assignment_uuid == assignment_uuid)
    assignment = (await db_session.execute(statement)).scalars().first()

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found",
        )

    # Check if activity exists
    statement = select(Activity).where(Activity.id == assignment.activity_id)
    activity = (await db_session.execute(statement)).scalars().first()

    statement = select(Course).where(Course.id == assignment.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # SECURITY: Require course ownership or instructor role for marking activities as done
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    # Check if user exists
    statement = select(User).where(User.id == user_id)
    user = (await db_session.execute(statement)).scalars().first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Check if user is enrolled in the course
    trailsteps = select(TrailStep).where(
        TrailStep.activity_id == activity.id,
        TrailStep.user_id == user_id,
    )
    trailstep = (await db_session.execute(trailsteps)).scalars().first()

    if not trailstep:
        raise HTTPException(
            status_code=404,
            detail="User not enrolled in the course",
        )

    # Mark activity as done
    trailstep.complete = True
    trailstep.update_date = str(datetime.now())

    # Insert TrailStep in DB
    db_session.add(trailstep)
    await db_session.commit()
    await db_session.refresh(trailstep)

    # Check if all activities in the course are completed and create certificate if so
    if course and course.id:
        await check_course_completion_and_create_certificate(
            request, user_id, course.id, db_session
        )

    # return OK
    return {"message": "Activity marked as done for user"}


async def get_assignments_from_course(
    request: Request,
    course_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    db_session: AsyncSession,
):
    _block_api_tokens(current_user)
    # Find course
    statement = select(Course).where(Course.course_uuid == course_uuid)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # Get Assignments
    statement = select(Assignment).where(Assignment.course_id == course.id)
    assignments = (await db_session.execute(statement)).scalars().all()

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # return assignments read
    return [_assignment_read(assignment) for assignment in assignments]
