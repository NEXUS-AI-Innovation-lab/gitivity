"""Dead Letter Queue manager for failed operations"""
import logging
from datetime import datetime
from typing import Any

from prisma import Prisma

from app.db.repositories.provisioning_repository import ProvisioningRepository
from app.services.audit_service import AuditService
from app.utils.enums import OperationStatus

logger = logging.getLogger(__name__)


class DLQManager:
    """Manages the Dead Letter Queue for failed operations

    Operations are sent to DLQ when:
    - Max retry attempts exceeded
    - Non-retriable error occurred
    - Manual intervention required
    """

    def __init__(self, db: Prisma) -> None:
        self._db = db
        self._repo = ProvisioningRepository(db)
        self._audit = AuditService(db)

    async def send_to_dlq(
        self,
        operation_id: str,
        error_type: str,
        error_message: str,
        error_stacktrace: str | None = None,
    ) -> None:
        """Send a failed operation to the Dead Letter Queue

        Args:
            operation_id: The operation ID
            error_type: Type/class of the error
            error_message: Error description
            error_stacktrace: Optional stack trace
        """
        operation = await self._repo.get_by_id(operation_id)
        if not operation:
            logger.error(f"Operation not found for DLQ: {operation_id}")
            return

        logger.warning(
            f"Sending operation {operation_id} to DLQ",
            extra={
                "operation_id": operation_id,
                "error_type": error_type,
                "retry_count": operation.retry_count,
            },
        )

        # Update operation status
        await self._repo.update_status(
            id=operation_id,
            status=OperationStatus.DLQ,
            error_message=error_message,
            error_stacktrace=error_stacktrace,
        )

        # Create DLQ entry
        await self._db.deadletterqueue.create(
            data={
                "operation_id": operation_id,
                "original_message": operation.original_message,
                "broker_message_id": operation.broker_message_id,
                "error_type": error_type,
                "error_message": error_message,
                "error_stacktrace": error_stacktrace,
                "retry_count": operation.retry_count,
            }
        )

        # Log audit event
        await self._audit.log_sent_to_dlq(
            operation_id=operation_id,
            reason=error_message,
            retry_count=operation.retry_count,
        )

        await self._audit.log_status_change(
            operation_id=operation_id,
            old_status=operation.status,
            new_status=OperationStatus.DLQ,
            message=f"Sent to DLQ: {error_message}",
        )

        logger.info(
            f"Operation {operation_id} sent to DLQ",
            extra={"operation_id": operation_id},
        )

    async def list_dlq_messages(
        self,
        resolved: bool | None = None,
        skip: int = 0,
        take: int = 50,
    ) -> tuple[list, int]:
        """List messages in the Dead Letter Queue

        Args:
            resolved: Filter by resolved status (None for all)
            skip: Number of records to skip
            take: Number of records to return

        Returns:
            Tuple of (messages list, total count)
        """
        where: dict[str, Any] = {}
        if resolved is not None:
            where["resolved"] = resolved

        messages = await self._db.deadletterqueue.find_many(
            where=where if where else None,
            skip=skip,
            take=take,
            order={"created_at": "desc"},
        )

        total = await self._db.deadletterqueue.count(
            where=where if where else None
        )

        return messages, total

    async def get_dlq_message(self, dlq_id: str):
        """Get a specific DLQ message by ID

        Args:
            dlq_id: The DLQ entry ID

        Returns:
            The DLQ entry if found, None otherwise
        """
        return await self._db.deadletterqueue.find_unique(
            where={"id": dlq_id}
        )

    async def retry_from_dlq(
        self,
        dlq_id: str,
        orchestrator,
        actor_id: str | None = None,
    ) -> bool:
        """Retry an operation from the DLQ

        Args:
            dlq_id: The DLQ entry ID
            orchestrator: The ProvisioningOrchestrator instance
            actor_id: Optional ID of user triggering retry

        Returns:
            True if retry was triggered successfully
        """
        dlq_entry = await self.get_dlq_message(dlq_id)
        if not dlq_entry:
            logger.error(f"DLQ entry not found: {dlq_id}")
            return False

        if not dlq_entry.operation_id:
            logger.error(f"DLQ entry has no operation_id: {dlq_id}")
            return False

        operation_id = dlq_entry.operation_id

        logger.info(
            f"Retrying operation {operation_id} from DLQ",
            extra={
                "dlq_id": dlq_id,
                "operation_id": operation_id,
            },
        )

        # Reset operation for retry
        operation = await self._repo.get_by_id(operation_id)
        if not operation:
            logger.error(f"Operation not found for DLQ retry: {operation_id}")
            return False

        # Reset status and retry count
        await self._db.provisioningoperation.update(
            where={"id": operation_id},
            data={
                "status": OperationStatus.PENDING,
                "retry_count": 0,
                "error_message": None,
                "error_stacktrace": None,
                "sent_to_dlq_at": None,
            },
        )

        # Log manual retry
        await self._audit.log_manual_retry(
            operation_id=operation_id,
            actor_id=actor_id,
        )

        await self._audit.log_status_change(
            operation_id=operation_id,
            old_status=OperationStatus.DLQ,
            new_status=OperationStatus.PENDING,
            message="Manual retry from DLQ",
        )

        # Mark DLQ entry as resolved
        await self._db.deadletterqueue.update(
            where={"id": dlq_id},
            data={
                "resolved": True,
                "resolved_at": datetime.utcnow(),
                "resolution_notes": "Retried manually",
                "resolved_by": actor_id,
            },
        )

        # Trigger retry
        try:
            await orchestrator.retry_operation(operation_id)
            return True
        except Exception as e:
            logger.error(
                f"Failed to retry operation from DLQ: {e}",
                extra={"operation_id": operation_id},
            )
            return False

    async def resolve_dlq_message(
        self,
        dlq_id: str,
        resolution_notes: str,
        resolved_by: str | None = None,
    ) -> bool:
        """Mark a DLQ message as resolved without retrying

        Args:
            dlq_id: The DLQ entry ID
            resolution_notes: Notes about the resolution
            resolved_by: ID of user resolving

        Returns:
            True if successfully resolved
        """
        dlq_entry = await self.get_dlq_message(dlq_id)
        if not dlq_entry:
            return False

        await self._db.deadletterqueue.update(
            where={"id": dlq_id},
            data={
                "resolved": True,
                "resolved_at": datetime.utcnow(),
                "resolution_notes": resolution_notes,
                "resolved_by": resolved_by,
            },
        )

        logger.info(
            f"DLQ message {dlq_id} resolved",
            extra={
                "dlq_id": dlq_id,
                "resolved_by": resolved_by,
            },
        )

        return True
