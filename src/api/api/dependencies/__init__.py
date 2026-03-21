import threading
from collections.abc import AsyncGenerator

import structlog

from shared.database.connection import get_db as _get_db
from shared.database.connection import AsyncSession

logger = structlog.get_logger(__name__)

_celery_app = None
_celery_lock = threading.Lock()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in _get_db():
        yield session


def get_celery():
    global _celery_app
    if _celery_app is None:
        with _celery_lock:
            if _celery_app is None:
                from celery import Celery
                from api.config import get_api_settings

                settings = get_api_settings()
                _celery_app = Celery(
                    "gds_worker",
                    broker=settings.celery_broker_url,
                    backend="rpc://",
                )
                _celery_app.conf.update(
                    task_serializer="json",
                    result_serializer="json",
                    accept_content=["json"],
                )
    return _celery_app
