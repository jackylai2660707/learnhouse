from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.db.courses.assignments import AssignmentTaskRead, AssignmentTaskTypeEnum
from src.db.question_bank import QuestionBankItemRead, QuestionBankVisibilityEnum


AutoGradableAssignmentType = Literal[
    "QUIZ",
    "FORM",
    "CODE",
    "SHORT_ANSWER",
    "NUMBER_ANSWER",
]


class GenerateAssignmentTasksRequest(BaseModel):
    assignment_uuid: str
    prompt: str = Field(min_length=8, max_length=6000)
    language: str = "zh"
    count: int = Field(default=5, ge=1, le=10)
    difficulty: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    question_types: List[AutoGradableAssignmentType] = Field(
        default_factory=lambda: ["QUIZ", "SHORT_ANSWER", "NUMBER_ANSWER", "CODE"]
    )
    include_images: bool = False
    save_to_question_bank: bool = False
    question_bank_category_id: Optional[int] = None
    question_bank_visibility: QuestionBankVisibilityEnum = QuestionBankVisibilityEnum.ORG
    question_bank_tags: List[str] = []


class GeneratedAssignmentTaskDraft(BaseModel):
    title: str
    description: str = ""
    hint: str = ""
    assignment_type: AssignmentTaskTypeEnum
    contents: Dict = Field(default_factory=dict)
    image_prompt: Optional[str] = None


class GenerateAssignmentTasksResponse(BaseModel):
    tasks: List[AssignmentTaskRead]
    question_bank_items: List[QuestionBankItemRead] = []
    warnings: List[str] = []
