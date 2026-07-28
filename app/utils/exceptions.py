"""Custom exceptions for the application"""
from typing import Any

from app.utils.enums import TargetService


class GatewayIAMError(Exception):
    """Base exception for all Gateway IAM errors"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class ValidationRejectedError(GatewayIAMError):
    """Raised when n8n validation rejects an operation"""

    def __init__(
        self,
        operation_id: str,
        reason: str | None = None,
        validation_response: dict[str, Any] | None = None,
    ) -> None:
        self.operation_id = operation_id
        self.reason = reason
        self.validation_response = validation_response
        super().__init__(
            message=f"Validation rejected for operation {operation_id}: {reason}",
            details={
                "operation_id": operation_id,
                "reason": reason,
                "validation_response": validation_response,
            },
        )


class ValidationTimeoutError(GatewayIAMError):
    """Raised when n8n validation times out"""

    def __init__(self, operation_id: str, timeout_seconds: int) -> None:
        self.operation_id = operation_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            message=f"Validation timeout for operation {operation_id} after {timeout_seconds}s",
            details={
                "operation_id": operation_id,
                "timeout_seconds": timeout_seconds,
            },
        )


class ProvisioningError(GatewayIAMError):
    """Raised when provisioning to target service fails"""

    def __init__(
        self,
        operation_id: str,
        target_service: TargetService,
        error_message: str,
        error_code: str | None = None,
        is_retriable: bool = True,
    ) -> None:
        self.operation_id = operation_id
        self.target_service = target_service
        self.error_code = error_code
        self.is_retriable = is_retriable
        super().__init__(
            message=f"Provisioning failed for operation {operation_id} on {target_service}: {error_message}",
            details={
                "operation_id": operation_id,
                "target_service": target_service.value,
                "error_code": error_code,
                "is_retriable": is_retriable,
            },
        )


class ConnectorNotFoundError(GatewayIAMError):
    """Raised when no connector is found for a target service"""

    def __init__(self, target_service: TargetService | str) -> None:
        self.target_service = target_service
        value = (
            target_service.value
            if isinstance(target_service, TargetService)
            else str(target_service)
        )
        super().__init__(
            message=f"No connector found for target service: {target_service}",
            details={"target_service": value},
        )


class ConnectorConnectionError(GatewayIAMError):
    """Raised when connector cannot connect to target service"""

    def __init__(
        self,
        target_service: TargetService,
        error_message: str,
    ) -> None:
        self.target_service = target_service
        super().__init__(
            message=f"Cannot connect to {target_service}: {error_message}",
            details={
                "target_service": target_service.value,
                "error": error_message,
            },
        )


class RetryExhaustedError(GatewayIAMError):
    """Raised when all retry attempts have been exhausted"""

    def __init__(
        self,
        operation_id: str,
        retry_count: int,
        max_retries: int,
        last_error: str | None = None,
    ) -> None:
        self.operation_id = operation_id
        self.retry_count = retry_count
        self.max_retries = max_retries
        self.last_error = last_error
        super().__init__(
            message=f"Retry exhausted for operation {operation_id}: {retry_count}/{max_retries} attempts",
            details={
                "operation_id": operation_id,
                "retry_count": retry_count,
                "max_retries": max_retries,
                "last_error": last_error,
            },
        )


class OperationNotFoundError(GatewayIAMError):
    """Raised when a provisioning operation is not found"""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__(
            message=f"Operation not found: {operation_id}",
            details={"operation_id": operation_id},
        )


class InvalidOperationStateError(GatewayIAMError):
    """Raised when an operation is in an invalid state for the requested action"""

    def __init__(
        self,
        operation_id: str,
        current_state: str,
        required_states: list[str],
        action: str,
    ) -> None:
        self.operation_id = operation_id
        self.current_state = current_state
        self.required_states = required_states
        self.action = action
        super().__init__(
            message=f"Cannot {action} operation {operation_id}: current state is {current_state}, "
            f"required states: {required_states}",
            details={
                "operation_id": operation_id,
                "current_state": current_state,
                "required_states": required_states,
                "action": action,
            },
        )


class BrokerConnectionError(GatewayIAMError):
    """Raised when connection to message broker fails"""

    def __init__(self, broker_type: str, error_message: str) -> None:
        self.broker_type = broker_type
        super().__init__(
            message=f"Cannot connect to {broker_type} broker: {error_message}",
            details={
                "broker_type": broker_type,
                "error": error_message,
            },
        )


class MessageParsingError(GatewayIAMError):
    """Raised when a broker message cannot be parsed"""

    def __init__(self, error_message: str, raw_message: str | None = None) -> None:
        super().__init__(
            message=f"Failed to parse broker message: {error_message}",
            details={
                "error": error_message,
                "raw_message": raw_message[:500] if raw_message else None,
            },
        )


class NotificationError(GatewayIAMError):
    """Raised when notification to n8n fails"""

    def __init__(
        self,
        operation_id: str,
        error_message: str,
    ) -> None:
        self.operation_id = operation_id
        super().__init__(
            message=f"Notification failed for operation {operation_id}: {error_message}",
            details={
                "operation_id": operation_id,
                "error": error_message,
            },
        )
