"""HTTP client for n8n webhook integration"""
import logging
from typing import Any

import httpx

from app.config.settings import settings
from app.models.domain import ValidationResponse, ProvisioningResult
from app.utils.enums import OperationType, OperationStatus, TargetService
from app.utils.exceptions import ValidationTimeoutError, NotificationError

logger = logging.getLogger(__name__)


class N8NClient:
    """Client for communicating with n8n webhooks

    Handles validation requests and notifications for provisioning operations.
    """

    def __init__(
        self,
        validation_url: str | None = None,
        notification_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """Initialize n8n client

        Args:
            validation_url: Override validation webhook URL
            notification_url: Override notification webhook URL
            timeout: Override request timeout in seconds
        """
        self._validation_url = validation_url or settings.N8N_VALIDATION_WEBHOOK_URL
        self._notification_url = (
            notification_url or settings.N8N_NOTIFICATION_WEBHOOK_URL
        )
        self._timeout = timeout or settings.N8N_TIMEOUT

    async def validate(
        self,
        operation_id: str,
        request_id: str,
        operation_type: OperationType,
        target_service: TargetService,
        user_data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ValidationResponse:
        """Send validation request to n8n

        Args:
            operation_id: Internal operation ID
            request_id: MidPoint request ID
            operation_type: Type of operation
            target_service: Target service
            user_data: User data to validate
            metadata: Optional additional metadata

        Returns:
            ValidationResponse with approval status

        Raises:
            ValidationTimeoutError: If request times out
        """
        payload = {
            "operation_id": operation_id,
            "request_id": request_id,
            "operation_type": operation_type.value,
            "target_service": target_service.value,
            "user_data": user_data,
            "metadata": metadata or {},
        }

        logger.info(
            "Sending validation request to n8n",
            extra={
                "operation_id": operation_id,
                "request_id": request_id,
                "target_service": target_service.value,
            },
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._validation_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()

                logger.info(
                    "Validation response received",
                    extra={
                        "operation_id": operation_id,
                        "approved": data.get("approved"),
                    },
                )

                return ValidationResponse(
                    approved=data.get("approved", False),
                    validation_id=data.get("validation_id"),
                    reason=data.get("reason"),
                    modified_data=data.get("modified_data"),
                    metadata=data.get("metadata", {}),
                )

        except httpx.TimeoutException:
            logger.error(
                "Validation request timed out",
                extra={"operation_id": operation_id},
            )
            raise ValidationTimeoutError(
                operation_id=operation_id,
                timeout_seconds=self._timeout,
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Validation request failed with status {e.response.status_code}",
                extra={"operation_id": operation_id, "status": e.response.status_code},
            )
            # Return rejected validation for HTTP errors
            return ValidationResponse(
                approved=False,
                reason=f"Validation service returned HTTP {e.response.status_code}",
            )
        except Exception as e:
            logger.error(
                f"Validation request failed: {e}",
                extra={"operation_id": operation_id},
            )
            return ValidationResponse(
                approved=False,
                reason=f"Validation service error: {str(e)}",
            )

    async def notify_success(
        self,
        operation_id: str,
        request_id: str | None,
        operation_type: OperationType,
        target_service: TargetService,
        result: ProvisioningResult,
        user_data: dict[str, Any],
    ) -> bool:
        """Send success notification to n8n

        Args:
            operation_id: Internal operation ID
            request_id: MidPoint request ID
            operation_type: Type of operation
            target_service: Target service
            result: Provisioning result
            user_data: User data that was provisioned

        Returns:
            True if notification sent successfully

        Raises:
            NotificationError: If notification fails
        """
        payload = {
            "operation_id": operation_id,
            "request_id": request_id,
            "status": OperationStatus.SUCCESS.value,
            "operation_type": operation_type.value,
            "target_service": target_service.value,
            "result": {
                "success": result.success,
                "service_user_id": result.service_user_id,
                "message": result.message,
                "details": result.details,
            },
            "user_data": user_data,
        }

        return await self._send_notification(operation_id, payload)

    async def notify_failure(
        self,
        operation_id: str,
        request_id: str | None,
        operation_type: OperationType,
        target_service: TargetService,
        error_message: str,
        retry_count: int,
        sent_to_dlq: bool = False,
    ) -> bool:
        """Send failure notification to n8n

        Args:
            operation_id: Internal operation ID
            request_id: MidPoint request ID
            operation_type: Type of operation
            target_service: Target service
            error_message: Error description
            retry_count: Number of retries attempted
            sent_to_dlq: Whether operation was sent to DLQ

        Returns:
            True if notification sent successfully

        Raises:
            NotificationError: If notification fails
        """
        status = OperationStatus.DLQ if sent_to_dlq else OperationStatus.FAILED

        payload = {
            "operation_id": operation_id,
            "request_id": request_id,
            "status": status.value,
            "operation_type": operation_type.value,
            "target_service": target_service.value,
            "error": {
                "message": error_message,
                "retry_count": retry_count,
                "sent_to_dlq": sent_to_dlq,
            },
        }

        return await self._send_notification(operation_id, payload)

    async def _send_notification(
        self,
        operation_id: str,
        payload: dict[str, Any],
    ) -> bool:
        """Internal method to send notification

        Args:
            operation_id: Operation ID for logging
            payload: Notification payload

        Returns:
            True if successful

        Raises:
            NotificationError: If notification fails after retries
        """
        logger.info(
            "Sending notification to n8n",
            extra={
                "operation_id": operation_id,
                "status": payload.get("status"),
            },
        )

        last_error: Exception | None = None

        for attempt in range(settings.N8N_RETRY_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._notification_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()

                    logger.info(
                        "Notification sent successfully",
                        extra={"operation_id": operation_id},
                    )
                    return True

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    f"Notification timeout, attempt {attempt + 1}/{settings.N8N_RETRY_ATTEMPTS}",
                    extra={"operation_id": operation_id},
                )
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    f"Notification failed with status {e.response.status_code}, "
                    f"attempt {attempt + 1}/{settings.N8N_RETRY_ATTEMPTS}",
                    extra={"operation_id": operation_id},
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Notification error: {e}, attempt {attempt + 1}/{settings.N8N_RETRY_ATTEMPTS}",
                    extra={"operation_id": operation_id},
                )

        # All retries exhausted
        logger.error(
            f"Notification failed after {settings.N8N_RETRY_ATTEMPTS} attempts",
            extra={"operation_id": operation_id},
        )
        raise NotificationError(
            operation_id=operation_id,
            error_message=str(last_error) if last_error else "Unknown error",
        )


# Global client instance
n8n_client = N8NClient()
