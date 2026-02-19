"""Audit service for logging operation events"""
import logging
from datetime import datetime
from typing import Any

from prisma import Prisma

from app.db.repositories.audit_repository import AuditRepository
from app.utils.enums import OperationStatus, ActorType

logger = logging.getLogger(__name__)


class AuditService:
    """Service for managing audit logs

    Provides high-level methods for logging various events
    during provisioning operations.
    """

    def __init__(self, db: Prisma) -> None:
        """Initialize audit service

        Args:
            db: Prisma client instance
        """
        self._repo = AuditRepository(db)

    async def log_operation_created(
        self,
        operation_id: str,
        midpoint_request_id: str | None = None,
        target_service: str | None = None,
    ) -> None:
        """Log that a new operation was created"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="operation_created",
            new_status=OperationStatus.PENDING,
            message="Operation created from broker message",
            metadata={
                "midpoint_request_id": midpoint_request_id,
                "target_service": target_service,
            },
            actor_type=ActorType.SYSTEM.value,
        )
        logger.debug(f"Logged operation_created for {operation_id}")

    async def log_status_change(
        self,
        operation_id: str,
        old_status: OperationStatus,
        new_status: OperationStatus,
        message: str | None = None,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_id: str | None = None,
    ) -> None:
        """Log a status change event"""
        await self._repo.log_status_change(
            operation_id=operation_id,
            old_status=old_status,
            new_status=new_status,
            message=message,
            actor_type=actor_type.value,
            actor_id=actor_id,
        )
        logger.debug(f"Logged status change for {operation_id}: {old_status} -> {new_status}")

    async def log_validation_sent(
        self,
        operation_id: str,
        validation_request_id: str | None = None,
    ) -> None:
        """Log that validation request was sent to n8n"""
        await self._repo.log_validation_sent(
            operation_id=operation_id,
            validation_request_id=validation_request_id or "",
            webhook_url="[configured]",
        )
        logger.debug(f"Logged validation_sent for {operation_id}")

    async def log_validation_response(
        self,
        operation_id: str,
        approved: bool,
        reason: str | None = None,
        validation_id: str | None = None,
    ) -> None:
        """Log validation response from n8n"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="validation_response",
            message=f"Validation {'approved' if approved else 'rejected'}: {reason or 'N/A'}",
            metadata={
                "approved": approved,
                "reason": reason,
                "validation_id": validation_id,
            },
            actor_type=ActorType.SYSTEM.value,
        )
        logger.debug(f"Logged validation_response for {operation_id}: approved={approved}")

    async def log_approval_requested(
        self,
        operation_id: str,
        approval_request_id: str,
        worker_url: str,
    ) -> None:
        """Log that approval was requested from Flask worker"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="approval_requested",
            message=f"Approval requested from worker (request_id: {approval_request_id})",
            metadata={
                "approval_request_id": approval_request_id,
                "worker_url": worker_url,
            },
            actor_type=ActorType.SYSTEM.value,
        )
        logger.debug(f"Logged approval_requested for {operation_id}")

    async def log_approval_response(
        self,
        operation_id: str,
        approved: bool,
        reason: str,
        worker_id: str | None = None,
    ) -> None:
        """Log approval decision from Flask worker"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="approval_response",
            message=f"Approval {'granted' if approved else 'rejected'} by {worker_id or 'worker'}: {reason}",
            metadata={
                "approved": approved,
                "reason": reason,
                "worker_id": worker_id,
            },
            actor_type=ActorType.SYSTEM.value,
            actor_id=worker_id,
        )
        logger.debug(f"Logged approval_response for {operation_id}: approved={approved}")

    async def log_provisioning_started(
        self,
        operation_id: str,
        target_service: str,
    ) -> None:
        """Log that provisioning started"""
        await self._repo.log_provisioning_started(
            operation_id=operation_id,
            target_service=target_service,
        )
        logger.debug(f"Logged provisioning_started for {operation_id}")

    async def log_provisioning_completed(
        self,
        operation_id: str,
        target_service: str,
        service_user_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log that provisioning completed successfully"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="provisioning_completed",
            message=f"Provisioning completed on {target_service}",
            metadata={
                "target_service": target_service,
                "service_user_id": service_user_id,
                "details": details,
            },
            actor_type=ActorType.SYSTEM.value,
        )
        logger.debug(f"Logged provisioning_completed for {operation_id}")

    async def log_error(
        self,
        operation_id: str,
        error_type: str,
        error_message: str,
        error_stacktrace: str | None = None,
    ) -> None:
        """Log an error event"""
        await self._repo.log_error(
            operation_id=operation_id,
            error_type=error_type,
            error_message=error_message,
            error_stacktrace=error_stacktrace,
        )
        logger.debug(f"Logged error for {operation_id}: {error_type}")

    async def log_retry_scheduled(
        self,
        operation_id: str,
        retry_count: int,
        next_retry_at: datetime,
    ) -> None:
        """Log that a retry has been scheduled"""
        await self._repo.log_retry_scheduled(
            operation_id=operation_id,
            retry_count=retry_count,
            next_retry_at=next_retry_at.isoformat(),
        )
        logger.debug(f"Logged retry_scheduled for {operation_id}: retry #{retry_count}")

    async def log_sent_to_dlq(
        self,
        operation_id: str,
        reason: str,
        retry_count: int,
    ) -> None:
        """Log that operation was sent to DLQ"""
        await self._repo.log_sent_to_dlq(
            operation_id=operation_id,
            reason=reason,
            retry_count=retry_count,
        )
        logger.debug(f"Logged sent_to_dlq for {operation_id}")

    async def log_notification_sent(
        self,
        operation_id: str,
        notification_type: str,
        success: bool,
    ) -> None:
        """Log that notification was sent to n8n"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="notification_sent",
            message=f"{notification_type} notification {'sent' if success else 'failed'}",
            metadata={
                "notification_type": notification_type,
                "success": success,
            },
            actor_type=ActorType.SYSTEM.value,
        )
        logger.debug(f"Logged notification_sent for {operation_id}")

    async def log_manual_retry(
        self,
        operation_id: str,
        actor_id: str | None = None,
    ) -> None:
        """Log that a manual retry was triggered"""
        await self._repo.create_log(
            operation_id=operation_id,
            event_type="manual_retry",
            message="Manual retry triggered via API",
            actor_type=ActorType.API.value,
            actor_id=actor_id,
        )
        logger.debug(f"Logged manual_retry for {operation_id}")
