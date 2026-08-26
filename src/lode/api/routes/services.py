"""Global runtime service directory."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.audit import audit_action
from lode.api.deps import require_admin, require_user
from lode.api.schemas import ServiceIn, ServiceOut
from lode.db.models.application import Service
from lode.db.models.git import GitRepo
from lode.db.session import AsyncSessionLocal


router = APIRouter(prefix="/services", tags=["services"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[ServiceOut])
async def list_services(
    _user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[ServiceOut]:
    rows = (await session.execute(select(Service).order_by(Service.service_name))).scalars().all()
    return [ServiceOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("", response_model=ServiceOut, status_code=201)
async def create_service(
    payload: ServiceIn,
    actor_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ServiceOut:
    repo = await session.get(GitRepo, payload.repo_id)
    if repo is None or repo.scope != "global":
        raise HTTPException(status_code=404, detail="global repository not found")
    row = Service(**payload.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="service_name already exists") from exc
    await session.refresh(row)
    await audit_action(
        action="service.create",
        actor_id=actor_id,
        target_type="service",
        target_id=str(row.id),
        detail={"service_name": row.service_name, "repo_id": row.repo_id},
    )
    return ServiceOut.model_validate(row, from_attributes=True)


@router.put("/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: int,
    payload: ServiceIn,
    actor_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ServiceOut:
    row = await session.get(Service, service_id)
    if row is None:
        raise HTTPException(status_code=404, detail="service not found")
    repo = await session.get(GitRepo, payload.repo_id)
    if repo is None or repo.scope != "global":
        raise HTTPException(status_code=404, detail="global repository not found")
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="service_name already exists") from exc
    await audit_action(
        action="service.update",
        actor_id=actor_id,
        target_type="service",
        target_id=str(row.id),
        detail={"service_name": row.service_name, "repo_id": row.repo_id, "state": row.state},
    )
    return ServiceOut.model_validate(row, from_attributes=True)
