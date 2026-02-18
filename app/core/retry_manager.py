"""Retry manager with exponential backoff"""
import logging
from datetime import datetime, timedelta

from prisma import Prisma

from app.config.settings import settings
from app.db.repositories.provisioning_repository import ProvisioningRepository
from app.services.audit_service import AuditService
from app.utils.enums import OperationStatus

logger = logging.getLogger(__name__)


class RetryManager:
    """Manages retry logic with exponential backoff

    Implements the retry strategy:
    - delay = INITIAL_DELAY × (BACKOFF_MULTIPLIER ^ attempt)
    - Capped at MAX_DELAY
    - After max_retries, sends to DLQ
    """

    def __init__(self, db: Prisma) -> None:
        """Initialize retry manager

        Args:
            db: Prisma database client
        """
        self._db = db
        self._repo = ProvisioningRepository(db)
        self._audit = AuditService(db)

    def calculate_next_retry(self, retry_count: int) -> tuple[datetime, int]:
        """Calculate the next retry time using exponential backoff

        Formula: delay = INITIAL_DELAY × (BACKOFF_MULTIPLIER ^ retry_count)

        Args:
            retry_count: Current retry attempt (0-indexed)

        Returns:
            Tuple of (next_retry_at datetime, delay_seconds)
        """
        delay = settings.RETRY_INITIAL_DELAY * (
            settings.RETRY_BACKOFF_MULTIPLIER ** retry_count
        )
        delay = min(delay, settings.RETRY_MAX_DELAY)
        delay_int = int(delay)

        next_retry_at = datetime.utcnow() + timedelta(seconds=delay_int)

        return next_retry_at, delay_int

    async def schedule_retry(
        self,
        operation_id: str,
        error_message: str | None = None,
    ) -> bool:
        """Schedule a retry for a failed operation

        Args:
            operation_id: The operation to retry
            error_message: Optional error message from the failure

        Returns:
            True if retry was scheduled, False if sent to DLQ
        """
        operation = await self._repo.get_by_id(operation_id)
        if not operation:
            logger.error(f"Operation not found for retry: {operation_id}")
            return False

        current_retry = operation.retry_count
        max_retries = operation.max_retries

        logger.info(
            f"Scheduling retry for operation {operation_id}",
            extra={
                "operation_id": operation_id,
                "current_retry": current_retry,
                "max_retries": max_retries,
            },
        )

        # Check if max retries exceeded
        if current_retry >= max_retries:
            logger.warning(
                f"Max retries exceeded for operation {operation_id}",
                extra={"operation_id": operation_id},
            )
            # Import here to avoid circular dependency
            from app.core.dead_letter_queue import DLQManager

            dlq_manager = DLQManager(self._db)
            await dlq_manager.send_to_dlq(
                operation_id=operation_id,
                error_type="RetryExhausted",
                error_message=error_message or "Max retries exceeded",
            )
            return False

        # Calculate next retry time
        next_retry_at, delay_seconds = self.calculate_next_retry(current_retry)

        # Update operation for retry
        await self._repo.reset_for_retry(
            id=operation_id,
            next_retry_at=next_retry_at,
        )

        await self._audit.log_retry_scheduled(
            operation_id=operation_id,
            retry_count=current_retry + 1,
            next_retry_at=next_retry_at,
        )

        logger.info(
            f"Retry scheduled for operation {operation_id}",
            extra={
                "operation_id": operation_id,
                "retry_count": current_retry + 1,
                "next_retry_at": next_retry_at.isoformat(),
                "delay_seconds": delay_seconds,
            },
        )

        return True

    async def get_pending_retries(self, limit: int = 100) -> list:
        """Get operations that are due for retry

        Args:
            limit: Maximum number of operations to return

        Returns:
            List of operations ready for retry
        """
        operations = await self._repo.get_operations_for_retry(limit=limit)

        logger.debug(
            f"Found {len(operations)} operations pending retry",
            extra={"count": len(operations)},
        )

        return operations

    async def process_pending_retries(self, orchestrator) -> int:
        """Process all pending retry operations

        This should be called periodically by a background task.

        Args:
            orchestrator: The ProvisioningOrchestrator instance

        Returns:
            Number of operations processed
        """
        operations = await self.get_pending_retries()
        processed = 0

        for operation in operations:
            try:
                logger.info(
                    f"Processing retry for operation {operation.id}",
                    extra={
                        "operation_id": operation.id,
                        "retry_count": operation.retry_count,
                    },
                )

                # Update status to indicate retry is in progress
                await self._repo.update_status(
                    id=operation.id,
                    status=OperationStatus.PENDING,
                )

                await self._audit.log_status_change(
                    operation_id=operation.id,
                    old_status=OperationStatus.RETRYING,
                    new_status=OperationStatus.PENDING,
                    message=f"Retry attempt #{operation.retry_count}",
                )

                # Trigger orchestrator to reprocess
                await orchestrator.retry_operation(operation.id)
                processed += 1

            except Exception as e:
                logger.error(
                    f"Failed to process retry for operation {operation.id}: {e}",
                    extra={"operation_id": operation.id},
                )
                # Schedule another retry
                await self.schedule_retry(
                    operation_id=operation.id,
                    error_message=str(e),
                )

        logger.info(
            f"Processed {processed} retry operations",
            extra={"processed": processed, "total": len(operations)},
        )

        return processed
