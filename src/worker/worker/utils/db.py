import uuid
from contextlib import contextmanager
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

logger = structlog.get_logger(__name__)

_sync_engine = None
_SyncSession: sessionmaker | None = None


def _get_sync_engine():
    global _sync_engine, _SyncSession
    if _sync_engine is None:
        from shared.config import get_settings

        settings = get_settings()
        _sync_engine = create_engine(
            settings.database_url_sync,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        _SyncSession = sessionmaker(bind=_sync_engine, autoflush=False, autocommit=False)
    return _sync_engine, _SyncSession


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    _, session_factory = _get_sync_engine()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_lecture_status_sync(
    lecture_id: str,
    status: Any,
    error_message: str | None = None,
    error_code: str | None = None,
) -> None:
    from shared.database.models import LectureVideo, VideoStatus
    from sqlalchemy import update as sa_update

    with get_sync_session() as session:
        values: dict = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if error_code is not None:
            values["error_code"] = error_code
        if status == VideoStatus.COMPLETED:
            values["processed_at"] = text("NOW()")
        session.execute(
            sa_update(LectureVideo)
            .where(LectureVideo.id == uuid.UUID(lecture_id))
            .values(**values)
        )
        session.commit()


def mark_processing_started(lecture_id: str) -> None:
    from shared.database.models import LectureVideo
    from sqlalchemy import update as sa_update

    with get_sync_session() as session:
        session.execute(
            sa_update(LectureVideo)
            .where(LectureVideo.id == uuid.UUID(lecture_id))
            .values(processing_started_at=text("NOW()"))
        )
        session.commit()


def mark_processing_ended(lecture_id: str, scene_count: int | None = None) -> None:
    """Set processing_ended_at and compute processing_duration_sec."""
    from shared.database.models import LectureVideo
    from sqlalchemy import update as sa_update

    with get_sync_session() as session:
        row = session.get(LectureVideo, uuid.UUID(lecture_id))
        if row is None:
            return
        now = datetime.now(timezone.utc)
        duration = None
        if row.processing_started_at:
            duration = (now - row.processing_started_at).total_seconds()
        values: dict = {
            "processing_ended_at": now,
            "processing_duration_sec": duration,
        }
        if scene_count is not None:
            values["scene_count"] = scene_count
        session.execute(
            sa_update(LectureVideo)
            .where(LectureVideo.id == uuid.UUID(lecture_id))
            .values(**values)
        )
        session.commit()


def increment_retry_count(lecture_id: str) -> None:
    from shared.database.models import LectureVideo
    from sqlalchemy import update as sa_update

    with get_sync_session() as session:
        session.execute(
            sa_update(LectureVideo)
            .where(LectureVideo.id == uuid.UUID(lecture_id))
            .values(retry_count=LectureVideo.retry_count + 1)
        )
        session.commit()


def estimate_compute_cost(duration_sec: float, scene_count: int) -> dict:
    """Rough GPU-second estimate per processing step."""
    return {
        "transnet_sec": round(duration_sec * 0.012, 2),
        "whisper_sec":  round(duration_sec * 0.15, 2),
        "ocr_sec":      round(scene_count  * 0.008, 2),
        "clip_sec":     round(scene_count  * 0.005, 2),
        "embed_sec":    round(scene_count  * 0.003, 2),
        "total_gpu_sec": round(
            duration_sec * 0.012
            + duration_sec * 0.15
            + scene_count * (0.008 + 0.005 + 0.003),
            2,
        ),
    }
