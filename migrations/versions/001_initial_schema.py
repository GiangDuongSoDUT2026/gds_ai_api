"""initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_programs_name", "programs", ["name"])

    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50)),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_courses_program_id", "courses", ["program_id"])
    op.create_index("ix_courses_name", "courses", ["name"])

    op.create_table(
        "chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("order_index", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chapters_course_id", "chapters", ["course_id"])

    op.execute(
        """
        CREATE TYPE video_status_enum AS ENUM (
            'PENDING', 'DOWNLOADING', 'SCENE_DETECTING', 'ASR', 'OCR', 'EMBEDDING', 'COMPLETED', 'FAILED'
        )
        """
    )

    op.create_table(
        "lecture_videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("minio_key", sa.String(1000), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "DOWNLOADING", "SCENE_DETECTING", "ASR", "OCR", "EMBEDDING", "COMPLETED", "FAILED",
                name="video_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("fps", sa.Float),
        sa.Column("duration_sec", sa.Float),
        sa.Column("frame_count", sa.BigInteger),
        sa.Column("uploaded_by", sa.String(255)),
        sa.Column("error_message", sa.Text),
        sa.Column("celery_task_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lecture_videos_chapter_id", "lecture_videos", ["chapter_id"])
    op.create_index("ix_lecture_videos_status", "lecture_videos", ["status"])

    op.create_table(
        "scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("lecture_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("lecture_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shot_index", sa.Integer, nullable=False),
        sa.Column("frame_start", sa.BigInteger, nullable=False),
        sa.Column("frame_end", sa.BigInteger, nullable=False),
        sa.Column("timestamp_start", sa.Float, nullable=False),
        sa.Column("timestamp_end", sa.Float, nullable=False),
        sa.Column("keyframe_minio_key", sa.String(1000)),
        sa.Column("transcript", sa.Text),
        sa.Column("ocr_text", sa.Text),
        sa.Column("visual_tags", postgresql.ARRAY(sa.String)),
        sa.Column("fts_vector", postgresql.TSVECTOR),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scenes_lecture_id", "scenes", ["lecture_id"])
    op.create_index("ix_scenes_fts_vector", "scenes", ["fts_vector"], postgresql_using="gin")
    op.create_index("ix_scenes_lecture_shot", "scenes", ["lecture_id", "shot_index"], unique=True)
    op.execute(
        "CREATE INDEX ix_scenes_ocr_trgm ON scenes USING gin (ocr_text gin_trgm_ops) WHERE ocr_text IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_scenes_transcript_trgm ON scenes USING gin (transcript gin_trgm_ops) WHERE transcript IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE scene_embeddings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            scene_id UUID NOT NULL UNIQUE REFERENCES scenes(id) ON DELETE CASCADE,
            image_embedding vector(768),
            text_embedding vector(1024),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.create_index("ix_scene_embeddings_scene_id", "scene_embeddings", ["scene_id"])
    op.execute(
        """
        CREATE INDEX ix_scene_embeddings_image_hnsw
        ON scene_embeddings
        USING hnsw (image_embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_scene_embeddings_text_hnsw
        ON scene_embeddings
        USING hnsw (text_embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    op.execute(
        "CREATE TYPE chat_role_enum AS ENUM ('user', 'assistant', 'tool')"
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.String(255)),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("courses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_index("ix_chat_sessions_course_id", "chat_sessions", ["course_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM("user", "assistant", "tool", name="chat_role_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_calls", postgresql.JSONB),
        sa.Column("citations", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql'
        """
    )

    for table in ["programs", "courses", "chapters", "lecture_videos", "scenes", "scene_embeddings", "chat_sessions"]:
        op.execute(
            f"""
            CREATE TRIGGER update_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
            """
        )


def downgrade() -> None:
    for table in ["programs", "courses", "chapters", "lecture_videos", "scenes", "scene_embeddings", "chat_sessions"]:
        op.execute(f"DROP TRIGGER IF EXISTS update_{table}_updated_at ON {table}")

    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")

    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.execute("DROP TABLE IF EXISTS scene_embeddings")
    op.drop_table("scenes")
    op.drop_table("lecture_videos")
    op.drop_table("chapters")
    op.drop_table("courses")
    op.drop_table("programs")

    op.execute("DROP TYPE IF EXISTS chat_role_enum")
    op.execute("DROP TYPE IF EXISTS video_status_enum")

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
