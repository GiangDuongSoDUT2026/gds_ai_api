from __future__ import annotations

import threading
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.search import SearchResponse, SearchResult
from shared.config import get_settings

router = APIRouter(prefix="/search", tags=["search"])
logger = structlog.get_logger(__name__)

# ── Singleton ML models (loaded lazily, only in semantic mode) ─────────────────

_e5_model = None
_e5_lock = threading.Lock()

_clip_model = None
_clip_lock = threading.Lock()


def _get_e5():
    global _e5_model
    if _e5_model is None:
        with _e5_lock:
            if _e5_model is None:
                from sentence_transformers import SentenceTransformer
                _e5_model = SentenceTransformer("intfloat/multilingual-e5-large")
    return _e5_model


def _get_clip():
    global _clip_model
    if _clip_model is None:
        with _clip_lock:
            if _clip_model is None:
                import open_clip
                model, _, _ = open_clip.create_model_and_transforms(
                    "ViT-L-14", pretrained="openai"
                )
                model.eval()
                tokenizer = open_clip.get_tokenizer("ViT-L-14")
                _clip_model = (model, tokenizer)
    return _clip_model


def _encode_e5(query: str) -> str:
    vec = _get_e5().encode(f"query: {query}", normalize_embeddings=True).tolist()
    return "[" + ",".join(str(v) for v in vec) + "]"


def _encode_clip(query: str) -> str:
    import torch
    model, tokenizer = _get_clip()
    with torch.no_grad():
        tokens = tokenizer([query])
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    return "[" + ",".join(str(v) for v in features[0].tolist()) + "]"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _keyframe_url(key: str | None) -> str | None:
    if not key:
        return None
    settings = get_settings()
    return f"{settings.storage_base_url}/{settings.storage_bucket_frames}/{key}"


# ── Keyword-only FTS search ────────────────────────────────────────────────────

async def _keyword_search(
    db: AsyncSession, q: str, course_id: Any, limit: int, offset: int
) -> list[SearchResult]:
    course_filter = "AND co.id = :course_id" if course_id else ""
    sql = text(f"""
        SELECT
            s.id              AS scene_id,
            lv.id             AS lecture_id,
            lv.title          AS lecture_title,
            ch.title          AS chapter_title,
            co.name           AS course_name,
            s.timestamp_start,
            s.timestamp_end,
            s.transcript,
            s.ocr_text,
            s.keyframe_minio_key,
            ts_rank(
                setweight(COALESCE(s.transcript_fts, to_tsvector('')), 'A')
             || setweight(COALESCE(s.ocr_fts,        to_tsvector('')), 'B'),
                plainto_tsquery('simple', :q)
            ) AS score
        FROM scenes s
        JOIN lecture_videos lv ON lv.id = s.lecture_id
        JOIN chapters ch        ON ch.id = lv.chapter_id
        JOIN courses co          ON co.id = ch.course_id
        WHERE (s.transcript_fts @@ plainto_tsquery('simple', :q)
            OR s.ocr_fts        @@ plainto_tsquery('simple', :q))
          AND lv.status = 'COMPLETED'
          {course_filter}
        ORDER BY score DESC
        LIMIT :limit OFFSET :offset
    """)
    params: dict[str, Any] = {"q": q, "limit": limit, "offset": offset}
    if course_id:
        params["course_id"] = course_id

    rows = (await db.execute(sql, params)).fetchall()
    return [
        SearchResult(
            scene_id=str(row.scene_id),
            lecture_id=str(row.lecture_id),
            lecture_title=row.lecture_title,
            chapter_title=row.chapter_title,
            course_name=row.course_name,
            timestamp_start=float(row.timestamp_start),
            timestamp_end=float(row.timestamp_end),
            transcript=row.transcript,
            ocr_text=row.ocr_text,
            keyframe_url=_keyframe_url(row.keyframe_minio_key),
            score=float(row.score),
        )
        for row in rows
    ]


# ── Hybrid 3-arm RRF search → flatten to scene-level results ──────────────────

async def _hybrid_search(
    db: AsyncSession, q: str, course_id: Any, limit: int, offset: int
) -> list[SearchResult]:
    text_vec = _encode_e5(q)
    clip_vec = _encode_clip(q)

    course_filter = "AND co.id = :course_id" if course_id else ""
    candidate_k = max(limit * 10, 100)

    sql = text(f"""
    WITH
    kw AS (
        SELECT s.id AS scene_id, s.lecture_id,
            ts_rank(
                setweight(COALESCE(s.transcript_fts, to_tsvector('')), 'A')
             || setweight(COALESCE(s.ocr_fts, to_tsvector('')), 'B'),
                plainto_tsquery('simple', :q)
            ) AS kw_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank(
                setweight(COALESCE(s.transcript_fts, to_tsvector('')), 'A')
             || setweight(COALESCE(s.ocr_fts, to_tsvector('')), 'B'),
                plainto_tsquery('simple', :q)
            ) DESC) AS rnk
        FROM scenes s
        JOIN lecture_videos lv ON lv.id = s.lecture_id
        JOIN chapters ch        ON ch.id = lv.chapter_id
        JOIN courses co          ON co.id = ch.course_id
        WHERE (s.transcript_fts @@ plainto_tsquery('simple', :q)
            OR s.ocr_fts        @@ plainto_tsquery('simple', :q))
          AND lv.status = 'COMPLETED'
          {course_filter}
        LIMIT :k
    ),
    txt AS (
        SELECT s.id AS scene_id, s.lecture_id,
            1 - (se.text_embedding <=> :text_vec::vector) AS text_score,
            ROW_NUMBER() OVER (ORDER BY se.text_embedding <=> :text_vec::vector) AS rnk
        FROM scene_embeddings se
        JOIN scenes s           ON s.id  = se.scene_id
        JOIN lecture_videos lv  ON lv.id = s.lecture_id
        JOIN chapters ch        ON ch.id = lv.chapter_id
        JOIN courses co         ON co.id = ch.course_id
        WHERE lv.status = 'COMPLETED'
          AND se.text_embedding IS NOT NULL
          {course_filter}
        ORDER BY se.text_embedding <=> :text_vec::vector
        LIMIT :k
    ),
    vis AS (
        SELECT s.id AS scene_id, s.lecture_id,
            1 - (se.image_embedding <=> :clip_vec::vector) AS visual_score,
            ROW_NUMBER() OVER (ORDER BY se.image_embedding <=> :clip_vec::vector) AS rnk
        FROM scene_embeddings se
        JOIN scenes s           ON s.id  = se.scene_id
        JOIN lecture_videos lv  ON lv.id = s.lecture_id
        JOIN chapters ch        ON ch.id = lv.chapter_id
        JOIN courses co         ON co.id = ch.course_id
        WHERE lv.status = 'COMPLETED'
          AND se.image_embedding IS NOT NULL
          {course_filter}
        ORDER BY se.image_embedding <=> :clip_vec::vector
        LIMIT :k
    ),
    rrf AS (
        SELECT
            COALESCE(kw.scene_id,   txt.scene_id,  vis.scene_id)   AS scene_id,
            COALESCE(kw.lecture_id, txt.lecture_id, vis.lecture_id) AS lecture_id,
            COALESCE(1.0 / (60 + kw.rnk),  0) * 1.0
          + COALESCE(1.0 / (60 + txt.rnk), 0) * 1.2
          + COALESCE(1.0 / (60 + vis.rnk), 0) * 0.6  AS rrf_score
        FROM kw
        FULL OUTER JOIN txt USING (scene_id)
        FULL OUTER JOIN vis USING (scene_id)
        ORDER BY rrf_score DESC
        LIMIT :limit OFFSET :offset
    )
    SELECT
        r.scene_id,
        r.rrf_score            AS score,
        lv.id                  AS lecture_id,
        lv.title               AS lecture_title,
        ch.title               AS chapter_title,
        co.name                AS course_name,
        s.timestamp_start,
        s.timestamp_end,
        s.transcript,
        s.ocr_text,
        s.keyframe_minio_key
    FROM rrf r
    JOIN scenes s           ON s.id  = r.scene_id
    JOIN lecture_videos lv  ON lv.id = r.lecture_id
    JOIN chapters ch        ON ch.id = lv.chapter_id
    JOIN courses co         ON co.id = ch.course_id
    ORDER BY r.rrf_score DESC
    """)

    params: dict[str, Any] = {
        "q": q,
        "text_vec": text_vec,
        "clip_vec": clip_vec,
        "k": candidate_k,
        "limit": limit,
        "offset": offset,
    }
    if course_id:
        params["course_id"] = course_id

    rows = (await db.execute(sql, params)).fetchall()
    return [
        SearchResult(
            scene_id=str(row.scene_id),
            lecture_id=str(row.lecture_id),
            lecture_title=row.lecture_title,
            chapter_title=row.chapter_title,
            course_name=row.course_name,
            timestamp_start=float(row.timestamp_start),
            timestamp_end=float(row.timestamp_end),
            transcript=row.transcript,
            ocr_text=row.ocr_text,
            keyframe_url=_keyframe_url(row.keyframe_minio_key),
            score=float(row.score),
        )
        for row in rows
    ]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    mode: str = Query(default="keyword"),
    course_id: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    logger.info("search_requested", query=q, mode=mode, limit=limit, offset=offset)

    results: list[SearchResult] = []

    if mode == "semantic":
        try:
            results = await _hybrid_search(db, q, course_id, limit, offset)
        except Exception as e:
            logger.warning("hybrid_search_failed_fallback_keyword", error=str(e))
            results = await _keyword_search(db, q, course_id, limit, offset)
    else:
        results = await _keyword_search(db, q, course_id, limit, offset)

    return SearchResponse(
        results=results,
        total=len(results),
        query=q,
        mode=mode,
    )
