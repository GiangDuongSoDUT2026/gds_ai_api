from pathlib import Path

import structlog

from worker.app import app
from worker.utils.db import update_lecture_status_sync

logger = structlog.get_logger(__name__)


@app.task(bind=True, queue="gpu.medium", max_retries=3, default_retry_delay=60)
def run_asr(self, lecture_id: str, video_tmp_path: str) -> dict:
    from shared.database.models import VideoStatus
    from worker.models.loader import get_whisper
    from worker.utils.video import extract_audio

    log = logger.bind(lecture_id=lecture_id, task_id=self.request.id)
    log.info("asr_started")

    try:
        update_lecture_status_sync(lecture_id, VideoStatus.ASR)

        video_path = Path(video_tmp_path)
        audio_path = video_path.parent / f"{video_path.stem}_audio.wav"

        extract_audio(video_path, audio_path)
        log.info("audio_extracted", path=str(audio_path))

        whisper = get_whisper()
        segments = whisper.transcribe(audio_path)

        if audio_path.exists():
            audio_path.unlink()

        log.info("asr_completed", segment_count=len(segments))
        return {
            "lecture_id": lecture_id,
            "segments": segments,
        }

    except Exception as exc:
        log.error("asr_failed", error=str(exc))
        raise self.retry(exc=exc)
