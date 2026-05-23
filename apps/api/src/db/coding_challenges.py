from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Column, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class CodingChallengeSolutionVisibility(str, Enum):
    NEVER = "never"
    AFTER_PASS = "after_pass"
    ALWAYS = "always"


class CodingChallengeTestVisibility(str, Enum):
    VISIBLE = "visible"
    HIDDEN = "hidden"


class CodingChallenge(SQLModel, table=True):
    __tablename__ = "coding_challenge"
    __table_args__ = (
        UniqueConstraint("challenge_uuid", name="uq_coding_challenge_uuid"),
        Index("ix_coding_challenge_activity_block", "activity_id", "block_id"),
        Index("ix_coding_challenge_org_course", "org_id", "course_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    challenge_uuid: str = Field(index=True)
    external_id: Optional[str] = Field(default=None, index=True)
    source_template_uuid: Optional[str] = Field(default=None, index=True)
    tags: list = Field(default_factory=list, sa_column=Column(JSON))

    org_id: int = Field(sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), index=True))
    course_id: int = Field(sa_column=Column(Integer, ForeignKey("course.id", ondelete="CASCADE"), index=True))
    activity_id: int = Field(sa_column=Column(Integer, ForeignKey("activity.id", ondelete="CASCADE"), index=True))
    block_id: str = Field(index=True)

    required: bool = False
    archived: bool = False
    language_id: int = 71
    title: str = ""
    description: str = ""
    starter_code: str = ""
    solution_code: str = ""
    solution_visibility: CodingChallengeSolutionVisibility = CodingChallengeSolutionVisibility.AFTER_PASS
    hints: list = Field(default_factory=list, sa_column=Column(JSON))
    difficulty: str = "medium"
    time_limit_ms: int = 10000
    sqlite_db_path: str = ""
    additional_files: list = Field(default_factory=list, sa_column=Column(JSON))
    extra_metadata: dict = Field(default_factory=dict, sa_column=Column("metadata", JSONB))

    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class CodingChallengeTest(SQLModel, table=True):
    __tablename__ = "coding_challenge_test"
    __table_args__ = (
        Index("ix_coding_challenge_test_challenge_order", "challenge_id", "order"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    challenge_id: int = Field(sa_column=Column(Integer, ForeignKey("coding_challenge.id", ondelete="CASCADE"), index=True))
    test_uuid: str = Field(index=True)
    label: str = ""
    stdin: str = ""
    expected_stdout: str = ""
    visibility: CodingChallengeTestVisibility = CodingChallengeTestVisibility.VISIBLE
    order: int = 0
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class CodingChallengeSubmission(SQLModel, table=True):
    __tablename__ = "coding_challenge_submission"
    __table_args__ = (
        Index("ix_coding_challenge_submission_user_challenge", "user_id", "challenge_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    submission_uuid: str = Field(index=True)
    challenge_id: int = Field(sa_column=Column(Integer, ForeignKey("coding_challenge.id", ondelete="CASCADE"), index=True))
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True))
    activity_id: int = Field(sa_column=Column(Integer, ForeignKey("activity.id", ondelete="CASCADE"), index=True))
    course_id: int = Field(sa_column=Column(Integer, ForeignKey("course.id", ondelete="CASCADE"), index=True))
    org_id: int = Field(sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), index=True))

    attempt_number: int = 1
    language_id: int
    source_code: str
    passed: bool = False
    total_tests: int = 0
    passed_tests: int = 0
    visible_total_tests: int = 0
    visible_passed_tests: int = 0
    hidden_total_tests: int = 0
    hidden_passed_tests: int = 0
    results: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    execution_time_ms: Optional[int] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class CodingChallengeProgress(SQLModel, table=True):
    __tablename__ = "coding_challenge_progress"
    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_coding_challenge_progress_user"),
        Index("ix_coding_challenge_progress_user_activity", "user_id", "activity_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    challenge_id: int = Field(sa_column=Column(Integer, ForeignKey("coding_challenge.id", ondelete="CASCADE"), index=True))
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True))
    activity_id: int = Field(sa_column=Column(Integer, ForeignKey("activity.id", ondelete="CASCADE"), index=True))
    course_id: int = Field(sa_column=Column(Integer, ForeignKey("course.id", ondelete="CASCADE"), index=True))
    org_id: int = Field(sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), index=True))
    passed: bool = False
    first_passed_at: Optional[str] = None
    latest_submission_id: Optional[int] = Field(default=None, sa_column=Column(Integer, ForeignKey("coding_challenge_submission.id", ondelete="SET NULL")))
    attempt_count: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
