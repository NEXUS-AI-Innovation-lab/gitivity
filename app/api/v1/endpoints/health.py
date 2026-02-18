"""Health check and metrics endpoints"""
from datetime import datetime

from fastapi import APIRouter, Depends
from prisma import Prisma

from app.config.settings import settings
from app.db import get_db
from app.models.schemas import HealthCheckResponse, MetricsResponse
from app.utils.enums import OperationStatus

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthCheckResponse)
async def health_check(db: Prisma = Depends(get_db)) -> HealthCheckResponse:
    """Check API and database health"""
    db_status = "healthy"

    try:
        # Test database connection
        await db.execute_raw("SELECT 1")
    except Exception:
        db_status = "unhealthy"

    overall_status = "healthy" if db_status == "healthy" else "unhealthy"

    return HealthCheckResponse(
        status=overall_status,
        database=db_status,
        version=settings.APP_VERSION,
        timestamp=datetime.utcnow(),
    )


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(db: Prisma = Depends(get_db)) -> MetricsResponse:
    """Get application metrics (Prometheus-compatible format)"""
    # Count operations by status
    total = await db.provisioningoperation.count()
    pending = await db.provisioningoperation.count(
        where={"status": OperationStatus.PENDING}
    )
    processing = await db.provisioningoperation.count(
        where={"status": OperationStatus.PROCESSING}
    )
    success = await db.provisioningoperation.count(
        where={"status": OperationStatus.SUCCESS}
    )
    failed = await db.provisioningoperation.count(
        where={"status": OperationStatus.FAILED}
    )
    retrying = await db.provisioningoperation.count(
        where={"status": OperationStatus.RETRYING}
    )
    dlq_count = await db.deadletterqueue.count(where={"resolved": False})

    # Calculate average processing time for successful operations
    avg_time = None
    successful_ops = await db.provisioningoperation.find_many(
        where={
            "status": OperationStatus.SUCCESS,
            "processing_started_at": {"not": None},
            "processing_completed_at": {"not": None},
        },
        take=100,
        order={"created_at": "desc"},
    )

    if successful_ops:
        total_time = 0
        count = 0
        for op in successful_ops:
            if op.processing_started_at and op.processing_completed_at:
                diff = (op.processing_completed_at - op.processing_started_at).total_seconds() * 1000
                total_time += diff
                count += 1
        if count > 0:
            avg_time = total_time / count

    return MetricsResponse(
        total_operations=total,
        pending_operations=pending,
        processing_operations=processing,
        success_operations=success,
        failed_operations=failed,
        retrying_operations=retrying,
        dlq_count=dlq_count,
        avg_processing_time_ms=avg_time,
        snapshot_at=datetime.utcnow(),
    )
