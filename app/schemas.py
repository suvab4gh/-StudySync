"""
schemas.py – Pydantic v2 models for StudySync syllabus extraction and scheduling.

These models form the data contract between the PDF parser, LLM extraction
pipeline, OR-Tools scheduler, and the FastAPI response layer.  Every field
that originates from an LLM carries a companion ``confidence`` score and
may have associated ``warnings`` so that callers can decide how much to trust
each datum without resorting to free-text parsing.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConfidenceLevel(str, Enum):
    """Qualitative confidence band for LLM-extracted fields."""

    HIGH = "high"       # ≥ 0.85 – safe to use without human review
    MEDIUM = "medium"   # 0.60 – 0.84 – flag for review
    LOW = "low"         # < 0.60 – likely wrong; requires manual correction


class AssignmentType(str, Enum):
    """Coarse category used for cognitive-load weighting in the scheduler."""

    EXAM = "exam"
    QUIZ = "quiz"
    HOMEWORK = "homework"
    PROJECT = "project"
    READING = "reading"
    LAB = "lab"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Low-level extraction models
# ---------------------------------------------------------------------------


class ConfidenceScore(BaseModel):
    """A numeric score paired with its qualitative band."""

    score: Annotated[float, Field(ge=0.0, le=1.0)]
    level: ConfidenceLevel

    @classmethod
    def from_float(cls, value: float) -> "ConfidenceScore":
        """Factory that derives the qualitative level from a raw float."""
        if value >= 0.85:
            level = ConfidenceLevel.HIGH
        elif value >= 0.60:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW
        return cls(score=round(value, 4), level=level)


class ExtractedDate(BaseModel):
    """
    A date value extracted (and validated) from a syllabus.

    ``raw_text`` preserves the original string so that validators down the
    pipeline can re-parse if the structured date looks suspicious.
    """

    value: date | None = None
    raw_text: str = ""
    confidence: ConfidenceScore = Field(
        default_factory=lambda: ConfidenceScore.from_float(0.0)
    )
    warnings: list[str] = Field(default_factory=list)

    @field_validator("value", mode="before")
    @classmethod
    def coerce_datetime_to_date(cls, v: Any) -> Any:
        """Accept datetime objects and strip the time component."""
        if isinstance(v, datetime):
            return v.date()
        return v


class Assignment(BaseModel):
    """
    A single graded event extracted from a course syllabus.

    Confidence scores are stored per-field because LLMs are much better at
    identifying the *existence* of an assignment than at extracting its exact
    due date or point weight.
    """

    title: str = Field(..., min_length=1, description="Human-readable name of the assignment")
    assignment_type: AssignmentType = AssignmentType.OTHER
    due_date: ExtractedDate = Field(default_factory=ExtractedDate)
    estimated_hours: float = Field(
        default=2.0,
        ge=0.25,
        le=200.0,
        description="Estimated total study/work hours required",
    )
    weight_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percentage of final grade; None if not specified",
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Titles of assignments that must be completed first",
    )
    cognitive_load: Annotated[int, Field(ge=1, le=5)] = Field(
        default=3,
        description="1 (light reading) – 5 (high-stakes exam); used for scheduling",
    )
    # Per-field confidence for the most error-prone extractions
    title_confidence: ConfidenceScore = Field(
        default_factory=lambda: ConfidenceScore.from_float(1.0)
    )
    date_confidence: ConfidenceScore = Field(
        default_factory=lambda: ConfidenceScore.from_float(0.0)
    )
    hours_confidence: ConfidenceScore = Field(
        default_factory=lambda: ConfidenceScore.from_float(0.5)
    )
    warnings: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def propagate_date_warning(self) -> "Assignment":
        """Flag assignments whose due date could not be parsed."""
        if self.due_date.value is None and not any(
            "due date" in w.lower() for w in self.warnings
        ):
            self.warnings.append("due_date: could not be parsed – manual review required")
        return self


# ---------------------------------------------------------------------------
# Course / Syllabus level
# ---------------------------------------------------------------------------


class CourseInfo(BaseModel):
    """Top-level metadata for a single course."""

    course_name: str = Field(..., min_length=1)
    course_code: str = ""
    instructor: str = ""
    semester: str = ""
    # Dates bounding the course; used by the scheduler for feasibility checks
    start_date: ExtractedDate = Field(default_factory=ExtractedDate)
    end_date: ExtractedDate = Field(default_factory=ExtractedDate)
    confidence: ConfidenceScore = Field(
        default_factory=lambda: ConfidenceScore.from_float(0.5)
    )
    warnings: list[str] = Field(default_factory=list)

    @field_validator("course_name", "course_code", "instructor", "semester", mode="before")
    @classmethod
    def coerce_none_to_empty(cls, v: Any) -> Any:
        return v if v is not None else ""


class SyllabusExtraction(BaseModel):
    """
    Full structured output of the PDF → LLM extraction pipeline.

    This is the object returned by ``parser.py`` and consumed by
    ``scheduler.py``.  ``overall_confidence`` is the arithmetic mean of
    per-assignment date confidence scores; if it drops below 0.60 the
    API response includes a top-level warning.
    """

    course_info: CourseInfo
    assignments: list[Assignment] = Field(default_factory=list)
    overall_confidence: ConfidenceScore = Field(
        default_factory=lambda: ConfidenceScore.from_float(0.5)
    )
    extraction_warnings: list[str] = Field(default_factory=list)
    raw_text_length: int = Field(
        default=0,
        ge=0,
        description="Character count of text fed to the LLM; useful for debugging",
    )

    @model_validator(mode="after")
    def compute_overall_confidence(self) -> "SyllabusExtraction":
        """Re-compute overall confidence from per-assignment date scores."""
        if not self.assignments:
            return self
        scores = [a.date_confidence.score for a in self.assignments]
        mean = sum(scores) / len(scores)
        self.overall_confidence = ConfidenceScore.from_float(mean)
        if self.overall_confidence.level == ConfidenceLevel.LOW:
            self.extraction_warnings.append(
                "overall_confidence is LOW – most due dates may be incorrect"
            )
        return self


# ---------------------------------------------------------------------------
# Scheduling I/O models
# ---------------------------------------------------------------------------


class DailyAvailability(BaseModel):
    """
    Study-hour budget per weekday (0 = Monday … 6 = Sunday).

    Defaults to 3 hours per day if not provided.
    """

    weekday: Annotated[int, Field(ge=0, le=6)]
    max_hours: Annotated[float, Field(ge=0.0, le=24.0)] = 3.0


class ScheduleRequest(BaseModel):
    """
    Payload sent to ``POST /schedule``.

    ``syllabus`` is the structured extraction result; ``availability`` lets
    the user override daily hour budgets before the solver runs.
    """

    syllabus: SyllabusExtraction
    availability: list[DailyAvailability] = Field(default_factory=list)
    start_date: date = Field(default_factory=date.today)
    max_daily_hours: float = Field(
        default=8.0,
        ge=0.5,
        le=24.0,
        description="Hard upper bound on total study hours in a single day",
    )
    # Safety valve: solver aborts after this many seconds to stay responsive
    solver_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)

    @field_validator("availability", mode="before")
    @classmethod
    def deduplicate_weekdays(cls, v: list[Any]) -> list[Any]:
        """Last entry wins if the same weekday appears more than once."""
        seen: dict[int, Any] = {}
        for item in v:
            wd = item["weekday"] if isinstance(item, dict) else item.weekday
            seen[wd] = item
        return list(seen.values())


class StudyBlock(BaseModel):
    """A scheduled chunk of study time for a specific assignment."""

    assignment_title: str
    assignment_type: AssignmentType
    scheduled_date: date
    duration_hours: float = Field(..., ge=0.25)
    cognitive_load: int = Field(..., ge=1, le=5)
    is_buffer: bool = Field(
        default=False,
        description="True for review/buffer blocks auto-inserted before exams",
    )


class ScheduleResponse(BaseModel):
    """
    Response returned by ``POST /schedule``.

    ``feasible`` is False when OR-Tools cannot satisfy all hard constraints
    (e.g., a deadline in the past).  ``warnings`` lists soft violations or
    solver notes that the frontend should surface to the user.
    """

    feasible: bool
    blocks: list[StudyBlock] = Field(default_factory=list)
    total_study_hours: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    solver_status: str = ""


# ---------------------------------------------------------------------------
# API-level request/response wrappers
# ---------------------------------------------------------------------------


class UploadResponse(BaseModel):
    """Response returned by ``POST /upload``."""

    extraction: SyllabusExtraction
    message: str = "Syllabus parsed successfully"
    warnings: list[str] = Field(default_factory=list)
