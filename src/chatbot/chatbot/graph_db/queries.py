"""Cypher query helpers for GraphRAG reasoning."""
from __future__ import annotations

from chatbot.graph_db.client import get_graph


def recommend_for_student(student_id: str, limit: int = 5) -> list[dict]:
    """
    4-tier graph-based recommendation with explanation:
    1. In-progress lectures (has WATCHED edge, not completed)
    2. Next in chapter after completed lecture
    3. Concept-based: lectures sharing concepts with watched lectures
    4. Fallback: any COMPLETED lecture not yet watched
    """
    graph = get_graph()
    if graph is None:
        return []

    results = []

    # Tier 1: In-progress
    t1 = graph.query(
        """
        MATCH (s:Student {id: $sid})-[w:WATCHED]->(l:Lecture)
        WHERE w.completed = false AND w.position_sec > 30
        RETURN l.id as lecture_id, l.title as title,
               w.position_sec as position_sec, w.watched_seconds as watched_seconds,
               l.duration_sec as duration_sec,
               'continue' as reason, 'Tiếp tục xem' as reason_vi
        ORDER BY w.last_watched DESC
        LIMIT $limit
        """,
        {"sid": str(student_id), "limit": limit},
    )
    for row in t1.result_set:
        results.append(dict(zip(["lecture_id", "title", "position_sec", "watched_seconds", "duration_sec", "reason", "reason_vi"], row)))

    if len(results) >= limit:
        return results[:limit]

    # Tier 2: Next in chapter
    t2 = graph.query(
        """
        MATCH (s:Student {id: $sid})-[w:WATCHED {completed: true}]->(done:Lecture)
              -[:IN_CHAPTER]->(ch:Chapter)<-[:IN_CHAPTER]-(next:Lecture)
        WHERE NOT (s)-[:WATCHED]->(next)
          AND next.id <> done.id
        RETURN DISTINCT next.id as lecture_id, next.title as title,
               0 as position_sec, 0 as watched_seconds, next.duration_sec as duration_sec,
               'next' as reason,
               'Bài tiếp theo trong ' + ch.title as reason_vi
        LIMIT $limit
        """,
        {"sid": str(student_id), "limit": limit - len(results)},
    )
    for row in t2.result_set:
        results.append(dict(zip(["lecture_id", "title", "position_sec", "watched_seconds", "duration_sec", "reason", "reason_vi"], row)))

    if len(results) >= limit:
        return results[:limit]

    # Tier 3: Concept similarity
    t3 = graph.query(
        """
        MATCH (s:Student {id: $sid})-[:WATCHED]->(watched:Lecture)-[:HAS_CONCEPT]->(c:Concept)
              <-[:HAS_CONCEPT]-(rec:Lecture)
        WHERE NOT (s)-[:WATCHED]->(rec)
        WITH rec, collect(DISTINCT c.name) as shared_concepts, count(DISTINCT c) as score
        ORDER BY score DESC
        LIMIT $limit
        RETURN rec.id as lecture_id, rec.title as title,
               0 as position_sec, 0 as watched_seconds, rec.duration_sec as duration_sec,
               'related' as reason,
               'Liên quan: ' + shared_concepts[0] as reason_vi
        """,
        {"sid": str(student_id), "limit": limit - len(results)},
    )
    for row in t3.result_set:
        results.append(dict(zip(["lecture_id", "title", "position_sec", "watched_seconds", "duration_sec", "reason", "reason_vi"], row)))

    if len(results) >= limit:
        return results[:limit]

    # Tier 4: Fallback — newest lectures not watched
    t4 = graph.query(
        """
        MATCH (l:Lecture)
        WHERE NOT (:Student {id: $sid})-[:WATCHED]->(l)
          AND l.status = 'COMPLETED'
        RETURN l.id as lecture_id, l.title as title,
               0 as position_sec, 0 as watched_seconds, l.duration_sec as duration_sec,
               'new' as reason, 'Bài học mới' as reason_vi
        LIMIT $limit
        """,
        {"sid": str(student_id), "limit": limit - len(results)},
    )
    for row in t4.result_set:
        results.append(dict(zip(["lecture_id", "title", "position_sec", "watched_seconds", "duration_sec", "reason", "reason_vi"], row)))

    return results[:limit]


def explain_relationship(lecture_a_id: str, lecture_b_id: str) -> dict:
    """Find and explain why lecture A and B are related via concept graph."""
    graph = get_graph()
    if graph is None:
        return {"error": "graph not available"}

    result = graph.query(
        """
        MATCH (a:Lecture {id: $aid})-[:HAS_CONCEPT]->(c:Concept)<-[:HAS_CONCEPT]-(b:Lecture {id: $bid})
        RETURN a.title as a_title, b.title as b_title,
               collect(DISTINCT c.name) as shared_concepts,
               count(DISTINCT c) as shared_count
        """,
        {"aid": str(lecture_a_id), "bid": str(lecture_b_id)},
    )
    if not result.result_set:
        return {"related": False, "reason": "Không tìm thấy khái niệm chung"}

    row = result.result_set[0]
    return {
        "related": True,
        "lecture_a": row[0],
        "lecture_b": row[1],
        "shared_concepts": row[2],
        "shared_count": row[3],
        "reason_vi": f"Cả hai bài giảng đều đề cập đến: {', '.join(row[2][:5])}",
    }


def student_knowledge_map(student_id: str) -> dict:
    """What concepts has a student covered and what is still unexplored?"""
    graph = get_graph()
    if graph is None:
        return {}

    covered = graph.query(
        """
        MATCH (s:Student {id: $sid})-[:WATCHED]->(l:Lecture)-[:HAS_CONCEPT]->(c:Concept)
        RETURN c.name as concept, count(DISTINCT l) as lecture_count
        ORDER BY lecture_count DESC LIMIT 20
        """,
        {"sid": str(student_id)},
    )

    uncovered = graph.query(
        """
        MATCH (l:Lecture)-[:HAS_CONCEPT]->(c:Concept)
        WHERE NOT EXISTS {
            MATCH (:Student {id: $sid})-[:WATCHED]->(:Lecture)-[:HAS_CONCEPT]->(c)
        }
        RETURN c.name as concept, count(DISTINCT l) as available_lectures
        ORDER BY available_lectures DESC LIMIT 10
        """,
        {"sid": str(student_id)},
    )

    return {
        "covered_concepts": [
            {"concept": r[0], "lecture_count": r[1]} for r in covered.result_set
        ],
        "unexplored_concepts": [
            {"concept": r[0], "available_lectures": r[1]} for r in uncovered.result_set
        ],
    }


def concept_lectures(concept_name: str, limit: int = 5) -> list[dict]:
    """Find all lectures covering a specific concept."""
    graph = get_graph()
    if graph is None:
        return []

    result = graph.query(
        """
        MATCH (l:Lecture)-[:HAS_CONCEPT]->(c:Concept)
        WHERE toLower(c.name) CONTAINS toLower($name)
        MATCH (l)-[:IN_CHAPTER]->(ch:Chapter)-[:IN_COURSE]->(co:Course)
        RETURN l.id as lecture_id, l.title as lecture_title,
               ch.title as chapter_title, co.title as course_title,
               collect(DISTINCT c.name) as matched_concepts
        LIMIT $limit
        """,
        {"name": concept_name, "limit": limit},
    )
    return [
        {
            "lecture_id": r[0],
            "lecture_title": r[1],
            "chapter_title": r[2],
            "course_title": r[3],
            "matched_concepts": r[4],
        }
        for r in result.result_set
    ]


def teacher_coverage(teacher_id: str) -> dict:
    """What topics/concepts does a teacher cover across their lectures?"""
    graph = get_graph()
    if graph is None:
        return {}

    result = graph.query(
        """
        MATCH (t:Teacher {id: $tid})-[:UPLOADED]->(l:Lecture)-[:HAS_CONCEPT]->(c:Concept)
        RETURN t.name as teacher_name, c.name as concept,
               count(DISTINCT l) as lecture_count
        ORDER BY lecture_count DESC LIMIT 15
        """,
        {"tid": str(teacher_id)},
    )
    if not result.result_set:
        return {"teacher_id": teacher_id, "concepts": []}

    return {
        "teacher_name": result.result_set[0][0],
        "concepts": [
            {"concept": r[1], "lecture_count": r[2]} for r in result.result_set
        ],
    }
