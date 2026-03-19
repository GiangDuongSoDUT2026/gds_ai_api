import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.dependencies.auth import get_current_user, require_super_admin
from shared.database.models import Organization, User

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrgCreate(BaseModel):
    name: str
    short_name: str | None = None


class OrgResponse(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str | None

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[OrgResponse])
async def list_organizations(
    db: AsyncSession = Depends(get_db),
) -> list[OrgResponse]:
    result = await db.execute(select(Organization).order_by(Organization.name))
    return [OrgResponse.model_validate(o) for o in result.scalars().all()]


@router.post("/", response_model=OrgResponse, status_code=201)
async def create_organization(
    body: OrgCreate,
    _: Annotated[User, Depends(require_super_admin)],
    db: AsyncSession = Depends(get_db),
) -> OrgResponse:
    org = Organization(id=uuid.uuid4(), name=body.name, short_name=body.short_name)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return OrgResponse.model_validate(org)
