"""
Learning progress tracking and recommendations for students.
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from shared.config import get_settings
from api.dependencies.auth import get_current_user
from shared.database.models import (
    StudentVideoProgress, StudentLearningEvent,
    LectureVideo, Scene, Chapter, Course, Program,
    User, UserRole, SceneEmbedding,
)

router = APIRouter(prefix="/progress", tags=["progress"])
logger = structlog.get_logger(__name__)


# ─── Schemas ──────────────────────────────────────────────────────────────────

class ProgressUpdate(BaseModel):
    position_sec: float
    watched_seconds: float
    completed: bool = False
    scenes_viewed: list[str] = []  # scene IDs seen


class ProgressResponse(BaseModel):
    lecture_id: str
    lecture_title: str
    watched_seconds: float
    duration_sec: float | None
    percent: float
    completed: bool
    last_position_sec: float
    last_watched_at: str | None
    course_name: str
    chapter_title: str


class LearningStatsResponse(BaseModel):
    total_watched_seconds: float
    total_hours: float
    completed_lectures: int
    in_progress_lectures: int
    total_scenes_viewed: int
    most_active_course: str | None
    streak_days: int


class EventLog(BaseModel):
    event_type: str   # "watch" | "scene_view" | "search" | "chat"
    lecture_id: str | None = None
    scene_id: str | None = None
    payload: dict | None = None


class RecommendedLecture(BaseModel):
    lecture_id: str
    lecture_title: str
    course_name: str
    chapter_title: str
    reason: str       # "continue", "next_in_chapter", "related_topic"
    progress_percent: float
    last_position_sec: float
    duration_sec: float | None
    keyframe_url: str | None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/{lecture_id}", status_code=204)
async def update_progress(
    lecture_id: uuid.UUID,
    body: ProgressUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Upsert watch progress. Called by frontend every 30s while watching."""
    result = await db.execute(
        select(StudentVideoProgress).where(
            StudentVideoProgress.student_id == current_user.id,
            StudentVideoProgress.lecture_id == lecture_id,
        )
    )
    progress = result.scalar_one_or_none()
    if not progress:
        progress = StudentVideoProgress(
            student_id=current_user.id,
            lecture_id=lecture_id,
        )
        db.add(progress)

    progress.last_position_sec = body.position_sec
    progress.watched_seconds = max(progress.watched_seconds, body.watched_seconds)
    progress.completed = body.completed or progress.completed
    # Merge scenes_viewed (deduplicate)
    existing_scenes = set(progress.scenes_viewed or [])
    existing_scenes.update(body.scenes_viewed)
    progress.scenes_viewed = list(existing_scenes)
    progress.last_watched_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/events", status_code=204)
async def log_event(
    body: EventLog,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Log a learning event (scene view, search, chat query)."""
    event = StudentLearningEvent(
        student_id=current_user.id,
        event_type=body.event_type,
        lecture_id=uuid.UUID(body.lecture_id) if body.lecture_id else None,
        scene_id=uuid.UUID(body.scene_id) if body.scene_id else None,
        payload=body.payload,
    )
    db.add(event)
    await db.commit()


@router.get("/", response_model=list[ProgressResponse])
async def get_my_progress(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> list[ProgressResponse]:
    """Get all lectures the student has started watching."""
    result = await db.execute(
        select(StudentVideoProgress, LectureVideo, Chapter, Course)
        .join(LectureVideo, LectureVideo.id == StudentVideoProgress.lecture_id)
        .join(Chapter, Chapter.id == LectureVideo.chapter_id)
        .join(Course, Course.id == Chapter.course_id)
        .where(StudentVideoProgress.student_id == current_user.id)
        .order_by(StudentVideoProgress.last_watched_at.desc())
    )
    rows = result.all()
    return [
        ProgressResponse(
            lecture_id=str(p.lecture_id),
            lecture_title=lv.title,
            watched_seconds=p.watched_seconds,
            duration_sec=lv.duration_sec,
            percent=round(
                min(100.0, (p.watched_seconds / lv.duration_sec * 100)) if lv.duration_sec else 0.0, 1
            ),
            completed=p.completed,
            last_position_sec=p.last_position_sec,
            last_watched_at=p.last_watched_at.isoformat() if p.last_watched_at else None,
            course_name=co.name,
            chapter_title=ch.title,
        )
        for p, lv, ch, co in rows
    ]


@router.get("/stats", response_model=LearningStatsResponse)
async def get_my_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> LearningStatsResponse:
    """Aggregate learning statistics for the current student."""
    # Use raw SQL for simplicity
    raw = await db.execute(
        text("""
            SELECT
                COALESCE(SUM(watched_seconds), 0) AS total_sec,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE completed = true) AS completed,
                COUNT(*) FILTER (WHERE completed = false) AS in_progress,
                COALESCE(SUM(JSONB_ARRAY_LENGTH(scenes_viewed)), 0) AS total_scenes
            FROM student_video_progress
            WHERE student_id = :uid
        """),
        {"uid": str(current_user.id)},
    )
    row = raw.fetchone()

    # Most active course
    course_row = await db.execute(
        text("""
            SELECT co.name, COUNT(svp.id) AS cnt
            FROM student_video_progress svp
            JOIN lecture_videos lv ON lv.id = svp.lecture_id
            JOIN chapters ch ON ch.id = lv.chapter_id
            JOIN courses co ON co.id = ch.course_id
            WHERE svp.student_id = :uid
            GROUP BY co.id, co.name
            ORDER BY cnt DESC
            LIMIT 1
        """),
        {"uid": str(current_user.id)},
    )
    course = course_row.fetchone()

    # Streak: consecutive days with learning events
    streak_row = await db.execute(
        text("""
            WITH daily AS (
                SELECT DISTINCT created_at::date AS day
                FROM student_learning_events
                WHERE student_id = :uid
                  AND event_type = 'watch'
                ORDER BY day DESC
            ),
            numbered AS (
                SELECT day, ROW_NUMBER() OVER (ORDER BY day DESC) AS rn
                FROM daily
            )
            SELECT COUNT(*) AS streak
            FROM numbered
            WHERE day = (CURRENT_DATE - (rn - 1) * INTERVAL '1 day')::date
        """),
        {"uid": str(current_user.id)},
    )
    streak = streak_row.scalar() or 0

    total_sec = float(row.total_sec) if row else 0.0
    return LearningStatsResponse(
        total_watched_seconds=total_sec,
        total_hours=round(total_sec / 3600, 1),
        completed_lectures=int(row.completed) if row else 0,
        in_progress_lectures=int(row.in_progress) if row else 0,
        total_scenes_viewed=int(row.total_scenes) if row else 0,
        most_active_course=course.name if course else None,
        streak_days=int(streak),
    )


@router.get("/recommendations", response_model=list[RecommendedLecture])
async def get_recommendations(
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = 8,
    db: AsyncSession = Depends(get_db),
) -> list[RecommendedLecture]:
    """
    Personalized recommendations:
    1. Continue watching (started but not finished, sorted by recency)
    2. Next in chapter (after completed lectures)
    3. Related by topic (vector similarity from watched content)
    """
    recommendations: list[RecommendedLecture] = []
    seen_ids: set[str] = set()

    # Helper to build RecommendedLecture
    async def _make_rec(lecture_id_str: str, reason: str) -> RecommendedLecture | None:
        if lecture_id_str in seen_ids:
            return None
        r = await db.execute(
            text("""
                SELECT lv.id, lv.title, lv.duration_sec,
                       ch.title AS chapter_title,
                       co.name AS course_name,
                       s.keyframe_minio_key,
                       COALESCE(svp.watched_seconds, 0) AS watched_sec,
                       COALESCE(svp.last_position_sec, 0) AS last_pos,
                       COALESCE(svp.completed, false) AS completed
                FROM lecture_videos lv
                JOIN chapters ch ON ch.id = lv.chapter_id
                JOIN courses co ON co.id = ch.course_id
                LEFT JOIN scenes s ON s.lecture_id = lv.id AND s.shot_index = 0
                LEFT JOIN student_video_progress svp
                    ON svp.lecture_id = lv.id AND svp.student_id = :uid
                WHERE lv.id = :lid AND lv.status = 'COMPLETED'
            """),
            {"uid": str(current_user.id), "lid": lecture_id_str},
        )
        row = r.fetchone()
        if not row:
            return None
        duration = float(row.duration_sec) if row.duration_sec else None
        pct = min(100.0, (float(row.watched_sec) / duration * 100)) if duration else 0.0
        seen_ids.add(lecture_id_str)
        return RecommendedLecture(
            lecture_id=lecture_id_str,
            lecture_title=row.title,
            course_name=row.course_name,
            chapter_title=row.chapter_title,
            reason=reason,
            progress_percent=round(pct, 1),
            last_position_sec=float(row.last_pos),
            duration_sec=duration,
            keyframe_url=(
                f"{get_settings().storage_base_url}/{get_settings().storage_bucket_frames}/{row.keyframe_minio_key}"
                if row.keyframe_minio_key else None
            ),
        )

    # 1. Continue watching (in-progress, not completed, recent)
    in_progress = await db.execute(
        text("""
            SELECT svp.lecture_id::text
            FROM student_video_progress svp
            WHERE svp.student_id = :uid
              AND svp.completed = false
              AND svp.watched_seconds > 0
            ORDER BY svp.last_watched_at DESC
            LIMIT 4
        """),
        {"uid": str(current_user.id)},
    )
    for row in in_progress.fetchall():
        rec = await _make_rec(row.lecture_id, "continue")
        if rec:
            recommendations.append(rec)

    # 2. Next in chapter (after any completed lecture in that chapter)
    next_in_chapter = await db.execute(
        text("""
            SELECT DISTINCT lv2.id::text AS next_lecture_id
            FROM student_video_progress svp
            JOIN lecture_videos lv ON lv.id = svp.lecture_id
            JOIN chapters ch ON ch.id = lv.chapter_id
            -- Find other lectures in same chapter that student hasn't started
            JOIN lecture_videos lv2 ON lv2.chapter_id = ch.id
              AND lv2.id != lv.id
              AND lv2.status = 'COMPLETED'
            LEFT JOIN student_video_progress svp2
              ON svp2.lecture_id = lv2.id AND svp2.student_id = :uid
            WHERE svp.student_id = :uid
              AND svp.completed = true
              AND svp2.id IS NULL
            ORDER BY next_lecture_id
            LIMIT 3
        """),
        {"uid": str(current_user.id)},
    )
    for row in next_in_chapter.fetchall():
        rec = await _make_rec(row.next_lecture_id, "next_in_chapter")
        if rec:
            recommendations.append(rec)

    # 3. Related by topic: use average embedding of watched lectures, find similar unwatched
    if len(recommendations) < limit:
        related = await db.execute(
            text("""
                WITH watched_embeddings AS (
                    SELECT se.text_embedding
                    FROM student_video_progress svp
                    JOIN scenes s ON s.lecture_id = svp.lecture_id
                    JOIN scene_embeddings se ON se.scene_id = s.id
                    WHERE svp.student_id = :uid
                      AND svp.watched_seconds > 30
                    LIMIT 50
                ),
                avg_embed AS (
                    SELECT AVG(text_embedding) AS avg_vec
                    FROM watched_embeddings
                ),
                unwatched AS (
                    SELECT lv.id::text AS lid
                    FROM lecture_videos lv
                    LEFT JOIN student_video_progress svp
                        ON svp.lecture_id = lv.id AND svp.student_id = :uid
                    WHERE svp.id IS NULL
                      AND lv.status = 'COMPLETED'
                )
                SELECT u.lid,
                       MIN(se.text_embedding <=> (SELECT avg_vec FROM avg_embed)) AS dist
                FROM unwatched u
                JOIN scenes s ON s.lecture_id = u.lid::uuid
                JOIN scene_embeddings se ON se.scene_id = s.id
                GROUP BY u.lid
                ORDER BY dist ASC
                LIMIT 4
            """),
            {"uid": str(current_user.id)},
        )
        for row in related.fetchall():
            rec = await _make_rec(row.lid, "related_topic")
            if rec:
                recommendations.append(rec)

    # 4. Fallback: newest completed lectures student hasn't seen
    if len(recommendations) < 3:
        fallback = await db.execute(
            text("""
                SELECT lv.id::text
                FROM lecture_videos lv
                LEFT JOIN student_video_progress svp
                    ON svp.lecture_id = lv.id AND svp.student_id = :uid
                WHERE svp.id IS NULL AND lv.status = 'COMPLETED'
                ORDER BY lv.created_at DESC
                LIMIT 4
            """),
            {"uid": str(current_user.id)},
        )
        for row in fallback.fetchall():
            rec = await _make_rec(row.id, "new")
            if rec:
                recommendations.append(rec)

    return recommendations[:limit]
