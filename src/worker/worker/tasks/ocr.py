from pathlib import Path

import structlog

from worker.app import app
from worker.utils.retry import classify_error, get_retry_params, is_retryable

logger = structlog.get_logger(__name__)


@app.task(bind=True, queue="gpu.medium", max_retries=3, default_retry_delay=60)
def run_ocr(self, scene_ids: list[str], keyframe_paths: list[str]) -> dict:
    from worker.models.loader import get_ocr

    log = logger.bind(task_id=self.request.id, scene_count=len(scene_ids))
    log.info("ocr_started")

    try:
        ocr_model = get_ocr()
        ocr_results: dict[str, str] = {}

        for scene_id, keyframe_path in zip(scene_ids, keyframe_paths):
            path = Path(keyframe_path)
            if not path.exists():
                ocr_results[scene_id] = ""
                continue
            text = ocr_model.extract_text(path)
            ocr_results[scene_id] = text

        log.info("ocr_completed", processed=len(ocr_results))
        return {"ocr_results": ocr_results}

    except Exception as exc:
        error_code = classify_error(exc)
        log.error("ocr_failed", error=str(exc), error_code=error_code.value)
        if not is_retryable(error_code):
            return {"ocr_results": {}}

        params = get_retry_params(error_code)
        if self.request.retries >= params["max_retries"]:
            return {"ocr_results": {}}
        raise self.retry(exc=exc, countdown=params["countdown"])
