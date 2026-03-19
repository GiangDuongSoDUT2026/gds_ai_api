from pydantic import BaseModel


class JobStatus(BaseModel):
    task_id: str
    status: str
    result: dict | None = None
    error: str | None = None
