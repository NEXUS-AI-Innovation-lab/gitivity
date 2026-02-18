"""Repository for AuditLog model"""
from typing import Any

from prisma import Json
from prisma.models import AuditLog
from prisma.enums import OperationStatus

from app.db.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    """Repository for managing audit logs"""

    @property
    def model_name(self) -> str:
        return "AuditLog"

    async def create_log(
        self,
        operation_id: str,
        event_type: str,
        old_status: OperationStatus | None = None,
        new_status: OperationStatus | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> AuditLog:
        """Create a new audit log entry

        Args:
            operation_id: Related provisioning operation ID
            event_type: Type of event (e.g., 'status_change', 'validation_sent')
            old_status: Previous status (for status changes)
            new_status: New status (for status changes)
            message: Human-readable message
            metadata: Additional structured data
            actor_type: Type of actor ('system', 'user', 'retry_manager', 'api')
            actor_id: Identifier of the actor

        Returns:
            Created AuditLog
        """
        data: dict[str, Any] = {
            "operation": {"connect": {"id": operation_id}},
            "event_type": event_type,
            "old_status": old_status,
            "new_status": new_status,
            "message": message,
            "actor_type": actor_type,
            "actor_id": actor_id,
        }

        # Handle metadata JSON field
        if metadata is not None:
            data["metadata"] = Json(metadata)

        return await self._db.auditlog.create(data=data)

    async def get_by_id(self, id: str) -> AuditLog | None:
        """Get an audit log by ID

        Args:
            id: The audit log ID

        Returns:
            The audit log if found, None otherwise
        """
        return await self._db.auditlog.find_unique(where={"id": id})

    async def get_logs_for_operation(
        self,
        operation_id: str,
        event_type: str | None = None,
        skip: int = 0,
        take: int = 100,
    ) -> list[AuditLog]:
        """Get all audit logs for a specific operation

        Args:
            operation_id: The provisioning operation ID
            event_type: Optional filter by event type
            skip: Number of records to skip
            take: Number of records to return

        Returns:
            List of audit logs ordered by creation time (newest first)
        """
        where: dict[str, Any] = {"operation_id": operation_id}

        if event_type is not None:
            where["event_type"] = event_type

        return await self._db.auditlog.find_many(
            where=where,
            skip=skip,
            take=take,
            order={"created_at": "desc"},
        )

    async def log_status_change(
        self,
        operation_id: str,
        old_status: OperationStatus,
        new_status: OperationStatus,
        message: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
    ) -> AuditLog:
        """Convenience method to log a status change

        Args:
            operation_id: The provisioning operation ID
            old_status: Previous status
            new_status: New status
            message: Optional message
            actor_type: Type of actor
            actor_id: Actor identifier

        Returns:
            Created AuditLog
        """
        return await self.create_log(
            operation_id=operation_id,
            event_type="status_change",
            old_status=old_status,
            new_status=new_status,
            message=message or f"Status changed from {old_status} to {new_status}",
            actor_type=actor_type,
            actor_id=actor_id,
        )

    async def log_validation_sent(
        self,
        operation_id: str,
        validation_request_id: str,
        webhook_url: str,
    ) -> AuditLog:
        """Log that validation request was sent to n8n

        Args:
            operation_id: The provisioning operation ID
            validation_request_id: ID of the validation request
            webhook_url: n8n webhook URL used

        Returns:
            Created AuditLog
        """
        return await self.create_log(
            operation_id=operation_id,
            event_type="validation_sent",
            message="Validation request sent to n8n",
            metadata={
                "validation_request_id": validation_request_id,
                "webhook_url": webhook_url,
            },
            actor_type="system",
        )

    async def log_provisioning_started(
        self,
        operation_id: str,
        target_service: str,
    ) -> AuditLog:
        """Log that provisioning started on target service

        Args:
            operation_id: The provisioning operation ID
            target_service: Name of the target service

        Returns:
            Created AuditLog
        """
        return await self.create_log(
            operation_id=operation_id,
            event_type="provisioning_started",
            message=f"Provisioning started on {target_service}",
            metadata={"target_service": target_service},
            actor_type="system",
        )

    async def log_error(
        self,
        operation_id: str,
        error_type: str,
        error_message: str,
        error_stacktrace: str | None = None,
    ) -> AuditLog:
        """Log an error that occurred during processing

        Args:
            operation_id: The provisioning operation ID
            error_type: Type/class of the error
            error_message: Error message
            error_stacktrace: Optional stacktrace

        Returns:
            Created AuditLog
        """
        return await self.create_log(
            operation_id=operation_id,
            event_type="error",
            message=error_message,
            metadata={
                "error_type": error_type,
                "error_stacktrace": error_stacktrace,
            },
            actor_type="system",
        )

    async def log_retry_scheduled(
        self,
        operation_id: str,
        retry_count: int,
        next_retry_at: str,
    ) -> AuditLog:
        """Log that a retry has been scheduled

        Args:
            operation_id: The provisioning operation ID
            retry_count: Current retry count
            next_retry_at: Timestamp of next retry

        Returns:
            Created AuditLog
        """
        return await self.create_log(
            operation_id=operation_id,
            event_type="retry_scheduled",
            message=f"Retry #{retry_count} scheduled for {next_retry_at}",
            metadata={
                "retry_count": retry_count,
                "next_retry_at": next_retry_at,
            },
            actor_type="retry_manager",
        )

    async def log_sent_to_dlq(
        self,
        operation_id: str,
        reason: str,
        retry_count: int,
    ) -> AuditLog:
        """Log that operation was sent to Dead Letter Queue

        Args:
            operation_id: The provisioning operation ID
            reason: Reason for sending to DLQ
            retry_count: Number of retries attempted

        Returns:
            Created AuditLog
        """
        return await self.create_log(
            operation_id=operation_id,
            event_type="sent_to_dlq",
            message=f"Operation sent to DLQ: {reason}",
            metadata={
                "reason": reason,
                "retry_count": retry_count,
            },
            actor_type="retry_manager",
        )
