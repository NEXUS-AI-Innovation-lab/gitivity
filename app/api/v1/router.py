"""API v1 router aggregating all endpoints"""
from fastapi import APIRouter

from app.api.v1.endpoints import health, provisioning, audit, connectors, approvers

api_router = APIRouter(prefix="/api/v1")

# Include endpoint routers
api_router.include_router(provisioning.router)
api_router.include_router(audit.router)
api_router.include_router(connectors.router)
api_router.include_router(approvers.router)

# Health endpoints are at root level (no /api/v1 prefix)
health_router = health.router
