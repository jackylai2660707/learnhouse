from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


PDFBuildStageValue = Literal[
    "extracting", "planning", "creating", "indexing", "done", "failed"
]


class PDFBuildIssue(BaseModel):
    code: str
    message: str
    retryable: bool = False


class PDFBuildDraftCourse(BaseModel):
    course_id: int
    course_uuid: str
    public: Literal[False] = False
    published: Literal[False] = False


class PDFBuildIndexing(BaseModel):
    status: Literal["success", "skipped", "failed", "degraded"]
    code: str | None = None
    chunks: int = 0


class PDFBuildJobSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_uuid: str
    org_id: int
    creator_user_id: int
    stage: PDFBuildStageValue
    progress_current: int
    progress_total: int
    draft_course: PDFBuildDraftCourse | None = None
    chapters_created: int = 0
    activities_created: int = 0
    source_documents_created: int = 0
    indexing: PDFBuildIndexing | None = None
    warning: PDFBuildIssue | None = None
    error: PDFBuildIssue | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
