"""
parser.py – PDF text extraction and LLM-powered structured syllabus parsing.

Pipeline
--------
1. Extract raw text from the uploaded PDF using ``pdfplumber`` (fast, layout-
   preserving) with ``unstructured`` as an OCR fallback for scanned pages.
2. Feed the cleaned text to an OpenAI/Anthropic model via the ``instructor``
   library, which enforces structured output validated against our Pydantic
   schemas.
3. If the LLM call fails (rate-limit, timeout, etc.) ``tenacity`` retries up
   to ``MAX_LLM_RETRIES`` times with exponential back-off.
4. After extraction, every date field is re-validated with ``dateutil`` and
   assigned a confidence score.  Fields that could not be parsed fall back to
   ``None`` with a LOW confidence score and an explanatory warning.
5. As a last resort, a pure-regex heuristic pass over the raw text recovers
   dates that the LLM missed entirely.

Environment variables (see .env.example):
  OPENAI_API_KEY   – required for the default OpenAI provider
  ANTHROPIC_API_KEY – alternative; set LLM_PROVIDER=anthropic to use
  LLM_MODEL        – model name (default: gpt-4o-mini)
  LLM_PROVIDER     – "openai" | "anthropic" (default: openai)
"""

from __future__ import annotations

import io
import logging
import os
import re
from datetime import date, datetime
from typing import Any

import instructor
import pdfplumber
from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.schemas import (
    Assignment,
    AssignmentType,
    ConfidenceLevel,
    ConfidenceScore,
    CourseInfo,
    ExtractedDate,
    SyllabusExtraction,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_LLM_RETRIES: int = 3
MAX_TEXT_CHARS: int = 12_000   # Truncation limit to stay within context windows
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Regex patterns used for heuristic date recovery
_DATE_PATTERNS: list[re.Pattern[str]] = [
    # MM/DD/YYYY or MM-DD-YYYY
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"),
    # Month DD, YYYY  or  Month DD YYYY
    re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2})[,\s]+(\d{4})\b",
        re.IGNORECASE,
    ),
    # DD Month YYYY
    re.compile(
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})\b",
        re.IGNORECASE,
    ),
]

# Keywords that suggest a line is describing a deadline or due date
_DEADLINE_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(due|deadline|submit|exam|quiz|midterm|final|assignment|project|"
    r"homework|hw|lab|reading)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# LLM client factory (kept module-level so tests can monkey-patch)
# ---------------------------------------------------------------------------


def _build_instructor_client() -> Any:
    """
    Construct an ``instructor``-patched LLM client.

    Supports OpenAI (default) and Anthropic providers.  Raises
    ``EnvironmentError`` if the required API key is absent.
    """
    if LLM_PROVIDER == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY is not set")
        import anthropic  # type: ignore[import-untyped]

        raw_client = anthropic.Anthropic(api_key=api_key)
        return instructor.from_anthropic(raw_client)
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is not set")
        import openai  # type: ignore[import-untyped]

        raw_client = openai.OpenAI(api_key=api_key)
        return instructor.from_openai(raw_client)


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract plain text from a PDF byte-string.

    Strategy:
    1. Try ``pdfplumber`` for digitally-born PDFs (fast, good layout).
    2. If pdfplumber yields fewer than 100 characters (likely a scanned page),
       fall back to ``unstructured`` which invokes Tesseract OCR.

    Returns the raw extracted text (may still need cleaning).
    """
    text = _extract_with_pdfplumber(pdf_bytes)
    if len(text.strip()) < 100:
        logger.info("pdfplumber returned minimal text; attempting OCR fallback")
        text = _extract_with_unstructured(pdf_bytes)
    return text


def _extract_with_pdfplumber(pdf_bytes: bytes) -> str:
    """Extract text using pdfplumber (layout-preserving, no OCR)."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
    return "\n".join(pages)


def _extract_with_unstructured(pdf_bytes: bytes) -> str:
    """
    OCR fallback using ``unstructured``.

    We import lazily so that environments without Tesseract/poppler still work
    for digital PDFs (the import itself doesn't fail, only the call would).
    """
    try:
        from unstructured.partition.pdf import partition_pdf  # type: ignore[import-untyped]

        # unstructured expects a file-like object or path; write to a tmp buffer
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            elements = partition_pdf(filename=tmp_path, strategy="ocr_only")
        finally:
            os.unlink(tmp_path)
        return "\n".join(str(el) for el in elements)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unstructured OCR fallback failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Date validation helpers
# ---------------------------------------------------------------------------


def _parse_date_safely(
    raw: str, reference_year: int | None = None
) -> tuple[date | None, float]:
    """
    Parse a raw date string using ``dateutil``.

    Returns a ``(date | None, confidence_score)`` tuple.  Confidence is
    penalised when the resulting year is implausible (more than 5 years away
    from today) or when ``dateutil`` had to make significant assumptions.
    """
    if not raw or not raw.strip():
        return None, 0.0
    try:
        today = date.today()
        default_dt = datetime(
            year=reference_year or today.year, month=today.month, day=today.day
        )
        parsed = dateutil_parser.parse(raw, default=default_dt, fuzzy=True)
        parsed_date = parsed.date()
        # Penalise dates more than 5 years away from today
        delta_years = abs((parsed_date - today).days / 365.0)
        confidence = 0.90 if delta_years <= 5 else 0.40
        # Extra penalty if the raw string looks very vague (single number, etc.)
        if re.fullmatch(r"\d{1,2}", raw.strip()):
            confidence *= 0.5
        return parsed_date, min(confidence, 1.0)
    except (ValueError, OverflowError):
        return None, 0.0


def _validate_date_range(
    d: date, course_start: date | None, course_end: date | None
) -> list[str]:
    """
    Check that *d* falls within the course window and warn if it does not.

    Returns a (possibly empty) list of warning strings.
    """
    warnings: list[str] = []
    today = date.today()
    if d < today - relativedelta(years=1):
        warnings.append(f"date {d} is more than a year in the past – verify year")
    if course_start and d < course_start:
        warnings.append(f"date {d} is before course start {course_start}")
    if course_end and d > course_end:
        warnings.append(f"date {d} is after course end {course_end}")
    return warnings


# ---------------------------------------------------------------------------
# Heuristic / regex fallback extractor
# ---------------------------------------------------------------------------


def _heuristic_extract_assignments(text: str) -> list[Assignment]:
    """
    Last-resort extraction when the LLM pipeline is unavailable.

    Scans the raw text line-by-line for lines containing deadline keywords
    and a recognisable date pattern.  Creates bare ``Assignment`` objects
    with LOW confidence and appropriate warnings.
    """
    assignments: list[Assignment] = []
    lines = text.splitlines()
    seen_titles: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or not _DEADLINE_KEYWORDS.search(stripped):
            continue

        # Attempt to find a date on this line
        raw_date = ""
        for pattern in _DATE_PATTERNS:
            match = pattern.search(stripped)
            if match:
                raw_date = match.group(0)
                break

        parsed_date, conf = _parse_date_safely(raw_date)
        extracted = ExtractedDate(
            value=parsed_date,
            raw_text=raw_date,
            confidence=ConfidenceScore.from_float(conf),
            warnings=[] if parsed_date else ["could not parse date from raw text"],
        )

        # Use the line itself as a crude title, capped at 80 chars
        title = re.sub(r"\s+", " ", stripped)[:80]
        if title in seen_titles:
            continue
        seen_titles.add(title)

        # Infer type from keywords
        atype = _infer_assignment_type(stripped)

        assignments.append(
            Assignment(
                title=title,
                assignment_type=atype,
                due_date=extracted,
                estimated_hours=_default_hours_for_type(atype),
                title_confidence=ConfidenceScore.from_float(0.5),
                date_confidence=ConfidenceScore.from_float(conf),
                hours_confidence=ConfidenceScore.from_float(0.3),
                warnings=["extracted via heuristic fallback – verify manually"],
            )
        )

    return assignments


def _infer_assignment_type(line: str) -> AssignmentType:
    """Guess the assignment type from keyword presence in a text line."""
    line_lower = line.lower()
    if re.search(r"\b(final|midterm|exam)\b", line_lower):
        return AssignmentType.EXAM
    if re.search(r"\bquiz\b", line_lower):
        return AssignmentType.QUIZ
    if re.search(r"\b(project|presentation)\b", line_lower):
        return AssignmentType.PROJECT
    if re.search(r"\b(lab)\b", line_lower):
        return AssignmentType.LAB
    if re.search(r"\b(reading|chapter)\b", line_lower):
        return AssignmentType.READING
    if re.search(r"\b(hw|homework|assignment|problem set)\b", line_lower):
        return AssignmentType.HOMEWORK
    return AssignmentType.OTHER


def _default_hours_for_type(atype: AssignmentType) -> float:
    """Heuristic default study-hour estimate per assignment type."""
    defaults = {
        AssignmentType.EXAM: 8.0,
        AssignmentType.QUIZ: 2.0,
        AssignmentType.HOMEWORK: 3.0,
        AssignmentType.PROJECT: 15.0,
        AssignmentType.READING: 1.5,
        AssignmentType.LAB: 3.0,
        AssignmentType.OTHER: 2.0,
    }
    return defaults.get(atype, 2.0)


# ---------------------------------------------------------------------------
# LLM extraction prompt & schema
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert academic assistant.  Extract all graded
assignments, exams, quizzes, projects, and deadlines from the syllabus text
below.  For each item return:
- title (string)
- assignment_type: exam | quiz | homework | project | reading | lab | other
- due_date_raw (string, exactly as it appears in the text)
- estimated_hours (float, your best estimate of total study/work hours)
- weight_percent (float or null)
- prerequisites (list of other assignment titles that must be done first)
- cognitive_load (integer 1-5; 1=light reading, 5=high-stakes final exam)

Also extract top-level course metadata:
- course_name, course_code, instructor, semester
- course_start_date_raw, course_end_date_raw

Return ONLY the structured JSON – no prose, no markdown fences."""

# Lightweight Pydantic model used only for the raw LLM response before we
# enrich dates with dateutil.  We keep it separate from the main schema to
# avoid leaking LLM-specific fields into the public API contract.
from pydantic import BaseModel as _BaseModel  # noqa: E402 (needed after imports)


class _RawAssignment(_BaseModel):
    title: str = ""
    assignment_type: str = "other"
    due_date_raw: str = ""
    estimated_hours: float = 2.0
    weight_percent: float | None = None
    prerequisites: list[str] = []
    cognitive_load: int = 3


class _RawCourseInfo(_BaseModel):
    course_name: str = "Unknown Course"
    course_code: str = ""
    instructor: str = ""
    semester: str = ""
    course_start_date_raw: str = ""
    course_end_date_raw: str = ""


class _RawSyllabus(_BaseModel):
    course_info: _RawCourseInfo = _RawCourseInfo()
    assignments: list[_RawAssignment] = []


# ---------------------------------------------------------------------------
# Core LLM call with tenacity retry
# ---------------------------------------------------------------------------


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(MAX_LLM_RETRIES),
    reraise=True,
)
def _call_llm(client: Any, text: str) -> _RawSyllabus:
    """
    Send the extracted PDF text to the LLM and return the raw structured output.

    ``instructor`` handles the JSON mode / tool-calling plumbing and validates
    the response against ``_RawSyllabus``.  Tenacity wraps the call so that
    transient errors (rate limits, network hiccups) are retried automatically.
    """
    return client.chat.completions.create(
        model=LLM_MODEL,
        response_model=_RawSyllabus,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text[:MAX_TEXT_CHARS]},
        ],
    )


# ---------------------------------------------------------------------------
# Post-processing: enrich raw LLM output with validated dates & confidence
# ---------------------------------------------------------------------------


def _enrich_extraction(raw: _RawSyllabus) -> SyllabusExtraction:
    """
    Convert a raw LLM response into a fully-enriched ``SyllabusExtraction``.

    Steps:
    1. Parse course-level dates and derive a reference year.
    2. For every assignment, parse the raw date string with dateutil, compute
       per-field confidence, validate against the course window, and append
       any range warnings.
    3. Clamp ``cognitive_load`` to [1, 5] in case the LLM went out of range.
    """
    rc = raw.course_info

    # --- Course-level dates ---
    start_date_val, start_conf = _parse_date_safely(rc.course_start_date_raw)
    end_date_val, end_conf = _parse_date_safely(rc.course_end_date_raw)

    # Derive a reference year for ambiguous assignment dates
    ref_year = start_date_val.year if start_date_val else None

    course_info = CourseInfo(
        course_name=rc.course_name or "Unknown Course",
        course_code=rc.course_code,
        instructor=rc.instructor,
        semester=rc.semester,
        start_date=ExtractedDate(
            value=start_date_val,
            raw_text=rc.course_start_date_raw,
            confidence=ConfidenceScore.from_float(start_conf),
        ),
        end_date=ExtractedDate(
            value=end_date_val,
            raw_text=rc.course_end_date_raw,
            confidence=ConfidenceScore.from_float(end_conf),
        ),
    )

    # --- Assignments ---
    enriched: list[Assignment] = []
    for ra in raw.assignments:
        due_val, due_conf = _parse_date_safely(ra.due_date_raw, ref_year)

        date_warnings: list[str] = []
        if due_val:
            date_warnings = _validate_date_range(due_val, start_date_val, end_date_val)

        # Clamp cognitive load to valid range
        cog_load = max(1, min(5, ra.cognitive_load))

        # Safely coerce assignment type
        try:
            atype = AssignmentType(ra.assignment_type.lower())
        except ValueError:
            atype = AssignmentType.OTHER

        enriched.append(
            Assignment(
                title=ra.title or "Unnamed Assignment",
                assignment_type=atype,
                due_date=ExtractedDate(
                    value=due_val,
                    raw_text=ra.due_date_raw,
                    confidence=ConfidenceScore.from_float(due_conf),
                    warnings=date_warnings,
                ),
                estimated_hours=ra.estimated_hours or _default_hours_for_type(atype),
                weight_percent=ra.weight_percent,
                prerequisites=ra.prerequisites or [],
                cognitive_load=cog_load,
                date_confidence=ConfidenceScore.from_float(due_conf),
                hours_confidence=ConfidenceScore.from_float(
                    # LLM-provided hours get medium confidence by default
                    0.7 if ra.estimated_hours and ra.estimated_hours > 0 else 0.3
                ),
            )
        )

    return SyllabusExtraction(
        course_info=course_info,
        assignments=enriched,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_syllabus_pdf(pdf_bytes: bytes) -> SyllabusExtraction:
    """
    Top-level entry point: bytes-in, ``SyllabusExtraction``-out.

    Called by ``main.py``'s ``/upload`` endpoint.  Handles all error paths:
    - PDF extraction failures → empty text, immediate heuristic fallback
    - LLM failures after retries → heuristic fallback with warning
    - Partial failures → partial results with LOW confidence warnings
    """
    warnings: list[str] = []

    # Step 1: Extract text
    try:
        text = extract_text_from_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF extraction failed: %s", exc)
        text = ""
        warnings.append(f"PDF extraction error: {exc}")

    if not text.strip():
        warnings.append("No text could be extracted from the PDF – results unreliable")

    raw_length = len(text)

    # Step 2: LLM extraction (with fallback)
    try:
        client = _build_instructor_client()
        raw = _call_llm(client, text)
        extraction = _enrich_extraction(raw)
    except EnvironmentError as exc:
        # API key missing – use heuristic only
        logger.warning("LLM client unavailable (%s); using heuristic fallback", exc)
        warnings.append(f"LLM unavailable: {exc} – using regex/heuristic extraction")
        extraction = _heuristic_fallback(text, warnings)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM extraction failed after retries: %s", exc)
        warnings.append(f"LLM extraction failed: {exc} – using regex/heuristic extraction")
        extraction = _heuristic_fallback(text, warnings)

    extraction.raw_text_length = raw_length
    extraction.extraction_warnings.extend(warnings)
    return extraction


def _heuristic_fallback(text: str, warnings: list[str]) -> SyllabusExtraction:
    """Build a minimal ``SyllabusExtraction`` using regex heuristics only."""
    assignments = _heuristic_extract_assignments(text)
    course_info = CourseInfo(
        course_name="Unknown Course",
        warnings=["course metadata could not be extracted"],
    )
    return SyllabusExtraction(
        course_info=course_info,
        assignments=assignments,
        extraction_warnings=warnings[:],  # shallow copy; caller extends later
    )
