"""Durable approval workflow for MidPoint roles whose native entitlement vanished."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from prisma import Json, Prisma

from app.api.v1.endpoints.approvers import _read_approvers
from app.config.entitlement_sync import load_entitlement_sync_config
from app.config.settings import settings
from app.config.target_catalog import target_catalog
from app.core.orchestrator import ProvisioningOrchestrator
from app.db.redis_client import RedisClient
from app.db.repositories.approval_redis_repository import ApprovalRedisRepository
from app.models.domain import MidPointMessage, UserData
from app.services.email_service import EmailService, email_service
from app.services.email_templates import (
    build_entitlement_approval_email,
    build_entitlement_result_email,
)
from app.services.midpoint_client import MidPointClient, midpoint_client
from app.utils.enums import OperationType

logger = logging.getLogger(__name__)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class EntitlementRemovalService:
    def __init__(
        self,
        db: Prisma,
        email: EmailService | None = None,
        midpoint: MidPointClient | None = None,
    ) -> None:
        self.db = db
        self.email = email or email_service
        self.midpoint = midpoint or midpoint_client

    async def _request_orphan_account_deletions(
        self, request: Any, target: Any, result: dict[str, Any]
    ) -> list[str]:
        """Request deletion when removed role was a user's last role on target."""
        remaining_inventory = await self.db.entitlementinventory.find_many(
            where={"target_id": request.target_id, "active": True}
        )
        target_role_oids = {
            item.role_oid
            for item in remaining_inventory
            if item.role_oid != request.role_oid
        }
        operation_ids = []
        approval_repo = ApprovalRedisRepository(await RedisClient.get_client())
        for user in result.get("affected_users", []):
            if target_role_oids.intersection(user.get("remaining_role_oids", [])):
                continue
            username = user.get("username")
            if not username:
                continue
            previous_state = await approval_repo.get_user_state(
                username, request.target_id
            ) or {}
            operation_ids.append(
                await ProvisioningOrchestrator(self.db).process_message(
                    MidPointMessage(
                        request_id=(
                            f"entitlement-removal-{request.id}-{user['oid']}-"
                            f"{uuid.uuid4()}"
                        ),
                        operation_type=OperationType.DELETE_USER,
                        target_service=target.family,
                        target_id=target.id,
                        user_data=UserData(
                            username=username,
                            email=previous_state.get("email") or user.get("email"),
                            first_name=previous_state.get("first_name"),
                            last_name=previous_state.get("last_name"),
                        ),
                        metadata={
                            "source": "entitlement-removal",
                            "midpoint_user_oid": user["oid"],
                            "removed_role_oid": request.role_oid,
                            "reason": "Last target entitlement disappeared",
                        },
                    )
                )
            )
        return operation_ids

    async def reconcile(self, manifests: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist a complete successful discovery snapshot and open missing-role requests."""
        now = datetime.now(timezone.utc)
        current: set[tuple[str, str]] = set()
        discovered_targets = {manifest["target_id"] for manifest in manifests}
        if not discovered_targets:
            raise ValueError("At least one successful target manifest is required")
        created_requests: list[str] = []

        for manifest in manifests:
            target_id = manifest["target_id"]
            excluded_native_names = set(manifest.get("excluded_native_names", []))
            current_native_names = {
                item["native_name"] for item in manifest.get("entitlements", [])
            }
            # Accounts already deleted from PostgreSQL no longer appear in the
            # live rolcanlogin list. Successful Gateway history still proves
            # that these names represented login accounts, not entitlements.
            if manifest.get("target_type") == "postgresql":
                for operation in await self.db.provisioningoperation.find_many(
                    where={"status": "SUCCESS"}
                ):
                    original = operation.original_message or {}
                    username = (operation.user_data or {}).get("username")
                    if (
                        original.get("target_id") == target_id
                        and username
                        and username not in current_native_names
                    ):
                        excluded_native_names.add(username)
            if excluded_native_names:
                stale_accounts = await self.db.entitlementinventory.find_many(
                    where={"target_id": target_id}
                )
                for inventory in stale_accounts:
                    if inventory.native_name not in excluded_native_names:
                        continue
                    pending = await self.db.entitlementremovalrequest.find_many(
                        where={"inventory_id": inventory.id, "status": "PENDING"}
                    )
                    for request in pending:
                        await self.db.entitlementremovalrequest.update(
                            where={"id": request.id},
                            data={
                                "status": "CANCELLED",
                                "decision_reason": (
                                    "Automatically removed: PostgreSQL LOGIN account "
                                    "was historically misclassified as an entitlement"
                                ),
                                "decision_token_hash": None,
                                "token_expires_at": None,
                                "decided_at": now,
                            },
                        )
                    await self.midpoint.remove_role_everywhere(inventory.role_oid)
                    await self.db.entitlementinventory.delete(
                        where={"id": inventory.id}
                    )

            for item in manifest.get("entitlements", []):
                key = item["key"]
                current.add((target_id, key))
                existing = await self.db.entitlementinventory.find_unique(
                    where={
                        "target_id_entitlement_key": {
                            "target_id": target_id,
                            "entitlement_key": key,
                        }
                    }
                )
                data = {
                    "native_name": item["native_name"],
                    "role_oid": item["role_oid"],
                    "role_name": item["role_name"],
                    "association_ref": item["association_ref"],
                    "association_value": item["association_value"],
                    "active": True,
                    "last_seen_at": now,
                    "missing_since": None,
                }
                if existing:
                    await self.db.entitlementinventory.update(
                        where={"id": existing.id}, data=data
                    )
                    pending = await self.db.entitlementremovalrequest.find_first(
                        where={"inventory_id": existing.id, "status": "PENDING"}
                    )
                    if pending:
                        await self.db.entitlementremovalrequest.update(
                            where={"id": pending.id},
                            data={
                                "status": "CANCELLED",
                                "decision_reason": "Native entitlement reappeared",
                                "decided_at": now,
                                "decision_token_hash": None,
                                "token_expires_at": None,
                            },
                        )
                else:
                    await self.db.entitlementinventory.create(
                        data={"target_id": target_id, "entitlement_key": key, **data}
                    )

        inventories = await self.db.entitlementinventory.find_many(
            where={"active": True, "target_id": {"in": sorted(discovered_targets)}}
        )
        for inventory in inventories:
            if (inventory.target_id, inventory.entitlement_key) in current:
                continue
            await self.db.entitlementinventory.update(
                where={"id": inventory.id},
                data={"active": False, "missing_since": now},
            )
            pending = await self.db.entitlementremovalrequest.find_first(
                where={"inventory_id": inventory.id, "status": "PENDING"}
            )
            if pending:
                continue
            approvers = sorted(
                _read_approvers(), key=lambda value: value.get("level", 0)
            )
            if not approvers:
                approvers = [
                    {
                        "name": "Admin",
                        "email": settings.ADMIN_APPROVAL_EMAIL,
                        "level": 1,
                    }
                ]
            request = await self.db.entitlementremovalrequest.create(
                data={
                    "inventory_id": inventory.id,
                    "target_id": inventory.target_id,
                    "entitlement_key": inventory.entitlement_key,
                    "native_name": inventory.native_name,
                    "role_oid": inventory.role_oid,
                    "role_name": inventory.role_name,
                    "approvers": Json(approvers),
                }
            )
            await self.send_current_level(request.id)
            created_requests.append(request.id)

        return {"tracked": len(current), "created_removal_requests": created_requests}

    async def renew(self, token: str) -> bool:
        """Replace an expired/current link and email it to the same approval level."""
        request = await self.db.entitlementremovalrequest.find_first(
            where={"decision_token_hash": _token_hash(token), "status": "PENDING"}
        )
        if not request:
            return False
        await self.send_current_level(request.id)
        return True

    async def register_legacy_roles(
        self,
        roles: list[dict[str, str]],
        manifests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Replace legacy assignments, then queue approval to delete only the old role."""
        now = datetime.now(timezone.utc)
        created: list[str] = []
        migrated: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        replacements = {
            (manifest["target_id"], item["key"]): item
            for manifest in manifests
            for item in manifest.get("entitlements", [])
        }
        for role in roles:
            replacement = replacements.get((role["target_id"], role["replacement_key"]))
            if not replacement:
                skipped.append(
                    {
                        "role_oid": role["role_oid"],
                        "reason": (
                            f"Replacement entitlement {role['target_id']}/"
                            f"{role['replacement_key']} was not discovered"
                        ),
                    }
                )
                continue
            migration = await self.midpoint.replace_role_everywhere(
                role["role_oid"], replacement["role_oid"]
            )
            migrated.append(
                {
                    "role_oid": role["role_oid"],
                    "replacement": replacement["role_oid"],
                    **migration,
                }
            )
            key = f"legacy-{role['role_oid']}"
            inventory = await self.db.entitlementinventory.find_unique(
                where={
                    "target_id_entitlement_key": {
                        "target_id": role["target_id"],
                        "entitlement_key": key,
                    }
                }
            )
            inventory_data = {
                "native_name": replacement["native_name"],
                "role_oid": role["role_oid"],
                "role_name": role["role_name"],
                "association_ref": replacement["association_ref"],
                "association_value": replacement["association_value"],
                "active": False,
                "missing_since": now,
            }
            if not inventory:
                inventory = await self.db.entitlementinventory.create(
                    data={
                        "target_id": role["target_id"],
                        "entitlement_key": key,
                        **inventory_data,
                    }
                )
            else:
                inventory = await self.db.entitlementinventory.update(
                    where={"id": inventory.id}, data=inventory_data
                )
            existing = await self.db.entitlementremovalrequest.find_first(
                where={"inventory_id": inventory.id}
            )
            if existing:
                if existing.status == "PENDING" and not existing.replacement_role_oid:
                    await self.db.entitlementremovalrequest.update(
                        where={"id": existing.id},
                        data={
                            "native_name": replacement["native_name"],
                            "replacement_role_oid": replacement["role_oid"],
                            "replacement_role_name": replacement["role_name"],
                            "decision_token_hash": None,
                            "token_expires_at": None,
                            "execution_result": Json(migration),
                        },
                    )
                    await self.send_current_level(existing.id)
                continue
            approvers = sorted(
                _read_approvers(), key=lambda value: value.get("level", 0)
            )
            if not approvers:
                approvers = [
                    {
                        "name": "Admin",
                        "email": settings.ADMIN_APPROVAL_EMAIL,
                        "level": 1,
                    }
                ]
            request = await self.db.entitlementremovalrequest.create(
                data={
                    "inventory_id": inventory.id,
                    "target_id": inventory.target_id,
                    "entitlement_key": inventory.entitlement_key,
                    "native_name": inventory.native_name,
                    "role_oid": inventory.role_oid,
                    "role_name": inventory.role_name,
                    "replacement_role_oid": replacement["role_oid"],
                    "replacement_role_name": replacement["role_name"],
                    "approvers": Json(approvers),
                    "execution_result": Json(migration),
                }
            )
            await self.send_current_level(request.id)
            created.append(request.id)
        return {
            "created_removal_requests": created,
            "migrated_roles": migrated,
            "skipped_roles": skipped,
        }

    async def send_current_level(self, request_id: str) -> None:
        request = await self.db.entitlementremovalrequest.find_unique(
            where={"id": request_id}
        )
        if not request or request.status != "PENDING":
            raise ValueError("Pending entitlement removal request not found")
        approvers = list(request.approvers)
        approver = approvers[request.current_approver_index]
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(
            days=settings.ENTITLEMENT_DECISION_TOKEN_DAYS
        )
        await self.db.entitlementremovalrequest.update(
            where={"id": request.id},
            data={
                "decision_token_hash": _token_hash(token),
                "token_expires_at": expires,
            },
        )
        base = f"{settings.GATEWAY_EXTERNAL_URL}/api/v1/entitlement-removals/decision?token={token}"
        renew_url = f"{settings.GATEWAY_EXTERNAL_URL}/api/v1/entitlement-removals/renew?token={token}"
        legacy = request.entitlement_key.startswith("legacy-")
        subject, body = build_entitlement_approval_email(
            request_id=request.id,
            role_name=request.role_name,
            native_name=request.native_name,
            target_id=request.target_id,
            approver_name=approver["name"],
            approver_level=request.current_approver_index + 1,
            total_approvers=len(approvers),
            approve_url=f"{base}&approved=true",
            keep_url=f"{base}&approved=false",
            renew_url=renew_url,
            replacement_role_name=request.replacement_role_name,
            legacy=legacy,
        )
        await self.email.send_html(approver["email"], subject, body)

    async def _send_execution_result(
        self, request: Any, *, success: bool, detail: str
    ) -> None:
        approvers = list(request.approvers)
        recipients = list(dict.fromkeys(
            approver.get("email") for approver in approvers if approver.get("email")
        ))
        subject, body = build_entitlement_result_email(
            role_name=request.role_name,
            target_id=request.target_id,
            request_id=request.id,
            success=success,
            detail=detail,
        )
        for recipient in recipients:
            try:
                await self.email.send_html(recipient, subject, body)
            except Exception:
                logger.exception(
                    "Could not send entitlement cleanup result to %s", recipient
                )

    async def process_approved(self) -> list[dict[str, Any]]:
        """Execute durable approved removals outside the HTTP decision request."""
        results = []
        for request in await self.db.entitlementremovalrequest.find_many(
            where={"status": "APPROVED"}, order={"created_at": "asc"}
        ):
            try:
                results.append(await self.execute(request.id))
            except Exception as exc:
                results.append(
                    {"status": "failed", "request_id": request.id, "error": str(exc)}
                )
        return results

    async def decide(self, token: str, approved: bool) -> dict[str, Any]:
        request = await self.db.entitlementremovalrequest.find_first(
            where={"decision_token_hash": _token_hash(token), "status": "PENDING"}
        )
        now = datetime.now(timezone.utc)
        if (
            not request
            or not request.token_expires_at
            or request.token_expires_at < now
        ):
            return {"status": "invalid"}
        approvers = list(request.approvers)
        approver = approvers[request.current_approver_index]
        await self.db.entitlementremovalrequest.update(
            where={"id": request.id},
            data={"decision_token_hash": None, "token_expires_at": None},
        )
        if not approved:
            await self.db.entitlementremovalrequest.update(
                where={"id": request.id},
                data={
                    "status": "KEPT",
                    "decision_reason": "Approver chose to keep the MidPoint role",
                    "decided_by": approver["email"],
                    "decided_at": now,
                },
            )
            return {"status": "kept", "request_id": request.id}

        next_index = request.current_approver_index + 1
        if next_index < len(approvers):
            await self.db.entitlementremovalrequest.update(
                where={"id": request.id}, data={"current_approver_index": next_index}
            )
            await self.send_current_level(request.id)
            return {"status": "next_level", "request_id": request.id}

        await self.db.entitlementremovalrequest.update(
            where={"id": request.id},
            data={
                "status": "APPROVED",
                "decided_by": approver["email"],
                "decided_at": now,
            },
        )
        return {"status": "processing", "request_id": request.id}

    async def execute(self, request_id: str) -> dict[str, Any]:
        request = await self.db.entitlementremovalrequest.find_unique(
            where={"id": request_id}
        )
        if not request or request.status != "APPROVED":
            raise ValueError("Approved entitlement removal request not found")
        reappeared = False
        if (
            request.entitlement_key.startswith("legacy-")
            and request.replacement_role_oid
        ):
            replacement = await self.midpoint.get_role(request.replacement_role_oid)
            if not replacement:
                await self.db.entitlementremovalrequest.update(
                    where={"id": request.id},
                    data={
                        "status": "CANCELLED",
                        "decision_reason": "Replacement MidPoint role is absent",
                    },
                )
                return {"status": "cancelled", "request_id": request.id}
        if not request.entitlement_key.startswith("legacy-"):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{settings.GATEWAY_HTTP_URL}/entitlements/manifest",
                    params={"target": request.target_id},
                )
                response.raise_for_status()
                manifest = response.json()
            reappeared = any(
                item["key"] == request.entitlement_key
                for item in manifest["entitlements"]
            )
        if reappeared:
            await self.db.entitlementremovalrequest.update(
                where={"id": request.id},
                data={
                    "status": "CANCELLED",
                    "decision_reason": "Native entitlement reappeared before execution",
                },
            )
            return {"status": "cancelled", "request_id": request.id}
        try:
            result = await self.midpoint.remove_role_everywhere(request.role_oid)
            if not request.entitlement_key.startswith("legacy-"):
                target = target_catalog.get(request.target_id)
                result["delete_operation_ids"] = (
                    await self._request_orphan_account_deletions(
                        request, target, result
                    )
                )
                config = load_entitlement_sync_config(
                    settings.ENTITLEMENT_SYNC_CONFIG_PATH
                )
                object_class = config.midpoint.object_classes.get(target.type)
                if object_class and config.midpoint.delete_shadows_on_disappearance:
                    active_names = {
                        str(item["association_value"])
                        for item in manifest.get("entitlements", [])
                    }
                    result["deleted_shadows"] = (
                        await self.midpoint.delete_stale_entitlement_shadows(
                            config.midpoint.resource_oid,
                            object_class,
                            target.id,
                            active_names,
                        )
                    )
            await self.db.entitlementremovalrequest.update(
                where={"id": request.id},
                data={"status": "COMPLETED", "execution_result": Json(result)},
            )
            await self._send_execution_result(
                request,
                success=True,
                detail=(
                    "Le rôle MidPoint a été désassigné et supprimé. "
                    f"{len(result.get('delete_operation_ids', []))} demande(s) "
                    "de suppression utilisateur ont été créées."
                ),
            )
            return {"status": "completed", "request_id": request.id, "result": result}
        except Exception as exc:
            await self.db.entitlementremovalrequest.update(
                where={"id": request.id},
                data={"status": "FAILED", "decision_reason": str(exc)},
            )
            await self._send_execution_result(
                request, success=False, detail=f"Erreur : {exc}"
            )
            raise
