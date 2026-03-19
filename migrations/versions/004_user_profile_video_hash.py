"""Add user profile fields and video hash

Revision ID: 004
Revises: 003
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # User profile fields
    op.add_column("users", sa.Column("student_code", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("major", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("teacher_code", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("department", sa.String(255), nullable=True))
    # LectureVideo fields
    op.add_column("lecture_videos", sa.Column("video_hash", sa.String(64), nullable=True))
    op.add_column("lecture_videos", sa.Column("file_size_bytes", sa.Integer, nullable=True))
    op.create_index("ix_lecture_videos_video_hash", "lecture_videos", ["video_hash"])


def downgrade() -> None:
    op.drop_index("ix_lecture_videos_video_hash", "lecture_videos")
    op.drop_column("lecture_videos", "file_size_bytes")
    op.drop_column("lecture_videos", "video_hash")
    op.drop_column("users", "department")
    op.drop_column("users", "teacher_code")
    op.drop_column("users", "major")
    op.drop_column("users", "student_code")
