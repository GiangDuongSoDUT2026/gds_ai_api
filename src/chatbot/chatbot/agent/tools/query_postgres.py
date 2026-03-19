import json
import re
from typing import Any, Optional, Type

import structlog
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

ALLOWED_TABLES = {"programs", "courses", "chapters", "lecture_videos"}

_SAFE_SQL_PATTERN = re.compile(
    r"^\s*SELECT\s+",
    re.IGNORECASE,
)

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def _is_safe_sql(sql: str) -> bool:
    if not _SAFE_SQL_PATTERN.match(sql):
        return False
    if _FORBIDDEN_KEYWORDS.search(sql):
        return False
    return True


class QueryDatabaseInput(BaseModel):
    query: str = Field(description="Natural language question about course structure or statistics")


class QueryDatabaseTool(BaseTool):
    name: str = "query_database"
    description: str = (
        "Query structured information about programs, courses, chapters, lecture counts. "
        "Input: natural language question. Returns: JSON data."
    )
    args_schema: Type[BaseModel] = QueryDatabaseInput

    def _run(self, query: str) -> str:
        import psycopg2
        import psycopg2.extras

        from shared.config import get_settings

        settings = get_settings()

        sql = self._nl_to_sql(query, settings)
        if not sql or not _is_safe_sql(sql):
            return json.dumps({"error": "Could not generate a safe SQL query for this request."})

        try:
            conn = psycopg2.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
            )
            conn.set_session(readonly=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchmany(50)
                result = [dict(r) for r in rows]
            conn.close()
            return json.dumps(result, default=str)
        except Exception as exc:
            logger.error("query_database_failed", error=str(exc), sql=sql)
            return json.dumps({"error": str(exc)})

    def _nl_to_sql(self, query: str, settings) -> str:
        query_lower = query.lower()

        if "program" in query_lower and ("list" in query_lower or "all" in query_lower or "how many" in query_lower):
            return "SELECT id, name, description, created_at FROM programs ORDER BY name LIMIT 50"

        if "course" in query_lower and ("list" in query_lower or "all" in query_lower):
            return (
                "SELECT c.id, c.name, c.code, p.name AS program_name "
                "FROM courses c JOIN programs p ON p.id = c.program_id "
                "ORDER BY p.name, c.name LIMIT 50"
            )

        if "chapter" in query_lower and ("list" in query_lower or "all" in query_lower):
            return (
                "SELECT ch.id, ch.title, ch.order_index, c.name AS course_name "
                "FROM chapters ch JOIN courses c ON c.id = ch.course_id "
                "ORDER BY c.name, ch.order_index LIMIT 50"
            )

        if "lecture" in query_lower or "video" in query_lower:
            if "count" in query_lower or "how many" in query_lower:
                return (
                    "SELECT c.name AS course_name, COUNT(lv.id) AS lecture_count "
                    "FROM lecture_videos lv "
                    "JOIN chapters ch ON ch.id = lv.chapter_id "
                    "JOIN courses c ON c.id = ch.course_id "
                    "GROUP BY c.name ORDER BY c.name"
                )
            return (
                "SELECT lv.id, lv.title, lv.status, lv.duration_sec, ch.title AS chapter_title "
                "FROM lecture_videos lv JOIN chapters ch ON ch.id = lv.chapter_id "
                "ORDER BY lv.created_at DESC LIMIT 20"
            )

        return (
            "SELECT p.name AS program, c.name AS course, ch.title AS chapter, "
            "COUNT(lv.id) AS lectures "
            "FROM programs p "
            "JOIN courses c ON c.program_id = p.id "
            "JOIN chapters ch ON ch.course_id = c.id "
            "LEFT JOIN lecture_videos lv ON lv.chapter_id = ch.id "
            "GROUP BY p.name, c.name, ch.title, ch.order_index "
            "ORDER BY p.name, c.name, ch.order_index LIMIT 50"
        )

    async def _arun(self, query: str) -> str:
        import asyncio

        return await asyncio.get_event_loop().run_in_executor(None, self._run, query)
