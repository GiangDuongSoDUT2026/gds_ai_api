import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog

from worker.app import app
from worker.utils.db import (
    get_sync_session,
    update_lecture_status_sync,
    mark_processing_ended,
    estimate_compute_cost,
)
from worker.utils.retry import classify_error, get_retry_params, is_retryable

logger = structlog.get_logger(__name__)


# ─── Transcript chunking ──────────────────────────────────────────────────────

def _create_transcript_chunks(
    asr_segments: list[dict],
    silence_threshold: float = 1.5,
    target_duration: float = 45.0,
    overlap_sec: float = 12.0,
) -> list[dict]:
    """
    Split ASR segments into semantic chunks.
    - Breaks at silence gaps >= silence_threshold seconds
    - Merges short groups to approach target_duration
    - Adds overlap_sec seconds of context from previous chunk
    Returns: [{chunk_index, text, start_sec, end_sec, overlap_prev_sec, overlap_next_sec}]
    """
    if not asr_segments:
        return []

    # Step 1: find natural break points (silence >= threshold between segments)
    break_after: set[int] = set()
    for i in range(len(asr_segments) - 1):
        gap = asr_segments[i + 1]["start"] - asr_segments[i]["end"]
        if gap >= silence_threshold:
            break_after.add(i)

    # Step 2: group segments at break points
    groups: list[list[dict]] = []
    current: list[dict] = []
    for i, seg in enumerate(asr_segments):
        current.append(seg)
        if i in break_after or i == len(asr_segments) - 1:
            groups.append(current)
            current = []

    # Step 3: merge small groups toward target_duration
    merged: list[list[dict]] = []
    buffer: list[dict] = []
    for group in groups:
        if not buffer:
            buffer = group
            continue
        buffer_dur = buffer[-1]["end"] - buffer[0]["start"]
        group_dur = group[-1]["end"] - group[0]["start"]
        if buffer_dur + group_dur <= target_duration:
            buffer.extend(group)
        else:
            merged.append(buffer)
            buffer = group
    if buffer:
        merged.append(buffer)

    # Step 4: build result with prev-overlap
    result: list[dict] = []
    for i, segs in enumerate(merged):
        base_text = " ".join(s["text"].strip() for s in segs)
        start = segs[0]["start"]
        end = segs[-1]["end"]
        overlap_prev = 0.0

        # Borrow segments from previous chunk for context
        if i > 0:
            prev_segs = merged[i - 1]
            borrowed: list[dict] = []
            for s in reversed(prev_segs):
                if start - s["start"] <= overlap_sec:
                    borrowed.insert(0, s)
                else:
                    break
            if borrowed:
                overlap_text = " ".join(s["text"].strip() for s in borrowed)
                base_text = overlap_text + " " + base_text
                overlap_prev = start - borrowed[0]["start"]
                start = borrowed[0]["start"]

        result.append({
            "chunk_index": i,
            "text": base_text.strip(),
            "start_sec": start,
            "end_sec": end,
            "overlap_prev_sec": overlap_prev,
            "overlap_next_sec": 0.0,
        })

    # Fill overlap_next_sec symmetrically
    for i in range(len(result) - 1):
        result[i]["overlap_next_sec"] = result[i + 1]["overlap_prev_sec"]

    return result


def _find_overlapping_scenes(
    chunk_start: float,
    chunk_end: float,
    scenes: list,
) -> list[str]:
    """Return scene IDs whose time range overlaps with the chunk."""
    return [
        str(s.id)
        for s in scenes
        if not (s.timestamp_end <= chunk_start or s.timestamp_start >= chunk_end)
    ]


def _assign_transcript_from_chunks(
    scenes: list,
    chunks: list[dict],
) -> dict[str, str]:
    """
    Assign transcript text to each scene from overlapping chunks.
    Uses the chunk text (which has full context + overlap) instead of raw segments.
    """
    assignment: dict[str, str] = {}
    for scene in scenes:
        scene_id = str(scene.id)
        texts: list[str] = []
        for chunk in chunks:
            # chunk overlaps scene if they share any time range
            if chunk["end_sec"] <= scene.timestamp_start:
                continue
            if chunk["start_sec"] >= scene.timestamp_end:
                continue
            texts.append(chunk["text"])
        assignment[scene_id] = " ".join(texts).strip()
    return assignment


# ─── Main indexing task ───────────────────────────────────────────────────────

@app.task(bind=True, queue="db", max_retries=5, default_retry_delay=30)
def run_indexing(self, group_results: list, lecture_id: str) -> dict:
    from sqlalchemy import func, text as sa_text, update as sa_update
    from shared.database.models import (
        LectureVideo, Scene, SceneEmbedding, TranscriptChunk, VideoStatus
    )
    from worker.models.loader import get_text_embedder

    log = logger.bind(lecture_id=lecture_id, task_id=self.request.id)
    log.info("indexing_started")

    try:
        asr_result  = next((r for r in group_results if r and "segments" in r), None)
        ocr_result  = next((r for r in group_results if r and "ocr_results" in r), None)
        clip_result = next((r for r in group_results if r and "embeddings" in r), None)

        asr_segments: list[dict]           = asr_result["segments"]   if asr_result  else []
        ocr_texts:    dict[str, str]       = ocr_result["ocr_results"] if ocr_result  else {}
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

            # ── Build semantic transcript chunks ──────────────────────────────
            chunks_data = _create_transcript_chunks(asr_segments)
            transcript_map = _assign_transcript_from_chunks(scenes, chunks_data)

            # Persist transcript chunks with embeddings
            # Delete old chunks for this lecture first (re-indexing)
            session.query(TranscriptChunk).filter(
                TranscriptChunk.lecture_id == uuid.UUID(lecture_id)
            ).delete()

            for chunk in chunks_data:
                overlapping_scene_ids = _find_overlapping_scenes(
                    chunk["start_sec"], chunk["end_sec"], scenes
                )
                chunk_embedding = text_embedder.embed(chunk["text"])

                tc = TranscriptChunk(
                    id=uuid.uuid4(),
                    lecture_id=uuid.UUID(lecture_id),
                    chunk_index=chunk["chunk_index"],
                    text=chunk["text"],
                    start_sec=chunk["start_sec"],
                    end_sec=chunk["end_sec"],
                    overlap_prev_sec=chunk["overlap_prev_sec"],
                    overlap_next_sec=chunk["overlap_next_sec"],
                    scene_ids=[uuid.UUID(sid) for sid in overlapping_scene_ids],
                    text_embedding=chunk_embedding,
                )
                session.add(tc)
                session.flush()

                # Set FTS for chunk via raw SQL
                if chunk["text"].strip():
                    session.execute(
                        sa_text(
                            "UPDATE transcript_chunks SET fts_vector = "
                            "to_tsvector('simple', :txt) WHERE id = :cid"
                        ),
                        {"txt": chunk["text"], "cid": tc.id},
                    )

            # ── Update scenes ─────────────────────────────────────────────────
            for scene in scenes:
                scene_id_str = str(scene.id)
                transcript = transcript_map.get(scene_id_str, "")
                ocr_text   = ocr_texts.get(scene_id_str, "")

                scene.transcript = transcript if transcript else None
                scene.ocr_text   = ocr_text   if ocr_text   else None

                # Split FTS: transcript_fts (A) + ocr_fts (B) + combined fts_vector
                session.execute(
                    sa_text("""
                        UPDATE scenes SET
                            transcript_fts = to_tsvector('simple', :tr),
                            ocr_fts        = to_tsvector('simple', :ocr),
                            fts_vector     = setweight(to_tsvector('simple', :tr),  'A')
                                          || setweight(to_tsvector('simple', :ocr), 'B')
                        WHERE id = :sid
                    """),
                    {
                        "tr":  transcript or "",
                        "ocr": ocr_text   or "",
                        "sid": scene.id,
                    },
                )

                # Scene embeddings
                combined_text = " ".join(filter(None, [transcript, ocr_text]))
                text_for_embed = combined_text if combined_text.strip() else "empty"
                text_embedding  = text_embedder.embed(text_for_embed)
                image_embedding = clip_embeddings.get(scene_id_str)

                existing_emb = (
                    session.query(SceneEmbedding).filter_by(scene_id=scene.id).first()
                )
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

            # ── Finalize lecture ──────────────────────────────────────────────
            session.execute(
                sa_update(LectureVideo)
                .where(LectureVideo.id == uuid.UUID(lecture_id))
                .values(
                    status=VideoStatus.COMPLETED,
                    processed_at=func.now(),
                    scene_count=len(scenes),
                )
            )
            session.commit()

        # Track timing + compute cost
        mark_processing_ended(lecture_id, scene_count=len(scenes))
        _update_batch_item_timing(lecture_id, len(scenes))

        _cleanup_tmp(lecture_id)
        _notify_graph_sync(lecture_id, log)

        log.info("indexing_completed", lecture_id=lecture_id, scene_count=len(scenes),
                 chunk_count=len(chunks_data))
        return {"lecture_id": lecture_id, "status": "COMPLETED"}

    except Exception as exc:
        error_code = classify_error(exc)
        log.error("indexing_failed", error=str(exc), error_code=error_code.value)
        update_lecture_status_sync(
            lecture_id, VideoStatus.FAILED,
            error_message=str(exc), error_code=error_code.value,
        )
        if not is_retryable(error_code):
            return {"lecture_id": lecture_id, "status": "FAILED", "error_code": error_code.value}

        params = get_retry_params(error_code)
        if self.request.retries >= params["max_retries"]:
            return {"lecture_id": lecture_id, "status": "FAILED", "error_code": error_code.value}
        raise self.retry(exc=exc, countdown=params["countdown"])


def _update_batch_item_timing(lecture_id: str, scene_count: int) -> None:
    """Update the batch item JSONB with timing + compute cost for this lecture."""
    from shared.database.models import LectureVideo, UploadBatch
    from sqlalchemy import update as sa_update
    from datetime import datetime, timezone

    try:
        with get_sync_session() as session:
            lecture = session.get(LectureVideo, uuid.UUID(lecture_id))
            if lecture is None:
                return

            duration = lecture.processing_duration_sec or 0.0
            compute = estimate_compute_cost(lecture.duration_sec or 0.0, scene_count)

            # Find batch that contains this lecture
            batches = (
                session.query(UploadBatch)
                .filter(UploadBatch.items.contains([{"lecture_id": lecture_id}]))
                .all()
            )
            for batch in batches:
                updated_items = []
                for item in (batch.items or []):
                    if item.get("lecture_id") == lecture_id:
                        item["processing_sec"] = duration
                        item["scene_count"] = scene_count
                        item["compute_cost"] = compute
                        item["ended_at"] = datetime.now(timezone.utc).isoformat()
                    updated_items.append(item)
                batch.items = updated_items

                # Update batch total timing
                all_done = all(
                    i.get("status") in ("COMPLETED", "FAILED")
                    for i in updated_items
                )
                if all_done:
                    total_sec = sum(
                        i.get("processing_sec", 0) for i in updated_items
                    )
                    batch.total_processing_sec = total_sec
                    batch.processing_completed_at = datetime.now(timezone.utc)
            session.commit()
    except Exception as e:
        logger.warning("batch_timing_update_failed", error=str(e))


def _cleanup_tmp(lecture_id: str) -> None:
    tmp_dir = Path("/tmp/gds_worker") / lecture_id
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _notify_graph_sync(lecture_id: str, log) -> None:
    try:
        import httpx
        httpx.post(
            f"http://chatbot:8001/graph/sync/lecture/{lecture_id}",
            timeout=10,
        )
        log.info("graph_sync_notified", lecture_id=lecture_id)
    except Exception as e:
        log.warning("graph_sync_notify_failed", error=str(e))
