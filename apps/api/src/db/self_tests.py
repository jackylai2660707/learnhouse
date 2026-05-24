from enum import Enum
from typing import Dict, List, Optional

from sqlalchemy import JSON, Column, ForeignKey, Index
from sqlmodel import Field, SQLModel

from src.db.courses.assignments import AssignmentTaskTypeEnum


class SelfTestAttemptStatus(str, Enum):
    STARTED = "STARTED"
    SUBMITTED = "SUBMITTED"
    REVIEWED = "REVIEWED"


class StartSelfTestRequest(SQLModel):
    org_id: int
    question_count: int = 5
    category_id: Optional[int] = None
    tags: List[str] = []
    assignment_types: List[AssignmentTaskTypeEnum] = []


class SubmitSelfTestAnswer(SQLModel):
    attempt_question_uuid: str
    answer: Dict = Field(default_factory=dict)


class SubmitSelfTestRequest(SQLModel):
    answers: List[SubmitSelfTestAnswer]


class ReviewSelfTestAttemptRequest(SQLModel):
    teacher_score: Optional[int] = None
    teacher_feedback: str = ""
    counts_for_grade: bool = True


class SelfTestAttemptBase(SQLModel):
    org_id: int
    user_id: int
    category_id: Optional[int] = None
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    question_count: int = 0
    status: SelfTestAttemptStatus = SelfTestAttemptStatus.STARTED
    score: int = 0
    max_score: int = 0
    percentage: float = 0
    teacher_score: Optional[int] = None
    teacher_feedback: str = ""
    counts_for_grade: bool = False
    reviewed_by_user_id: Optional[int] = None


class SelfTestAttempt(SelfTestAttemptBase, table=True):
    __table_args__ = (
        Index("ix_selftestattempt_org_user", "org_id", "user_id"),
        Index("ix_selftestattempt_org_status", "org_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    attempt_uuid: str = Field(default="", unique=True, index=True)
    creation_date: str = ""
    update_date: str = ""
    submitted_at: Optional[str] = None
    reviewed_at: Optional[str] = None

    org_id: int = Field(
        sa_column=Column("org_id", ForeignKey("organization.id", ondelete="CASCADE"))
    )
    user_id: int = Field(
        sa_column=Column("user_id", ForeignKey("user.id", ondelete="CASCADE"))
    )
    reviewed_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column("reviewed_by_user_id", ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )


class SelfTestAttemptQuestionBase(SQLModel):
    question_bank_item_id: Optional[int] = None
    question_bank_item_uuid: Optional[str] = None
    title: str
    description: str = ""
    hint: str = ""
    assignment_type: AssignmentTaskTypeEnum
    contents: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    answer: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    grade: int = 0
    max_grade: int = 100
    feedback: str = ""
    display_order: int = 0


class SelfTestAttemptQuestion(SelfTestAttemptQuestionBase, table=True):
    __table_args__ = (
        Index("ix_selftestattemptquestion_attempt_id", "attempt_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    attempt_question_uuid: str = Field(default="", unique=True, index=True)
    attempt_id: int = Field(
        sa_column=Column("attempt_id", ForeignKey("selftestattempt.id", ondelete="CASCADE"))
    )


class SelfTestAttemptQuestionRead(SelfTestAttemptQuestionBase):
    id: int
    attempt_question_uuid: str
    contents: Dict = Field(default_factory=dict)


class SelfTestAttemptRead(SelfTestAttemptBase):
    id: int
    attempt_uuid: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    creation_date: str
    update_date: str
    submitted_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    questions: List[SelfTestAttemptQuestionRead] = []
