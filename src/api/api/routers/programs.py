import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.dependencies.auth import require_faculty_admin, require_school_admin, require_teacher
from api.schemas.lecture import (
    ChapterCreate,
    ChapterResponse,
    CourseCreate,
    CourseResponse,
    ProgramCreate,
    ProgramResponse,
)
from shared.database.models import Chapter, Course, Program, User, UserRole

router = APIRouter(tags=["programs"])
logger = structlog.get_logger(__name__)


@router.post("/programs", response_model=ProgramResponse, status_code=status.HTTP_201_CREATED)
async def create_program(
    data: ProgramCreate,
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    program = Program(id=uuid.uuid4(), name=data.name, description=data.description)
    db.add(program)
    await db.commit()
    await db.refresh(program)
    return ProgramResponse.model_validate(program)


@router.get("/programs", response_model=list[ProgramResponse])
async def list_programs(db: AsyncSession = Depends(get_db)) -> list[ProgramResponse]:
    result = await db.execute(select(Program).order_by(Program.created_at.desc()))
    programs = result.scalars().all()
    return [ProgramResponse.model_validate(p) for p in programs]


@router.get("/programs/{program_id}", response_model=ProgramResponse)
async def get_program(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    program = await db.get(Program, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    return ProgramResponse.model_validate(program)


@router.patch("/programs/{program_id}", response_model=ProgramResponse)
async def update_program(
    program_id: uuid.UUID,
    body: ProgramCreate,
    current_user: Annotated[User, Depends(require_school_admin)],
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    result = await db.execute(select(Program).where(Program.id == program_id))
    prog = result.scalar_one_or_none()
    if not prog:
        raise HTTPException(404, "Program not found")
    # SCHOOL_ADMIN can only edit programs in their organization
    if current_user.role == UserRole.SCHOOL_ADMIN and prog.organization_id != current_user.organization_id:
        raise HTTPException(403, "Not your organization's program")
    if body.name:
        prog.name = body.name
    if body.description is not None:
        prog.description = body.description
    await db.commit()
    await db.refresh(prog)
    return ProgramResponse.model_validate(prog)


@router.delete("/programs/{program_id}", status_code=204)
async def delete_program(
    program_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_school_admin)],
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Program).where(Program.id == program_id))
    prog = result.scalar_one_or_none()
    if not prog:
        raise HTTPException(404)
    if current_user.role == UserRole.SCHOOL_ADMIN and prog.organization_id != current_user.organization_id:
        raise HTTPException(403)
    await db.delete(prog)
    await db.commit()


@router.post("/programs/{program_id}/courses", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    program_id: uuid.UUID,
    data: CourseCreate,
    db: AsyncSession = Depends(get_db),
) -> CourseResponse:
    program = await db.get(Program, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    course = Course(
        id=uuid.uuid4(),
        program_id=program_id,
        name=data.name,
        code=data.code,
        description=data.description,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return CourseResponse.model_validate(course)


@router.get("/programs/{program_id}/courses", response_model=list[CourseResponse])
async def list_courses(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[CourseResponse]:
    result = await db.execute(
        select(Course).where(Course.program_id == program_id).order_by(Course.created_at)
    )
    courses = result.scalars().all()
    return [CourseResponse.model_validate(c) for c in courses]


@router.patch("/courses/{course_id}", response_model=CourseResponse)
async def update_course(
    course_id: uuid.UUID,
    body: CourseCreate,
    current_user: Annotated[User, Depends(require_faculty_admin)],
    db: AsyncSession = Depends(get_db),
) -> CourseResponse:
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(404)
    if current_user.role == UserRole.FACULTY_ADMIN and course.faculty != current_user.faculty:
        raise HTTPException(403)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(course, field, val)
    await db.commit()
    await db.refresh(course)
    return CourseResponse.model_validate(course)


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(
    course_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_faculty_admin)],
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(404)
    if current_user.role == UserRole.FACULTY_ADMIN and course.faculty != current_user.faculty:
        raise HTTPException(403)
    await db.delete(course)
    await db.commit()


@router.post("/courses/{course_id}/chapters", response_model=ChapterResponse, status_code=status.HTTP_201_CREATED)
async def create_chapter(
    course_id: uuid.UUID,
    data: ChapterCreate,
    db: AsyncSession = Depends(get_db),
) -> ChapterResponse:
    course = await db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    chapter = Chapter(
        id=uuid.uuid4(),
        course_id=course_id,
        title=data.title,
        order_index=data.order_index,
    )
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)
    return ChapterResponse.model_validate(chapter)


@router.get("/courses/{course_id}/chapters", response_model=list[ChapterResponse])
async def list_chapters(
    course_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ChapterResponse]:
    result = await db.execute(
        select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.order_index)
    )
    chapters = result.scalars().all()
    return [ChapterResponse.model_validate(c) for c in chapters]


@router.patch("/chapters/{chapter_id}", response_model=ChapterResponse)
async def update_chapter(
    chapter_id: uuid.UUID,
    body: ChapterCreate,
    current_user: Annotated[User, Depends(require_teacher)],
    db: AsyncSession = Depends(get_db),
) -> ChapterResponse:
    result = await db.execute(select(Chapter).where(Chapter.id == chapter_id))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(chapter, field, val)
    await db.commit()
    await db.refresh(chapter)
    return ChapterResponse.model_validate(chapter)


@router.delete("/chapters/{chapter_id}", status_code=204)
async def delete_chapter(
    chapter_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_teacher)],
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Chapter).where(Chapter.id == chapter_id))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404)
    await db.delete(chapter)
    await db.commit()
