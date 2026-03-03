"""Main orchestrator for provisioning operations"""
import asyncio
import logging
import traceback
import uuid
from datetime import datetime, timezone

import httpx
from prisma import Prisma

from app.config.settings import settings
from app.core.connectors.factory import ConnectorFactory
from app.db.redis_client import RedisClient
from app.db.repositories.approval_redis_repository import ApprovalRedisRepository
from app.db.repositories.provisioning_repository import ProvisioningRepository
from app.models.domain import MidPointMessage, ProvisioningResult
from app.services.audit_service import AuditService
from app.utils.enums import OperationStatus, OperationType, TargetService
from app.utils.exceptions import ProvisioningError

# NOTE: n8n est utilisé UNIQUEMENT pour l'approbation (approval-workflow).
# Les workflows validation et notification ont été supprimés.

logger = logging.getLogger(__name__)


class ProvisioningOrchestrator:
    """Orchestrates the complete provisioning flow

    Coordinates validation, provisioning, and notifications for
    user provisioning operations received from MidPoint.
    """

    def __init__(self, db: Prisma, n8n_client=None) -> None:
        self._db = db
        self._repo = ProvisioningRepository(db)
        self._audit = AuditService(db)
        self._approval_redis_repo: ApprovalRedisRepository | None = None

    async def _get_approval_repo(self) -> ApprovalRedisRepository:
        """Get or create approval Redis repository (lazy initialization)"""
        if self._approval_redis_repo is None:
            redis_client = await RedisClient.get_client()
            self._approval_redis_repo = ApprovalRedisRepository(redis_client)
        return self._approval_redis_repo

    async def process_message(self, message: MidPointMessage) -> str:
        """Process a provisioning message from MidPoint

        This is the main entry point for processing incoming messages.
        It orchestrates the full flow:
        1. Create operation record (PENDING)
        2. Validate with n8n (VALIDATING -> VALIDATED)
        3. Request approval (APPROVAL_PENDING) - NEW
        4. Provision to target (PROCESSING -> SUCCESS/FAILED)
        5. Send notification

        Args:
            message: The MidPoint message to process

        Returns:
            The operation ID

        Raises:
            Various exceptions that should be handled by the caller
            for retry logic
        """
        operation_id: str | None = None

        try:
            # Step 1: Create operation record
            operation = await self._create_operation(message)
            operation_id = operation.id

            logger.info(
                f"Processing operation {operation_id}",
                extra={
                    "operation_id": operation_id,
                    "operation_type": message.operation_type.value,
                    "target_service": message.target_service.value,
                },
            )

            # If a previous CREATE was rejected, the user doesn't exist on the target service.
            # DELETE is a no-op; UPDATE must be converted to CREATE to re-attempt provisioning.
            if message.operation_type in (OperationType.UPDATE_USER, OperationType.DELETE_USER):
                approval_repo = await self._get_approval_repo()
                username = message.user_data.username
                target_svc = message.target_service.value

                if await approval_repo.check_rejected_create(username, target_svc):
                    if message.operation_type == OperationType.DELETE_USER:
                        logger.info(
                            f"Skipping DELETE for {username} on {target_svc}: "
                            f"CREATE was previously rejected, user was never provisioned"
                        )
                        await self._repo.update_status(
                            id=operation_id,
                            status=OperationStatus.SUCCESS,
                            error_message="Skipped: user was never provisioned (CREATE was rejected)",
                        )
                        await approval_repo.clear_rejected_create(username, target_svc)
                        return operation_id

                    elif message.operation_type == OperationType.UPDATE_USER:
                        logger.info(
                            f"Converting UPDATE to CREATE for {username} on {target_svc}: "
                            f"previous CREATE was rejected"
                        )
                        message.operation_type = OperationType.CREATE_USER

            # Check if UPDATE has actual changes for this service
            if message.operation_type == OperationType.UPDATE_USER:
                try:
                    approval_repo = await self._get_approval_repo()
                    username = message.user_data.username
                    target_svc = message.target_service.value
                    old_state = await approval_repo.get_user_state(username, target_svc)
                    if old_state:
                        new_state = message.user_data.model_dump(mode="json")
                        diff = self._compute_user_diff(old_state, new_state)
                        if not diff:
                            logger.info(
                                f"No changes detected for {username} on {target_svc} - skipping"
                            )
                            await self._repo.update_status(
                                id=operation_id,
                                status=OperationStatus.SUCCESS,
                                error_message="Skipped: no changes for this service",
                            )
                            return operation_id
                except Exception as e:
                    logger.warning(f"Failed to check for changes, proceeding: {e}")

            # Step 2: Mark as VALIDATED directly (no n8n validation workflow)
            await self._mark_validated(operation_id)

            # Step 3: Request approval via n8n approval workflow
            if settings.APPROVAL_ENABLED:
                await self._request_approval(operation_id, message)
                logger.info(
                    f"Operation {operation_id} sent for approval. Waiting for callback.",
                    extra={"operation_id": operation_id},
                )
                return operation_id

            # Step 4: Provision to target service (if approval bypassed)
            await self._provision_to_target(operation_id, message)

            logger.info(
                f"Operation {operation_id} completed successfully",
                extra={"operation_id": operation_id},
            )

            return operation_id

        except ProvisioningError as e:
            # Provisioning failed
            if operation_id:
                await self._handle_error(
                    operation_id,
                    message,
                    e,
                    is_retriable=e.is_retriable,
                )
            raise

        except Exception as e:
            # Unexpected error
            logger.exception(f"Unexpected error processing message: {e}")
            if operation_id:
                await self._handle_error(
                    operation_id,
                    message,
                    e,
                    is_retriable=True,
                )
            raise

    async def _create_operation(self, message: MidPointMessage):
        """Create a new operation record in PENDING state"""
        operation = await self._repo.create_operation(
            operation_type=message.operation_type,
            target_service=message.target_service,
            user_data=message.user_data.model_dump(),
            original_message=message.model_dump(mode="json"),
            midpoint_request_id=message.request_id,
            max_retries=settings.RETRY_MAX_ATTEMPTS,
        )

        await self._audit.log_operation_created(
            operation_id=operation.id,
            midpoint_request_id=message.request_id,
            target_service=message.target_service.value,
        )

        return operation

    async def _mark_validated(self, operation_id: str) -> None:
        """Mark operation as VALIDATED directly (no validation workflow)"""
        await self._repo.update_status(
            id=operation_id,
            status=OperationStatus.VALIDATED,
        )
        await self._audit.log_status_change(
            operation_id=operation_id,
            old_status=OperationStatus.PENDING,
            new_status=OperationStatus.VALIDATED,
            message="Auto-validated (no validation workflow)",
        )

    async def _request_approval(
        self,
        operation_id: str,
        message: MidPointMessage,
    ) -> None:
        """Request approval via n8n and store state in Redis.

        Implements a short grouping window so that multiple services being
        provisioned for the same user at the same time result in a single
        approval email instead of one per service.

        The first operation for a given (username, operation_type) becomes the
        group leader.  It waits APPROVAL_GROUP_WINDOW_SECONDS for sibling
        operations to register, then sends ONE n8n request covering all services.
        Sibling operations simply store their data in Redis and return — they will
        be processed when the leader's callback arrives.
        """
        try:
            # Update status to APPROVAL_PENDING
            await self._update_status(
                operation_id,
                OperationStatus.VALIDATED,
                OperationStatus.APPROVAL_PENDING,
            )

            # Store request timestamp
            await self._db.provisioningoperation.update(
                where={"id": operation_id},
                data={"approval_requested_at": datetime.now(timezone.utc)},
            )

            # Get approval repository
            approval_repo = await self._get_approval_repo()

            # Store the full MidPoint message in Redis so it can be reconstructed after the
            # callback arrives (which may be hours later, after the API process restarts).
            await approval_repo.add_pending_approval(
                operation_id,
                {
                    "target_service": message.target_service.value,
                    "operation_type": message.operation_type.value,
                    "user_data": message.user_data.model_dump(mode="json"),
                    "midpoint_message": message.model_dump(mode="json"),
                },
            )

            # --- Grouping logic ---
            username = message.user_data.username
            operation_type = message.operation_type.value

            group_result = await approval_repo.create_or_join_approval_group(
                username=username,
                operation_type=operation_type,
                operation_id=operation_id,
                target_service=message.target_service.value,
            )

            if not group_result["group_leader"]:
                # This operation is a group member — the leader will send the email.
                logger.info(
                    f"Operation {operation_id} ({message.target_service.value}) joined "
                    f"approval group led by {group_result['leader_operation_id']} "
                    f"for user '{username}'. Waiting for leader callback.",
                    extra={"operation_id": operation_id},
                )
                return

            # This operation is the group leader.
            # Wait for sibling messages to arrive before sending the email.
            window = settings.APPROVAL_GROUP_WINDOW_SECONDS
            logger.info(
                f"Operation {operation_id} is group leader for '{username}/{operation_type}'. "
                f"Waiting {window}s for sibling operations to join.",
                extra={"operation_id": operation_id},
            )
            await asyncio.sleep(window)

            # Collect the final group membership
            group = await approval_repo.finalize_approval_group(username, operation_type)
            members = group.get("members", [{"operation_id": operation_id, "target_service": message.target_service.value}])

            grouped_services = [m["target_service"] for m in members]
            grouped_operation_ids = [m["operation_id"] for m in members]

            logger.info(
                f"Sending grouped approval for '{username}': services={grouped_services}",
                extra={"operation_id": operation_id},
            )

            # Generate approval request ID
            approval_request_id = str(uuid.uuid4())

            # Send ONE approval request to n8n covering all grouped services
            await self._send_approval_request(
                operation_id=operation_id,
                request_id=approval_request_id,
                operation_data={
                    "operation_type": operation_type,
                    "target_service": message.target_service.value,
                    "user_data": message.user_data.model_dump(mode="json"),
                    "grouped_services": grouped_services,
                    "grouped_operation_ids": grouped_operation_ids,
                },
            )

            # Store request ID in database for the leader
            await self._db.provisioningoperation.update(
                where={"id": operation_id},
                data={"approval_request_id": approval_request_id},
            )

            # Log audit trail
            await self._audit.log_approval_requested(
                operation_id=operation_id,
                approval_request_id=approval_request_id,
                worker_url=settings.APPROVAL_WORKER_URL,
            )

            logger.info(
                f"Grouped approval requested for operation {operation_id} "
                f"(leader, {len(members)} service(s)).",
                extra={
                    "operation_id": operation_id,
                    "approval_request_id": approval_request_id,
                    "grouped_services": grouped_services,
                },
            )

        except Exception as e:
            logger.error(f"Failed to request approval: {e}")
            await self._repo.update_status(
                id=operation_id,
                status=OperationStatus.FAILED,
                error_message=f"Approval request failed: {str(e)}",
            )
            await self._audit.log_error(
                operation_id=operation_id,
                error_type=type(e).__name__,
                error_message=f"Approval request failed: {str(e)}",
                error_stacktrace=traceback.format_exc(),
            )
            raise

    def _load_approvers(self) -> list[dict]:
        """Load approvers from JSON file, sorted by level"""
        import json
        from pathlib import Path

        data_file = Path(__file__).resolve().parents[2] / "data" / "approvers.json"
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            approvers = sorted(data.get("approvers", []), key=lambda a: a.get("level", 0))
            logger.info(f"Loaded {len(approvers)} approvers for approval chain")
            return approvers
        except Exception as e:
            logger.error(f"Failed to load approvers: {e}")
            return []

    def _compute_user_diff(self, old_data: dict, new_data: dict) -> list[dict]:
        """Compute diff between old and new user data

        Returns a list of {field, old_value, new_value} dicts for changed fields.
        """
        changes = []
        fields_to_check = [
            ("email", "Email"),
            ("first_name", "Prenom"),
            ("last_name", "Nom"),
            ("password", "Mot de passe"),
        ]

        for field_key, field_label in fields_to_check:
            old_val = old_data.get(field_key) or ""
            new_val = new_data.get(field_key) or ""
            if field_key == "password":
                # Don't compare passwords, just detect if set
                if new_val and new_val != old_val:
                    changes.append({"field": field_label, "old": "(ancien)", "new": "(modifie)"})
            elif str(old_val) != str(new_val) and new_val:
                changes.append({"field": field_label, "old": str(old_val), "new": str(new_val)})

        # Compare roles
        old_roles = set(old_data.get("roles") or [])
        new_roles = set(new_data.get("roles") or [])
        if old_roles != new_roles:
            added = new_roles - old_roles
            removed = old_roles - new_roles
            role_parts = []
            if added:
                role_parts.append("Ajoutes: " + ", ".join(sorted(added)))
            if removed:
                role_parts.append("Retires: " + ", ".join(sorted(removed)))
            changes.append({
                "field": "Roles",
                "old": ", ".join(sorted(old_roles)) or "Aucun",
                "new": ", ".join(sorted(new_roles)) or "Aucun",
                "details": " | ".join(role_parts),
            })

        # Compare attributes
        old_attrs = old_data.get("attributes") or {}
        new_attrs = new_data.get("attributes") or {}
        for attr_key in set(list(old_attrs.keys()) + list(new_attrs.keys())):
            old_val = str(old_attrs.get(attr_key, ""))
            new_val = str(new_attrs.get(attr_key, ""))
            if old_val != new_val and new_val:
                changes.append({"field": attr_key, "old": old_val, "new": new_val})

        return changes

    async def _send_approval_request(
        self,
        operation_id: str,
        request_id: str,
        operation_data: dict,
    ) -> dict:
        """Send approval request to n8n workflow via HTTP POST"""
        # Load approvers list sorted by level
        approvers = self._load_approvers()

        payload = {
            "operation_id": operation_id,
            "request_id": request_id,
            "callback_url": f"{settings.GATEWAY_EXTERNAL_URL}/api/v1/provisioning/{operation_id}/approve-callback",
            "admin_email": settings.ADMIN_APPROVAL_EMAIL,
            "operation_type": operation_data["operation_type"],
            "target_service": operation_data["target_service"],
            "user_data": operation_data["user_data"],
            "approvers": [
                {"email": a["email"], "name": a["name"], "level": a["level"]}
                for a in approvers
            ],
        }

        # For UPDATE, compute diff with previous state
        if operation_data["operation_type"] == "UPDATE_USER":
            try:
                approval_repo = await self._get_approval_repo()
                username = operation_data["user_data"].get("username", "")
                target_svc = operation_data["target_service"]
                old_state = await approval_repo.get_user_state(username, target_svc)
                if old_state:
                    diff = self._compute_user_diff(old_state, operation_data["user_data"])
                    payload["changes"] = diff
                    logger.info(f"Computed {len(diff)} changes for UPDATE {username} on {target_svc}")
                else:
                    logger.info(f"No previous state for {username} on {target_svc}, cannot compute diff")
            except Exception as e:
                logger.warning(f"Failed to compute diff: {e}")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.N8N_APPROVAL_WEBHOOK_URL,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def process_approval_response(
        self,
        operation_id: str,
        approved: bool,
        reason: str,
        worker_id: str,
    ) -> None:
        """Process approval decision from Flask worker callback

        This method is called by the API endpoint when the Flask worker
        sends the approval decision after sleeping.

        Args:
            operation_id: The operation being approved/rejected
            approved: Whether the operation was approved
            reason: Reason for approval/rejection
            worker_id: ID of the worker that made the decision
        """
        try:
            # Retrieve operation
            operation = await self._repo.get_by_id(operation_id)
            if not operation:
                raise ValueError(f"Operation {operation_id} not found")

            # Validate current status
            if operation.status != OperationStatus.APPROVAL_PENDING:
                raise ValueError(
                    f"Operation {operation_id} has status {operation.status}, "
                    f"expected APPROVAL_PENDING"
                )

            # Store approval response in database
            from prisma import Json
            await self._db.provisioningoperation.update(
                where={"id": operation_id},
                data={
                    "approval_response": Json({
                        "approved": approved,
                        "reason": reason,
                        "worker_id": worker_id,
                        "decided_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    "approved_at": datetime.now(timezone.utc),
                    "approved_by": worker_id,
                    "approval_reason": reason,
                },
            )

            # Get approval repository
            approval_repo = await self._get_approval_repo()

            if approved:
                # APPROVED: Continue to provisioning
                logger.info(
                    f"Operation {operation_id} APPROVED by {worker_id}: {reason}",
                    extra={"operation_id": operation_id, "worker_id": worker_id},
                )

                # Log audit trail
                await self._audit.log_approval_response(
                    operation_id=operation_id,
                    approved=True,
                    reason=reason,
                    worker_id=worker_id,
                )

                # Update status to PROCESSING
                await self._update_status(
                    operation_id,
                    OperationStatus.APPROVAL_PENDING,
                    OperationStatus.PROCESSING,
                )

                # Remove from Redis
                await approval_repo.remove_pending_approval(operation_id)

                # Reconstruct MidPointMessage and continue to provisioning
                message = await self._reconstruct_midpoint_message(operation)
                await self._provision_to_target(operation_id, message)

                # Clear rejected CREATE marker if this CREATE succeeded
                if operation.operation_type == "CREATE_USER":
                    username = (operation.user_data or {}).get("username", "")
                    if username:
                        await approval_repo.clear_rejected_create(
                            username, operation.target_service
                        )

                # Snapshot the provisioned state in Redis so that the next UPDATE
                # can compute a diff and detect no-op changes without hitting the target service.
                try:
                    username = (operation.user_data or {}).get("username", "")
                    if username:
                        await approval_repo.store_user_state(
                            username,
                            operation.target_service,
                            operation.user_data or {},
                        )
                except Exception as e:
                    logger.warning(f"Failed to store user state for diff: {e}")

                logger.info(
                    f"Operation {operation_id} completed successfully after approval",
                    extra={"operation_id": operation_id},
                )

            else:
                # REJECTED: Mark as failed
                logger.warning(
                    f"Operation {operation_id} REJECTED by {worker_id}: {reason}",
                    extra={"operation_id": operation_id, "worker_id": worker_id},
                )

                # Update status to FAILED
                await self._repo.update_status(
                    id=operation_id,
                    status=OperationStatus.FAILED,
                    error_message=f"Approval rejected: {reason}",
                )

                # Log audit trail
                await self._audit.log_approval_response(
                    operation_id=operation_id,
                    approved=False,
                    reason=reason,
                    worker_id=worker_id,
                )

                await self._audit.log_status_change(
                    operation_id=operation_id,
                    old_status=OperationStatus.APPROVAL_PENDING,
                    new_status=OperationStatus.FAILED,
                    message=f"Approval rejected: {reason}",
                )

                # Remove from Redis
                await approval_repo.remove_pending_approval(operation_id)

                # Mark this CREATE as rejected in Redis (TTL: 7 days).
                # Subsequent UPDATE/DELETE messages for this user+service will detect this marker
                # and skip or convert the operation accordingly.
                if operation.operation_type == "CREATE_USER":
                    username = (operation.user_data or {}).get("username", "")
                    if username:
                        await approval_repo.store_rejected_create(
                            username, operation.target_service, operation_id, reason
                        )
                    # Roll back the MidPoint role assignment so IAM stays in sync with reality
                    await self._remove_midpoint_role(operation)

            # --- Propagate decision to group members (bulk approval) ---
            # If this operation was the group leader, apply the same decision to all
            # sibling operations that were grouped into the same approval email.
            group = await approval_repo.get_group_by_leader(operation_id)
            if group:
                for member in group.get("members", []):
                    member_id = member["operation_id"]
                    if member_id == operation_id:
                        continue  # already processed above
                    try:
                        logger.info(
                            f"Propagating {'approval' if approved else 'rejection'} "
                            f"to group member {member_id} ({member['target_service']})",
                            extra={"operation_id": member_id, "leader_operation_id": operation_id},
                        )
                        await self.process_approval_response(
                            operation_id=member_id,
                            approved=approved,
                            reason=reason,
                            worker_id=worker_id,
                        )
                    except Exception as member_exc:
                        logger.error(
                            f"Failed to propagate approval to group member {member_id}: {member_exc}",
                            extra={"operation_id": member_id},
                        )
                # Clean up the group entry from Redis
                await approval_repo.remove_approval_group(
                    group.get("username", ""),
                    group.get("operation_type", ""),
                )

        except Exception as e:
            logger.error(f"Failed to process approval response: {e}")
            await self._audit.log_error(
                operation_id=operation_id,
                error_type=type(e).__name__,
                error_message=f"Approval callback processing failed: {str(e)}",
                error_stacktrace=traceback.format_exc(),
            )
            raise

    async def _reconstruct_midpoint_message(
        self,
        operation,
    ) -> MidPointMessage:
        """Reconstruct MidPointMessage from stored operation data

        Tries Redis first (faster, includes complete data), falls back to
        PostgreSQL original_message field if Redis entry is gone.

        Args:
            operation: The ProvisioningOperation record

        Returns:
            Reconstructed MidPointMessage

        Raises:
            ValueError: If message cannot be reconstructed
        """
        # Try Redis first (includes complete midpoint_message)
        try:
            approval_repo = await self._get_approval_repo()
            redis_data = await approval_repo.get_pending_approval(operation.id)
            if redis_data and "midpoint_message" in redis_data:
                return MidPointMessage(**redis_data["midpoint_message"])
        except Exception as e:
            logger.warning(f"Failed to retrieve message from Redis: {e}")

        # Fallback: reconstruct from PostgreSQL
        # Use existing operation.original_message field (already populated in _create_operation)
        if operation.original_message:
            return MidPointMessage(**operation.original_message)

        raise ValueError(f"Cannot reconstruct MidPointMessage for operation {operation.id}")

    async def _provision_to_target(
        self,
        operation_id: str,
        message: MidPointMessage,
    ) -> ProvisioningResult:
        """Provision user to target service

        Can be called from two contexts:
        1. After validation (if APPROVAL_ENABLED=False)
        2. After approval granted (if APPROVAL_ENABLED=True)
        """
        # Get current operation to determine previous status
        operation = await self._repo.get_by_id(operation_id)
        if not operation:
            raise ValueError(f"Operation {operation_id} not found")

        # Update status to PROCESSING
        # Previous status could be VALIDATED (no approval) or APPROVAL_PENDING (after approval)
        await self._update_status(
            operation_id,
            operation.status,
            OperationStatus.PROCESSING,
        )

        await self._audit.log_provisioning_started(
            operation_id=operation_id,
            target_service=message.target_service.value,
        )

        # Get connector for target service
        connector = ConnectorFactory.create(message.target_service)

        try:
            async with connector:
                # Execute the appropriate operation
                user_data = message.user_data
                result = await self._execute_operation(
                    connector,
                    message.operation_type,
                    user_data,
                )

            # Update status to SUCCESS
            await self._repo.update_status(
                id=operation_id,
                status=OperationStatus.SUCCESS,
                provisioning_result=result.model_dump(),
            )

            await self._audit.log_provisioning_completed(
                operation_id=operation_id,
                target_service=message.target_service.value,
                service_user_id=result.service_user_id,
                details=result.details,
            )

            await self._audit.log_status_change(
                operation_id=operation_id,
                old_status=OperationStatus.PROCESSING,
                new_status=OperationStatus.SUCCESS,
            )

            return result

        except ProvisioningError:
            raise
        except Exception as e:
            raise ProvisioningError(
                operation_id=operation_id,
                target_service=message.target_service,
                error_message=str(e),
                is_retriable=True,
            )

    async def _execute_operation(
        self,
        connector,
        operation_type: OperationType,
        user_data,
    ) -> ProvisioningResult:
        """Execute the specific operation on the connector"""
        if operation_type == OperationType.CREATE_USER:
            return await connector.provision_user(
                username=user_data.username,
                password=user_data.password,
                email=user_data.email,
                roles=user_data.roles,
                attributes=user_data.attributes,
            )
        elif operation_type == OperationType.UPDATE_USER:
            return await connector.update_user(
                username=user_data.username,
                password=user_data.password,
                email=user_data.email,
                roles=user_data.roles,
                attributes=user_data.attributes,
            )
        elif operation_type == OperationType.DELETE_USER:
            return await connector.delete_user(
                username=user_data.username,
                email=user_data.email,
                attributes=user_data.attributes,
            )
        else:
            raise ProvisioningError(
                operation_id="",
                target_service=connector.service_name,
                error_message=f"Unsupported operation type: {operation_type}",
                is_retriable=False,
            )

    async def _remove_midpoint_role(self, operation) -> None:
        """Remove the MidPoint role assignment when a CREATE is rejected.

        Searches the user in MidPoint, finds the role matching the rejected
        target_service, and unassigns it. Non-blocking: logs errors but
        never raises.
        """
        from app.services.midpoint_client import midpoint_client

        username = (operation.user_data or {}).get("username", "")
        target_service = operation.target_service  # e.g. "MYSQL", "ODOO"

        if not username:
            logger.warning("Cannot remove MidPoint role: no username")
            return

        try:
            # 1. Find user in MidPoint
            user = await midpoint_client.search_user(username)
            if not user:
                logger.warning(
                    f"MidPoint user not found for role removal: {username}"
                )
                return

            user_oid = user.get("oid")
            if not user_oid:
                logger.warning(f"No OID for MidPoint user: {username}")
                return

            # 2. Get user assignments
            assignments = user.get("assignment", [])
            if not isinstance(assignments, list):
                assignments = [assignments]

            # 3. For each assignment, resolve the role name via the MidPoint REST API
            for assignment in assignments:
                target_ref = assignment.get("targetRef", {})
                ref_oid = target_ref.get("oid")
                ref_type = target_ref.get("type", "")

                # Only look at role assignments (skip org/service assignments)
                if not ref_oid or "RoleType" not in ref_type:
                    continue

                role = await midpoint_client.get_role(ref_oid)
                if not role:
                    continue

                role_name = role.get("name", "")
                # MidPoint may return name as a PolyString dict {"orig": "...", "norm": "..."}
                if isinstance(role_name, dict):
                    role_name = role_name.get("orig", "")

                # 4. Match the role by checking if the target service name appears in the role name
                # e.g. target_service="MYSQL" matches role name "MySQL-Admin"
                if target_service.lower() in role_name.lower():
                    success = await midpoint_client.unassign_role(user_oid, ref_oid)
                    if success:
                        logger.info(
                            f"Removed MidPoint role '{role_name}' from user "
                            f"'{username}' after rejected CREATE on {target_service}"
                        )
                    else:
                        logger.warning(
                            f"Failed to remove MidPoint role '{role_name}' "
                            f"from user '{username}'"
                        )
                    return  # Only remove the matching role

            logger.info(
                f"No matching MidPoint role found for {target_service} "
                f"on user {username} - nothing to remove"
            )

        except Exception as e:
            logger.error(
                f"Error removing MidPoint role for {username} "
                f"on {target_service}: {e}"
            )

    async def _handle_error(
        self,
        operation_id: str,
        message: MidPointMessage,
        error: Exception,
        is_retriable: bool,
    ) -> None:
        """Handle general errors during processing"""
        error_message = str(error)
        error_stacktrace = traceback.format_exc()

        await self._audit.log_error(
            operation_id=operation_id,
            error_type=type(error).__name__,
            error_message=error_message,
            error_stacktrace=error_stacktrace,
        )

        # Get current operation to check status
        operation = await self._repo.get_by_id(operation_id)
        if not operation:
            return

        if is_retriable:
            # Mark as FAILED for now - RetryManager will handle scheduling
            await self._repo.update_status(
                id=operation_id,
                status=OperationStatus.FAILED,
                error_message=error_message,
                error_stacktrace=error_stacktrace,
            )

            await self._audit.log_status_change(
                operation_id=operation_id,
                old_status=operation.status,
                new_status=OperationStatus.FAILED,
                message=f"Error (retriable): {error_message}",
            )
        else:
            # Non-retriable error
            await self._repo.update_status(
                id=operation_id,
                status=OperationStatus.FAILED,
                error_message=error_message,
                error_stacktrace=error_stacktrace,
            )

            await self._audit.log_status_change(
                operation_id=operation_id,
                old_status=operation.status,
                new_status=OperationStatus.FAILED,
                message=f"Error (non-retriable): {error_message}",
            )

    async def _update_status(
        self,
        operation_id: str,
        old_status: OperationStatus,
        new_status: OperationStatus,
    ) -> None:
        """Update operation status and log the change"""
        await self._repo.update_status(id=operation_id, status=new_status)
        await self._audit.log_status_change(
            operation_id=operation_id,
            old_status=old_status,
            new_status=new_status,
        )

    async def retry_operation(self, operation_id: str) -> None:
        """Retry a failed operation

        This is called by the RetryManager or manually via API.

        Args:
            operation_id: The operation to retry
        """
        operation = await self._repo.get_by_id(operation_id)
        if not operation:
            raise ValueError(f"Operation not found: {operation_id}")

        # Reconstruct message from stored data
        message = MidPointMessage(
            request_id=operation.midpoint_request_id or operation.id,
            operation_type=OperationType(operation.operation_type),
            target_service=TargetService(operation.target_service),
            user_data=operation.user_data,
            metadata=operation.original_message.get("metadata", {}),
        )

        # Process the message again
        await self.process_message(message)
