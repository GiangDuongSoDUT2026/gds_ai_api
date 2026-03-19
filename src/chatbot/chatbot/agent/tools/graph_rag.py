"""GraphRAG tool — queries FalkorDB knowledge graph to answer with relationship context."""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool

from chatbot.graph_db import queries as gq


class GraphRAGTool(BaseTool):
    """Query the knowledge graph for relationship-aware answers and explainable recommendations."""

    name: str = "graph_knowledge"
    description: str = (
        "Query the knowledge graph to find relationships between lectures, concepts, students, and teachers. "
        "Use this for: recommending lectures WITH explanations, finding lectures by concept/topic, "
        "explaining why two lectures are related, showing a student's knowledge coverage. "
        "Actions:\n"
        "  recommend                          — personalized lecture recommendations with reasons\n"
        "  explain <lecture_id_a> <lecture_id_b> — explain relationship between two lectures\n"
        "  knowledge_map                      — show student's covered and unexplored concepts\n"
        "  concept <concept_name>             — find lectures covering a specific concept/topic\n"
        "  teacher_coverage <teacher_id>      — show what topics a teacher covers\n"
    )
    user_context: dict | None = None

    class Config:
        arbitrary_types_allowed = True

    def _run(self, action: str) -> str:
        action = action.strip()
        parts = action.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        user_id = str(self.user_context.get("user_id", "")) if self.user_context else ""

        if cmd == "recommend":
            recs = gq.recommend_for_student(user_id)
            if not recs:
                return json.dumps({
                    "__card_type": "table",
                    "title": "Đề xuất bài học",
                    "columns": ["Bài giảng", "Lý do"],
                    "rows": [],
                    "empty_message": "Chưa có đề xuất. Hãy xem một vài bài giảng trước!"
                })
            rows = [
                [r["title"], r["reason_vi"]]
                for r in recs
            ]
            return json.dumps({
                "__card_type": "table",
                "title": "Đề xuất bài học dựa trên đồ thị kiến thức",
                "columns": ["Bài giảng", "Lý do đề xuất"],
                "rows": rows,
                "citations": [
                    {
                        "lecture_id": r["lecture_id"],
                        "lecture_title": r["title"],
                        "timestamp_start": r.get("position_sec", 0),
                        "deep_link": f"/lectures/{r['lecture_id']}?t={int(r.get('position_sec', 0))}",
                    }
                    for r in recs
                ],
            })

        elif cmd == "explain":
            ids = arg.split()
            if len(ids) < 2:
                return "Vui lòng cung cấp 2 lecture_id: explain <id_a> <id_b>"
            result = gq.explain_relationship(ids[0], ids[1])
            if not result.get("related"):
                return result.get("reason", "Không tìm thấy mối liên hệ")
            return json.dumps({
                "__card_type": "stats",
                "title": "Mối liên hệ giữa hai bài giảng",
                "metrics": [
                    {"label": result["lecture_a"], "value": "Bài giảng A", "icon": "video"},
                    {"label": result["lecture_b"], "value": "Bài giảng B", "icon": "video"},
                    {"label": "Khái niệm chung", "value": result["shared_count"], "icon": "tag"},
                    {"label": "Nội dung liên quan", "value": ", ".join(result["shared_concepts"][:5]), "icon": "link"},
                ],
                "explanation": result["reason_vi"],
            })

        elif cmd == "knowledge_map":
            result = gq.student_knowledge_map(user_id)
            if not result:
                return "Không thể truy vấn bản đồ kiến thức"
            covered = result.get("covered_concepts", [])
            unexplored = result.get("unexplored_concepts", [])
            return json.dumps({
                "__card_type": "table",
                "title": "Bản đồ kiến thức của bạn",
                "columns": ["Khái niệm", "Số bài đã xem", "Trạng thái"],
                "rows": (
                    [[c["concept"], c["lecture_count"], "Đã học"] for c in covered] +
                    [[c["concept"], c["available_lectures"], "Chưa học"] for c in unexplored]
                ),
            })

        elif cmd == "concept":
            if not arg:
                return "Vui lòng cung cấp tên khái niệm: concept <tên>"
            lectures = gq.concept_lectures(arg)
            if not lectures:
                return f"Không tìm thấy bài giảng về '{arg}'"
            rows = [[l["lecture_title"], l["chapter_title"], l["course_title"]] for l in lectures]
            return json.dumps({
                "__card_type": "table",
                "title": f"Bài giảng về: {arg}",
                "columns": ["Bài giảng", "Chương", "Môn học"],
                "rows": rows,
                "citations": [
                    {
                        "lecture_id": l["lecture_id"],
                        "lecture_title": l["lecture_title"],
                        "chapter_title": l["chapter_title"],
                        "deep_link": f"/lectures/{l['lecture_id']}",
                    }
                    for l in lectures
                ],
            })

        elif cmd == "teacher_coverage":
            tid = arg.strip() or (str(self.user_context.get("user_id", "")) if self.user_context else "")
            result = gq.teacher_coverage(tid)
            if not result.get("concepts"):
                return "Chưa có dữ liệu chủ đề cho giảng viên này"
            rows = [[c["concept"], c["lecture_count"]] for c in result["concepts"]]
            return json.dumps({
                "__card_type": "table",
                "title": f"Nội dung giảng dạy: {result.get('teacher_name', 'Giảng viên')}",
                "columns": ["Khái niệm/Chủ đề", "Số bài giảng"],
                "rows": rows,
            })

        else:
            return f"Action không hợp lệ: '{cmd}'. Dùng: recommend, explain, knowledge_map, concept, teacher_coverage"

    async def _arun(self, action: str) -> Any:
        return self._run(action)
