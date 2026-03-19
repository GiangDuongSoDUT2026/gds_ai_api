import shutil
import uuid
from pathlib import Path

import structlog

from worker.app import app
from worker.utils.db import get_sync_session, update_lecture_status_sync

logger = structlog.get_logger(__name__)


def _assign_transcript_to_scenes(
    scenes: list,
    asr_segments: list[dict],
) -> dict[str, str]:
    assignment: dict[str, str] = {}

    for scene in scenes:
        scene_id = str(scene.id)
        texts: list[str] = []

        for seg in asr_segments:
            seg_start = seg["start"]
            seg_end = seg["end"]

            if seg_end <= scene.timestamp_start or seg_start >= scene.timestamp_end:
                continue

            texts.append(seg["text"])

        assignment[scene_id] = " ".join(texts).strip()

    return assignment


@app.task(bind=True, queue="db", max_retries=5, default_retry_delay=30)
def run_indexing(self, group_results: list, lecture_id: str) -> dict:
    from sqlalchemy import func, text as sa_text, update as sa_update
    from shared.database.models import LectureVideo, Scene, SceneEmbedding, VideoStatus
    from worker.models.loader import get_text_embedder

    log = logger.bind(lecture_id=lecture_id, task_id=self.request.id)
    log.info("indexing_started")

    try:
        asr_result = next((r for r in group_results if "segments" in r), None)
        ocr_result = next((r for r in group_results if "ocr_results" in r), None)
        clip_result = next((r for r in group_results if "embeddings" in r), None)

        asr_segments: list[dict] = asr_result["segments"] if asr_result else []
        ocr_texts: dict[str, str] = ocr_result["ocr_results"] if ocr_result else {}
        clip_embeddings: dict[str, list[float]] = clip_result["embeddings"] if clip_result else {}

        text_embedder = get_text_embedder()

        update_lecture_status_sync(lecture_id, VideoStatus.EMBEDDING)

        with get_sync_session() as session:
            scenes = (
                session.query(Scene)
                .filter(Scene.lecture_id == uuid.UUID(lecture_id))
                .order_by(Scene.shot_index)
                .all()
            )

            transcript_map = _assign_transcript_to_scenes(scenes, asr_segments)

            for scene in scenes:
                scene_id_str = str(scene.id)
                transcript = transcript_map.get(scene_id_str, "")
                ocr_text = ocr_texts.get(scene_id_str, "")

                scene.transcript = transcript if transcript else None
                scene.ocr_text = ocr_text if ocr_text else None

                combined_text = " ".join(filter(None, [transcript, ocr_text]))

                if combined_text.strip():
                    session.execute(
                        sa_text(
                            """
                            UPDATE scenes
                            SET fts_vector = setweight(to_tsvector('simple', :transcript), 'A')
                                || setweight(to_tsvector('simple', :ocr), 'B')
                            WHERE id = :scene_id
                            """
                        ),
                        {
                            "transcript": transcript or "",
                            "ocr": ocr_text or "",
                            "scene_id": scene.id,
                        },
                    )

                text_for_embed = combined_text if combined_text.strip() else "empty"
                text_embedding = text_embedder.embed(text_for_embed)
                image_embedding = clip_embeddings.get(scene_id_str)

                existing_emb = session.query(SceneEmbedding).filter_by(scene_id=scene.id).first()
                if existing_emb:
                    existing_emb.text_embedding = text_embedding
                    if image_embedding:
                        existing_emb.image_embedding = image_embedding
                else:
                    emb = SceneEmbedding(
                        id=uuid.uuid4(),
                        scene_id=scene.id,
                        image_embedding=image_embedding,
                        text_embedding=text_embedding,
                    )
                    session.add(emb)

            session.execute(
                sa_update(LectureVideo)
                .where(LectureVideo.id == uuid.UUID(lecture_id))
                .values(
                    status=VideoStatus.COMPLETED,
                    processed_at=func.now(),
                )
            )
            session.commit()

        _cleanup_tmp(lecture_id)

        # Notify chatbot to sync this lecture into FalkorDB knowledge graph.
        # Non-critical — failure here must not fail the indexing task.
        _notify_graph_sync(lecture_id, log)

        log.info("indexing_completed", lecture_id=lecture_id)
        return {"lecture_id": lecture_id, "status": "COMPLETED"}

    except Exception as exc:
        log.error("indexing_failed", error=str(exc))
        raise self.retry(exc=exc)


def _cleanup_tmp(lecture_id: str) -> None:
    tmp_dir = Path("/tmp/gds_worker") / lecture_id
    frames_dir = Path("/tmp/gds_worker") / f"frames_{lecture_id}"

    for d in [tmp_dir, frames_dir]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def _notify_graph_sync(lecture_id: str, log) -> None:
    """HTTP POST to chatbot service to sync the newly completed lecture into FalkorDB."""
    try:
        import httpx
        httpx.post(
            f"http://chatbot:8001/graph/sync/lecture/{lecture_id}",
            timeout=10,
        )
        log.info("graph_sync_notified", lecture_id=lecture_id)
    except Exception as e:
        log.warning("graph_sync_notify_failed", error=str(e), lecture_id=lecture_id)
