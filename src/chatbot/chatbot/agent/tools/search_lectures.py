import json
import threading

from langchain.tools import BaseTool
from sqlalchemy import create_engine, text

from chatbot.config import get_settings

_embed_model = None
_embed_lock = threading.Lock()


def _get_embedder():
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer

                _embed_model = SentenceTransformer("intfloat/multilingual-e5-large")
    return _embed_model


class SearchLecturesTool(BaseTool):
    name: str = "search_lectures"
    description: str = (
        "Search for lecture scenes by topic, keyword, or question. "
        "Returns scenes with timestamps and transcripts. "
        "Use this to find WHERE in a lecture a topic is discussed."
    )
    user_context: dict | None = None

    def _run(self, query: str) -> str:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)
        embedder = _get_embedder()

        query_vec = embedder.encode(f"query: {query}").tolist()
        vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"

        # Build scope filter based on role
        role = self.user_context.get("role", "STUDENT") if self.user_context else "STUDENT"
        org_id = self.user_context.get("organization_id") if self.user_context else None
        user_id = self.user_context.get("user_id") if self.user_context else None
        faculty = self.user_context.get("faculty") if self.user_context else None

        # Scope filter by role:
        # STUDENT → toàn bộ hệ thống (open access)
        # TEACHER → chỉ môn được phân công
        # FACULTY_ADMIN → chỉ khoa của mình
        # SCHOOL_ADMIN → chỉ trường của mình
        # SUPER_ADMIN / None → không giới hạn
        scope_join = ""
        scope_where = ""

        if role == "TEACHER" and user_id:
            scope_join = """
                JOIN course_teachers ct ON ct.course_id = co.id AND ct.teacher_id = :user_id
            """
        elif role == "FACULTY_ADMIN" and faculty:
            scope_where = "AND co.faculty = :faculty"
        elif role == "SCHOOL_ADMIN" and org_id:
            scope_where = "AND p.organization_id = :org_id"
        # STUDENT, SUPER_ADMIN, anonymous: no filter — full access

        sql = f"""
            SELECT
                s.id as scene_id,
                lv.id as lecture_id,
                lv.title as lecture_title,
                ch.title as chapter_title,
                co.name as course_name,
                s.timestamp_start,
                s.timestamp_end,
                LEFT(s.transcript, 400) as transcript_snippet,
                s.keyframe_minio_key as keyframe_url,
                1 - (se.text_embedding <=> :vec::vector) as score
            FROM scene_embeddings se
            JOIN scenes s ON s.id = se.scene_id
            JOIN lecture_videos lv ON lv.id = s.lecture_id
            JOIN chapters ch ON ch.id = lv.chapter_id
            JOIN courses co ON co.id = ch.course_id
            JOIN programs p ON p.id = co.program_id
            {scope_join}
            WHERE 1=1 {scope_where}
            ORDER BY se.text_embedding <=> :vec::vector
            LIMIT 5
        """
        params: dict = {"vec": vec_str}
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
