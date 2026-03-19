from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    transnetv2_weights_dir: str = "/app/weights/transnetv2"
    whisper_model: str = "large-v3"
    clip_model: str = "ViT-L/14"
    text_embed_model: str = "intfloat/multilingual-e5-large"
    cuda_visible_devices: str = "0"
    tmp_dir: Path = Path("/tmp/gds_worker")


_worker_settings: WorkerSettings | None = None


def get_worker_settings() -> WorkerSettings:
    global _worker_settings
    if _worker_settings is None:
        _worker_settings = WorkerSettings()
    return _worker_settings
