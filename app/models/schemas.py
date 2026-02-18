"""Pydantic schemas for FastAPI request/response"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.utils.enums import OperationType, OperationStatus, TargetService


# --- Audit Log Schemas ---


class AuditLogResponse(BaseModel):
    """Response schema for audit log entry"""

    id: str
    operation_id: str
    event_type: str
    old_status: OperationStatus | None = None
    new_status: OperationStatus | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Provisioning Operation Schemas ---


class ProvisioningOperationResponse(BaseModel):
    """Response schema for a provisioning operation"""

    id: str
    midpoint_request_id: str | None = None
    broker_message_id: str | None = None
    operation_type: OperationType
    target_service: TargetService
    status: OperationStatus
    user_data: dict[str, Any]
    original_message: dict[str, Any]

    # Validation tracking
    validation_request_id: str | None = None
    validation_response: dict[str, Any] | None = None
    validated_at: datetime | None = None

    # Approval workflow tracking
    approval_request_id: str | None = None
    approval_requested_at: datetime | None = None
    approval_response: dict[str, Any] | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    approval_reason: str | None = None
    approval_timeout_at: datetime | None = None

    # Processing tracking
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    provisioning_result: dict[str, Any] | None = None

    # Retry logic
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None = None

    # Error handling
    error_message: str | None = None
    error_stacktrace: str | None = None
    sent_to_dlq_at: datetime | None = None

    # Notification tracking
    notification_sent: bool
    notification_sent_at: datetime | None = None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    # Optional relations
    audit_logs: list[AuditLogResponse] | None = None

    model_config = {"from_attributes": True}


class ProvisioningOperationSummary(BaseModel):
    """Summary schema for listing operations"""

    id: str
    midpoint_request_id: str | None = None
    operation_type: OperationType
    target_service: TargetService
    status: OperationStatus
    retry_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProvisioningListResponse(BaseModel):
    """Paginated list response for provisioning operations"""

    items: list[ProvisioningOperationSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProvisioningListParams(BaseModel):
    """Query parameters for listing operations"""

    status: OperationStatus | None = None
    target_service: TargetService | None = None
    operation_type: OperationType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


# --- Retry Schemas ---


class RetryOperationRequest(BaseModel):
    """Request schema for manual retry"""

    reset_retry_count: bool = Field(
        default=False,
        description="Reset retry count to 0 before retrying",
    )


class RetryOperationResponse(BaseModel):
    """Response schema for retry operation"""

    operation_id: str
    status: OperationStatus
    retry_count: int
    next_retry_at: datetime | None = None
    message: str


# --- Health Check Schemas ---


class HealthCheckResponse(BaseModel):
    """Response schema for health check endpoint"""

    status: str = Field(..., description="Overall health status: healthy or unhealthy")
    database: str = Field(..., description="Database connection status")
    version: str = Field(..., description="Application version")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class HealthCheckDetail(BaseModel):
    """Detailed health check with component status"""

    status: str
    database: str
    broker: str | None = None
    version: str
    uptime_seconds: float | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# --- Error Schemas ---


class ErrorResponse(BaseModel):
    """Standard error response"""

    error: str
    message: str
    details: dict[str, Any] | None = None


class ValidationErrorDetail(BaseModel):
    """Validation error detail"""

    field: str
    message: str
    type: str


class ValidationErrorResponse(BaseModel):
    """Validation error response (422)"""

    error: str = "validation_error"
    message: str = "Request validation failed"
    details: list[ValidationErrorDetail]


# --- Metrics Schemas ---


class MetricsResponse(BaseModel):
    """Response schema for metrics endpoint"""

    total_operations: int
    pending_operations: int
    processing_operations: int
    success_operations: int
    failed_operations: int
    retrying_operations: int
    dlq_count: int
    avg_processing_time_ms: float | None = None
    snapshot_at: datetime = Field(default_factory=datetime.utcnow)


# --- Approval Schemas ---


class ApprovalCallbackRequest(BaseModel):
    """Request schema for approval callback from Flask worker"""

    approved: bool = Field(..., description="Whether the operation was approved")
    reason: str = Field(..., description="Reason for approval/rejection")
    worker_id: str = Field(
        default="flask-approval-worker", description="ID of the worker making decision"
    )
    request_id: str | None = Field(None, description="Original request ID")
    decided_at: str | None = Field(None, description="ISO 8601 timestamp of decision")


class ApprovalCallbackResponse(BaseModel):
    """Response schema for approval callback"""

    status: str = Field(..., description="Status of callback processing")
    operation_id: str
    message: str | None = None


class PendingApprovalResponse(BaseModel):
    """Response schema for pending approval from Redis"""

    operation_id: str
    target_service: str
    operation_type: str
    requested_at: str
    timeout_at: str
    worker_id: str | None = None
    status: str
