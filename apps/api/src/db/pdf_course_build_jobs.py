from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PDFBuildStage(str, Enum):
    EXTRACTING = "extracting"
    PLANNING = "planning"
    CREATING = "creating"
    INDEXING = "indexing"
    DONE = "done"
    FAILED = "failed"


class PDFBuildCreditState(str, Enum):
    PENDING = "pending"
    RESERVED = "reserved"
    CONSUMED = "consumed"
    REFUNDED = "refunded"


class PDFCourseBuildJob(SQLModel, table=True):
    """Private durable ledger for a PDF-to-course build.

    ``options`` and ``plan_checkpoint`` are deliberately not part of any API
    response model. They may contain teacher instructions and generated course
    content and are worker-only state.
    """

    __tablename__ = "pdf_course_build_job"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "creator_user_id",
            "idempotency_key",
            name="uq_pdf_build_org_creator_idempotency",
        ),
        CheckConstraint(
            "stage IN ('extracting','planning','creating','indexing','done','failed')",
            name="ck_pdf_build_stage",
        ),
        CheckConstraint(
            "credit_state IN ('pending','reserved','consumed','refunded')",
            name="ck_pdf_build_credit_state",
        ),
        CheckConstraint("progress_current >= 0", name="ck_pdf_build_progress_current"),
        CheckConstraint("progress_total >= 0", name="ck_pdf_build_progress_total"),
        CheckConstraint("attempts >= 0", name="ck_pdf_build_attempts"),
        Index("ix_pdf_build_org_creator_created", "org_id", "creator_user_id", "created_at"),
        Index("ix_pdf_build_org_stage_created", "org_id", "stage", "created_at"),
        Index("ix_pdf_build_claim", "stage", "next_attempt_at", "lease_expires_at", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_uuid: str = Field(sa_column=Column(String(80), nullable=False, unique=True, index=True))
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False)
    )
    creator_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    idempotency_key: str = Field(sa_column=Column(String(255), nullable=False))
    request_fingerprint: str = Field(sa_column=Column(String(64), nullable=False))

    stage: str = Field(default=PDFBuildStage.EXTRACTING.value, sa_column=Column(String(24), nullable=False))
    progress_current: int = 0
    progress_total: int = 0

    # Worker-private request and recovery state.
    options: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    plan_checkpoint: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    course_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("course.id", ondelete="SET NULL"), nullable=True),
    )
    chapters_created: int = 0
    activities_created: int = 0
    source_documents_created: int = 0

    indexing_status: Optional[str] = Field(default=None, sa_column=Column(String(24), nullable=True))
    indexing_code: Optional[str] = Field(default=None, sa_column=Column(String(80), nullable=True))
    chunks_indexed: int = 0

    warning_code: Optional[str] = Field(default=None, sa_column=Column(String(80), nullable=True))
    warning_message: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    error_code: Optional[str] = Field(default=None, sa_column=Column(String(80), nullable=True))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    error_retryable: bool = False

    credit_amount: int = 8
    credit_period_token: str = Field(default="0", sa_column=Column(String(80), nullable=False))
    credit_state: str = Field(default=PDFBuildCreditState.PENDING.value, sa_column=Column(String(24), nullable=False))

    attempts: int = 0
    next_attempt_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    lease_owner: Optional[str] = Field(default=None, sa_column=Column(String(160), nullable=True))
    lease_expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    created_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    started_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    finished_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    staging_cleaned_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
