from functools import lru_cache

from pydantic_settings import SettingsConfigDict

from shared.config import Settings


class ApiSettings(Settings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = "change-me-in-production"
    max_upload_size_bytes: int = 10 * 1024**3


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
