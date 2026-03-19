from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    q: str
    mode: Literal["keyword", "semantic"] = "keyword"
    course_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchResult(BaseModel):
    scene_id: UUID
    lecture_id: UUID
    lecture_title: str
    chapter_title: str
    course_name: str
    timestamp_start: float
    timestamp_end: float
    transcript: str | None
    ocr_text: str | None
    keyframe_url: str | None
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    query: str
    mode: str
