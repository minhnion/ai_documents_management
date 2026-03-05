import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)


async def bootstrap_auth_data(session: AsyncSession) -> None:
    auth_service = AuthService(session)

    if not settings.SEED_AUTH_DATA:
        return

    admin_user = await auth_service.ensure_default_admin(
        email=settings.DEFAULT_ADMIN_EMAIL,
        password=settings.DEFAULT_ADMIN_PASSWORD,
        full_name=settings.DEFAULT_ADMIN_FULL_NAME,
    )
    if admin_user is not None:
        logger.info("Auth seed: default admin ready (%s)", admin_user.email)
