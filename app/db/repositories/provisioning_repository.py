"""Repository for ProvisioningOperation model"""
from datetime import datetime
from typing import Any

from prisma import Json
from prisma.models import ProvisioningOperation
from prisma.enums import OperationStatus, OperationType, TargetService

from app.db.repositories.base import BaseRepository


class ProvisioningRepository(BaseRepository[ProvisioningOperation]):
    """Repository for managing provisioning operations"""

    @property
    def model_name(self) -> str:
        return "ProvisioningOperation"

    async def create_operation(
        self,
        operation_type: OperationType,
        target_service: TargetService,
        user_data: dict[str, Any],
        original_message: dict[str, Any],
        midpoint_request_id: str | None = None,
        broker_message_id: str | None = None,
        max_retries: int = 3,
    ) -> ProvisioningOperation:
        """Create a new provisioning operation

        Args:
            operation_type: Type of operation (CREATE_USER, UPDATE_USER, etc.)
            target_service: Target service (MYSQL, POSTGRESQL, ODOO, LDAP)
            user_data: User data to provision
            original_message: Complete original message from MidPoint
            midpoint_request_id: Optional MidPoint request ID
            broker_message_id: Optional message broker ID
            max_retries: Maximum retry attempts

        Returns:
            Created ProvisioningOperation
        """
        return await self._db.provisioningoperation.create(
            data={
                "operation_type": operation_type,
                "target_service": target_service,
                "user_data": Json(user_data),
                "original_message": Json(original_message),
                "midpoint_request_id": midpoint_request_id,
                "broker_message_id": broker_message_id,
                "max_retries": max_retries,
                "status": OperationStatus.PENDING,
            }
        )

    async def get_by_id(
        self, id: str, include_audit_logs: bool = False
    ) -> ProvisioningOperation | None:
        """Get a provisioning operation by ID

        Args:
            id: The operation ID
            include_audit_logs: Whether to include related audit logs

        Returns:
            The operation if found, None otherwise
        """
        return await self._db.provisioningoperation.find_unique(
            where={"id": id},
            include={"audit_logs": include_audit_logs} if include_audit_logs else None,
        )

    async def update_status(
        self,
        id: str,
        status: OperationStatus,
        error_message: str | None = None,
        error_stacktrace: str | None = None,
        validation_response: dict[str, Any] | None = None,
        provisioning_result: dict[str, Any] | None = None,
    ) -> ProvisioningOperation | None:
        """Update the status of an operation

        Args:
            id: The operation ID
            status: New status
            error_message: Optional error message
            error_stacktrace: Optional error stacktrace
            validation_response: Optional validation response from n8n
            provisioning_result: Optional result from target service

        Returns:
            Updated operation if found, None otherwise
        """
        update_data: dict[str, Any] = {"status": status}

        if error_message is not None:
            update_data["error_message"] = error_message
        if error_stacktrace is not None:
            update_data["error_stacktrace"] = error_stacktrace
        if validation_response is not None:
            update_data["validation_response"] = Json(validation_response)
            update_data["validated_at"] = datetime.utcnow()
        if provisioning_result is not None:
            update_data["provisioning_result"] = Json(provisioning_result)
            update_data["processing_completed_at"] = datetime.utcnow()

        # Set processing timestamps based on status
        if status == OperationStatus.PROCESSING:
            update_data["processing_started_at"] = datetime.utcnow()
        elif status == OperationStatus.DLQ:
            update_data["sent_to_dlq_at"] = datetime.utcnow()

        return await self._db.provisioningoperation.update(
            where={"id": id},
            data=update_data,
        )

    async def get_operations_for_retry(
        self, limit: int = 100
    ) -> list[ProvisioningOperation]:
        """Get operations that are due for retry

        Returns operations with status RETRYING where next_retry_at
        is in the past and retry_count < max_retries.

        Args:
            limit: Maximum number of operations to return

        Returns:
            List of operations ready for retry
        """
        # Get operations due for retry
        operations = await self._db.provisioningoperation.find_many(
            where={
                "status": OperationStatus.RETRYING,
                "next_retry_at": {"lte": datetime.utcnow()},
            },
            order={"next_retry_at": "asc"},
            take=limit * 2,  # Fetch more to account for filtering
        )

        # Filter to only include operations where retry_count < max_retries
        return [op for op in operations if op.retry_count < op.max_retries][:limit]

    async def list_operations(
        self,
        status: OperationStatus | None = None,
        target_service: TargetService | None = None,
        operation_type: OperationType | None = None,
        skip: int = 0,
        take: int = 50,
        order_by: str = "created_at",
        order_direction: str = "desc",
    ) -> tuple[list[ProvisioningOperation], int]:
        """List operations with filters and pagination

        Args:
            status: Filter by status
            target_service: Filter by target service
            operation_type: Filter by operation type
            skip: Number of records to skip (pagination offset)
            take: Number of records to return (page size)
            order_by: Field to order by
            order_direction: 'asc' or 'desc'

        Returns:
            Tuple of (operations list, total count)
        """
        where: dict[str, Any] = {}

        if status is not None:
            where["status"] = status
        if target_service is not None:
            where["target_service"] = target_service
        if operation_type is not None:
            where["operation_type"] = operation_type

        operations = await self._db.provisioningoperation.find_many(
            where=where if where else None,
            skip=skip,
            take=take,
            order={order_by: order_direction},
        )

        total = await self._db.provisioningoperation.count(
            where=where if where else None
        )

        return operations, total

    async def reset_for_retry(
        self,
        id: str,
        next_retry_at: datetime,
    ) -> ProvisioningOperation | None:
        """Reset an operation for retry

        Increments retry count and sets next retry time.

        Args:
            id: The operation ID
            next_retry_at: When to retry next

        Returns:
            Updated operation if found, None otherwise
        """
        operation = await self.get_by_id(id)
        if operation is None:
            return None

        return await self._db.provisioningoperation.update(
            where={"id": id},
            data={
                "status": OperationStatus.RETRYING,
                "retry_count": operation.retry_count + 1,
                "next_retry_at": next_retry_at,
                "error_message": None,
                "error_stacktrace": None,
            },
        )

    async def mark_notification_sent(self, id: str) -> ProvisioningOperation | None:
        """Mark that notification was sent for an operation

        Args:
            id: The operation ID

        Returns:
            Updated operation if found, None otherwise
        """
        return await self._db.provisioningoperation.update(
            where={"id": id},
            data={
                "notification_sent": True,
                "notification_sent_at": datetime.utcnow(),
            },
        )

    async def get_by_midpoint_request_id(
        self, midpoint_request_id: str
    ) -> ProvisioningOperation | None:
        """Get an operation by MidPoint request ID

        Args:
            midpoint_request_id: The MidPoint request ID

        Returns:
            The operation if found, None otherwise
        """
        return await self._db.provisioningoperation.find_unique(
            where={"midpoint_request_id": midpoint_request_id}
        )

    async def get_by_broker_message_id(
        self, broker_message_id: str
    ) -> ProvisioningOperation | None:
        """Get an operation by broker message ID

        Args:
            broker_message_id: The message broker ID

        Returns:
            The operation if found, None otherwise
        """
        return await self._db.provisioningoperation.find_unique(
            where={"broker_message_id": broker_message_id}
        )
