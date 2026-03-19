from uuid import UUID

from pydantic import BaseModel


class UploadVideoRequest(BaseModel):
    chapter_id: UUID
    title: str
    uploaded_by: str | None = None


class UploadVideoResponse(BaseModel):
    lecture_id: UUID
    task_id: str
    status: str
    message: str
