import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.bootstrap import bootstrap_auth_data
from app.core.config import settings
from app.core.database import (
    AsyncSessionLocal,
    init_db_schema,
    migrate_auth_schema_to_single_role,
)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(
        "Application startup: %s v%s",
        settings.APP_NAME,
        settings.APP_VERSION,
    )
    if settings.AUTO_CREATE_TABLES:
        await init_db_schema()
        await migrate_auth_schema_to_single_role()
        logger.info("Database schema ready.")

    async with AsyncSessionLocal() as session:
        try:
            await bootstrap_auth_data(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield
    # Shutdown
    logger.info("Application shutdown.")


def create_application() -> FastAPI:
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router, prefix=settings.API_V1_PREFIX)

    return application


app = create_application()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        # reload=settings.DEBUG,
        reload=False,
        log_level="debug" if settings.DEBUG else "info",
    )
