from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SceneResponse(BaseModel):
    id: UUID
    shot_index: int
    timestamp_start: float
    timestamp_end: float
    transcript: str | None
    ocr_text: str | None
    visual_tags: list[str] | None
    keyframe_url: str | None

    model_config = {"from_attributes": True}


class LectureResponse(BaseModel):
    id: UUID
    title: str
    status: str
    fps: float | None
    duration_sec: float | None
    scenes: list[SceneResponse]
    video_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProgramCreate(BaseModel):
    name: str
    description: str | None = None


class ProgramResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CourseCreate(BaseModel):
    name: str
    code: str | None = None
    description: str | None = None


class CourseResponse(BaseModel):
    id: UUID
    program_id: UUID
    name: str
    code: str | None
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChapterCreate(BaseModel):
    title: str
    order_index: int = 0


class ChapterResponse(BaseModel):
    id: UUID
    course_id: UUID
    title: str
    order_index: int
    created_at: datetime

    model_config = {"from_attributes": True}


class LectureUpdate(BaseModel):
    title: str | None = None
