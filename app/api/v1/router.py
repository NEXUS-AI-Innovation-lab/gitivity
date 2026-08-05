"""API v1 router aggregating all endpoints"""
from fastapi import APIRouter

from app.api.v1.endpoints import (
    approvals,
    approvers,
    audit,
    connectors,
    entitlement_removals,
    health,
    provisioning,
)

api_router = APIRouter(prefix="/api/v1")

# Include endpoint routers
api_router.include_router(provisioning.router)
api_router.include_router(audit.router)
api_router.include_router(connectors.router)
api_router.include_router(approvers.router)
api_router.include_router(approvals.router)
api_router.include_router(entitlement_removals.router)

# Health endpoints are at root level (no /api/v1 prefix)
health_router = health.router
