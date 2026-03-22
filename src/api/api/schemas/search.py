from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Scene-level result — matches frontend SearchResult type."""
    scene_id: str
    lecture_id: str
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
