from enum import Enum
from typing import Dict, List, Optional

from sqlalchemy import JSON, Column, ForeignKey, Index, Text
from sqlmodel import Field, SQLModel

from src.db.courses.assignments import AssignmentTaskRead, AssignmentTaskTypeEnum


class QuestionBankVisibilityEnum(str, Enum):
    PRIVATE = "PRIVATE"
    ORG = "ORG"


class QuestionBankCategoryBase(SQLModel):
    name: str
    description: str = ""
    color: str = "#111827"
    org_id: int
    parent_category_id: Optional[int] = None


class QuestionBankCategoryCreate(QuestionBankCategoryBase):
    pass


class QuestionBankCategoryRead(QuestionBankCategoryBase):
    id: int
    category_uuid: str
    created_by_user_id: Optional[int] = None
    creation_date: str
    update_date: str


class QuestionBankCategoryUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    parent_category_id: Optional[int] = None


class QuestionBankCategory(QuestionBankCategoryBase, table=True):
    __table_args__ = (
        Index("ix_questionbankcategory_org_id", "org_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    category_uuid: str = Field(default="", unique=True, index=True)
    created_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column("created_by_user_id", ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    )
    creation_date: str = ""
    update_date: str = ""

    org_id: int = Field(
        sa_column=Column("org_id", ForeignKey("organization.id", ondelete="CASCADE"))
    )
    parent_category_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            "parent_category_id",
            ForeignKey("questionbankcategory.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


class QuestionBankItemBase(SQLModel):
    title: str
    description: str = ""
    hint: str = ""
    assignment_type: AssignmentTaskTypeEnum
    contents: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    difficulty: str = "intermediate"
    visibility: QuestionBankVisibilityEnum = QuestionBankVisibilityEnum.ORG
    category_id: Optional[int] = None
    org_id: int
    source_assignment_task_uuid: Optional[str] = None
    reference_file: Optional[str] = None


class QuestionBankItemCreate(QuestionBankItemBase):
    pass


class QuestionBankItemRead(QuestionBankItemBase):
    id: int
    item_uuid: str
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    usage_count: int = 0
    creation_date: str
    update_date: str


class QuestionBankItemUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    hint: Optional[str] = None
    assignment_type: Optional[AssignmentTaskTypeEnum] = None
    contents: Optional[Dict] = None
    tags: Optional[List[str]] = None
    difficulty: Optional[str] = None
    visibility: Optional[QuestionBankVisibilityEnum] = None
    category_id: Optional[int] = None
    reference_file: Optional[str] = None


class QuestionBankItem(QuestionBankItemBase, table=True):
    __table_args__ = (
        Index("ix_questionbankitem_org_id", "org_id"),
        Index("ix_questionbankitem_org_type", "org_id", "assignment_type"),
        Index("ix_questionbankitem_org_category", "org_id", "category_id"),
        Index("ix_questionbankitem_created_by", "created_by_user_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    item_uuid: str = Field(default="", unique=True, index=True)
    explanation: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    usage_count: int = 0
    created_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column("created_by_user_id", ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    )
    updated_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column("updated_by_user_id", ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    creation_date: str = ""
    update_date: str = ""

    org_id: int = Field(
        sa_column=Column("org_id", ForeignKey("organization.id", ondelete="CASCADE"))
    )
    category_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            "category_id",
            ForeignKey("questionbankcategory.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


class SaveAssignmentTaskToQuestionBankRequest(SQLModel):
    assignment_task_uuid: str
    category_id: Optional[int] = None
    tags: List[str] = []
    difficulty: str = "intermediate"
    visibility: QuestionBankVisibilityEnum = QuestionBankVisibilityEnum.ORG


class AddQuestionBankItemToAssignmentRequest(SQLModel):
    assignment_uuid: str


class AddQuestionBankItemToAssignmentResponse(SQLModel):
    task: AssignmentTaskRead
    item: QuestionBankItemRead
