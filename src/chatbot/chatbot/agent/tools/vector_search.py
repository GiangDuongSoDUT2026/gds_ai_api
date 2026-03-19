import json
import threading
from typing import Optional, Type

import structlog
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# Singleton — loaded once, reused for every tool call
_embed_model = None
_embed_model_lock = threading.Lock()


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        with _embed_model_lock:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer
                _embed_model = SentenceTransformer("intfloat/multilingual-e5-large")
    return _embed_model


class VectorSearchInput(BaseModel):
    query: str = Field(description="Search query in Vietnamese or English")
    course_id: Optional[str] = Field(default=None, description="Optional course UUID to filter results")


class VectorSearchTool(BaseTool):
    name: str = "search_video"
    description: str = (
        "Search lecture video segments by semantic content. "
        "Input: search query in Vietnamese or English. "
        "Returns: list of relevant video segments with timestamps."
    )
    args_schema: Type[BaseModel] = VectorSearchInput

    def _run(self, query: str, course_id: Optional[str] = None) -> str:
        import psycopg2
        import psycopg2.extras

        from shared.config import get_settings

        settings = get_settings()

        try:
            model = _get_embed_model()
            embedding = model.encode(query, normalize_embeddings=True).tolist()
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            conn = psycopg2.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
            )
            conn.set_session(readonly=True)

            course_filter = ""
            params: list = [embedding_str, embedding_str]
            if course_id:
                course_filter = "AND co.id = %s"
                params.append(course_id)
            params.append(5)

            sql = f"""
                SELECT
                    s.id AS scene_id,
                    lv.title AS lecture_title,
                    ch.title AS chapter_title,
                    co.name AS course_name,
                    s.timestamp_start,
                    s.timestamp_end,
                    LEFT(s.transcript, 300) AS transcript_snippet,
                    s.keyframe_minio_key,
                    1 - (se.text_embedding <=> %s::vector) AS score
                FROM scene_embeddings se
                JOIN scenes s ON s.id = se.scene_id
                JOIN lecture_videos lv ON lv.id = s.lecture_id
                JOIN chapters ch ON ch.id = lv.chapter_id
                JOIN courses co ON co.id = ch.course_id
                WHERE lv.status = 'COMPLETED'
                  AND se.text_embedding IS NOT NULL
                  {course_filter}
                ORDER BY se.text_embedding <=> %s::vector
                LIMIT %s
            """

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

            conn.close()

            results = []
            for row in rows:
                keyframe_url = None
                if row["keyframe_minio_key"]:
                    keyframe_url = (
                        f"{settings.minio_public_url}/{settings.minio_bucket_frames}/{row['keyframe_minio_key']}"
                    )

                results.append(
                    {
                        "lecture_title": row["lecture_title"],
                        "chapter_title": row["chapter_title"],
                        "course_name": row["course_name"],
                        "timestamp_start": float(row["timestamp_start"]),
                        "timestamp_end": float(row["timestamp_end"]),
                        "transcript_snippet": row["transcript_snippet"],
                        "keyframe_url": keyframe_url,
                        "relevance_score": float(row["score"]) if row["score"] else 0.0,
                    }
                )

            return json.dumps(results, ensure_ascii=False)

        except Exception as exc:
            logger.error("vector_search_failed", error=str(exc))
            return json.dumps({"error": str(exc)})

    async def _arun(self, query: str, course_id: Optional[str] = None) -> str:
        import asyncio

        return await asyncio.get_event_loop().run_in_executor(None, self._run, query, course_id)
