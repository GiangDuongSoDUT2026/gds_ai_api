import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from api.dependencies import get_db
from api.dependencies.auth import get_current_user, require_school_admin
from api.schemas.auth import (
    AssignTeacherRequest,
    EnrollRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from shared.database.models import Course, CourseEnrollment, CourseTeacher, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])
logger = structlog.get_logger(__name__)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, db: AsyncSession = Depends(get_db)
) -> UserResponse:
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    # Only STUDENT and TEACHER can self-register; higher roles need admin
    if body.role not in (UserRole.STUDENT, UserRole.TEACHER):
        raise HTTPException(
            status_code=403, detail="Admin roles must be assigned by a SUPER_ADMIN"
        )
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        organization_id=body.organization_id,
        faculty=body.faculty,
        department=body.department,
        teacher_code=body.teacher_code,
        major=body.major,
        student_code=body.student_code,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(
        select(User).where(User.email == body.email, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token_data = {
        "sub": str(user.id),
        "role": user.role.value,
        "org": str(user.organization_id) if user.organization_id else None,
        "faculty": user.faculty,
    }
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    token_data = {
        "sub": str(user.id),
        "role": user.role.value,
        "org": str(user.organization_id) if user.organization_id else None,
        "faculty": user.faculty,
    }
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse.model_validate(current_user)


# ─── Admin: create higher-role users ──────────────────────────────────────────


@router.post("/admin/users", response_model=UserResponse, status_code=201)
async def admin_create_user(
    body: RegisterRequest,
    _admin: Annotated[User, Depends(require_school_admin)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """SCHOOL_ADMIN+ can create any role."""
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        organization_id=body.organization_id,
        faculty=body.faculty,
        department=body.department,
        teacher_code=body.teacher_code,
        major=body.major,
        student_code=body.student_code,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ─── Enrollment management ────────────────────────────────────────────────────


@router.post("/courses/{course_id}/enroll", status_code=204)
async def enroll_student(
    course_id: uuid.UUID,
    body: EnrollRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> None:
    """TEACHER+ can enroll students; students can self-enroll."""
    student_id = current_user.id if current_user.role == UserRole.STUDENT else body.student_id
    enrollment = CourseEnrollment(course_id=course_id, student_id=student_id)
    db.add(enrollment)
    try:
        await db.commit()
    except Exception:
        await db.rollback()


@router.post("/courses/{course_id}/teachers", status_code=204)
async def assign_teacher(
    course_id: uuid.UUID,
    body: AssignTeacherRequest,
    _: Annotated[User, Depends(require_school_admin)],
    db: AsyncSession = Depends(get_db),
) -> None:
    ct = CourseTeacher(course_id=course_id, teacher_id=body.teacher_id)
    db.add(ct)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
