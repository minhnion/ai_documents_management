import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.bootstrap import bootstrap_auth_data
from app.core.config import settings
from app.core.database import (
    AsyncSessionLocal,
    init_db_schema,
    migrate_guidelines_ten_benh_schema,
    migrate_sections_quality_schema,
    migrate_auth_schema_to_single_role,
)

FRONTEND_DIST = Path(__file__).parent.parent / "web" / "dist"

print(f"###Frontend dist path: {FRONTEND_DIST}")

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """
    Custom StaticFiles class to serve an SPA fallback index.html
    and prevent returning HTML for missing API routes.
    """
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            if ex.status_code == 404:
                # Don't serve index.html for missing API endpoints
                api_prefix = settings.API_V1_PREFIX.strip("/")
                if path.startswith(api_prefix):
                    raise ex
                # Fallback to serving the SPA's entry point
                return await super().get_response("index.html", scope)
            raise ex


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
        await migrate_guidelines_ten_benh_schema()
        await migrate_sections_quality_schema()
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

    if FRONTEND_DIST.exists():
        # Mount the custom SPA static files handler at root
        application.mount(
            "/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="spa"
        )

    return application


app = create_application()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level="debug" if settings.DEBUG else "info",
    )
