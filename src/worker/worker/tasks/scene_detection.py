import uuid
from pathlib import Path

import cv2
import structlog

from worker.app import app
from worker.utils.db import get_sync_session, update_lecture_status_sync, increment_retry_count
from worker.utils.retry import classify_error, get_retry_params, is_retryable

logger = structlog.get_logger(__name__)


@app.task(bind=True, queue="gpu.high", max_retries=3, default_retry_delay=60)
def detect_scenes(self, lecture_id: str, video_tmp_path: str) -> dict:
    from shared.database.models import Scene, VideoStatus
    from shared.config import get_settings
    from worker.models.loader import get_transnetv2
    from worker.utils.video import extract_frame

    log = logger.bind(lecture_id=lecture_id, task_id=self.request.id)
    log.info("scene_detection_started")

    try:
        update_lecture_status_sync(lecture_id, VideoStatus.SCENE_DETECTING)

        model = get_transnetv2()
        scenes_data = model.detect_scenes(video_tmp_path, threshold=0.01)
        log.info("scenes_detected", count=len(scenes_data))

        video_path = Path(video_tmp_path)
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        settings = get_settings()
        scene_ids: list[str] = []
        keyframe_paths: list[str] = []

        with get_sync_session() as session:
            for scene_info in scenes_data:
                shot_idx = scene_info["shot_index"]
                frame_start = scene_info["frame_start"]
                frame_end = scene_info["frame_end"]
                keyframe_frame_idx = (frame_start + frame_end) // 2

                frame = extract_frame(video_path, keyframe_frame_idx)

                minio_key: str | None = None
                frame_dest_path: Path | None = None

                if frame is not None:
                    import cv2 as _cv2

                    frame_key = f"{lecture_id}/shot_{shot_idx:04d}.jpg"
                    frame_dest_path = (
                        Path(settings.storage_path) / settings.storage_bucket_frames / frame_key
                    )
                    frame_dest_path.parent.mkdir(parents=True, exist_ok=True)
                    _cv2.imwrite(str(frame_dest_path), frame)
                    minio_key = frame_key

                scene = Scene(
                    id=uuid.uuid4(),
                    lecture_id=uuid.UUID(lecture_id),
                    shot_index=shot_idx,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    timestamp_start=scene_info["timestamp_start"],
                    timestamp_end=scene_info["timestamp_end"],
                    keyframe_minio_key=minio_key,
                )
                session.add(scene)
                session.flush()

                scene_ids.append(str(scene.id))
                if frame_dest_path:
                    keyframe_paths.append(str(frame_dest_path))

            from sqlalchemy import update as sa_update
            from shared.database.models import LectureVideo

            session.execute(
                sa_update(LectureVideo)
                .where(LectureVideo.id == uuid.UUID(lecture_id))
                .values(fps=fps, frame_count=total_frames)
            )
            session.commit()

        log.info("scene_detection_completed", scene_count=len(scene_ids))
        return {
            "lecture_id": lecture_id,
            "scene_ids": scene_ids,
            "keyframe_paths": keyframe_paths,
            "video_tmp_path": video_tmp_path,
        }

    except Exception as exc:
        error_code = classify_error(exc)
        log.error("scene_detection_failed", error=str(exc), error_code=error_code.value)
        update_lecture_status_sync(
            lecture_id, VideoStatus.FAILED,
            error_message=str(exc), error_code=error_code.value,
        )
        if not is_retryable(error_code):
            return {"lecture_id": lecture_id, "status": "FAILED", "error_code": error_code.value}

        increment_retry_count(lecture_id)
        params = get_retry_params(error_code)
        if self.request.retries >= params["max_retries"]:
            return {"lecture_id": lecture_id, "status": "FAILED", "error_code": error_code.value}
        raise self.retry(exc=exc, countdown=params["countdown"])
