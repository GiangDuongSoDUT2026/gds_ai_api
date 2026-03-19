from uuid import UUID
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.security import decode_token
from api.dependencies import get_db
from shared.database.models import User, UserRole

logger = structlog.get_logger(__name__)
bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
            )
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


# Role guard factories
def require_roles(*roles: UserRole):
    async def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return user

    return _guard


require_teacher = require_roles(
    UserRole.TEACHER, UserRole.FACULTY_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.SUPER_ADMIN
)
require_faculty_admin = require_roles(
    UserRole.FACULTY_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.SUPER_ADMIN
)
require_school_admin = require_roles(UserRole.SCHOOL_ADMIN, UserRole.SUPER_ADMIN)
require_super_admin = require_roles(UserRole.SUPER_ADMIN)

# Helpers
ROLE_HIERARCHY = {
    UserRole.SUPER_ADMIN: 5,
    UserRole.SCHOOL_ADMIN: 4,
    UserRole.FACULTY_ADMIN: 3,
    UserRole.TEACHER: 2,
    UserRole.STUDENT: 1,
}


def has_role_or_above(user: User, min_role: UserRole) -> bool:
    return ROLE_HIERARCHY.get(user.role, 0) >= ROLE_HIERARCHY.get(min_role, 0)
