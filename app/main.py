"""
main.py – FastAPI application entry-point for StudySync.

Endpoints
---------
POST /upload
    Accepts a multipart PDF upload, extracts and parses the syllabus,
    and returns a ``SyllabusExtraction`` JSON object.

POST /schedule
    Accepts a ``ScheduleRequest`` JSON body (containing a previously
    extracted syllabus plus availability preferences) and returns a
    ``ScheduleResponse`` with OR-Tools-generated study blocks.

GET /health
    Lightweight liveness probe for Render / load-balancer health checks.

Error handling
--------------
- 400 Bad Request  – invalid file type, empty PDF, validation errors
- 422 Unprocessable Entity – Pydantic validation failures (auto by FastAPI)
- 500 Internal Server Error – unexpected runtime errors, with a sanitised
  message (stack traces are logged but never returned to the client)
"""

from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.parser import parse_syllabus_pdf
from app.scheduler import build_schedule
from app.schemas import ScheduleRequest, ScheduleResponse, UploadResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup / shutdown hooks.

    Currently used for startup logging.  Add DB connection pool initialisation
    or model pre-loading here when needed.
    """
    logger.info("StudySync API starting up")
    yield
    logger.info("StudySync API shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="StudySync API",
    description=(
        "AI-powered academic planner: convert messy syllabus PDFs into "
        "adaptive, stress-optimised study schedules."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS – allow all origins in development; restrict in production via env vars
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    ["application/pdf", "application/x-pdf", "binary/octet-stream"]
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _validate_pdf_upload(file: UploadFile) -> None:
    """
    Raise ``HTTPException(400)`` if the upload is not a plausible PDF.

    We check the declared content-type *and* the file extension because
    browsers / curl clients don't always send the right MIME type.
    """
    if file.filename and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted (expected .pdf extension)",
        )
    # Allow octet-stream since some clients use that as a generic binary type
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct and ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content type '{ct}'. Expected application/pdf",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health_check() -> dict[str, str]:
    """Return ``{"status": "ok"}`` to confirm the service is alive."""
    return {"status": "ok"}


@app.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    tags=["syllabus"],
    summary="Upload a syllabus PDF and extract structured data",
    responses={
        400: {"description": "Invalid file or empty PDF"},
        500: {"description": "Unexpected server error during extraction"},
    },
)
async def upload_syllabus(
    file: UploadFile = File(..., description="PDF syllabus file (max 10 MB)"),
) -> UploadResponse:
    """
    Parse an uploaded syllabus PDF into structured assignment data.

    **Pipeline**: PDF bytes → text extraction (pdfplumber / OCR) →
    LLM structured extraction (instructor + OpenAI/Anthropic) →
    date validation (dateutil) → confidence scoring.

    Returns a ``SyllabusExtraction`` with per-field confidence scores and
    warnings so the frontend can highlight fields that need manual review.
    """
    _validate_pdf_upload(file)

    # Read and size-check the upload
    try:
        pdf_bytes = await file.read()
    except Exception as exc:
        logger.error("Failed to read uploaded file: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded file",
        ) from exc

    if not pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large ({len(pdf_bytes)} bytes). Maximum is {MAX_UPLOAD_BYTES} bytes",
        )

    # Delegate to parser (runs sync in the default thread pool via FastAPI)
    try:
        extraction = parse_syllabus_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error during syllabus parsing:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while parsing the syllabus",
        ) from exc

    # Surface low-confidence extractions as top-level warnings in the response
    response_warnings = list(extraction.extraction_warnings)
    if extraction.overall_confidence.score < 0.60:
        response_warnings.insert(
            0,
            f"Low overall extraction confidence ({extraction.overall_confidence.score:.2f}) – "
            "please review due dates manually",
        )

    return UploadResponse(
        extraction=extraction,
        warnings=response_warnings,
    )


@app.post(
    "/schedule",
    response_model=ScheduleResponse,
    status_code=status.HTTP_200_OK,
    tags=["schedule"],
    summary="Generate an optimised study schedule from a parsed syllabus",
    responses={
        400: {"description": "No assignments in syllabus or invalid request"},
        500: {"description": "Unexpected server error during scheduling"},
    },
)
async def generate_schedule(req: ScheduleRequest) -> ScheduleResponse:
    """
    Run the OR-Tools CP-SAT scheduler and return a calendar-ready study plan.

    **Constraints modelled**:
    - Daily study-hour budget (hard cap + per-weekday overrides)
    - Total hours required per assignment
    - Hard deadline cutoffs
    - Assignment prerequisites (topological ordering)
    - Cognitive load balancing (soft objective)
    - Earliness reward to reduce last-minute cramming (soft objective)

    When the problem is infeasible (e.g., deadline in the past), the response
    has ``"feasible": false`` with diagnostic warnings rather than an error.
    """
    if not req.syllabus.assignments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The syllabus contains no assignments to schedule",
        )

    try:
        response = build_schedule(req)
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error during scheduling:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while generating the schedule",
        ) from exc

    return response


# ---------------------------------------------------------------------------
# Global exception handler (catch-all safety net)
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def global_exception_handler(request: object, exc: Exception) -> JSONResponse:
    """
    Catch any unhandled exception and return a sanitised 500 response.

    Stack traces are logged server-side but never exposed to clients.
    """
    logger.error("Unhandled exception:\n%s", traceback.format_exc())
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred"},
    )


# ---------------------------------------------------------------------------
# Dev entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=bool(os.getenv("RELOAD", "true").lower() in ("true", "1", "yes")),
        log_level="info",
    )
