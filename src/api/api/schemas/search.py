from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    q: str
    course_id: UUID | None = None
    n_videos: int = Field(default=5, ge=1, le=20, description="Số video trả về")
    candidate_k: int = Field(default=100, ge=20, le=500,
                             description="Số candidate mỗi arm trước khi RRF")


class SceneSnippet(BaseModel):
    scene_id: UUID
    timestamp_start: float
    timestamp_end: float
    transcript: str | None
    ocr_text: str | None
    keyframe_url: str | None
    kw_score: float
    text_score: float
    visual_score: float
    rrf_score: float


class VideoSearchResult(BaseModel):
    lecture_id: UUID
    lecture_title: str
    chapter_title: str
    course_name: str
    duration_sec: float | None
    video_score: float          # RRF aggregated + multi-scene boost
    matching_scene_count: int   # số scene liên quan trong video này
    best_scene: SceneSnippet    # cảnh liên quan nhất để deep link
    top_scenes: list[SceneSnippet]  # top 3 cảnh preview


class SearchResponse(BaseModel):
    results: list[VideoSearchResult]
    total_videos: int
    query: str
