import json
import threading

from langchain.tools import BaseTool
from sqlalchemy import create_engine, text

from chatbot.config import get_settings

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
        features = model.encode_text(tokenizer([query]))
        features = features / features.norm(dim=-1, keepdim=True)
    return "[" + ",".join(str(v) for v in features[0].tolist()) + "]"


class SearchLecturesTool(BaseTool):
    name: str = "search_lectures"
    description: str = (
        "Search for lecture videos by topic, keyword, or question using hybrid search "
        "(keyword + semantic text + visual). Returns top relevant videos with timestamps. "
        "Use this to find WHERE in a lecture a topic is discussed."
    )
    user_context: dict | None = None

    def _run(self, query: str) -> str:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)

        text_vec = _encode_e5(query)
        clip_vec = _encode_clip(query)

        role    = (self.user_context or {}).get("role", "STUDENT")
        org_id  = (self.user_context or {}).get("organization_id")
        user_id = (self.user_context or {}).get("user_id")
        faculty = (self.user_context or {}).get("faculty")

        scope_join  = ""
        scope_where = ""
        if role == "TEACHER" and user_id:
            scope_join = "JOIN course_teachers ct ON ct.course_id = co.id AND ct.teacher_id = :user_id"
        elif role == "FACULTY_ADMIN" and faculty:
            scope_where = "AND co.faculty = :faculty"
        elif role == "SCHOOL_ADMIN" and org_id:
            scope_where = "AND p.organization_id = :org_id"

        sql = f"""
        WITH
        kw AS (
            SELECT s.id AS scene_id, s.lecture_id,
                   ts_rank(s.fts_vector, plainto_tsquery('simple', :q)) AS kw_score,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(s.fts_vector,
                       plainto_tsquery('simple', :q)) DESC) AS rnk
            FROM scenes s
            JOIN lecture_videos lv ON lv.id = s.lecture_id
            JOIN chapters ch ON ch.id = lv.chapter_id
            JOIN courses co  ON co.id = ch.course_id
            JOIN programs p  ON p.id  = co.program_id
            {scope_join}
            WHERE s.fts_vector @@ plainto_tsquery('simple', :q)
              AND lv.status = 'COMPLETED' {scope_where}
            LIMIT 60
        ),
        txt AS (
            SELECT s.id AS scene_id, s.lecture_id,
                   1-(se.text_embedding <=> :text_vec::vector) AS text_score,
                   ROW_NUMBER() OVER (ORDER BY se.text_embedding <=> :text_vec::vector) AS rnk
            FROM scene_embeddings se
            JOIN scenes s ON s.id = se.scene_id
            JOIN lecture_videos lv ON lv.id = s.lecture_id
            JOIN chapters ch ON ch.id = lv.chapter_id
            JOIN courses co  ON co.id = ch.course_id
            JOIN programs p  ON p.id  = co.program_id
            {scope_join}
            WHERE lv.status = 'COMPLETED' AND se.text_embedding IS NOT NULL {scope_where}
            ORDER BY se.text_embedding <=> :text_vec::vector
            LIMIT 60
        ),
        vis AS (
            SELECT s.id AS scene_id, s.lecture_id,
                   1-(se.image_embedding <=> :clip_vec::vector) AS visual_score,
                   ROW_NUMBER() OVER (ORDER BY se.image_embedding <=> :clip_vec::vector) AS rnk
            FROM scene_embeddings se
            JOIN scenes s ON s.id = se.scene_id
            JOIN lecture_videos lv ON lv.id = s.lecture_id
            JOIN chapters ch ON ch.id = lv.chapter_id
            JOIN courses co  ON co.id = ch.course_id
            JOIN programs p  ON p.id  = co.program_id
            {scope_join}
            WHERE lv.status = 'COMPLETED' AND se.image_embedding IS NOT NULL {scope_where}
            ORDER BY se.image_embedding <=> :clip_vec::vector
            LIMIT 60
        ),
        rrf AS (
            SELECT
                COALESCE(kw.scene_id, txt.scene_id, vis.scene_id)   AS scene_id,
                COALESCE(kw.lecture_id, txt.lecture_id, vis.lecture_id) AS lecture_id,
                COALESCE(kw.kw_score, 0)    AS kw_score,
                COALESCE(txt.text_score, 0) AS text_score,
                COALESCE(vis.visual_score,0) AS visual_score,
                COALESCE(1.0/(60+kw.rnk), 0)*1.0
              + COALESCE(1.0/(60+txt.rnk),0)*1.2
              + COALESCE(1.0/(60+vis.rnk),0)*0.6  AS rrf_score
            FROM kw FULL OUTER JOIN txt USING (scene_id)
                    FULL OUTER JOIN vis USING (scene_id)
        ),
        video_agg AS (
            SELECT lecture_id,
                   COUNT(*) AS matching_scenes,
                   MAX(rrf_score) * (1 + 0.3*LN(1+COUNT(*))) AS video_score,
                   (ARRAY_AGG(scene_id ORDER BY rrf_score DESC))[1] AS best_scene_id
            FROM rrf
            GROUP BY lecture_id
            ORDER BY video_score DESC
            LIMIT 5
        )
        SELECT
            va.lecture_id, va.video_score, va.matching_scenes,
            lv.title AS lecture_title, ch.title AS chapter_title, co.name AS course_name,
            lv.duration_sec,
            s.timestamp_start, s.timestamp_end,
            LEFT(s.transcript, 400) AS transcript_snippet,
            s.keyframe_minio_key,
            r.kw_score, r.text_score, r.visual_score, r.rrf_score
        FROM video_agg va
        JOIN lecture_videos lv ON lv.id = va.lecture_id
        JOIN chapters ch ON ch.id = lv.chapter_id
        JOIN courses co  ON co.id = ch.course_id
        JOIN programs p  ON p.id  = co.program_id
        JOIN scenes s    ON s.id  = va.best_scene_id
        JOIN rrf r       ON r.scene_id = va.best_scene_id
        ORDER BY va.video_score DESC
        """

        params: dict = {"q": query, "text_vec": text_vec, "clip_vec": clip_vec}
        if role == "TEACHER" and user_id:
            params["user_id"] = user_id
        elif role == "FACULTY_ADMIN" and faculty:
            params["faculty"] = faculty
        elif role == "SCHOOL_ADMIN" and org_id:
            params["org_id"] = org_id

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        results = [dict(r._mapping) for r in rows]
        return json.dumps(results, ensure_ascii=False, default=str)

    async def _arun(self, query: str) -> str:
        return self._run(query)
