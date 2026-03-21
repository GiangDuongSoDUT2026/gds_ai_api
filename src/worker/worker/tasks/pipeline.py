import uuid
from pathlib import Path

import structlog

from worker.app import app
from worker.utils.db import (
    update_lecture_status_sync,
    mark_processing_started,
    mark_processing_ended,
    increment_retry_count,
)
from worker.utils.retry import classify_error, get_retry_params, is_retryable

logger = structlog.get_logger(__name__)


@app.task(bind=True, max_retries=5, default_retry_delay=60)
def run_pipeline(self, lecture_id: str) -> dict:
    from celery import chord, group
    from shared.database.models import VideoStatus
    from shared.config import get_settings
    from worker.tasks.scene_detection import detect_scenes
    from worker.tasks.asr import run_asr
    from worker.tasks.ocr import run_ocr
    from worker.tasks.clip_embed import run_clip_embed
    from worker.tasks.indexing import run_indexing

    log = logger.bind(lecture_id=lecture_id, task_id=self.request.id)
    log.info("pipeline_started")

    try:
        mark_processing_started(lecture_id)
        update_lecture_status_sync(lecture_id, VideoStatus.DOWNLOADING)

        settings = get_settings()

        with _get_file_key(lecture_id) as minio_key:
            video_path = Path(settings.storage_path) / settings.storage_bucket_videos / minio_key
            if not video_path.exists():
                raise FileNotFoundError(f"Video not found at {video_path}")
            log.info("video_located", path=str(video_path))

        scene_result = detect_scenes.apply(args=[lecture_id, str(video_path)]).get()

        scene_ids = scene_result["scene_ids"]
        keyframe_paths = scene_result["keyframe_paths"]

        asr_task = run_asr.s(lecture_id, str(video_path))
        ocr_task = run_ocr.s(scene_ids, keyframe_paths)
        clip_task = run_clip_embed.s(scene_ids, keyframe_paths)

        update_lecture_status_sync(lecture_id, VideoStatus.ASR)

        result = chord(
            group(asr_task, ocr_task, clip_task),
            run_indexing.s(lecture_id=lecture_id),
        ).apply_async()

        log.info("pipeline_chord_dispatched")
        return {"lecture_id": lecture_id, "status": "PROCESSING", "chord_id": result.id}

    except Exception as exc:
        error_code = classify_error(exc)
        log.error("pipeline_failed", error=str(exc), error_code=error_code.value)

        update_lecture_status_sync(
            lecture_id,
            VideoStatus.FAILED,
            error_message=str(exc),
            error_code=error_code.value,
        )

        if not is_retryable(error_code):
            log.warning("pipeline_non_retryable", error_code=error_code.value)
            return {"lecture_id": lecture_id, "status": "FAILED", "error_code": error_code.value}

        increment_retry_count(lecture_id)
        params = get_retry_params(error_code)
        if self.request.retries >= params["max_retries"]:
            log.error("pipeline_max_retries_exceeded", retries=self.request.retries)
            return {"lecture_id": lecture_id, "status": "FAILED", "error_code": error_code.value}

        raise self.retry(exc=exc, countdown=params["countdown"])


def _get_file_key(lecture_id: str):
    from contextlib import contextmanager
    from worker.utils.db import get_sync_session
    from shared.database.models import LectureVideo

    @contextmanager
    def ctx():
        with get_sync_session() as session:
            lecture = session.get(LectureVideo, uuid.UUID(lecture_id))
            if lecture is None:
                raise ValueError(f"LectureVideo {lecture_id} not found")
            yield lecture.minio_key

    return ctx()
