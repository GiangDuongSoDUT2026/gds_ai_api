from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.search import SearchRequest, SearchResponse, SearchResult
from shared.config import get_settings

# Singleton embedder — loaded once on first semantic search request
_embedder = None
_embedder_lock = threading.Lock()

router = APIRouter(prefix="/search", tags=["search"])
logger = structlog.get_logger(__name__)


@router.get("/", response_model=SearchResponse)
async def search(
    request: SearchRequest = Depends(),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    log = logger.bind(query=request.q, mode=request.mode)
    log.info("search_requested")

    settings = get_settings()

    if request.mode == "keyword":
        return await _keyword_search(request, db, settings)
    else:
        return await _semantic_search(request, db, settings)


async def _keyword_search(
    request: SearchRequest,
    db: AsyncSession,
    settings,
) -> SearchResponse:
    course_filter = ""
    params: dict = {
        "query": request.q,
        "limit": request.limit,
        "offset": request.offset,
    }

    if request.course_id:
        course_filter = "AND co.id = :course_id"
        params["course_id"] = request.course_id

    sql = text(
        f"""
        SELECT
            s.id AS scene_id,
            lv.id AS lecture_id,
            lv.title AS lecture_title,
            ch.title AS chapter_title,
            co.name AS course_name,
            s.timestamp_start,
            s.timestamp_end,
            s.transcript,
            s.ocr_text,
            s.keyframe_minio_key,
            ts_rank(s.fts_vector, plainto_tsquery('simple', :query)) AS score,
            COUNT(*) OVER() AS total_count
        FROM scenes s
        JOIN lecture_videos lv ON lv.id = s.lecture_id
        JOIN chapters ch ON ch.id = lv.chapter_id
        JOIN courses co ON co.id = ch.course_id
        WHERE s.fts_vector @@ plainto_tsquery('simple', :query)
          AND lv.status = 'COMPLETED'
          {course_filter}
        ORDER BY score DESC
        LIMIT :limit OFFSET :offset
        """
    )

    result = await db.execute(sql, params)
    rows = result.fetchall()

    total = rows[0].total_count if rows else 0
    results = _rows_to_search_results(rows, settings)

    return SearchResponse(
        results=results,
        total=total,
        query=request.q,
        mode=request.mode,
    )


def _get_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer

                _embedder = SentenceTransformer("intfloat/multilingual-e5-large")
    return _embedder


async def _semantic_search(
    request: SearchRequest,
    db: AsyncSession,
    settings,
) -> SearchResponse:
    embedder = _get_embedder()
    if embedder is None:
        return SearchResponse(results=[], total=0, query=request.q, mode=request.mode)

    embedding = embedder.encode(request.q, normalize_embeddings=True).tolist()
    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    course_filter = ""
    params: dict = {
        "query_vec": embedding_str,
        "limit": request.limit,
        "offset": request.offset,
    }

    if request.course_id:
        course_filter = "AND co.id = :course_id"
        params["course_id"] = request.course_id

    sql = text(
        f"""
        SELECT
            s.id AS scene_id,
            lv.id AS lecture_id,
            lv.title AS lecture_title,
            ch.title AS chapter_title,
            co.name AS course_name,
            s.timestamp_start,
            s.timestamp_end,
            s.transcript,
            s.ocr_text,
            s.keyframe_minio_key,
            1 - (se.text_embedding <=> :query_vec::vector) AS score,
            COUNT(*) OVER() AS total_count
        FROM scene_embeddings se
        JOIN scenes s ON s.id = se.scene_id
        JOIN lecture_videos lv ON lv.id = s.lecture_id
        JOIN chapters ch ON ch.id = lv.chapter_id
        JOIN courses co ON co.id = ch.course_id
        WHERE lv.status = 'COMPLETED'
          AND se.text_embedding IS NOT NULL
          {course_filter}
        ORDER BY se.text_embedding <=> :query_vec::vector
        LIMIT :limit OFFSET :offset
        """
    )

    result = await db.execute(sql, params)
    rows = result.fetchall()

    total = rows[0].total_count if rows else 0
    results = _rows_to_search_results(rows, settings)

    return SearchResponse(
        results=results,
        total=total,
        query=request.q,
        mode=request.mode,
    )


def _rows_to_search_results(rows, settings) -> list[SearchResult]:
    results = []
    for row in rows:
        keyframe_url: str | None = None
        if row.keyframe_minio_key:
            keyframe_url = f"{settings.minio_public_url}/{settings.minio_bucket_frames}/{row.keyframe_minio_key}"

        results.append(
            SearchResult(
                scene_id=row.scene_id,
                lecture_id=row.lecture_id,
                lecture_title=row.lecture_title,
                chapter_title=row.chapter_title,
                course_name=row.course_name,
                timestamp_start=float(row.timestamp_start),
                timestamp_end=float(row.timestamp_end),
                transcript=row.transcript,
                ocr_text=row.ocr_text,
                keyframe_url=keyframe_url,
                score=float(row.score) if row.score is not None else 0.0,
            )
        )
    return results
