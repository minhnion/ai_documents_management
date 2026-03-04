from fastapi import APIRouter

from app.api.v1.endpoints import health

api_router = APIRouter()

api_router.include_router(health.router)

# Future feature routers will be included here:
# api_router.include_router(guidelines.router)
# api_router.include_router(versions.router)
# api_router.include_router(documents.router)
# api_router.include_router(sections.router)
