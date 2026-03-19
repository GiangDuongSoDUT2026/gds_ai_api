import threading
from pathlib import Path
from typing import Any

import boto3
import structlog

logger = structlog.get_logger(__name__)

_minio_client: Any = None
_minio_lock = threading.Lock()


def get_minio_client():
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                from shared.config import get_settings

                settings = get_settings()
                _minio_client = boto3.client(
                    "s3",
                    endpoint_url=f"{'https' if settings.minio_use_ssl else 'http'}://{settings.minio_endpoint}",
                    aws_access_key_id=settings.minio_access_key,
                    aws_secret_access_key=settings.minio_secret_key,
                    region_name="us-east-1",
                )
    return _minio_client


def upload_file(local_path: Path, bucket: str, key: str) -> str:
    from shared.config import get_settings

    client = get_minio_client()
    settings = get_settings()

    client.upload_file(str(local_path), bucket, key)
    logger.info("uploaded_file", bucket=bucket, key=key)

    return f"{settings.minio_public_url}/{bucket}/{key}"


def download_file(bucket: str, key: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client = get_minio_client()
    client.download_file(bucket, key, str(local_path))
    logger.info("downloaded_file", bucket=bucket, key=key, local_path=str(local_path))
    return local_path


def generate_presigned_url(bucket: str, key: str, expires: int = 3600) -> str:
    client = get_minio_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
    return url
