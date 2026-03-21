import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from shared.database.connection import Base


# ─── Enums ────────────────────────────────────────────────────────────────────


class VideoStatus(str, enum.Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    SCENE_DETECTING = "SCENE_DETECTING"
    ASR = "ASR"
    OCR = "OCR"
    EMBEDDING = "EMBEDDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ChatRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    SCHOOL_ADMIN = "SCHOOL_ADMIN"
    FACULTY_ADMIN = "FACULTY_ADMIN"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"


# ─── Organization ─────────────────────────────────────────────────────────────


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    short_name = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    programs = relationship("Program", back_populates="organization")
    users = relationship("User", back_populates="organization")


# ─── User ─────────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(
        Enum(UserRole, name="user_role_enum"),
        nullable=False,
        default=UserRole.STUDENT,
    )
    organization_id = Column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    faculty = Column(String(255), nullable=True)

    student_code = Column(String(50), nullable=True)
    major = Column(String(255), nullable=True)

    teacher_code = Column(String(50), nullable=True)
    department = Column(String(255), nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="users")
    owned_lectures = relationship("LectureVideo", back_populates="owner")
    course_enrollments = relationship(
        "CourseEnrollment",
        back_populates="student",
        foreign_keys="CourseEnrollment.student_id",
    )
    course_teachings = relationship(
        "CourseTeacher",
        back_populates="teacher",
        foreign_keys="CourseTeacher.teacher_id",
    )


# ─── Program ──────────────────────────────────────────────────────────────────


class Program(Base):
    __tablename__ = "programs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    organization_id = Column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    organization = relationship("Organization", back_populates="programs")
    courses = relationship("Course", back_populates="program", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_programs_name", "name"),)


# ─── Course ───────────────────────────────────────────────────────────────────


class Course(Base):
    __tablename__ = "courses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(
        UUID(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    code = Column(String(50))
    description = Column(Text)
    faculty = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    program = relationship("Program", back_populates="courses")
    chapters = relationship("Chapter", back_populates="course", cascade="all, delete-orphan")
    enrollments = relationship(
        "CourseEnrollment", back_populates="course", cascade="all, delete-orphan"
    )
    teachers = relationship(
        "CourseTeacher", back_populates="course", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_courses_program_id", "program_id"),
        Index("ix_courses_name", "name"),
    )


# ─── Chapter ──────────────────────────────────────────────────────────────────


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id = Column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(500), nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    course = relationship("Course", back_populates="chapters")
    lectures = relationship(
        "LectureVideo", back_populates="chapter", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_chapters_course_id", "course_id"),)


# ─── LectureVideo ─────────────────────────────────────────────────────────────


class LectureVideo(Base):
    __tablename__ = "lecture_videos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chapter_id = Column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(500), nullable=False)
    minio_key = Column(String(1000), nullable=False)
    video_hash = Column(String(64), nullable=True, index=True)
    file_size_bytes = Column(Integer, nullable=True)
    status = Column(
        Enum(VideoStatus, name="video_status_enum"),
        nullable=False,
        default=VideoStatus.PENDING,
    )
    fps = Column(Float)
    duration_sec = Column(Float)
    frame_count = Column(BigInteger)       # tổng số frame của video (fps × duration)
    scene_count = Column(Integer)          # số scene detect được
    uploaded_by = Column(String(255))
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    error_message = Column(Text)
    # plain VARCHAR — dùng constants từ shared.constants.errors.ProcessingErrorCode
    error_code = Column(String(50), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    celery_task_id = Column(String(255))
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    processing_ended_at = Column(DateTime(timezone=True), nullable=True)
    processing_duration_sec = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True))
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chapter = relationship("Chapter", back_populates="lectures")
    scenes = relationship("Scene", back_populates="lecture", cascade="all, delete-orphan")
    transcript_chunks = relationship(
        "TranscriptChunk", back_populates="lecture", cascade="all, delete-orphan"
    )
    owner = relationship("User", back_populates="owned_lectures")

    __table_args__ = (
        Index("ix_lecture_videos_chapter_id", "chapter_id"),
        Index("ix_lecture_videos_status", "status"),
    )


# ─── Scene ────────────────────────────────────────────────────────────────────


class Scene(Base):
    __tablename__ = "scenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lecture_id = Column(
        UUID(as_uuid=True), ForeignKey("lecture_videos.id", ondelete="CASCADE"), nullable=False
    )
    shot_index = Column(Integer, nullable=False)
    frame_start = Column(BigInteger, nullable=False)
    frame_end = Column(BigInteger, nullable=False)
    timestamp_start = Column(Float, nullable=False)
    timestamp_end = Column(Float, nullable=False)
    keyframe_minio_key = Column(String(1000))
    transcript = Column(Text)
    ocr_text = Column(Text)
    visual_tags = Column(ARRAY(String))
    # legacy combined FTS (backward compat)
    fts_vector = Column(TSVECTOR)
    # split FTS — transcript và OCR riêng để query độc lập
    transcript_fts = Column(TSVECTOR)
    ocr_fts = Column(TSVECTOR)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    lecture = relationship("LectureVideo", back_populates="scenes")
    embedding = relationship(
        "SceneEmbedding", back_populates="scene", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_scenes_lecture_id", "lecture_id"),
        Index("ix_scenes_fts_vector", "fts_vector", postgresql_using="gin"),
        Index("ix_scenes_transcript_fts", "transcript_fts", postgresql_using="gin"),
        Index("ix_scenes_ocr_fts", "ocr_fts", postgresql_using="gin"),
        Index("ix_scenes_lecture_shot", "lecture_id", "shot_index", unique=True),
    )


# ─── TranscriptChunk ──────────────────────────────────────────────────────────


class TranscriptChunk(Base):
    """
    Semantic transcript segment — cắt theo khoảng im lặng + overlap,
    không bị hard-cut theo scene boundary.
    """
    __tablename__ = "transcript_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lecture_id = Column(
        UUID(as_uuid=True), ForeignKey("lecture_videos.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    start_sec = Column(Float, nullable=False)
    end_sec = Column(Float, nullable=False)
    overlap_prev_sec = Column(Float, nullable=False, default=0.0)
    overlap_next_sec = Column(Float, nullable=False, default=0.0)
    # scene nào overlap với chunk này
    scene_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    text_embedding = Column(Vector(1024))
    fts_vector = Column(TSVECTOR)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lecture = relationship("LectureVideo", back_populates="transcript_chunks")

    __table_args__ = (
        Index("ix_transcript_chunks_lecture_id", "lecture_id"),
        Index("ix_transcript_chunks_lecture_idx", "lecture_id", "chunk_index", unique=True),
        Index("ix_transcript_chunks_fts", "fts_vector", postgresql_using="gin"),
    )


# ─── SceneEmbedding ───────────────────────────────────────────────────────────


class SceneEmbedding(Base):
    __tablename__ = "scene_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    image_embedding = Column(Vector(768))   # CLIP ViT-L/14 — visual search
    text_embedding = Column(Vector(1024))   # multilingual-e5-large — text search
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    scene = relationship("Scene", back_populates="embedding")

    __table_args__ = (Index("ix_scene_embeddings_scene_id", "scene_id"),)


# ─── CourseEnrollment ─────────────────────────────────────────────────────────


class CourseEnrollment(Base):
    __tablename__ = "course_enrollments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id = Column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    student_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    enrolled_at = Column(DateTime(timezone=True), server_default=func.now())

    course = relationship("Course", back_populates="enrollments")
    student = relationship(
        "User", back_populates="course_enrollments", foreign_keys=[student_id]
    )

    __table_args__ = (UniqueConstraint("course_id", "student_id", name="uq_enrollment"),)


# ─── CourseTeacher ────────────────────────────────────────────────────────────


class CourseTeacher(Base):
    __tablename__ = "course_teachers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id = Column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    teacher_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())

    course = relationship("Course", back_populates="teachers")
    teacher = relationship(
        "User", back_populates="course_teachings", foreign_keys=[teacher_id]
    )

    __table_args__ = (UniqueConstraint("course_id", "teacher_id", name="uq_course_teacher"),)


# ─── ChatSession ──────────────────────────────────────────────────────────────


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(255))
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.sequence_num",
    )

    __table_args__ = (
        Index("ix_chat_sessions_user_id", "user_id"),
        Index("ix_chat_sessions_course_id", "course_id"),
    )


# ─── ChatMessage ──────────────────────────────────────────────────────────────


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(Enum(ChatRole, name="chat_role_enum"), nullable=False)
    content = Column(Text, nullable=False)
    sequence_num = Column(Integer, nullable=False, default=0)
    # plain VARCHAR — dùng constants từ shared.constants.messages.MessageStatus
    status = Column(String(20), nullable=False, default="DONE")
    duration_ms = Column(Integer, nullable=True)
    # Flexible JSONB — chứa agent_steps, citations, tool results
    # Schema: {"agent_steps": [...], "citations": [...]}
    metadata = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("ix_chat_messages_session_id", "session_id"),
        Index("ix_chat_messages_session_seq", "session_id", "sequence_num"),
    )


# ─── UploadBatch ──────────────────────────────────────────────────────────────


class BatchStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status = Column(
        Enum(BatchStatus, name="batch_status_enum"),
        default=BatchStatus.PROCESSING,
        nullable=False,
    )
    total = Column(Integer, nullable=False, default=0)
    succeeded = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    processing_completed_at = Column(DateTime(timezone=True), nullable=True)
    total_processing_sec = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # items: [{lecture_id, task_id, filename, status, started_at, ended_at, processing_sec, scene_count, error_code}]
    items = Column(JSONB, nullable=False, default=list)


# ─── Learning Analytics ───────────────────────────────────────────────────────


class StudentVideoProgress(Base):
    __tablename__ = "student_video_progress"
    __table_args__ = (UniqueConstraint("student_id", "lecture_id", name="uq_student_lecture_progress"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lecture_id = Column(UUID(as_uuid=True), ForeignKey("lecture_videos.id", ondelete="CASCADE"), nullable=False)
    watched_seconds = Column(Float, nullable=False, default=0.0)
    completed = Column(Boolean, nullable=False, default=False)
    last_position_sec = Column(Float, nullable=False, default=0.0)
    scenes_viewed = Column(JSONB, nullable=False, default=list)
    last_watched_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("User", foreign_keys=[student_id])
    lecture = relationship("LectureVideo", foreign_keys=[lecture_id])


class StudentLearningEvent(Base):
    __tablename__ = "student_learning_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    lecture_id = Column(UUID(as_uuid=True), ForeignKey("lecture_videos.id", ondelete="SET NULL"), nullable=True)
    scene_id = Column(UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("User", foreign_keys=[student_id])
