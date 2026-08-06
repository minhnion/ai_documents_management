from fastapi import APIRouter

from app.api.v1.endpoints import auth, cost, documents, guidelines, health, versions

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(guidelines.router)
api_router.include_router(versions.router)
api_router.include_router(documents.router)
api_router.include_router(cost.router)

# Future feature routers will be included here:
# api_router.include_router(sections.router)
