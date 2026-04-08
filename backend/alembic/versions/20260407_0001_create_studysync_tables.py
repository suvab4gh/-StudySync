"""create StudySync core tables

Revision ID: 20260407_0001
Revises:
Create Date: 2026-04-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260407_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("color_hex", sa.String(), nullable=False, server_default="#3B82F6"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_courses_user_id", "courses", ["user_id"], unique=False)
    op.create_index("ix_courses_code", "courses", ["code"], unique=False)

    op.create_table(
        "syllabus_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("details", sa.String(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("weight_percent", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_syllabus_items_course_id", "syllabus_items", ["course_id"], unique=False)
    op.create_index("ix_syllabus_items_due_at", "syllabus_items", ["due_at"], unique=False)

    study_block_type = postgresql.ENUM(
        "lecture",
        "reading",
        "assignment",
        "review",
        "exam_prep",
        name="studyblocktype",
    )
    study_block_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "study_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("syllabus_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("syllabus_items.id"), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("block_type", study_block_type, nullable=False, server_default="review"),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_study_blocks_user_id", "study_blocks", ["user_id"], unique=False)
    op.create_index("ix_study_blocks_course_id", "study_blocks", ["course_id"], unique=False)
    op.create_index("ix_study_blocks_syllabus_item_id", "study_blocks", ["syllabus_item_id"], unique=False)
    op.create_index("ix_study_blocks_start_at", "study_blocks", ["start_at"], unique=False)
    op.create_index("ix_study_blocks_end_at", "study_blocks", ["end_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_study_blocks_end_at", table_name="study_blocks")
    op.drop_index("ix_study_blocks_start_at", table_name="study_blocks")
    op.drop_index("ix_study_blocks_syllabus_item_id", table_name="study_blocks")
    op.drop_index("ix_study_blocks_course_id", table_name="study_blocks")
    op.drop_index("ix_study_blocks_user_id", table_name="study_blocks")
    op.drop_table("study_blocks")

    study_block_type = postgresql.ENUM(
        "lecture",
        "reading",
        "assignment",
        "review",
        "exam_prep",
        name="studyblocktype",
    )
    study_block_type.drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_syllabus_items_due_at", table_name="syllabus_items")
    op.drop_index("ix_syllabus_items_course_id", table_name="syllabus_items")
    op.drop_table("syllabus_items")

    op.drop_index("ix_courses_code", table_name="courses")
    op.drop_index("ix_courses_user_id", table_name="courses")
    op.drop_table("courses")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
