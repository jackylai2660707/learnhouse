from typing import Optional, Dict, List
from sqlalchemy import JSON, Column, ForeignKey, Index
from sqlmodel import Field, SQLModel
from enum import Enum


## Assignment ##
class GradingTypeEnum(str, Enum):
    ALPHABET = "ALPHABET"
    NUMERIC = "NUMERIC"
    PERCENTAGE = "PERCENTAGE"
    PASS_FAIL = "PASS_FAIL"
    GPA_SCALE = "GPA_SCALE"


class AssignmentBase(SQLModel):
    """Represents the common fields for an assignment."""

    title: str
    description: str
    due_date: str
    published: Optional[bool] = False
    grading_type: GradingTypeEnum
    # When True, submissions are graded + marked as done automatically on
    # submit. Only applies when every task is auto-gradable (no file uploads
    # which require a human). Teacher can still override by re-grading later.
    auto_grading: Optional[bool] = True
    # When True, the student-facing task views block paste events on code
    # editors and text inputs. This is a soft deterrent — it can be bypassed
    # but it discourages casual AI-assisted copy/paste.
    anti_copy_paste: Optional[bool] = False
    # When True, after a submission is GRADED the student's task view reveals
    # the correct answers (which quiz options were right, the accepted short
    # answer, the expected number, the correct form blanks). New pilot
    # assignments default to True so students can review, fix, and retry
    # without needing the teacher to manually explain every wrong answer.
    show_correct_answers: Optional[bool] = True
    # When True, after a submission is GRADED the student may reset their work
    # and try the assignment again. The retry wipes per-task submissions and
    # the user submission row back to a fresh state — only the attempt
    # counter on AssignmentUserSubmission survives, so a future grade
    # replaces the previous one (no submission history is kept).
    allow_retries: Optional[bool] = True
    # Upper bound on the number of attempts. 0 means unlimited. The first
    # submission is attempt 1, so a teacher who sets max_retries=3 gives the
    # student up to 3 graded attempts total (initial + 2 retries).
    max_retries: Optional[int] = 0
    # School pilot metadata. UserGroup is reused as the first-stage "class"
    # primitive, so target_usergroup_ids stores the classes/groups that should
    # receive this assignment.
    subject: str = ""
    education_stage: str = ""  # primary / secondary / free-form local label
    grade_level: str = ""
    school_year: str = ""
    term: str = ""
    unit: str = ""
    learning_objectives: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    target_usergroup_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    score_policy: str = "highest"  # latest / highest
    teacher_review_required: Optional[bool] = False
    teacher_review_status: str = "not_required"  # not_required / pending / confirmed

    org_id: int
    course_id: int
    chapter_id: int
    activity_id: int


class AssignmentCreate(AssignmentBase):
    """Model for creating a new assignment."""

    pass  # Inherits all fields from AssignmentBase


class AssignmentRead(AssignmentBase):
    """Model for reading an assignment."""

    id: int
    assignment_uuid: str
    creation_date: Optional[str] = None
    update_date: Optional[str] = None
    # Populated by the read service from a joined query so the frontend can
    # build file-ref URLs without a second round-trip per request.
    course_uuid: Optional[str] = None
    activity_uuid: Optional[str] = None


class AssignmentUpdate(SQLModel):
    """Model for updating an assignment."""

    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    published: Optional[bool] = None
    grading_type: Optional[GradingTypeEnum] = None
    auto_grading: Optional[bool] = None
    anti_copy_paste: Optional[bool] = None
    show_correct_answers: Optional[bool] = None
    allow_retries: Optional[bool] = None
    max_retries: Optional[int] = None
    subject: Optional[str] = None
    education_stage: Optional[str] = None
    grade_level: Optional[str] = None
    school_year: Optional[str] = None
    term: Optional[str] = None
    unit: Optional[str] = None
    learning_objectives: Optional[List[str]] = None
    target_usergroup_ids: Optional[List[int]] = None
    score_policy: Optional[str] = None
    teacher_review_required: Optional[bool] = None
    teacher_review_status: Optional[str] = None
    org_id: Optional[int] = None
    course_id: Optional[int] = None
    chapter_id: Optional[int] = None
    activity_id: Optional[int] = None
    update_date: Optional[str] = None


class Assignment(AssignmentBase, table=True):
    """Represents an assignment with relevant details and foreign keys."""

    __table_args__ = (
        Index("ix_assignment_course_id", "course_id"),
        Index("ix_assignment_org_id", "org_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    creation_date: Optional[str] = None
    update_date: Optional[str] = None
    assignment_uuid: str = Field(default="", index=True)

    org_id: int = Field(
        sa_column=Column("org_id", ForeignKey("organization.id", ondelete="CASCADE"))
    )
    course_id: int = Field(
        sa_column=Column("course_id", ForeignKey("course.id", ondelete="CASCADE"))
    )
    chapter_id: int = Field(
        sa_column=Column("chapter_id", ForeignKey("chapter.id", ondelete="CASCADE"))
    )
    activity_id: int = Field(
        sa_column=Column("activity_id", ForeignKey("activity.id", ondelete="CASCADE"))
    )


## Assignment ##

## AssignmentTask ##


class AssignmentTaskTypeEnum(str, Enum):
    FILE_SUBMISSION = "FILE_SUBMISSION"
    QUIZ = "QUIZ"
    FORM = "FORM"
    CODE = "CODE"
    SHORT_ANSWER = "SHORT_ANSWER"
    NUMBER_ANSWER = "NUMBER_ANSWER"
    ESSAY = "ESSAY"
    OTHER = "OTHER"


class AssignmentTaskBase(SQLModel):
    """Represents the common fields for an assignment task."""

    title: str
    description: str
    hint: str
    reference_file: Optional[str] = None
    assignment_type: AssignmentTaskTypeEnum
    contents: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Internal grading scale for this task. Defaults to 100 — every task is
    # graded out of 100 (a percentage). The field stays in the DB because
    # legacy assignments set different values which, when summed, yielded a
    # weighted average. New tasks and the UI always use 100, so the
    # compute_assignment_grade math produces a simple average across tasks.
    max_grade_value: int = 100


class AssignmentTaskCreate(AssignmentTaskBase):
    """Model for creating a new assignment task."""

    pass  # Inherits all fields from AssignmentTaskBase


class AssignmentTaskRead(AssignmentTaskBase):
    """Model for reading an assignment task."""

    id: int
    assignment_task_uuid: str
    creation_date: str
    update_date: str


class AssignmentTaskUpdate(SQLModel):
    """Model for updating an assignment task."""

    title: Optional[str] = None
    description: Optional[str] = None
    hint: Optional[str] = None
    assignment_type: Optional[AssignmentTaskTypeEnum] = None
    contents: Optional[Dict] = Field(default=None, sa_column=Column(JSON))
    max_grade_value: Optional[int] = None


class AssignmentTask(AssignmentTaskBase, table=True):
    """Represents a task within an assignment with various attributes and foreign keys."""

    id: Optional[int] = Field(default=None, primary_key=True)

    assignment_task_uuid: str
    creation_date: str
    update_date: str

    assignment_id: int = Field(
        sa_column=Column(
            "assignment_id", ForeignKey("assignment.id", ondelete="CASCADE")
        )
    )
    org_id: int = Field(
        sa_column=Column("org_id", ForeignKey("organization.id", ondelete="CASCADE"))
    )
    course_id: int = Field(
        sa_column=Column("course_id", ForeignKey("course.id", ondelete="CASCADE"))
    )
    chapter_id: int = Field(
        sa_column=Column("chapter_id", ForeignKey("chapter.id", ondelete="CASCADE"))
    )
    activity_id: int = Field(
        sa_column=Column("activity_id", ForeignKey("activity.id", ondelete="CASCADE"))
    )


## AssignmentTask ##


## AssignmentTaskSubmission ##


class AssignmentTaskSubmissionBase(SQLModel):
    """Represents the common fields for an assignment task submission."""

    assignment_task_submission_uuid: str
    task_submission: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    grade: int = (
        0  # Task-local raw score; aggregated into AssignmentUserSubmission.grade
    )
    task_submission_grade_feedback: str
    assignment_type: AssignmentTaskTypeEnum

    user_id: int
    activity_id: int
    course_id: int
    chapter_id: int
    assignment_task_id: int


class AssignmentTaskSubmissionCreate(AssignmentTaskSubmissionBase):
    """Model for creating a new assignment task submission."""

    pass  # Inherits all fields from AssignmentTaskSubmissionBase


class AssignmentTaskSubmissionRead(AssignmentTaskSubmissionBase):
    """Model for reading an assignment task submission."""

    id: int
    creation_date: str
    update_date: str


class AssignmentTaskSubmissionUpdate(SQLModel):
    """Model for updating an assignment task submission."""

    assignment_task_id: Optional[int] = None
    assignment_task_submission_uuid: Optional[str] = None
    task_submission: Optional[Dict] = Field(default=None, sa_column=Column(JSON))
    grade: Optional[int] = None
    task_submission_grade_feedback: Optional[str] = None
    assignment_type: Optional[AssignmentTaskTypeEnum] = None


class AssignmentTaskSubmission(AssignmentTaskSubmissionBase, table=True):
    """Represents a submission for a specific assignment task with grade and feedback."""

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_task_submission_uuid: str
    task_submission: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    grade: int = (
        0  # Task-local raw score; aggregated into AssignmentUserSubmission.grade
    )
    task_submission_grade_feedback: str
    assignment_type: AssignmentTaskTypeEnum

    user_id: int = Field(
        sa_column=Column("user_id", ForeignKey("user.id", ondelete="CASCADE"))
    )
    activity_id: int = Field(
        sa_column=Column("activity_id", ForeignKey("activity.id", ondelete="CASCADE"))
    )
    course_id: int = Field(
        sa_column=Column("course_id", ForeignKey("course.id", ondelete="CASCADE"))
    )
    chapter_id: int = Field(
        sa_column=Column("chapter_id", ForeignKey("chapter.id", ondelete="CASCADE"))
    )
    assignment_task_id: int = Field(
        sa_column=Column(
            "assignment_task_id", ForeignKey("assignmenttask.id", ondelete="CASCADE")
        )
    )

    creation_date: str
    update_date: str


## AssignmentTaskSubmission ##

## AssignmentUserSubmission ##


class AssignmentUserSubmissionStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    GRADED = "GRADED"
    LATE = "LATE"
    NOT_SUBMITTED = "NOT_SUBMITTED"


class AssignmentUserSubmissionBase(SQLModel):
    """Represents the submission status of an assignment for a user."""

    submission_status: AssignmentUserSubmissionStatus = (
        AssignmentUserSubmissionStatus.SUBMITTED
    )
    grade: int
    # Optional overall note left by the instructor on the assignment as a whole
    # (separate from task_submission_grade_feedback which is per-task).
    overall_feedback: Optional[str] = None
    # Per-student teacher review state for the school pilot gradebook.
    # not_required / pending / confirmed. Kept on the submission, not only on
    # the assignment, because teachers confirm one student's work at a time.
    teacher_review_status: str = "pending"
    # Which attempt produced this row. The first submission is attempt 1.
    # When retries are enabled, the retry endpoint resets the row in place
    # (status -> PENDING, grade -> 0, attempt_number incremented) so a single
    # AssignmentUserSubmission row tracks the student's latest attempt while
    # still bounding how many times they can resubmit.
    attempt_number: int = 1
    best_grade: int = 0
    best_attempt_number: int = 1
    user_id: int = Field(
        sa_column=Column("user_id", ForeignKey("user.id", ondelete="CASCADE"))
    )
    assignment_id: int = Field(
        sa_column=Column(
            "assignment_id", ForeignKey("assignment.id", ondelete="CASCADE")
        )
    )


class AssignmentUserSubmissionCreate(SQLModel):
    """Model for creating a new assignment user submission."""

    assignment_id: int
    pass  # Inherits all fields from AssignmentUserSubmissionBase


class AssignmentUserSubmissionRead(AssignmentUserSubmissionBase):
    """Model for reading an assignment user submission."""

    id: int
    creation_date: str
    update_date: str


class AssignmentUserSubmissionUpdate(SQLModel):
    """Model for updating an assignment user submission."""

    submission_status: Optional[AssignmentUserSubmissionStatus] = None
    grade: Optional[str] = None
    overall_feedback: Optional[str] = None
    teacher_review_status: Optional[str] = None
    attempt_number: Optional[int] = None
    best_grade: Optional[int] = None
    best_attempt_number: Optional[int] = None
    user_id: Optional[int] = None
    assignment_id: Optional[int] = None


class AssignmentUserSubmission(AssignmentUserSubmissionBase, table=True):
    """Represents the submission status of an assignment for a user."""

    id: Optional[int] = Field(default=None, primary_key=True)
    creation_date: str
    update_date: str
    assignmentusersubmission_uuid: str

    submission_status: AssignmentUserSubmissionStatus = (
        AssignmentUserSubmissionStatus.SUBMITTED
    )
    grade: int
    overall_feedback: Optional[str] = None
    teacher_review_status: str = "pending"
    attempt_number: int = 1
    best_grade: int = 0
    best_attempt_number: int = 1
    user_id: int = Field(
        sa_column=Column("user_id", ForeignKey("user.id", ondelete="CASCADE"))
    )
    assignment_id: int = Field(
        sa_column=Column(
            "assignment_id", ForeignKey("assignment.id", ondelete="CASCADE")
        )
    )


## AssignmentRemediationPractice ##


class AssignmentRemediationPractice(SQLModel, table=True):
    """Supplemental remediation practice generated from a graded assignment submission."""

    __table_args__ = (
        Index("ix_assignmentremediationpractice_assignment_id", "assignment_id"),
        Index(
            "ix_assignmentremediationpractice_uuid",
            "assignment_remediation_practice_uuid",
        ),
        Index(
            "ix_assignmentremediationpractice_submission_user",
            "assignment_submission_id",
            "user_id",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_remediation_practice_uuid: str = Field(default="")
    status: str = "generated"  # generated / completed
    questions: List[Dict] = Field(default_factory=list, sa_column=Column(JSON))
    answers: Optional[List[Dict]] = Field(default=None, sa_column=Column(JSON))
    score: Optional[int] = None
    max_score: int = 0
    feedback: Dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None

    assignment_id: int = Field(
        sa_column=Column(
            "assignment_id", ForeignKey("assignment.id", ondelete="CASCADE")
        )
    )
    assignment_submission_id: int = Field(
        sa_column=Column(
            "assignment_submission_id",
            ForeignKey("assignmentusersubmission.id", ondelete="CASCADE"),
        )
    )
    user_id: int = Field(
        sa_column=Column("user_id", ForeignKey("user.id", ondelete="CASCADE"))
    )


## AssignmentRemediationPractice ##
