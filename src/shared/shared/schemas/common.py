import enum
from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel


class UUIDModel(BaseModel):
    id: UUID


class TimestampModel(BaseModel):
    created_at: datetime


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class VideoStatus(str, enum.Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    SCENE_DETECTING = "SCENE_DETECTING"
    ASR = "ASR"
    OCR = "OCR"
    EMBEDDING = "EMBEDDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
