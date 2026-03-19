from uuid import UUID
from pydantic import BaseModel, EmailStr
from shared.database.models import UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: UserRole = UserRole.STUDENT
    organization_id: UUID | None = None
    faculty: str | None = None        # TEACHER/FACULTY_ADMIN: khoa
    department: str | None = None     # TEACHER: bộ môn
    teacher_code: str | None = None   # TEACHER: mã GV
    major: str | None = None          # STUDENT: ngành học
    student_code: str | None = None   # STUDENT: mã SV


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: UserRole
    organization_id: UUID | None
    faculty: str | None
    department: str | None
    teacher_code: str | None
    major: str | None
    student_code: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class EnrollRequest(BaseModel):
    student_id: UUID


class AssignTeacherRequest(BaseModel):
    teacher_id: UUID
