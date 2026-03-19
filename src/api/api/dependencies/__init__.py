"""
Package: api.dependencies

Re-exports everything from the original dependencies module so that existing
`from api.dependencies import get_db, get_minio, get_celery` imports keep working.
"""
import threading
from collections.abc import AsyncGenerator
from typing import Any

import boto3
import structlog

from shared.database.connection import get_db as _get_db
from shared.database.connection import AsyncSession

logger = structlog.get_logger(__name__)

_minio_client: Any = None
_minio_lock = threading.Lock()

_celery_app = None
_celery_lock = threading.Lock()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in _get_db():
        yield session


def get_minio():
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                from api.config import get_api_settings

                settings = get_api_settings()
                _minio_client = boto3.client(
                    "s3",
                    endpoint_url=f"{'https' if settings.minio_use_ssl else 'http'}://{settings.minio_endpoint}",
                    aws_access_key_id=settings.minio_access_key,
                    aws_secret_access_key=settings.minio_secret_key,
                    region_name="us-east-1",
                )
    return _minio_client


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
