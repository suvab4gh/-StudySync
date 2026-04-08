from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel


class StudyBlockType(str, Enum):
    LECTURE = "lecture"
    READING = "reading"
    ASSIGNMENT = "assignment"
    REVIEW = "review"
    EXAM_PREP = "exam_prep"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True, nullable=False)
    email: str = Field(index=True, unique=True, nullable=False)
    full_name: str = Field(nullable=False)
    timezone: str = Field(default="UTC", nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    courses: list[Course] = Relationship(back_populates="user")
    study_blocks: list[StudyBlock] = Relationship(back_populates="user")


class Course(SQLModel, table=True):
    __tablename__ = "courses"

    id: UUID = Field(default_factory=uuid4, primary_key=True, nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    code: str = Field(nullable=False, index=True)
    title: str = Field(nullable=False)
    color_hex: str = Field(default="#3B82F6", nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    user: User = Relationship(back_populates="courses")
    syllabus_items: list[SyllabusItem] = Relationship(back_populates="course")
    study_blocks: list[StudyBlock] = Relationship(back_populates="course")


class SyllabusItem(SQLModel, table=True):
    __tablename__ = "syllabus_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True, nullable=False)
    course_id: UUID = Field(foreign_key="courses.id", nullable=False, index=True)
    title: str = Field(nullable=False)
    details: Optional[str] = Field(default=None)
    due_at: datetime = Field(nullable=False, index=True)
    weight_percent: Optional[float] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    course: Course = Relationship(back_populates="syllabus_items")
    study_blocks: list[StudyBlock] = Relationship(back_populates="syllabus_item")


class StudyBlock(SQLModel, table=True):
    __tablename__ = "study_blocks"

    id: UUID = Field(default_factory=uuid4, primary_key=True, nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    course_id: UUID = Field(foreign_key="courses.id", nullable=False, index=True)
    syllabus_item_id: Optional[UUID] = Field(
        default=None,
        foreign_key="syllabus_items.id",
        index=True,
    )
    title: str = Field(nullable=False)
    block_type: StudyBlockType = Field(default=StudyBlockType.REVIEW, nullable=False)
    start_at: datetime = Field(nullable=False, index=True)
    end_at: datetime = Field(nullable=False, index=True)
    is_completed: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    user: User = Relationship(back_populates="study_blocks")
    course: Course = Relationship(back_populates="study_blocks")
    syllabus_item: Optional[SyllabusItem] = Relationship(back_populates="study_blocks")
