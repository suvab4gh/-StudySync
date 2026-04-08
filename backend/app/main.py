from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .database import get_session, init_db
from .models import Course, StudyBlock, StudyBlockType, SyllabusItem, User

app = FastAPI(title="StudySync API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/upload")
async def upload_syllabus(
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(...)],
):
    if file.content_type not in {"application/pdf", "text/plain", "text/markdown"}:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    user = session.exec(select(User).order_by(User.created_at)).first()
    if not user:
        user = User(email="student@example.com", full_name="StudySync Student")
        session.add(user)
        session.commit()
        session.refresh(user)

    course = Course(user_id=user.id, code="AUTO-101", title=file.filename or "Uploaded Syllabus")
    session.add(course)
    session.commit()
    session.refresh(course)

    # Lightweight parser: each non-empty line becomes a syllabus item due on future days.
    text = raw.decode("utf-8", errors="ignore")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    created = 0
    for index, line in enumerate(lines[:25]):
        item = SyllabusItem(
            course_id=course.id,
            title=line[:180],
            due_at=datetime.utcnow() + timedelta(days=index + 1),
        )
        session.add(item)
        created += 1

    session.commit()
    return {"courseId": str(course.id), "parsedItems": created}


@app.get("/schedule")
def get_schedule(session: Annotated[Session, Depends(get_session)]):
    blocks = session.exec(select(StudyBlock).order_by(StudyBlock.start_at)).all()

    course_ids = {block.course_id for block in blocks}
    courses = session.exec(select(Course).where(Course.id.in_(course_ids))).all() if course_ids else []
    color_by_course = {course.id: course.color_hex for course in courses}

    payload = []
    for block in blocks:
        payload.append(
            {
                "id": str(block.id),
                "title": block.title,
                "start_at": block.start_at.isoformat(),
                "end_at": block.end_at.isoformat(),
                "block_type": block.block_type.value,
                "course_color": color_by_course.get(block.course_id),
            }
        )

    return {"blocks": payload}


@app.patch("/schedule/{block_id}")
def patch_schedule(
    block_id: str,
    body: dict[str, Any],
    session: Annotated[Session, Depends(get_session)],
):
    block = session.get(StudyBlock, block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Study block not found")

    try:
        start_at = datetime.fromisoformat(body["start_at"])
        end_at = datetime.fromisoformat(body["end_at"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime payload") from exc

    if end_at <= start_at:
        raise HTTPException(status_code=400, detail="end_at must be after start_at")

    block.start_at = start_at
    block.end_at = end_at
    block.updated_at = datetime.utcnow()
    session.add(block)
    session.commit()
    session.refresh(block)

    return {
        "id": str(block.id),
        "title": block.title,
        "start_at": block.start_at.isoformat(),
        "end_at": block.end_at.isoformat(),
        "block_type": block.block_type.value,
    }


@app.post("/seed")
def seed_data(session: Annotated[Session, Depends(get_session)]):
    user = User(email="seed@example.com", full_name="Seed User")
    session.add(user)
    session.commit()
    session.refresh(user)

    course = Course(user_id=user.id, code="MATH-201", title="Linear Algebra", color_hex="#2563EB")
    session.add(course)
    session.commit()
    session.refresh(course)

    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    blocks = [
        StudyBlock(
            user_id=user.id,
            course_id=course.id,
            title="Matrix review",
            block_type=StudyBlockType.REVIEW,
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=3),
        ),
        StudyBlock(
            user_id=user.id,
            course_id=course.id,
            title="Problem set",
            block_type=StudyBlockType.ASSIGNMENT,
            start_at=now + timedelta(days=1, hours=1),
            end_at=now + timedelta(days=1, hours=2),
        ),
    ]
    session.add_all(blocks)
    session.commit()

    return {"seeded": len(blocks)}
