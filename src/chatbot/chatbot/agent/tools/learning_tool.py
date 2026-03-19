"""
Learning progress and recommendation tool for students.
Only available when user_context has role=STUDENT (or any authenticated user).
"""
import json
from langchain.tools import BaseTool
from sqlalchemy import create_engine, text
from chatbot.config import get_settings


class LearningTool(BaseTool):
    name: str = "learning_progress"
    description: str = (
        "Get personalized learning information for the current student. "
        "Actions: "
        "'continue' (list in-progress videos to continue watching), "
        "'completed' (list finished lectures), "
        "'stats' (overall learning statistics: hours watched, streak, etc.), "
        "'recommendations' (personalized video recommendations based on watch history), "
        "'history <N>' (last N lectures watched, default 5)."
    )
    user_context: dict | None = None

    def _run(self, action: str) -> str:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)
        user_id = self.user_context.get("user_id") if self.user_context else None
        if not user_id:
            return json.dumps({"error": "Cần đăng nhập để xem lịch sử học tập"})

        action = action.strip().lower()

        with engine.connect() as conn:
            if action in ("continue", ""):
                rows = conn.execute(text("""
                    SELECT lv.id, lv.title, lv.duration_sec,
                           co.name AS course_name, ch.title AS chapter_title,
                           svp.watched_seconds, svp.last_position_sec,
                           svp.last_watched_at,
                           CASE WHEN lv.duration_sec > 0
                                THEN ROUND((svp.watched_seconds / lv.duration_sec * 100)::numeric, 1)
                                ELSE 0 END AS pct
                    FROM student_video_progress svp
                    JOIN lecture_videos lv ON lv.id = svp.lecture_id
                    JOIN chapters ch ON ch.id = lv.chapter_id
                    JOIN courses co ON co.id = ch.course_id
                    WHERE svp.student_id = :uid AND svp.completed = false AND svp.watched_seconds > 0
                    ORDER BY svp.last_watched_at DESC LIMIT 5
                """), {"uid": user_id}).fetchall()
                data = {
                    "__card_type": "table",
                    "title": "Video đang xem dở",
                    "columns": ["Bài giảng", "Môn học", "Tiến độ", "Xem lần cuối"],
                    "rows": [
                        [r.title, r.course_name, f"{r.pct}%",
                         r.last_watched_at.strftime("%d/%m %H:%M") if r.last_watched_at else "-"]
                        for r in rows
                    ],
                }
                if not rows:
                    return json.dumps({"message": "Bạn chưa có video nào đang xem dở."})
                return json.dumps(data, ensure_ascii=False, default=str)

            elif action == "stats":
                row = conn.execute(text("""
                    SELECT
                        COALESCE(SUM(watched_seconds), 0) AS total_sec,
                        COUNT(*) FILTER (WHERE completed) AS completed,
                        COUNT(*) FILTER (WHERE NOT completed AND watched_seconds > 0) AS in_progress,
                        COALESCE(SUM(JSONB_ARRAY_LENGTH(scenes_viewed)), 0) AS total_scenes
                    FROM student_video_progress WHERE student_id = :uid
                """), {"uid": user_id}).fetchone()

                streak_row = conn.execute(text("""
                    WITH daily AS (
                        SELECT DISTINCT created_at::date AS day FROM student_learning_events
                        WHERE student_id = :uid AND event_type = 'watch' ORDER BY day DESC
                    ), numbered AS (
                        SELECT day, ROW_NUMBER() OVER (ORDER BY day DESC) AS rn FROM daily
                    )
                    SELECT COUNT(*) AS streak FROM numbered
                    WHERE day = (CURRENT_DATE - (rn-1) * INTERVAL '1 day')::date
                """), {"uid": user_id}).fetchone()

                course_row = conn.execute(text("""
                    SELECT co.name, COUNT(*) AS cnt
                    FROM student_video_progress svp
                    JOIN lecture_videos lv ON lv.id = svp.lecture_id
                    JOIN chapters ch ON ch.id = lv.chapter_id
                    JOIN courses co ON co.id = ch.course_id
                    WHERE svp.student_id = :uid GROUP BY co.id, co.name ORDER BY cnt DESC LIMIT 1
                """), {"uid": user_id}).fetchone()

                total_sec = float(row.total_sec) if row else 0
                data = {
                    "__card_type": "stats",
                    "title": "Thống kê học tập của tôi",
                    "metrics": [
                        {"label": "Tổng giờ học", "value": round(total_sec / 3600, 1), "icon": "clock"},
                        {"label": "Hoàn thành", "value": int(row.completed) if row else 0, "icon": "check", "color": "green"},
                        {"label": "Đang học", "value": int(row.in_progress) if row else 0, "icon": "loader", "color": "blue"},
                        {"label": "Scenes đã xem", "value": int(row.total_scenes) if row else 0, "icon": "video"},
                        {"label": "Chuỗi ngày học", "value": int(streak_row.streak) if streak_row else 0, "icon": "clock", "color": "orange"},
                        {"label": "Môn yêu thích", "value": course_row.name if course_row else "—", "icon": "book"},
                    ],
                }
                return json.dumps(data, ensure_ascii=False)

            elif action == "completed":
                rows = conn.execute(text("""
                    SELECT lv.title, co.name AS course_name, ch.title AS chapter_title,
                           svp.watched_seconds, svp.last_watched_at
                    FROM student_video_progress svp
                    JOIN lecture_videos lv ON lv.id = svp.lecture_id
                    JOIN chapters ch ON ch.id = lv.chapter_id
                    JOIN courses co ON co.id = ch.course_id
                    WHERE svp.student_id = :uid AND svp.completed = true
                    ORDER BY svp.last_watched_at DESC LIMIT 10
                """), {"uid": user_id}).fetchall()
                data = {
                    "__card_type": "table",
                    "title": "Bài giảng đã hoàn thành",
                    "columns": ["Bài giảng", "Môn học", "Ngày hoàn thành"],
                    "rows": [
                        [r.title, r.course_name,
                         r.last_watched_at.strftime("%d/%m/%Y") if r.last_watched_at else "-"]
                        for r in rows
                    ],
                }
                return json.dumps(data, ensure_ascii=False, default=str)

            elif action == "recommendations":
                # Simple recommendation: continue watching + next in chapter
                rows = conn.execute(text("""
                    (
                        SELECT lv.id::text AS lid, lv.title, co.name AS course_name,
                               'Tiếp tục xem' AS reason,
                               ROUND((svp.watched_seconds / NULLIF(lv.duration_sec,0) * 100)::numeric,1) AS pct
                        FROM student_video_progress svp
                        JOIN lecture_videos lv ON lv.id = svp.lecture_id
                        JOIN chapters ch ON ch.id = lv.chapter_id
                        JOIN courses co ON co.id = ch.course_id
                        WHERE svp.student_id = :uid AND svp.completed = false AND svp.watched_seconds > 10
                        ORDER BY svp.last_watched_at DESC LIMIT 3
                    )
                    UNION ALL
                    (
                        SELECT lv2.id::text, lv2.title, co.name,
                               'Tiếp theo trong môn học' AS reason, 0 AS pct
                        FROM student_video_progress svp
                        JOIN lecture_videos lv ON lv.id = svp.lecture_id
                        JOIN chapters ch ON ch.id = lv.chapter_id
                        JOIN courses co ON co.id = ch.course_id
                        JOIN lecture_videos lv2 ON lv2.chapter_id = ch.id AND lv2.id != lv.id
                        LEFT JOIN student_video_progress svp2 ON svp2.lecture_id = lv2.id AND svp2.student_id = :uid
                        WHERE svp.student_id = :uid AND svp.completed = true AND svp2.id IS NULL
                        LIMIT 3
                    )
                    LIMIT 6
                """), {"uid": user_id}).fetchall()
                data = {
                    "__card_type": "table",
                    "title": "Đề xuất bài giảng cho bạn",
                    "columns": ["Bài giảng", "Môn học", "Lý do đề xuất", "Tiến độ"],
                    "rows": [[r.title, r.course_name, r.reason, f"{r.pct}%" if r.pct else "Mới"] for r in rows],
                }
                return json.dumps(data, ensure_ascii=False, default=str)

            elif action.startswith("history"):
                n = 5
                parts = action.split()
                if len(parts) > 1:
                    try:
                        n = int(parts[1])
                    except ValueError:
                        pass
                rows = conn.execute(text("""
                    SELECT lv.title, co.name AS course_name,
                           svp.watched_seconds, svp.completed, svp.last_watched_at
                    FROM student_video_progress svp
                    JOIN lecture_videos lv ON lv.id = svp.lecture_id
                    JOIN chapters ch ON ch.id = lv.chapter_id
                    JOIN courses co ON co.id = ch.course_id
                    WHERE svp.student_id = :uid
                    ORDER BY svp.last_watched_at DESC LIMIT :n
                """), {"uid": user_id, "n": n}).fetchall()
                data = {
                    "__card_type": "table",
                    "title": f"Lịch sử xem {n} bài gần nhất",
                    "columns": ["Bài giảng", "Môn học", "Trạng thái", "Xem lần cuối"],
                    "rows": [
                        [r.title, r.course_name,
                         "✅ Hoàn thành" if r.completed else "⏸ Đang xem",
                         r.last_watched_at.strftime("%d/%m %H:%M") if r.last_watched_at else "-"]
                        for r in rows
                    ],
                }
                return json.dumps(data, ensure_ascii=False, default=str)

            return json.dumps({"error": f"Hành động không hợp lệ: '{action}'"})

    async def _arun(self, action: str) -> str:
        return self._run(action)
