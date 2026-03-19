"""Sync PostgreSQL data → FalkorDB knowledge graph.

Uses MERGE so it is fully idempotent — safe to run repeatedly.
Graph schema:
  Nodes: Student, Teacher, Organization, Program, Course, Chapter, Lecture, Concept
  Edges:
    (Lecture)-[:IN_CHAPTER]->(Chapter)
    (Chapter)-[:IN_COURSE]->(Course)
    (Course)-[:IN_PROGRAM]->(Program)
    (Program)-[:IN_ORG]->(Organization)
    (Teacher)-[:TEACHES]->(Course)
    (Teacher)-[:UPLOADED]->(Lecture)
    (Student)-[:ENROLLED_IN]->(Course)
    (Student)-[:WATCHED {position_sec, completed, watched_seconds, last_watched}]->(Lecture)
    (Lecture)-[:HAS_CONCEPT]->(Concept)
"""
from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from chatbot.graph_db.client import get_graph

logger = structlog.get_logger(__name__)


def sync_all(db: Session) -> dict:
    """Full sync from PostgreSQL to FalkorDB. Returns counts."""
    graph = get_graph()
    if graph is None:
        return {"error": "FalkorDB not available"}

    counts = {}
    counts["organizations"] = _sync_organizations(db, graph)
    counts["programs"] = _sync_programs(db, graph)
    counts["courses"] = _sync_courses(db, graph)
    counts["chapters"] = _sync_chapters(db, graph)
    counts["lectures"] = _sync_lectures(db, graph)
    counts["teachers"] = _sync_teachers(db, graph)
    counts["students"] = _sync_students(db, graph)
    counts["concepts"] = _sync_concepts(db, graph)
    counts["watch_edges"] = _sync_watch_edges(db, graph)
    logger.info("falkordb_sync_complete", counts=counts)
    return counts


def sync_lecture(db: Session, lecture_id: str) -> None:
    """Sync a single lecture and its concepts (called after processing completes)."""
    graph = get_graph()
    if graph is None:
        return
    _sync_single_lecture(db, graph, lecture_id)
    _sync_lecture_concepts(db, graph, lecture_id)
    logger.info("falkordb_lecture_synced", lecture_id=lecture_id)


def update_watch_edge(student_id: str, lecture_id: str, position_sec: float, completed: bool, watched_seconds: int) -> None:
    """Update or create WATCHED edge — called in real time from progress API."""
    graph = get_graph()
    if graph is None:
        return
    try:
        graph.query(
            """
            MATCH (s:Student {id: $student_id}), (l:Lecture {id: $lecture_id})
            MERGE (s)-[w:WATCHED]->(l)
            SET w.position_sec = $position_sec,
                w.completed = $completed,
                w.watched_seconds = $watched_seconds,
                w.last_watched = timestamp()
            """,
            {
                "student_id": str(student_id),
                "lecture_id": str(lecture_id),
                "position_sec": position_sec,
                "completed": completed,
                "watched_seconds": watched_seconds,
            },
        )
    except Exception as e:
        logger.warning("falkordb_watch_update_failed", error=str(e))


# ─── Private sync helpers ─────────────────────────────────────────────────────

def _sync_organizations(db: Session, graph) -> int:
    rows = db.execute(text("SELECT id::text, name FROM organizations")).fetchall()
    for r in rows:
        graph.query(
            "MERGE (o:Organization {id: $id}) SET o.name = $name",
            {"id": r.id, "name": r.name},
        )
    return len(rows)


def _sync_programs(db: Session, graph) -> int:
    rows = db.execute(text(
        "SELECT id::text, name as title, organization_id::text FROM programs"
    )).fetchall()
    for r in rows:
        graph.query(
            "MERGE (p:Program {id: $id}) SET p.title = $title",
            {"id": r.id, "title": r.title},
        )
        if r.organization_id:
            graph.query(
                """
                MATCH (p:Program {id: $pid}), (o:Organization {id: $oid})
                MERGE (p)-[:IN_ORG]->(o)
                """,
                {"pid": r.id, "oid": r.organization_id},
            )
    return len(rows)


def _sync_courses(db: Session, graph) -> int:
    rows = db.execute(text(
        "SELECT id::text, name as title, faculty, program_id::text FROM courses"
    )).fetchall()
    for r in rows:
        graph.query(
            "MERGE (c:Course {id: $id}) SET c.title = $title, c.faculty = $faculty",
            {"id": r.id, "title": r.title, "faculty": r.faculty or ""},
        )
        if r.program_id:
            graph.query(
                """
                MATCH (c:Course {id: $cid}), (p:Program {id: $pid})
                MERGE (c)-[:IN_PROGRAM]->(p)
                """,
                {"cid": r.id, "pid": r.program_id},
            )
    return len(rows)


def _sync_chapters(db: Session, graph) -> int:
    rows = db.execute(text(
        "SELECT id::text, title, order_index, course_id::text FROM chapters"
    )).fetchall()
    for r in rows:
        graph.query(
            "MERGE (ch:Chapter {id: $id}) SET ch.title = $title, ch.order_index = $order_index",
            {"id": r.id, "title": r.title, "order_index": r.order_index or 0},
        )
        if r.course_id:
            graph.query(
                """
                MATCH (ch:Chapter {id: $chid}), (co:Course {id: $coid})
                MERGE (ch)-[:IN_COURSE]->(co)
                """,
                {"chid": r.id, "coid": r.course_id},
            )
    return len(rows)


def _sync_lectures(db: Session, graph) -> int:
    rows = db.execute(text(
        """
        SELECT lv.id::text, lv.title, lv.status, lv.duration_sec,
               lv.chapter_id::text, lv.owner_id::text
        FROM lecture_videos lv
        WHERE lv.status = 'COMPLETED'
        """
    )).fetchall()
    for r in rows:
        graph.query(
            """
            MERGE (l:Lecture {id: $id})
            SET l.title = $title, l.status = $status, l.duration_sec = $duration_sec
            """,
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "duration_sec": r.duration_sec or 0,
            },
        )
        if r.chapter_id:
            graph.query(
                """
                MATCH (l:Lecture {id: $lid}), (ch:Chapter {id: $chid})
                MERGE (l)-[:IN_CHAPTER]->(ch)
                """,
                {"lid": r.id, "chid": r.chapter_id},
            )
        if r.owner_id:
            graph.query(
                """
                MATCH (l:Lecture {id: $lid}), (t:Teacher {id: $tid})
                MERGE (t)-[:UPLOADED]->(l)
                """,
                {"lid": r.id, "tid": r.owner_id},
            )
    return len(rows)


def _sync_single_lecture(db: Session, graph, lecture_id: str) -> None:
    row = db.execute(text(
        """
        SELECT lv.id::text, lv.title, lv.status, lv.duration_sec,
               lv.chapter_id::text, lv.owner_id::text
        FROM lecture_videos lv WHERE lv.id = :lid
        """
    ), {"lid": lecture_id}).fetchone()
    if not row:
        return
    graph.query(
        """
        MERGE (l:Lecture {id: $id})
        SET l.title = $title, l.status = $status, l.duration_sec = $duration_sec
        """,
        {"id": row.id, "title": row.title, "status": row.status, "duration_sec": row.duration_sec or 0},
    )
    if row.chapter_id:
        graph.query(
            "MATCH (l:Lecture {id: $lid}), (ch:Chapter {id: $chid}) MERGE (l)-[:IN_CHAPTER]->(ch)",
            {"lid": row.id, "chid": row.chapter_id},
        )
    if row.owner_id:
        graph.query(
            "MATCH (l:Lecture {id: $lid}), (t:Teacher {id: $tid}) MERGE (t)-[:UPLOADED]->(l)",
            {"lid": row.id, "tid": row.owner_id},
        )


def _sync_teachers(db: Session, graph) -> int:
    rows = db.execute(text(
        """
        SELECT u.id::text, u.full_name, u.faculty, u.department
        FROM users u WHERE u.role = 'TEACHER'
        """
    )).fetchall()
    for r in rows:
        graph.query(
            """
            MERGE (t:Teacher {id: $id})
            SET t.name = $name, t.faculty = $faculty, t.department = $department
            """,
            {"id": r.id, "name": r.full_name or "", "faculty": r.faculty or "", "department": r.department or ""},
        )
    # TEACHES edges from course_teachers
    ct_rows = db.execute(text(
        "SELECT teacher_id::text, course_id::text FROM course_teachers"
    )).fetchall()
    for r in ct_rows:
        graph.query(
            "MATCH (t:Teacher {id: $tid}), (c:Course {id: $cid}) MERGE (t)-[:TEACHES]->(c)",
            {"tid": r.teacher_id, "cid": r.course_id},
        )
    return len(rows)


def _sync_students(db: Session, graph) -> int:
    rows = db.execute(text(
        """
        SELECT u.id::text, u.full_name, u.student_code, u.major
        FROM users u WHERE u.role = 'STUDENT'
        """
    )).fetchall()
    for r in rows:
        graph.query(
            """
            MERGE (s:Student {id: $id})
            SET s.name = $name, s.student_code = $student_code, s.major = $major
            """,
            {"id": r.id, "name": r.full_name or "", "student_code": r.student_code or "", "major": r.major or ""},
        )
    # ENROLLED_IN edges
    enroll_rows = db.execute(text(
        "SELECT student_id::text, course_id::text FROM course_enrollments"
    )).fetchall()
    for r in enroll_rows:
        graph.query(
            "MATCH (s:Student {id: $sid}), (c:Course {id: $cid}) MERGE (s)-[:ENROLLED_IN]->(c)",
            {"sid": r.student_id, "cid": r.course_id},
        )
    return len(rows)


def _sync_concepts(db: Session, graph) -> int:
    """Extract Concept nodes from scenes.visual_tags (ARRAY of strings)."""
    rows = db.execute(text(
        """
        SELECT DISTINCT lv.id::text as lecture_id, tag
        FROM lecture_videos lv
        JOIN scenes s ON s.lecture_id = lv.id
        CROSS JOIN LATERAL unnest(s.visual_tags) as tag
        WHERE lv.status = 'COMPLETED'
          AND s.visual_tags IS NOT NULL
          AND array_length(s.visual_tags, 1) > 0
        """
    )).fetchall()
    count = 0
    for r in rows:
        graph.query("MERGE (:Concept {name: $name})", {"name": r.tag})
        graph.query(
            "MATCH (l:Lecture {id: $lid}), (c:Concept {name: $name}) MERGE (l)-[:HAS_CONCEPT]->(c)",
            {"lid": r.lecture_id, "name": r.tag},
        )
        count += 1
    return count


def _sync_lecture_concepts(db: Session, graph, lecture_id: str) -> None:
    rows = db.execute(text(
        """
        SELECT DISTINCT tag
        FROM scenes s
        CROSS JOIN LATERAL unnest(s.visual_tags) as tag
        WHERE s.lecture_id = :lid
          AND s.visual_tags IS NOT NULL
          AND array_length(s.visual_tags, 1) > 0
        """
    ), {"lid": lecture_id}).fetchall()
    for r in rows:
        graph.query("MERGE (:Concept {name: $name})", {"name": r.tag})
        graph.query(
            "MATCH (l:Lecture {id: $lid}), (c:Concept {name: $name}) MERGE (l)-[:HAS_CONCEPT]->(c)",
            {"lid": lecture_id, "name": r.tag},
        )


def _sync_watch_edges(db: Session, graph) -> int:
    rows = db.execute(text(
        """
        SELECT student_id::text, lecture_id::text,
               last_position_sec as position_sec, completed, watched_seconds,
               last_watched_at::text
        FROM student_video_progress
        """
    )).fetchall()
    for r in rows:
        graph.query(
            """
            MATCH (s:Student {id: $sid}), (l:Lecture {id: $lid})
            MERGE (s)-[w:WATCHED]->(l)
            SET w.position_sec = $position_sec,
                w.completed = $completed,
                w.watched_seconds = $watched_seconds,
                w.last_watched = $last_watched_at
            """,
            {
                "sid": r.student_id,
                "lid": r.lecture_id,
                "position_sec": r.position_sec or 0,
                "completed": r.completed or False,
                "watched_seconds": r.watched_seconds or 0,
                "last_watched_at": str(r.last_watched_at) if r.last_watched_at else "",
            },
        )
    return len(rows)
