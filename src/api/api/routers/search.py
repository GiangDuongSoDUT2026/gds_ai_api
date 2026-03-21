from __future__ import annotations

import threading
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.search import SearchRequest, SearchResponse, VideoSearchResult, SceneSnippet
from shared.config import get_settings

router = APIRouter(prefix="/search", tags=["search"])
logger = structlog.get_logger(__name__)

# ── Singleton models ───────────────────────────────────────────────────────────

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
    embedder = _get_e5()
    vec = embedder.encode(f"query: {query}", normalize_embeddings=True).tolist()
    return "[" + ",".join(str(v) for v in vec) + "]"


def _encode_clip(query: str) -> str:
    import torch
    model, tokenizer = _get_clip()
    with torch.no_grad():
        tokens = tokenizer([query])
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    vec = features[0].tolist()
    return "[" + ",".join(str(v) for v in vec) + "]"


# ── Main endpoint ──────────────────────────────────────────────────────────────

@router.get("/", response_model=SearchResponse)
async def search(
    request: SearchRequest = Depends(),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    logger.info("search_requested", query=request.q, n_videos=request.n_videos)
    settings = get_settings()

    text_vec  = _encode_e5(request.q)
    clip_vec  = _encode_clip(request.q)

    course_filter = "AND co.id = :course_id" if request.course_id else ""
    params: dict[str, Any] = {
        "q":           request.q,
        "text_vec":    text_vec,
        "clip_vec":    clip_vec,
        "k":           request.candidate_k,
        "n_videos":    request.n_videos,
    }
    if request.course_id:
        params["course_id"] = request.course_id

    sql = text(f"""
    WITH
    -- ── Arm 1: Keyword FTS (transcript_fts weight A, ocr_fts weight B) ─────
    kw AS (
        SELECT
            s.id          AS scene_id,
            s.lecture_id,
            ts_rank(
                setweight(COALESCE(s.transcript_fts, to_tsvector('')), 'A')
             || setweight(COALESCE(s.ocr_fts,        to_tsvector('')), 'B'),
                plainto_tsquery('simple', :q)
            )              AS kw_score,
            ROW_NUMBER() OVER (
                ORDER BY ts_rank(
                    setweight(COALESCE(s.transcript_fts, to_tsvector('')), 'A')
                 || setweight(COALESCE(s.ocr_fts,        to_tsvector('')), 'B'),
                    plainto_tsquery('simple', :q)
                ) DESC
            ) AS rnk
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
    -- ── Arm 2: Text semantic — e5-large on scene text_embedding ────────────
    txt AS (
        SELECT
            s.id          AS scene_id,
            s.lecture_id,
            1 - (se.text_embedding <=> :text_vec::vector)  AS text_score,
            ROW_NUMBER() OVER (
                ORDER BY se.text_embedding <=> :text_vec::vector
            ) AS rnk
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
    -- ── Arm 3: Visual semantic — CLIP text→image_embedding ────────────────
    vis AS (
        SELECT
            s.id          AS scene_id,
            s.lecture_id,
            1 - (se.image_embedding <=> :clip_vec::vector) AS visual_score,
            ROW_NUMBER() OVER (
                ORDER BY se.image_embedding <=> :clip_vec::vector
            ) AS rnk
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
    -- ── RRF fusion (weighted: text 1.2x, visual 0.6x) ─────────────────────
    rrf AS (
        SELECT
            COALESCE(kw.scene_id,   txt.scene_id,  vis.scene_id)   AS scene_id,
            COALESCE(kw.lecture_id, txt.lecture_id, vis.lecture_id) AS lecture_id,
            COALESCE(kw.kw_score,     0)   AS kw_score,
            COALESCE(txt.text_score,  0)   AS text_score,
            COALESCE(vis.visual_score, 0)  AS visual_score,
            COALESCE(1.0 / (60 + kw.rnk),  0) * 1.0
          + COALESCE(1.0 / (60 + txt.rnk), 0) * 1.2
          + COALESCE(1.0 / (60 + vis.rnk), 0) * 0.6  AS rrf_score
        FROM kw
        FULL OUTER JOIN txt USING (scene_id)
        FULL OUTER JOIN vis USING (scene_id)
    ),
    -- ── Aggregate per video — boost videos with multiple matching scenes ───
    video_agg AS (
        SELECT
            lecture_id,
            COUNT(*)                                           AS matching_scenes,
            MAX(rrf_score)                                     AS max_rrf,
            -- boost: each additional matching scene adds ~30% weight
            MAX(rrf_score) * (1 + 0.3 * LN(1 + COUNT(*)))    AS video_score,
            (ARRAY_AGG(scene_id ORDER BY rrf_score DESC))[1]  AS best_scene_id,
            ARRAY_AGG(scene_id ORDER BY rrf_score DESC)[1:3]  AS top_scene_ids
        FROM rrf
        GROUP BY lecture_id
        ORDER BY video_score DESC
        LIMIT :n_videos
    )
    -- ── Final join ──────────────────────────────────────────────────────────
    SELECT
        va.lecture_id,
        va.video_score,
        va.max_rrf,
        va.matching_scenes,
        va.top_scene_ids,
        lv.title          AS lecture_title,
        lv.duration_sec,
        ch.title          AS chapter_title,
        co.name           AS course_name,
        -- best scene detail
        s.id              AS best_scene_id,
        s.timestamp_start AS best_ts_start,
        s.timestamp_end   AS best_ts_end,
        s.transcript      AS best_transcript,
        s.ocr_text        AS best_ocr,
        s.keyframe_minio_key AS best_keyframe,
        r.kw_score        AS best_kw_score,
        r.text_score      AS best_text_score,
        r.visual_score    AS best_visual_score,
        r.rrf_score       AS best_rrf_score
    FROM video_agg va
    JOIN lecture_videos lv  ON lv.id  = va.lecture_id
    JOIN chapters ch        ON ch.id  = lv.chapter_id
    JOIN courses co         ON co.id  = ch.course_id
    JOIN scenes s           ON s.id   = va.best_scene_id
    JOIN rrf r              ON r.scene_id = va.best_scene_id
    ORDER BY va.video_score DESC
    """)

    rows = (await db.execute(sql, params)).fetchall()

    if not rows:
        return SearchResponse(results=[], total_videos=0, query=request.q)

    # ── Fetch top_scenes detail ────────────────────────────────────────────────
    all_scene_ids: list[str] = []
    for row in rows:
        all_scene_ids.extend([str(sid) for sid in (row.top_scene_ids or [])])

    scene_details: dict[str, Any] = {}
    if all_scene_ids:
        scene_sql = text("""
            SELECT s.id, s.timestamp_start, s.timestamp_end,
                   s.transcript, s.ocr_text, s.keyframe_minio_key,
                   COALESCE(r.kw_score, 0)     AS kw_score,
                   COALESCE(r.text_score, 0)   AS text_score,
                   COALESCE(r.visual_score, 0) AS visual_score,
                   COALESCE(r.rrf_score, 0)    AS rrf_score
            FROM scenes s
            LEFT JOIN (
                SELECT
                    COALESCE(kw2.scene_id, txt2.scene_id, vis2.scene_id) AS scene_id,
                    COALESCE(kw2.kw_score, 0)    AS kw_score,
                    COALESCE(txt2.text_score, 0) AS text_score,
                    COALESCE(vis2.visual_score, 0) AS visual_score,
                    COALESCE(1.0/(60+kw2.rnk),0)*1.0
                  + COALESCE(1.0/(60+txt2.rnk),0)*1.2
                  + COALESCE(1.0/(60+vis2.rnk),0)*0.6 AS rrf_score
                FROM (SELECT s2.id AS scene_id,
                             ts_rank(s2.fts_vector, plainto_tsquery('simple', :q)) AS kw_score,
                             ROW_NUMBER() OVER (ORDER BY ts_rank(s2.fts_vector,
                                 plainto_tsquery('simple', :q)) DESC) AS rnk
                      FROM scenes s2 WHERE s2.id = ANY(:sids::uuid[])) kw2
                FULL OUTER JOIN
                     (SELECT se2.scene_id,
                             1-(se2.text_embedding <=> :text_vec::vector) AS text_score,
                             ROW_NUMBER() OVER (ORDER BY se2.text_embedding <=> :text_vec::vector) AS rnk
                      FROM scene_embeddings se2 WHERE se2.scene_id = ANY(:sids::uuid[])) txt2
                     USING (scene_id)
                FULL OUTER JOIN
                     (SELECT se3.scene_id,
                             1-(se3.image_embedding <=> :clip_vec::vector) AS visual_score,
                             ROW_NUMBER() OVER (ORDER BY se3.image_embedding <=> :clip_vec::vector) AS rnk
                      FROM scene_embeddings se3 WHERE se3.scene_id = ANY(:sids::uuid[])) vis2
                     USING (scene_id)
            ) r ON r.scene_id = s.id
            WHERE s.id = ANY(:sids::uuid[])
        """)
        import uuid as _uuid
        scene_rows = (await db.execute(scene_sql, {
            "sids": [_uuid.UUID(sid) for sid in all_scene_ids],
            "q": request.q,
            "text_vec": text_vec,
            "clip_vec": clip_vec,
        })).fetchall()
        for sr in scene_rows:
            scene_details[str(sr.id)] = sr

    # ── Build response ─────────────────────────────────────────────────────────
    settings = get_settings()

    def _keyframe_url(key: str | None) -> str | None:
        if not key:
            return None
        return f"{settings.storage_base_url}/{settings.storage_bucket_frames}/{key}"

    def _make_snippet(scene_id_str: str) -> SceneSnippet | None:
        sd = scene_details.get(scene_id_str)
        if sd is None:
            return None
        return SceneSnippet(
            scene_id=sd.id,
            timestamp_start=float(sd.timestamp_start),
            timestamp_end=float(sd.timestamp_end),
            transcript=sd.transcript,
            ocr_text=sd.ocr_text,
            keyframe_url=_keyframe_url(sd.keyframe_minio_key),
            kw_score=float(sd.kw_score),
            text_score=float(sd.text_score),
            visual_score=float(sd.visual_score),
            rrf_score=float(sd.rrf_score),
        )

    results: list[VideoSearchResult] = []
    for row in rows:
        best = SceneSnippet(
            scene_id=row.best_scene_id,
            timestamp_start=float(row.best_ts_start),
            timestamp_end=float(row.best_ts_end),
            transcript=row.best_transcript,
            ocr_text=row.best_ocr,
            keyframe_url=_keyframe_url(row.best_keyframe),
            kw_score=float(row.best_kw_score),
            text_score=float(row.best_text_score),
            visual_score=float(row.best_visual_score),
            rrf_score=float(row.best_rrf_score),
        )

        top_scenes: list[SceneSnippet] = []
        for sid in (row.top_scene_ids or []):
            snippet = _make_snippet(str(sid))
            if snippet:
                top_scenes.append(snippet)

        results.append(VideoSearchResult(
            lecture_id=row.lecture_id,
            lecture_title=row.lecture_title,
            chapter_title=row.chapter_title,
            course_name=row.course_name,
            duration_sec=row.duration_sec,
            video_score=float(row.video_score),
            matching_scene_count=int(row.matching_scenes),
            best_scene=best,
            top_scenes=top_scenes,
        ))

    return SearchResponse(results=results, total_videos=len(results), query=request.q)
