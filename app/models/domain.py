"""Domain models for internal business logic"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.utils.enums import OperationType, OperationStatus, TargetService


class UserData(BaseModel):
    """User data from MidPoint for provisioning"""

    username: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    password: str | None = None
    roles: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class MidPointMessage(BaseModel):
    """Message received from MidPoint via broker"""

    request_id: str = Field(..., description="Unique request ID from MidPoint")
    operation_type: OperationType
    target_service: TargetService
    target_id: str | None = Field(
        default=None,
        description="Configured target instance ID; defaults to the connector family",
    )
    user_data: UserData
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def target_key(self) -> str:
        """Stable key used for routing and per-target state."""
        return self.target_id or self.target_service.value.lower()


class ProvisioningRequest(BaseModel):
    """Internal request for provisioning an operation"""

    operation_id: str
    operation_type: OperationType
    target_service: TargetService
    user_data: dict[str, Any]
    retry_count: int = 0


class ProvisioningResult(BaseModel):
    """Result of a provisioning operation from target service"""

    success: bool
    service_user_id: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class ValidationRequest(BaseModel):
    """Request sent to n8n for validation"""

    operation_id: str
    operation_type: OperationType
    target_service: TargetService
    user_data: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationResponse(BaseModel):
    """Response from n8n validation webhook"""

    approved: bool
    validation_id: str | None = None
    reason: str | None = None
    modified_data: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationPayload(BaseModel):
    """Payload sent to n8n notification webhook"""

    operation_id: str
    operation_type: OperationType
    target_service: TargetService
    status: OperationStatus
    user_data: dict[str, Any]
    result: ProvisioningResult | None = None
    error_message: str | None = None
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class RetryInfo(BaseModel):
    """Information about retry scheduling"""

    operation_id: str
    retry_count: int
    max_retries: int
    next_retry_at: datetime
    delay_seconds: int
