import uuid
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db, get_minio
from api.dependencies.auth import require_teacher
from api.schemas.lecture import LectureResponse, LectureUpdate, SceneResponse
from shared.database.models import LectureVideo, Scene, User, UserRole
from shared.config import get_settings

router = APIRouter(prefix="/lectures", tags=["lectures"])
logger = structlog.get_logger(__name__)


def _build_presigned_url(bucket: str, key: str | None, minio) -> str | None:
    if not key:
        return None
    settings = get_settings()
    try:
        return minio.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
    except Exception:
        return f"{settings.minio_public_url}/{bucket}/{key}"


@router.get("/{lecture_id}", response_model=LectureResponse)
async def get_lecture(
    lecture_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    minio=Depends(get_minio),
) -> LectureResponse:
    result = await db.execute(
        select(LectureVideo)
        .options(selectinload(LectureVideo.scenes))
        .where(LectureVideo.id == lecture_id)
    )
    lecture = result.scalar_one_or_none()
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")

    scenes = sorted(lecture.scenes, key=lambda s: s.shot_index)
    scene_responses = [
        SceneResponse(
            id=scene.id,
            shot_index=scene.shot_index,
            timestamp_start=scene.timestamp_start,
            timestamp_end=scene.timestamp_end,
            transcript=scene.transcript,
            ocr_text=scene.ocr_text,
            visual_tags=scene.visual_tags,
            keyframe_url=_build_presigned_url(get_settings().minio_bucket_frames, scene.keyframe_minio_key, minio),
        )
        for scene in scenes
    ]

    return LectureResponse(
        id=lecture.id,
        title=lecture.title,
        status=lecture.status.value,
        fps=lecture.fps,
        duration_sec=lecture.duration_sec,
        scenes=scene_responses,
        video_url=_build_presigned_url(get_settings().minio_bucket_videos, lecture.minio_key, minio),
        created_at=lecture.created_at,
    )


@router.get("/", response_model=list[LectureResponse])
async def list_lectures(
    chapter_id: Optional[uuid.UUID] = Query(None),
    limit: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    minio=Depends(get_minio),
) -> list[LectureResponse]:
    query = select(LectureVideo).options(selectinload(LectureVideo.scenes))
    if chapter_id:
        query = query.where(LectureVideo.chapter_id == chapter_id)
    query = query.order_by(LectureVideo.created_at.desc())
    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    lectures = result.scalars().all()

    responses = []
    for lecture in lectures:
        scenes = sorted(lecture.scenes, key=lambda s: s.shot_index)
        scene_responses = [
            SceneResponse(
                id=scene.id,
                shot_index=scene.shot_index,
                timestamp_start=scene.timestamp_start,
                timestamp_end=scene.timestamp_end,
                transcript=scene.transcript,
                ocr_text=scene.ocr_text,
                visual_tags=scene.visual_tags,
                keyframe_url=_build_presigned_url(get_settings().minio_bucket_frames, scene.keyframe_minio_key, minio),
            )
            for scene in scenes
        ]
        responses.append(
            LectureResponse(
                id=lecture.id,
                title=lecture.title,
                status=lecture.status.value,
                fps=lecture.fps,
                duration_sec=lecture.duration_sec,
                scenes=scene_responses,
                video_url=_build_presigned_url(get_settings().minio_bucket_videos, lecture.minio_key, minio),
                created_at=lecture.created_at,
            )
        )
    return responses


@router.patch("/{lecture_id}", response_model=LectureResponse)
async def update_lecture(
    lecture_id: uuid.UUID,
    body: LectureUpdate,
    current_user: Annotated[User, Depends(require_teacher)],
    db: AsyncSession = Depends(get_db),
    minio=Depends(get_minio),
) -> LectureResponse:
    result = await db.execute(
        select(LectureVideo).options(selectinload(LectureVideo.scenes)).where(LectureVideo.id == lecture_id)
    )
    lecture = result.scalar_one_or_none()
    if not lecture:
        raise HTTPException(404)
    # TEACHER can only edit own lectures
    if current_user.role == UserRole.TEACHER and lecture.owner_id != current_user.id:
        raise HTTPException(403, "Not your lecture")
    if body.title:
        lecture.title = body.title
    await db.commit()
    await db.refresh(lecture)

    scenes = sorted(lecture.scenes, key=lambda s: s.shot_index)
    scene_responses = [
        SceneResponse(
            id=scene.id,
            shot_index=scene.shot_index,
            timestamp_start=scene.timestamp_start,
            timestamp_end=scene.timestamp_end,
            transcript=scene.transcript,
            ocr_text=scene.ocr_text,
            visual_tags=scene.visual_tags,
            keyframe_url=_build_presigned_url(get_settings().minio_bucket_frames, scene.keyframe_minio_key, minio),
        )
        for scene in scenes
    ]
    return LectureResponse(
        id=lecture.id,
        title=lecture.title,
        status=lecture.status.value,
        fps=lecture.fps,
        duration_sec=lecture.duration_sec,
        scenes=scene_responses,
        video_url=_build_presigned_url(get_settings().minio_bucket_videos, lecture.minio_key, minio),
        created_at=lecture.created_at,
    )


@router.delete("/{lecture_id}", status_code=204)
async def delete_lecture(
    lecture_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_teacher)],
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(LectureVideo).where(LectureVideo.id == lecture_id))
    lecture = result.scalar_one_or_none()
    if not lecture:
        raise HTTPException(404)
    if current_user.role == UserRole.TEACHER and lecture.owner_id != current_user.id:
        raise HTTPException(403)
    await db.delete(lecture)
    await db.commit()
