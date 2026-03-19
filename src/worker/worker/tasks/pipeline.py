import uuid
from pathlib import Path

import structlog

from worker.app import app
from worker.utils.db import update_lecture_status_sync

logger = structlog.get_logger(__name__)


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def run_pipeline(self, lecture_id: str) -> dict:
    from celery import chord, group
    from shared.database.models import VideoStatus
    from shared.config import get_settings
    from worker.utils.storage import download_file
    from worker.tasks.scene_detection import detect_scenes
    from worker.tasks.asr import run_asr
    from worker.tasks.ocr import run_ocr
    from worker.tasks.clip_embed import run_clip_embed
    from worker.tasks.indexing import run_indexing

    log = logger.bind(lecture_id=lecture_id, task_id=self.request.id)
    log.info("pipeline_started")

    try:
        update_lecture_status_sync(lecture_id, VideoStatus.DOWNLOADING)

        settings = get_settings()

        with _get_minio_key(lecture_id) as minio_key:
            tmp_dir = Path("/tmp/gds_worker") / lecture_id
            tmp_dir.mkdir(parents=True, exist_ok=True)

            video_filename = Path(minio_key).name
            video_tmp_path = tmp_dir / video_filename

            download_file(settings.minio_bucket_videos, minio_key, video_tmp_path)
            log.info("video_downloaded", path=str(video_tmp_path))

        scene_result = detect_scenes.apply(args=[lecture_id, str(video_tmp_path)]).get()

        scene_ids = scene_result["scene_ids"]
        keyframe_paths = scene_result["keyframe_paths"]

        asr_task = run_asr.s(lecture_id, str(video_tmp_path))
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
        log.error("pipeline_failed", error=str(exc))
        from shared.database.models import VideoStatus

        update_lecture_status_sync(lecture_id, VideoStatus.FAILED, error_message=str(exc))
        raise self.retry(exc=exc)


def _get_minio_key(lecture_id: str):
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
