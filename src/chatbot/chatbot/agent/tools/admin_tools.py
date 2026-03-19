import json

from langchain.tools import BaseTool
from sqlalchemy import create_engine, text

from chatbot.config import get_settings


class AdminStatsTool(BaseTool):
    name: str = "admin_stats"
    description: str = (
        "Get administrative statistics and management info. "
        "Actions: "
        "'overview' (system-wide counts), "
        "'courses' (list courses in your scope), "
        "'users' (list users in your organization), "
        "'lectures_by_course <course_id>' (all lectures in a course), "
        "'enrollments <course_id>' (student count and list for a course)."
    )
    user_context: dict | None = None

    def _run(self, action: str) -> str:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)
        role = (
            self.user_context.get("role", "FACULTY_ADMIN") if self.user_context else "FACULTY_ADMIN"
        )
        org_id = self.user_context.get("organization_id") if self.user_context else None
        faculty = self.user_context.get("faculty") if self.user_context else None

        action = action.strip().lower()

        if action == "overview":
            if role == "SUPER_ADMIN":
                sql = """
                    SELECT
                        (SELECT COUNT(*) FROM organizations) as organizations,
                        (SELECT COUNT(*) FROM programs) as programs,
                        (SELECT COUNT(*) FROM courses) as courses,
                        (SELECT COUNT(*) FROM lecture_videos) as lectures,
                        (SELECT COUNT(*) FROM lecture_videos WHERE status='COMPLETED') as completed_lectures,
                        (SELECT COUNT(*) FROM users) as users,
                        (SELECT COUNT(*) FROM users WHERE role='STUDENT') as students,
                        (SELECT COUNT(*) FROM users WHERE role='TEACHER') as teachers
                """
                params: dict = {}
            elif role == "SCHOOL_ADMIN" and org_id:
                sql = """
                    SELECT
                        (SELECT COUNT(*) FROM programs WHERE organization_id = :org) as programs,
                        (SELECT COUNT(*) FROM courses co JOIN programs p ON p.id=co.program_id WHERE p.organization_id=:org) as courses,
                        (SELECT COUNT(*) FROM users WHERE organization_id = :org) as users
                """
                params = {"org": org_id}
            elif role == "FACULTY_ADMIN" and faculty:
                sql = """
                    SELECT
                        (SELECT COUNT(*) FROM courses WHERE faculty = :fac) as courses,
                        (SELECT COUNT(*) FROM lecture_videos lv JOIN chapters ch ON ch.id=lv.chapter_id JOIN courses co ON co.id=ch.course_id WHERE co.faculty=:fac) as lectures,
                        (SELECT COUNT(DISTINCT ce.student_id) FROM course_enrollments ce JOIN courses co ON co.id=ce.course_id WHERE co.faculty=:fac) as students
                """
                params = {"fac": faculty}
            else:
                return json.dumps({"error": "Insufficient permissions or missing context"})

            with engine.connect() as conn:
                row = conn.execute(text(sql), params).fetchone()
                return json.dumps(
                    dict(row._mapping) if row else {}, ensure_ascii=False, default=str
                )

        elif action == "courses":
            if role == "SUPER_ADMIN":
                sql = "SELECT co.id, co.name, co.code, co.faculty, p.name as program FROM courses co JOIN programs p ON p.id=co.program_id LIMIT 50"
                params = {}
            elif role == "SCHOOL_ADMIN" and org_id:
                sql = "SELECT co.id, co.name, co.code, co.faculty, p.name as program FROM courses co JOIN programs p ON p.id=co.program_id WHERE p.organization_id=:org LIMIT 50"
                params = {"org": org_id}
            elif role == "FACULTY_ADMIN" and faculty:
                sql = "SELECT co.id, co.name, co.code, p.name as program FROM courses co JOIN programs p ON p.id=co.program_id WHERE co.faculty=:fac LIMIT 50"
                params = {"fac": faculty}
            else:
                return json.dumps({"error": "Insufficient permissions"})

            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
            return json.dumps([dict(r._mapping) for r in rows], ensure_ascii=False, default=str)

        elif action == "users":
            if role == "SUPER_ADMIN":
                sql = "SELECT id, email, full_name, role, faculty FROM users ORDER BY role, full_name LIMIT 100"
                params = {}
            elif role in ("SCHOOL_ADMIN", "FACULTY_ADMIN") and org_id:
                sql = "SELECT id, email, full_name, role, faculty FROM users WHERE organization_id=:org ORDER BY role, full_name LIMIT 100"
                params = {"org": org_id}
            else:
                return json.dumps({"error": "Insufficient permissions"})

            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
            return json.dumps([dict(r._mapping) for r in rows], ensure_ascii=False, default=str)

        elif action.startswith("enrollments "):
            course_id = action.replace("enrollments ", "").strip()
            sql = """
                SELECT u.id, u.email, u.full_name, ce.enrolled_at
                FROM course_enrollments ce
                JOIN users u ON u.id = ce.student_id
                WHERE ce.course_id = :cid
                ORDER BY ce.enrolled_at DESC
            """
            with engine.connect() as conn:
                rows = conn.execute(text(sql), {"cid": course_id}).fetchall()
            return json.dumps([dict(r._mapping) for r in rows], ensure_ascii=False, default=str)

        elif action.startswith("lectures_by_course "):
            course_id = action.replace("lectures_by_course ", "").strip()
            sql = """
                SELECT lv.id, lv.title, lv.status, lv.duration_sec, lv.created_at,
                       ch.title as chapter,
                       u.full_name as owner
                FROM lecture_videos lv
                JOIN chapters ch ON ch.id = lv.chapter_id
                LEFT JOIN users u ON u.id = lv.owner_id
                WHERE ch.course_id = :cid
                ORDER BY ch.order_index, lv.created_at
            """
            with engine.connect() as conn:
                rows = conn.execute(text(sql), {"cid": course_id}).fetchall()
            return json.dumps([dict(r._mapping) for r in rows], ensure_ascii=False, default=str)

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    async def _arun(self, action: str) -> str:
        return self._run(action)
