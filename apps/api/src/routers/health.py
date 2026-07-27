from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.services.health.health import check_health, check_readiness, get_liveness


router = APIRouter()


@router.get(
    "",
    summary="Health check (compatibility)",
    description="Legacy database health endpoint kept for existing monitors.",
    responses={
        200: {"description": "Database is healthy."},
        503: {"description": "Database is unavailable."},
    },
)
async def health(db_session: AsyncSession = Depends(get_db_session)):
    return await check_health(db_session)


@router.get(
    "/live",
    summary="API liveness",
    description="Dependency-free process liveness for orchestrators.",
)
async def liveness():
    return get_liveness()


@router.get(
    "/ready",
    summary="API readiness",
    description="Sanitized readiness for required dependencies and optional integrations.",
    responses={
        200: {"description": "Required dependencies are ready."},
        503: {"description": "At least one required dependency is not ready."},
    },
)
async def readiness(db_session: AsyncSession = Depends(get_db_session)):
    report = await check_readiness(db_session)
    if report.ready:
        return report
    return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
