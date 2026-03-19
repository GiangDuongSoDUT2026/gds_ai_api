import json
import re

from langchain.tools import BaseTool
from sqlalchemy import create_engine, text

from chatbot.config import get_settings

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b", re.IGNORECASE
)


class QueryDatabaseTool(BaseTool):
    name: str = "query_database"
    description: str = (
        "Execute a read-only SQL SELECT query on the database to get structured information "
        "about programs, courses, chapters, lectures, users, enrollments. "
        "Tables: organizations, programs, courses, chapters, lecture_videos, scenes, users, "
        "course_enrollments, course_teachers. "
        "Always use SELECT only."
    )
    user_context: dict | None = None

    def _run(self, sql: str) -> str:
        if FORBIDDEN.search(sql):
            return json.dumps({"error": "Only SELECT queries are allowed."})

        settings = get_settings()
        engine = create_engine(settings.database_url_sync)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(sql)).fetchmany(50)
                return json.dumps(
                    [dict(r._mapping) for r in rows], ensure_ascii=False, default=str
                )
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _arun(self, sql: str) -> str:
        return self._run(sql)
