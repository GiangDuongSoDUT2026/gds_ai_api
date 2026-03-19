from pathlib import Path

import structlog

from worker.app import app

logger = structlog.get_logger(__name__)


@app.task(bind=True, queue="gpu.medium", max_retries=3, default_retry_delay=60)
def run_clip_embed(self, scene_ids: list[str], keyframe_paths: list[str]) -> dict:
    from worker.models.loader import get_clip

    log = logger.bind(task_id=self.request.id, scene_count=len(scene_ids))
    log.info("clip_embed_started")

    try:
        clip_model = get_clip()
        embeddings: dict[str, list[float]] = {}

        for scene_id, keyframe_path in zip(scene_ids, keyframe_paths):
            path = Path(keyframe_path)
            if not path.exists():
                continue

            embedding = clip_model.embed_image(path)
            embeddings[scene_id] = embedding

        log.info("clip_embed_completed", processed=len(embeddings))
        return {"embeddings": embeddings}

    except Exception as exc:
        log.error("clip_embed_failed", error=str(exc))
        raise self.retry(exc=exc)
