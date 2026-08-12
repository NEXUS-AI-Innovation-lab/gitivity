"""Durable, approval-aware decommissioning for ephemeral runtime targets."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prisma import Prisma

from app.config.entitlement_sync import load_entitlement_sync_config
from app.config.settings import settings
from app.config.target_catalog import target_catalog
from app.core.orchestrator import ProvisioningOrchestrator
from app.db.repositories.approval_redis_repository import ApprovalRedisRepository
from app.db.redis_client import RedisClient
from app.models.domain import MidPointMessage, UserData
from app.services.midpoint_client import MidPointClient, midpoint_client
from app.utils.enums import OperationType, TargetService

logger = logging.getLogger(__name__)


class TargetDecommissionService:
    """Keep target configuration until accounts and MidPoint roles are gone."""

    STATE_KEY_PREFIX = "target_decommission:"

    def __init__(self, db: Prisma, midpoint: MidPointClient | None = None) -> None:
        self.db = db
        self.midpoint = midpoint or midpoint_client

    async def _redis(self):
        return await RedisClient.get_client()

    async def _save(self, target_id: str, state: dict[str, Any]) -> None:
        redis = await self._redis()
        await redis.set(
            f"{self.STATE_KEY_PREFIX}{target_id}",
            json.dumps(state, ensure_ascii=False),
        )

    async def begin(self, target_id: str) -> dict[str, Any]:
        target = target_catalog.get(target_id)
        if target_catalog.source(target.id) != "runtime":
            raise ValueError("Only runtime targets can be decommissioned")
        redis = await self._redis()
        key = f"{self.STATE_KEY_PREFIX}{target.id}"
        existing = await redis.get(key)
        if existing:
            return json.loads(existing)

        target_operations = []
        for operation in await self.db.provisioningoperation.find_many(
            order={"created_at": "asc"}
        ):
            original = operation.original_message or {}
            if original.get("target_id") == target.id:
                target_operations.append(operation)

        cancellable_statuses = {
            "PENDING",
            "VALIDATING",
            "VALIDATED",
            "APPROVAL_PENDING",
            "PROCESSING",
            "RETRYING",
        }
        approval_repo = ApprovalRedisRepository(redis)
        cancelled_operations: list[str] = []
        for operation in target_operations:
            original = operation.original_message or {}
            metadata = original.get("metadata") or {}
            if (
                operation.status not in cancellable_statuses
                or metadata.get("target_decommission")
            ):
                continue
            await approval_repo.cancel_operation_approval(operation.id)
            await self.db.provisioningoperation.update(
                where={"id": operation.id},
                data={
                    "status": "FAILED",
                    "error_message": (
                        f"Cancelled: target {target.id} is being decommissioned"
                    ),
                    "processing_completed_at": datetime.now(timezone.utc),
                },
            )
            cancelled_operations.append(operation.id)

        cancelled_entitlement_requests: list[str] = []
        for request in await self.db.entitlementremovalrequest.find_many(
            where={"target_id": target.id, "status": "PENDING"}
        ):
            await self.db.entitlementremovalrequest.update(
                where={"id": request.id},
                data={
                    "status": "CANCELLED",
                    "decision_reason": (
                        f"Cancelled: target {target.id} is being decommissioned"
                    ),
                    "decision_token_hash": None,
                    "token_expires_at": None,
                    "decided_at": datetime.now(timezone.utc),
                },
            )
            cancelled_entitlement_requests.append(request.id)

        snapshots: dict[str, dict[str, Any]] = {}
        async for state_key in redis.scan_iter(match=f"user_state:{target.id}:*"):
            raw = await redis.get(state_key)
            if not raw:
                continue
            snapshot = json.loads(raw)
            username = snapshot.get("username") or state_key.rsplit(":", 1)[-1]
            snapshots[username] = snapshot

        # Redis snapshots expire eventually. The durable operation history is a
        # second source for accounts successfully (or partially) provisioned by
        # Gateway, while a later successful DELETE removes the candidate.
        latest: dict[str, Any] = {}
        for operation in target_operations:
            username = (operation.user_data or {}).get("username")
            if username:
                latest[username] = operation
        for username, operation in latest.items():
            if (
                operation.operation_type == "DELETE_USER"
                and operation.status == "SUCCESS"
            ):
                snapshots.pop(username, None)
            elif operation.status in {"SUCCESS", "PROCESSING"}:
                snapshots.setdefault(username, operation.user_data or {})

        operations: list[str] = []
        for username, snapshot in sorted(snapshots.items()):
            message = MidPointMessage(
                request_id=f"decommission-{target.id}-{username}-{uuid.uuid4()}",
                operation_type=OperationType.DELETE_USER,
                target_service=TargetService(target.type.upper()),
                target_id=target.id,
                user_data=UserData(
                    username=username,
                    email=snapshot.get("email"),
                    first_name=snapshot.get("first_name"),
                    last_name=snapshot.get("last_name"),
                    roles=snapshot.get("roles") or [],
                    attributes=snapshot.get("attributes") or {},
                ),
                metadata={"target_decommission": True},
            )
            operations.append(
                await ProvisioningOrchestrator(self.db).process_message(message)
            )

        state = {
            "target_id": target.id,
            "status": "WAITING_APPROVALS" if operations else "CLEANUP_READY",
            "operation_ids": operations,
            "cancelled_operation_ids": cancelled_operations,
            "cancelled_entitlement_request_ids": cancelled_entitlement_requests,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._save(target.id, state)
        logger.warning(
            "Runtime target decommission started: target=%s delete_operations=%s",
            target.id,
            len(operations),
        )
        return state

    async def begin_all_runtime(self) -> list[dict[str, Any]]:
        return [
            await self.begin(target.id)
            for target in target_catalog.targets()
            if target_catalog.source(target.id) == "runtime"
        ]

    async def process_all(self) -> list[dict[str, Any]]:
        redis = await self._redis()
        results: list[dict[str, Any]] = []
        async for key in redis.scan_iter(match=f"{self.STATE_KEY_PREFIX}*"):
            raw = await redis.get(key)
            if raw:
                results.append(await self._process(json.loads(raw)))
        return results

    async def _process(self, state: dict[str, Any]) -> dict[str, Any]:
        target_id = state["target_id"]
        operation_ids = state.get("operation_ids", [])
        if operation_ids:
            operations = await self.db.provisioningoperation.find_many(
                where={"id": {"in": operation_ids}}
            )
            statuses = {operation.status for operation in operations}
            if len(operations) != len(operation_ids) or statuses - {"SUCCESS"}:
                state["status"] = (
                    "BLOCKED" if statuses & {"FAILED", "DLQ"} else "WAITING_APPROVALS"
                )
                await self._save(target_id, state)
                return state

        state["status"] = "CLEANING_MIDPOINT"
        await self._save(target_id, state)
        target = target_catalog.get(target_id)
        sync_config = load_entitlement_sync_config(
            settings.ENTITLEMENT_SYNC_CONFIG_PATH
        )
        object_class = sync_config.midpoint.object_classes.get(target.type)
        if object_class and sync_config.midpoint.delete_shadows_on_disappearance:
            await self.midpoint.delete_stale_entitlement_shadows(
                sync_config.midpoint.resource_oid,
                object_class,
                target_id,
                set(),
            )
        inventories = await self.db.entitlementinventory.find_many(
            where={"target_id": target_id}
        )
        for role_oid in sorted({item.role_oid for item in inventories}):
            await self.midpoint.remove_role_everywhere(role_oid)

        removal_requests = await self.db.entitlementremovalrequest.find_many(
            where={"target_id": target_id}
        )
        for request in removal_requests:
            await self.db.entitlementremovalrequest.delete(where={"id": request.id})
        for inventory in inventories:
            await self.db.entitlementinventory.delete(where={"id": inventory.id})

        redis = await self._redis()
        async for user_key in redis.scan_iter(match=f"user_state:{target_id}:*"):
            await redis.delete(user_key)

        state_path = Path(sync_config.state_file)
        if state_path.exists():
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload.setdefault("targets", {}).pop(target_id, None)
            temporary = state_path.with_suffix(f"{state_path.suffix}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(state_path)

        target_catalog.remove_runtime(target_id)
        await redis.delete(f"{self.STATE_KEY_PREFIX}{target_id}")
        logger.warning("Runtime target decommission completed: target=%s", target_id)
        return {**state, "status": "COMPLETED"}
