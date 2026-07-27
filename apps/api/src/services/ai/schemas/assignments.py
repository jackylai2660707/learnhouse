from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from src.db.courses.assignments import AssignmentTaskRead, AssignmentTaskTypeEnum
from src.db.question_bank import QuestionBankItemRead, QuestionBankVisibilityEnum


SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPES = ["QUIZ", "FORM", "SHORT_ANSWER"]
SIMPLE_AI_DEFAULT_TASK_COUNT = 3
SIMPLE_AI_MAX_TASK_COUNT = 3
SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPE_ALIASES = {
    "CHOICE": "QUIZ",
    "MCQ": "QUIZ",
    "MULTIPLE_CHOICE": "QUIZ",
    "MULTIPLECHOICE": "QUIZ",
    "選擇題": "QUIZ",
    "單選題": "QUIZ",
    "多項選擇題": "QUIZ",
    "选择题": "QUIZ",
    "单选题": "QUIZ",
    "多项选择题": "QUIZ",
    "FILL_BLANK": "FORM",
    "FILL_IN_BLANK": "FORM",
    "FILL_IN_THE_BLANK": "FORM",
    "FILLINTHEBLANK": "FORM",
    "BLANK": "FORM",
    "填空題": "FORM",
    "填充題": "FORM",
    "填空题": "FORM",
    "填充题": "FORM",
    "QA": "SHORT_ANSWER",
    "Q&A": "SHORT_ANSWER",
    "QUESTION_ANSWER": "SHORT_ANSWER",
    "SHORTANSWER": "SHORT_ANSWER",
    "問答": "SHORT_ANSWER",
    "問答題": "SHORT_ANSWER",
    "簡答": "SHORT_ANSWER",
    "簡答題": "SHORT_ANSWER",
    "短問答": "SHORT_ANSWER",
    "问答": "SHORT_ANSWER",
    "问答题": "SHORT_ANSWER",
    "简答": "SHORT_ANSWER",
    "简答题": "SHORT_ANSWER",
    "短问答": "SHORT_ANSWER",
}


class GenerateAssignmentTasksRequest(BaseModel):
    assignment_uuid: str
    prompt: str = Field(default="", max_length=6000)
    language: str = "zh"
    assignment_title: str = Field(default="", max_length=500)
    assignment_description: str = Field(default="", max_length=2000)
    subject: str = ""
    education_stage: str = ""
    grade_level: str = ""
    unit: str = ""
    learning_objectives: List[str] = Field(default_factory=list)
    count: int = Field(default=SIMPLE_AI_DEFAULT_TASK_COUNT, ge=1, le=SIMPLE_AI_MAX_TASK_COUNT)
    difficulty: str = "beginner"
    question_types: List[str] = Field(default_factory=lambda: SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPES.copy())
    include_images: bool = False
    save_to_question_bank: bool = False
    question_bank_category_id: Optional[int] = None
    question_bank_visibility: QuestionBankVisibilityEnum = QuestionBankVisibilityEnum.ORG
    question_bank_tags: List[str] = Field(default_factory=list)

    @field_validator("include_images", mode="before")
    @classmethod
    def force_no_images_for_simple_pilot(cls, _value: object) -> bool:
        return False

    @field_validator("difficulty", mode="before")
    @classmethod
    def force_simple_difficulty(cls, _value: object) -> str:
        return "beginner"

    @field_validator("count", mode="before")
    @classmethod
    def clamp_count_for_simple_pilot(cls, value: object) -> int:
        try:
            count = int(value)
        except (TypeError, ValueError):
            return SIMPLE_AI_DEFAULT_TASK_COUNT
        return max(1, min(count, SIMPLE_AI_MAX_TASK_COUNT))

    @field_validator("question_types", mode="before")
    @classmethod
    def normalize_question_types(cls, value: object) -> List[str]:
        if value is None:
            return SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPES.copy()
        if isinstance(value, str):
            raw_items = [
                item
                for item in value.replace("，", ",").replace("、", ",").split(",")
                if item.strip()
            ]
        else:
            try:
                raw_items = list(value)  # type: ignore[arg-type]
            except TypeError:
                raw_items = []

        normalized: List[str] = []
        for item in raw_items:
            if isinstance(item, dict):
                item = (
                    item.get("value")
                    or item.get("type")
                    or item.get("assignment_type")
                    or item.get("id")
                    or item.get("label")
                    or item.get("name")
                    or ""
                )
            question_type = str(getattr(item, "value", item)).strip().upper()
            question_type_key = question_type.replace(" ", "_").replace("-", "_")
            question_type = SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPE_ALIASES.get(
                question_type_key,
                SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPE_ALIASES.get(question_type, question_type_key),
            )
            if (
                question_type in SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPES
                and question_type not in normalized
            ):
                normalized.append(question_type)

        return normalized or SIMPLE_AUTO_GRADABLE_ASSIGNMENT_TYPES.copy()


class GeneratedAssignmentTaskDraft(BaseModel):
    title: str
    description: str = ""
    hint: str = ""
    assignment_type: AssignmentTaskTypeEnum
    contents: Dict = Field(default_factory=dict)


class GenerateAssignmentTasksResponse(BaseModel):
    tasks: List[AssignmentTaskRead]
    question_bank_items: List[QuestionBankItemRead] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
