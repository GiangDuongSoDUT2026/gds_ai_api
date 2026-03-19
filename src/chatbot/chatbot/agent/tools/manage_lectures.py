import json

from langchain.tools import BaseTool
from sqlalchemy import create_engine, text

from chatbot.config import get_settings


class ManageLecturesTool(BaseTool):
    name: str = "manage_lectures"
    description: str = (
        "Get status, list, and management info for YOUR OWN lectures. "
        "Actions: 'list' (list all your lectures with status), "
        "'status <lecture_id>' (get processing status of a specific lecture), "
        "'pending' (list lectures still processing), "
        "'stats' (count scenes, duration stats for your lectures)."
    )
    user_context: dict | None = None

    def _run(self, action: str) -> str:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)
        user_id = self.user_context.get("user_id") if self.user_context else None

        if not user_id:
            return json.dumps({"error": "Authentication required"})

        action = action.strip().lower()

        if action in ("list", ""):
            sql = """
                SELECT lv.id, lv.title, lv.status, lv.duration_sec, lv.created_at,
                       ch.title as chapter, co.name as course,
                       COUNT(s.id) as scene_count
                FROM lecture_videos lv
                JOIN chapters ch ON ch.id = lv.chapter_id
                JOIN courses co ON co.id = ch.course_id
                LEFT JOIN scenes s ON s.lecture_id = lv.id
                WHERE lv.owner_id = :uid
                GROUP BY lv.id, ch.title, co.name
                ORDER BY lv.created_at DESC
                LIMIT 30
            """
            params: dict = {"uid": user_id}
        elif action == "pending":
            sql = """
                SELECT lv.id, lv.title, lv.status, lv.created_at
                FROM lecture_videos lv
                WHERE lv.owner_id = :uid
                  AND lv.status NOT IN ('COMPLETED', 'FAILED')
                ORDER BY lv.created_at DESC
            """
            params = {"uid": user_id}
        elif action == "stats":
            sql = """
                SELECT
                    COUNT(lv.id) as total_lectures,
                    COUNT(CASE WHEN lv.status = 'COMPLETED' THEN 1 END) as completed,
                    COUNT(CASE WHEN lv.status = 'FAILED' THEN 1 END) as failed,
                    COUNT(CASE WHEN lv.status NOT IN ('COMPLETED','FAILED') THEN 1 END) as processing,
                    SUM(lv.duration_sec) as total_duration_sec,
                    COUNT(s.id) as total_scenes
                FROM lecture_videos lv
                LEFT JOIN scenes s ON s.lecture_id = lv.id
                WHERE lv.owner_id = :uid
            """
            params = {"uid": user_id}
        elif action.startswith("status "):
            lecture_id = action.replace("status ", "").strip()
            sql = """
                SELECT lv.id, lv.title, lv.status, lv.created_at, lv.updated_at,
                       COUNT(s.id) as scene_count
                FROM lecture_videos lv
                LEFT JOIN scenes s ON s.lecture_id = lv.id
                WHERE lv.id = :lid AND lv.owner_id = :uid
                GROUP BY lv.id
            """
            params = {"lid": lecture_id, "uid": user_id}
        else:
            return json.dumps(
                {"error": f"Unknown action: {action}. Use: list, pending, stats, status <id>"}
            )

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return json.dumps([dict(r._mapping) for r in rows], ensure_ascii=False, default=str)

    async def _arun(self, action: str) -> str:
        return self._run(action)
