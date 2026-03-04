from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DBSession
from app.core.config import settings
from app.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse, summary="Health Check")
async def health_check(db: DBSession) -> HealthResponse:
    """
    Check the health status of the application and its dependencies.

    Returns:
        - **status**: overall status (`ok` or `degraded`)
        - **database**: database connectivity (`connected` or `disconnected`)
    """
    db_status = "connected"
    overall_status = "ok"

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        timestamp=datetime.now(tz=timezone.utc),
        database=db_status,
    )
