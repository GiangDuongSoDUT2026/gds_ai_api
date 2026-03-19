"""Add learning analytics tables

Revision ID: 005
Revises: 004
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "student_video_progress",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lecture_id", UUID(as_uuid=True), sa.ForeignKey("lecture_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("watched_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("completed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("last_position_sec", sa.Float, nullable=False, server_default="0"),
        sa.Column("scenes_viewed", JSONB, nullable=False, server_default="[]"),
        sa.Column("last_watched_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), onupdate=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("student_id", "lecture_id", name="uq_student_lecture_progress"),
    )
    op.create_index("ix_svp_student", "student_video_progress", ["student_id"])
    op.create_index("ix_svp_lecture", "student_video_progress", ["lecture_id"])

    op.create_table(
        "student_learning_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("lecture_id", UUID(as_uuid=True), sa.ForeignKey("lecture_videos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_sle_student", "student_learning_events", ["student_id"])
    op.create_index("ix_sle_created", "student_learning_events", ["created_at"])

def downgrade() -> None:
    op.drop_index("ix_sle_created", "student_learning_events")
    op.drop_index("ix_sle_student", "student_learning_events")
    op.drop_table("student_learning_events")
    op.drop_index("ix_svp_lecture", "student_video_progress")
    op.drop_index("ix_svp_student", "student_video_progress")
    op.drop_table("student_video_progress")
