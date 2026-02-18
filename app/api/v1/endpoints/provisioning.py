"""Provisioning operations endpoints"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from prisma import Prisma

from app.db import get_db
from app.db.redis_client import RedisClient
from app.db.repositories.approval_redis_repository import ApprovalRedisRepository
from app.db.repositories.provisioning_repository import ProvisioningRepository
from app.core.orchestrator import ProvisioningOrchestrator
from app.core.retry_manager import RetryManager
from app.services.audit_service import AuditService
from app.models.schemas import (
    ProvisioningOperationResponse,
    ProvisioningOperationSummary,
    ProvisioningListResponse,
    RetryOperationRequest,
    RetryOperationResponse,
    AuditLogResponse,
    ApprovalCallbackRequest,
    ApprovalCallbackResponse,
    PendingApprovalResponse,
)
from app.utils.enums import OperationStatus, OperationType, TargetService
from app.utils.exceptions import OperationNotFoundError, InvalidOperationStateError

router = APIRouter(prefix="/provisioning", tags=["Provisioning"])


@router.get("", response_model=ProvisioningListResponse)
async def list_operations(
    status: Annotated[OperationStatus | None, Query(description="Filter by status")] = None,
    target_service: Annotated[TargetService | None, Query(description="Filter by target service")] = None,
    operation_type: Annotated[OperationType | None, Query(description="Filter by operation type")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    db: Prisma = Depends(get_db),
) -> ProvisioningListResponse:
    """List provisioning operations with optional filters and pagination"""
    repo = ProvisioningRepository(db)

    skip = (page - 1) * page_size
    operations, total = await repo.list_operations(
        status=status,
        target_service=target_service,
        operation_type=operation_type,
        skip=skip,
        take=page_size,
    )

    total_pages = (total + page_size - 1) // page_size

    items = [
        ProvisioningOperationSummary(
            id=op.id,
            midpoint_request_id=op.midpoint_request_id,
            operation_type=OperationType(op.operation_type),
            target_service=TargetService(op.target_service),
            status=OperationStatus(op.status),
            retry_count=op.retry_count,
            error_message=op.error_message,
            created_at=op.created_at,
            updated_at=op.updated_at,
        )
        for op in operations
    ]

    return ProvisioningListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{operation_id}", response_model=ProvisioningOperationResponse)
async def get_operation(
    operation_id: str,
    include_audit_logs: Annotated[bool, Query(description="Include audit logs")] = True,
    db: Prisma = Depends(get_db),
) -> ProvisioningOperationResponse:
    """Get a provisioning operation by ID with optional audit logs"""
    repo = ProvisioningRepository(db)

    operation = await repo.get_by_id(operation_id, include_audit_logs=include_audit_logs)

    if not operation:
        raise HTTPException(status_code=404, detail=f"Operation not found: {operation_id}")

    audit_logs = None
    if include_audit_logs and operation.audit_logs:
        audit_logs = [
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
            for log in operation.audit_logs
        ]

    return ProvisioningOperationResponse(
        id=operation.id,
        midpoint_request_id=operation.midpoint_request_id,
        broker_message_id=operation.broker_message_id,
        operation_type=OperationType(operation.operation_type),
        target_service=TargetService(operation.target_service),
        status=OperationStatus(operation.status),
        user_data=operation.user_data,
        original_message=operation.original_message,
        validation_request_id=operation.validation_request_id,
        validation_response=operation.validation_response,
        validated_at=operation.validated_at,
        approval_request_id=operation.approval_request_id,
        approval_requested_at=operation.approval_requested_at,
        approval_response=operation.approval_response,
        approved_at=operation.approved_at,
        approved_by=operation.approved_by,
        approval_reason=operation.approval_reason,
        approval_timeout_at=operation.approval_timeout_at,
        processing_started_at=operation.processing_started_at,
        processing_completed_at=operation.processing_completed_at,
        provisioning_result=operation.provisioning_result,
        retry_count=operation.retry_count,
        max_retries=operation.max_retries,
        next_retry_at=operation.next_retry_at,
        error_message=operation.error_message,
        error_stacktrace=operation.error_stacktrace,
        sent_to_dlq_at=operation.sent_to_dlq_at,
        notification_sent=operation.notification_sent,
        notification_sent_at=operation.notification_sent_at,
        created_at=operation.created_at,
        updated_at=operation.updated_at,
        audit_logs=audit_logs,
    )


@router.post("/{operation_id}/retry", response_model=RetryOperationResponse)
async def retry_operation(
    operation_id: str,
    request: RetryOperationRequest | None = None,
    db: Prisma = Depends(get_db),
) -> RetryOperationResponse:
    """Manually retry a failed operation"""
    repo = ProvisioningRepository(db)
    audit_service = AuditService(db)

    operation = await repo.get_by_id(operation_id)

    if not operation:
        raise HTTPException(status_code=404, detail=f"Operation not found: {operation_id}")

    # Check if operation can be retried
    retriable_statuses = [OperationStatus.FAILED, OperationStatus.DLQ]
    if OperationStatus(operation.status) not in retriable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Operation cannot be retried: current status is {operation.status}. "
            f"Only operations with status {retriable_statuses} can be retried.",
        )

    # Reset retry count if requested
    retry_count = 0 if (request and request.reset_retry_count) else operation.retry_count

    # Schedule retry using RetryManager
    retry_manager = RetryManager(db)
    next_retry_at, _ = retry_manager.calculate_next_retry(retry_count)

    # Update operation
    await db.provisioningoperation.update(
        where={"id": operation_id},
        data={
            "status": OperationStatus.RETRYING,
            "retry_count": retry_count,
            "next_retry_at": next_retry_at,
            "error_message": None,
            "error_stacktrace": None,
        },
    )

    # Log the manual retry
    await audit_service.log_manual_retry(operation_id=operation_id)
    await audit_service.log_status_change(
        operation_id=operation_id,
        old_status=OperationStatus(operation.status),
        new_status=OperationStatus.RETRYING,
        message="Manual retry triggered via API",
    )

    return RetryOperationResponse(
        operation_id=operation_id,
        status=OperationStatus.RETRYING,
        retry_count=retry_count,
        next_retry_at=next_retry_at,
        message=f"Retry scheduled for {next_retry_at.isoformat()}",
    )


@router.post("/{operation_id}/approve-callback", response_model=ApprovalCallbackResponse)
async def receive_approval_callback(
    operation_id: str,
    request: ApprovalCallbackRequest,
    db: Prisma = Depends(get_db),
) -> ApprovalCallbackResponse:
    """Receive approval decision callback from Flask worker

    This endpoint is called by the Flask approval worker after it
    sleeps and makes a random approval decision.

    Args:
        operation_id: The operation being approved/rejected
        request: Approval decision with reason and worker_id

    Returns:
        Confirmation of callback processing
    """
    try:
        # Create orchestrator instance
        orchestrator = ProvisioningOrchestrator(db)

        # Process the approval response
        await orchestrator.process_approval_response(
            operation_id=operation_id,
            approved=request.approved,
            reason=request.reason,
            worker_id=request.worker_id,
        )

        return ApprovalCallbackResponse(
            status="success",
            operation_id=operation_id,
            message=f"Approval {'granted' if request.approved else 'rejected'} successfully",
        )

    except ValueError as e:
        # Operation not found or invalid state
        raise HTTPException(status_code=404, detail=str(e))

    except Exception as e:
        # Unexpected error
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process approval callback: {str(e)}",
        )


@router.get("/pending-approvals", response_model=list[PendingApprovalResponse])
async def list_pending_approvals() -> list[PendingApprovalResponse]:
    """List all pending approvals from Redis

    This endpoint allows monitoring which operations are currently
    waiting for approval from the Flask worker.

    Returns:
        List of pending approvals with operation details
    """
    try:
        # Get Redis client and create repository
        redis_client = await RedisClient.get_client()
        approval_repo = ApprovalRedisRepository(redis_client)

        # Get all pending approvals
        pending = await approval_repo.list_all_pending()

        # Convert to response schema
        return [
            PendingApprovalResponse(
                operation_id=item["operation_id"],
                target_service=item["target_service"],
                operation_type=item["operation_type"],
                requested_at=item["requested_at"],
                timeout_at=item["timeout_at"],
                worker_id=item.get("worker_id"),
                status=item["status"],
            )
            for item in pending
        ]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list pending approvals: {str(e)}",
        )
