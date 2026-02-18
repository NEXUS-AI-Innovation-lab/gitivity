"""Audit log endpoints"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from prisma import Prisma

from app.db import get_db
from app.db.repositories.audit_repository import AuditRepository
from app.models.schemas import AuditLogResponse
from app.utils.enums import OperationStatus

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/{operation_id}", response_model=list[AuditLogResponse])
async def get_audit_logs(
    operation_id: str,
    event_type: Annotated[str | None, Query(description="Filter by event type")] = None,
    skip: Annotated[int, Query(ge=0, description="Number of records to skip")] = 0,
    take: Annotated[
        int, Query(ge=1, le=500, description="Number of records to return")
    ] = 100,
    db: Prisma = Depends(get_db),
) -> list[AuditLogResponse]:
    """Get audit logs for a specific operation"""
    repo = AuditRepository(db)

    # Check if operation exists
    operation = await db.provisioningoperation.find_unique(where={"id": operation_id})
    if not operation:
        raise HTTPException(
            status_code=404, detail=f"Operation not found: {operation_id}"
        )

    logs = await repo.get_logs_for_operation(
        operation_id=operation_id,
        event_type=event_type,
        skip=skip,
        take=take,
    )

    return [
        AuditLogResponse(
            id=log.id,
            operation_id=log.operation_id,
            event_type=log.event_type,
            old_status=OperationStatus(log.old_status) if log.old_status else None,
            new_status=OperationStatus(log.new_status) if log.new_status else None,
            message=log.message,
            metadata=log.metadata,
            actor_type=log.actor_type,
            actor_id=log.actor_id,
            created_at=log.created_at,
        )
        for log in logs
    ]
