import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _storage_root() -> Path:
    from shared.config import get_settings

    root = Path(get_settings().storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def upload_file(local_path: Path, bucket: str, key: str) -> str:
    from shared.config import get_settings

    dest = _storage_root() / bucket / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(local_path), str(dest))
    logger.info("stored_file", bucket=bucket, key=key)

    settings = get_settings()
    return f"{settings.storage_base_url}/{bucket}/{key}"


def download_file(bucket: str, key: str, local_path: Path) -> Path:
    src = _storage_root() / bucket / key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(local_path))
    logger.info("retrieved_file", bucket=bucket, key=key, local_path=str(local_path))
    return local_path


def get_file_url(bucket: str, key: str) -> str:
    from shared.config import get_settings

    return f"{get_settings().storage_base_url}/{bucket}/{key}"
