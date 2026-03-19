from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ChatMessageRequest(BaseModel):
    content: str
    role: str = "user"


class Citation(BaseModel):
    lecture_title: str
    chapter_title: str
    timestamp_start: float
    timestamp_end: float
    keyframe_url: str | None = None
    deep_link: str


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    citations: list[Citation] = []
    tool_calls_used: list[str] = []


class SessionCreate(BaseModel):
    user_id: str | None = None
    course_id: UUID | None = None


class SessionResponse(BaseModel):
    id: UUID
    user_id: str | None
    course_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}
