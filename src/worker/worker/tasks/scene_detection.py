import uuid
from pathlib import Path

import cv2
import structlog

from worker.app import app
from worker.utils.db import get_sync_session, update_lecture_status_sync

logger = structlog.get_logger(__name__)


@app.task(bind=True, queue="gpu.high", max_retries=3, default_retry_delay=60)
def detect_scenes(self, lecture_id: str, video_tmp_path: str) -> dict:
    from shared.database.models import Scene, VideoStatus
    from worker.models.loader import get_transnetv2
    from worker.utils.storage import upload_file
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

        from shared.config import get_settings

        settings = get_settings()
        tmp_frames_dir = Path("/tmp/gds_worker") / f"frames_{lecture_id}"
        tmp_frames_dir.mkdir(parents=True, exist_ok=True)

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
                local_frame_path: Path | None = None

                if frame is not None:
                    import cv2 as _cv2

                    local_frame_path = tmp_frames_dir / f"shot_{shot_idx:04d}.jpg"
                    _cv2.imwrite(str(local_frame_path), frame)

                    minio_key = f"{lecture_id}/shot_{shot_idx:04d}.jpg"
                    upload_file(local_frame_path, settings.minio_bucket_frames, minio_key)

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
                if local_frame_path:
                    keyframe_paths.append(str(local_frame_path))

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
        log.error("scene_detection_failed", error=str(exc))
        update_lecture_status_sync(lecture_id, VideoStatus.FAILED, error_message=str(exc))
        raise self.retry(exc=exc)
