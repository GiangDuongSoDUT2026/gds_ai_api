import json
from langchain.tools import BaseTool
from sqlalchemy import create_engine, text
from chatbot.config import get_settings


class StatsTool(BaseTool):
    name: str = "get_statistics"
    description: str = (
        "Get statistics and analytics about the lecture system. Returns structured data. "
        "Actions: "
        "'system' (total videos, courses, processing status breakdown), "
        "'by_course' (video count and duration per course), "
        "'by_status' (count of videos in each processing status), "
        "'recent' (recently uploaded/completed videos), "
        "'my_stats' (stats for the current teacher's own lectures)."
    )
    user_context: dict | None = None

    def _run(self, action: str) -> str:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)
        role = self.user_context.get("role", "STUDENT") if self.user_context else "STUDENT"
        org_id = self.user_context.get("organization_id") if self.user_context else None
        faculty = self.user_context.get("faculty") if self.user_context else None
        user_id = self.user_context.get("user_id") if self.user_context else None
        action = action.strip().lower()

        scope_filter = ""
        params: dict = {}
        if role == "SCHOOL_ADMIN" and org_id:
            scope_filter = "AND p.organization_id = :org_id"
            params["org_id"] = org_id
        elif role == "FACULTY_ADMIN" and faculty:
            scope_filter = "AND co.faculty = :faculty"
            params["faculty"] = faculty
        elif role == "TEACHER" and user_id:
            scope_filter = "AND lv.owner_id = :user_id"
            params["user_id"] = user_id

        if action == "system" or action == "":
            with engine.connect() as conn:
                def q(sql_str, p=None):
                    return conn.execute(text(sql_str), p or {}).scalar() or 0

                scope_j = f"""
                    FROM lecture_videos lv
                    JOIN chapters ch ON ch.id=lv.chapter_id
                    JOIN courses co ON co.id=ch.course_id
                    JOIN programs p ON p.id=co.program_id
                    WHERE 1=1 {scope_filter}
                """
                total = q(f"SELECT COUNT(*) {scope_j}", params)
                completed = q(f"SELECT COUNT(*) {scope_j} AND lv.status='COMPLETED'", params)
                failed = q(f"SELECT COUNT(*) {scope_j} AND lv.status='FAILED'", params)
                processing = q(f"SELECT COUNT(*) {scope_j} AND lv.status NOT IN ('COMPLETED','FAILED')", params)
                total_dur = q(f"SELECT COALESCE(SUM(lv.duration_sec),0) {scope_j} AND lv.status='COMPLETED'", params)

                courses_q = "SELECT COUNT(DISTINCT co.id) FROM courses co JOIN programs p ON p.id=co.program_id WHERE 1=1"
                courses_p: dict = {}
                if role == "FACULTY_ADMIN" and faculty:
                    courses_q += " AND co.faculty = :faculty"
                    courses_p["faculty"] = faculty
                elif role == "SCHOOL_ADMIN" and org_id:
                    courses_q += " AND p.organization_id = :org_id"
                    courses_p["org_id"] = org_id
                total_courses = q(courses_q, courses_p)

            data = {
                "__card_type": "stats",
                "title": "Thống kê hệ thống",
                "metrics": [
                    {"label": "Tổng bài giảng", "value": total, "icon": "video"},
                    {"label": "Hoàn thành", "value": completed, "icon": "check", "color": "green"},
                    {"label": "Đang xử lý", "value": processing, "icon": "loader", "color": "blue"},
                    {"label": "Thất bại", "value": failed, "icon": "x", "color": "red"},
                    {"label": "Môn học", "value": total_courses, "icon": "book"},
                    {"label": "Tổng thời lượng (giờ)", "value": round(total_dur / 3600, 1), "icon": "clock"},
                ],
            }
            return json.dumps(data, ensure_ascii=False)

        elif action == "by_course":
            sql = f"""
                SELECT co.name as course_name, co.code,
                       COUNT(lv.id) as lecture_count,
                       COUNT(CASE WHEN lv.status='COMPLETED' THEN 1 END) as completed,
                       ROUND(COALESCE(SUM(lv.duration_sec),0)/3600.0, 1) as total_hours
                FROM courses co
                JOIN programs p ON p.id=co.program_id
                LEFT JOIN chapters ch ON ch.course_id=co.id
                LEFT JOIN lecture_videos lv ON lv.chapter_id=ch.id
                WHERE 1=1 {scope_filter}
                GROUP BY co.id, co.name, co.code
                ORDER BY lecture_count DESC
                LIMIT 20
            """
            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
            data = {
                "__card_type": "table",
                "title": "Thống kê theo môn học",
                "columns": ["Môn học", "Mã môn", "Bài giảng", "Hoàn thành", "Tổng giờ"],
                "rows": [[r.course_name, r.code or "-", r.lecture_count, r.completed, float(r.total_hours)] for r in rows],
            }
            return json.dumps(data, ensure_ascii=False)

        elif action == "by_status":
            sql = f"""
                SELECT lv.status, COUNT(*) as count
                FROM lecture_videos lv
                JOIN chapters ch ON ch.id=lv.chapter_id
                JOIN courses co ON co.id=ch.course_id
                JOIN programs p ON p.id=co.program_id
                WHERE 1=1 {scope_filter}
                GROUP BY lv.status
            """
            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
            data = {
                "__card_type": "stats",
                "title": "Phân bổ trạng thái xử lý",
                "metrics": [{"label": r.status, "value": r.count} for r in rows],
            }
            return json.dumps(data, ensure_ascii=False)

        elif action == "recent":
            sql = f"""
                SELECT lv.id, lv.title, lv.status, lv.duration_sec, lv.created_at,
                       co.name as course_name, u.full_name as owner
                FROM lecture_videos lv
                JOIN chapters ch ON ch.id=lv.chapter_id
                JOIN courses co ON co.id=ch.course_id
                JOIN programs p ON p.id=co.program_id
                LEFT JOIN users u ON u.id=lv.owner_id
                WHERE 1=1 {scope_filter}
                ORDER BY lv.created_at DESC
                LIMIT 10
            """
            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
            data = {
                "__card_type": "table",
                "title": "Bài giảng gần đây",
                "columns": ["Tiêu đề", "Môn học", "Trạng thái", "Người đăng"],
                "rows": [[r.title, r.course_name, r.status, r.owner or "-"] for r in rows],
            }
            return json.dumps(data, ensure_ascii=False)

        elif action == "my_stats" and user_id:
            sql = """
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN status='COMPLETED' THEN 1 END) as completed,
                    COUNT(CASE WHEN status='FAILED' THEN 1 END) as failed,
                    COUNT(CASE WHEN status NOT IN ('COMPLETED','FAILED') THEN 1 END) as processing,
                    ROUND(COALESCE(SUM(duration_sec),0)/3600.0,1) as total_hours,
                    COUNT(DISTINCT chapter_id) as chapters
                FROM lecture_videos WHERE owner_id = :uid
            """
            with engine.connect() as conn:
                r = conn.execute(text(sql), {"uid": user_id}).fetchone()
            if not r:
                return json.dumps({"error": "No data"})
            data = {
                "__card_type": "stats",
                "title": "Thống kê bài giảng của tôi",
                "metrics": [
                    {"label": "Tổng bài giảng", "value": r.total, "icon": "video"},
                    {"label": "Hoàn thành", "value": r.completed, "icon": "check", "color": "green"},
                    {"label": "Đang xử lý", "value": r.processing, "icon": "loader", "color": "blue"},
                    {"label": "Thất bại", "value": r.failed, "icon": "x", "color": "red"},
                    {"label": "Tổng giờ", "value": float(r.total_hours), "icon": "clock"},
                ],
            }
            return json.dumps(data, ensure_ascii=False)

        return json.dumps({"error": f"Unknown action: {action}"})

    async def _arun(self, action: str) -> str:
        return self._run(action)
