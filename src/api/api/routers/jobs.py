import structlog
from celery.result import AsyncResult
from fastapi import APIRouter, Depends

from api.dependencies import get_celery
from api.schemas.job import JobStatus

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = structlog.get_logger(__name__)


@router.get("/{task_id}", response_model=JobStatus)
async def get_job_status(
    task_id: str,
    celery_app=Depends(get_celery),
) -> JobStatus:
    result = AsyncResult(task_id, app=celery_app)

    error: str | None = None
    job_result: dict | None = None

    if result.status == "FAILURE":
        error = str(result.result) if result.result else "Unknown error"
    elif result.status == "SUCCESS" and result.result:
        if isinstance(result.result, dict):
            job_result = result.result

    return JobStatus(
        task_id=task_id,
        status=result.status,
        result=job_result,
        error=error,
    )
