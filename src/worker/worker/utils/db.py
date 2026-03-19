import uuid
from contextlib import contextmanager
from collections.abc import Generator
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
) -> None:
    from shared.database.models import LectureVideo
    from sqlalchemy import update as sa_update

    with get_sync_session() as session:
        values: dict = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        from shared.database.models import VideoStatus as _VS

        if status == _VS.COMPLETED:
            values["processed_at"] = text("NOW()")
        session.execute(
            sa_update(LectureVideo)
            .where(LectureVideo.id == uuid.UUID(lecture_id))
            .values(**values)
        )
        session.commit()
