"""Continuous native entitlement discovery and MidPoint role synchronization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config.entitlement_sync import EntitlementSyncConfig
from app.config.settings import settings
from app.config.target_catalog import TargetDefinition, target_catalog
from app.services.midpoint_client import MidPointClient, midpoint_client

logger = logging.getLogger(__name__)


class EntitlementSyncState:
    """Small atomic JSON state used to confirm disappearances across restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "targets": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read entitlement sync state: {exc}") from exc
        payload.setdefault("version", 1)
        payload.setdefault("targets", {})
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class EntitlementSyncService:
    def __init__(
        self,
        config: EntitlementSyncConfig,
        midpoint: MidPointClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.midpoint = midpoint or midpoint_client
        self.http = http_client or httpx.AsyncClient(
            timeout=config.request_timeout_seconds
        )
        self._owns_http = http_client is None
        self.state = EntitlementSyncState(config.state_file)

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()
        await self.midpoint.close()

    @staticmethod
    def _role_xml(
        item: dict[str, Any], target: TargetDefinition, resource_oid: str
    ) -> str:
        role_oid = quoteattr(str(item["role_oid"]))
        role_name = escape(str(item["role_name"]))
        target_id = escape(target.id)
        association_ref = escape(str(item["association_ref"]))
        association_value = escape(str(item["association_value"]))
        resource = quoteattr(resource_oid)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<role xmlns="http://midpoint.evolveum.com/xml/ns/public/common/common-3"
      xmlns:c="http://midpoint.evolveum.com/xml/ns/public/common/common-3"
      xmlns:icfs="http://midpoint.evolveum.com/xml/ns/public/connector/icf-1/resource-schema-3"
      xmlns:q="http://prism.evolveum.com/xml/ns/public/query-3"
      xmlns:ri="http://midpoint.evolveum.com/xml/ns/public/resource/instance-3"
      oid={role_oid}>
    <name>{role_name}</name>
    <description>Generated continuously for target {target_id}</description>
    <inducement>
        <construction>
            <resourceRef oid={resource} type="c:ResourceType"/>
            <kind>account</kind>
            <intent>default</intent>
            <attribute>
                <ref>ri:roles</ref>
                <outbound><expression><value>{target_id}</value></expression></outbound>
            </attribute>
            <association>
                <ref>ri:{association_ref}</ref>
                <outbound>
                    <strength>strong</strength>
                    <expression>
                        <associationTargetSearch>
                            <filter>
                                <q:equal>
                                    <q:path>attributes/icfs:name</q:path>
                                    <q:value>{association_value}</q:value>
                                </q:equal>
                            </filter>
                            <searchStrategy>onResourceIfNeeded</searchStrategy>
                        </associationTargetSearch>
                    </expression>
                </outbound>
            </association>
        </construction>
    </inducement>
</role>
"""

    def _confirmed_manifest(
        self,
        manifest: dict[str, Any],
        previous: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        current_items = {
            item["key"]: item for item in manifest.get("entitlements", [])
        }
        known_items = previous.get("entitlements", {})
        old_counts = previous.get("missing_counts", {})
        effective_items = dict(current_items)
        next_known = dict(current_items)
        next_counts: dict[str, int] = {}
        removals: list[dict[str, Any]] = []

        for key, item in known_items.items():
            if key in current_items:
                continue
            count = int(old_counts.get(key, 0)) + 1
            if count < self.config.missing_confirmation_cycles:
                effective_items[key] = item
                next_known[key] = item
                next_counts[key] = count
            else:
                removals.append(item)
                logger.warning(
                    "Entitlement disappearance confirmed: target=%s key=%s cycles=%s",
                    manifest["target_id"],
                    key,
                    count,
                )

        additions = [
            item for key, item in current_items.items() if key not in known_items
        ]
        effective = {
            **manifest,
            "entitlements": sorted(
                effective_items.values(), key=lambda value: value["key"]
            ),
        }
        next_state = {
            "entitlements": next_known,
            "missing_counts": next_counts,
        }
        return effective, next_state, additions, removals

    async def _discover(self, target: TargetDefinition) -> dict[str, Any]:
        response = await self.http.get(
            f"{self.config.gateway_http_url.rstrip('/')}/entitlements/manifest",
            params={"target": target.id},
        )
        response.raise_for_status()
        return response.json()

    async def _reconcile(self, manifest: dict[str, Any]) -> dict[str, Any]:
        response = await self.http.post(
            f"{self.config.gateway_api_url.rstrip('/')}/api/v1/entitlement-removals/reconcile",
            json=[manifest],
            headers={"X-Gateway-Reconcile-Token": settings.ENTITLEMENT_RECONCILE_TOKEN},
        )
        response.raise_for_status()
        return response.json()

    async def _process_decommissions(self) -> None:
        try:
            response = await self.http.post(
                f"{self.config.gateway_api_url.rstrip('/')}/api/v1/connectors/"
                "runtime-targets/decommissions/process",
                headers={
                    "X-Gateway-Reconcile-Token": settings.ENTITLEMENT_RECONCILE_TOKEN
                },
                timeout=self.config.decommission_timeout_seconds,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - keep entitlement discovery alive
            logger.exception(
                "Runtime target decommission processing failed; "
                "it will be retried during the next synchronization cycle"
            )

    async def _process_approved_entitlement_removals(self) -> None:
        try:
            response = await self.http.post(
                f"{self.config.gateway_api_url.rstrip('/')}/api/v1/"
                "entitlement-removals/process-approved",
                headers={
                    "X-Gateway-Reconcile-Token": settings.ENTITLEMENT_RECONCILE_TOKEN
                },
                timeout=self.config.decommission_timeout_seconds,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - retry during the next durable cycle
            logger.exception("Approved entitlement cleanup processing failed")

    async def _upsert_additions(
        self, target: TargetDefinition, additions: list[dict[str, Any]]
    ) -> None:
        for item in additions:
            action = await self.midpoint.upsert_role_xml(
                item["role_oid"],
                self._role_xml(item, target, self.config.midpoint.resource_oid),
            )
            logger.info(
                "MidPoint role %s for new entitlement: target=%s key=%s",
                action,
                target.id,
                item["key"],
            )

        object_class = self.config.midpoint.object_classes.get(target.type)
        if (
            additions
            and self.config.midpoint.import_shadows_on_addition
            and object_class
        ):
            await self.midpoint.import_resource_object_class(
                self.config.midpoint.resource_oid, object_class
            )

    async def _delete_disappeared_shadows(
        self, target: TargetDefinition, effective: dict[str, Any]
    ) -> None:
        # The shadow can still be referenced by account associations while the
        # corresponding role-removal approval is pending. Physical deletion is
        # therefore performed by EntitlementRemovalService after approval.
        return

    async def run_cycle(self) -> dict[str, Any]:
        await self._process_decommissions()
        await self._process_approved_entitlement_removals()
        payload = self.state.load()
        target_states = payload["targets"]
        result: dict[str, Any] = {"synchronized": [], "failed": {}}

        for target in target_catalog.targets():
            if not target.entitlements:
                continue
            try:
                manifest = await self._discover(target)
                effective, next_state, additions, _removals = self._confirmed_manifest(
                    manifest, target_states.get(target.id, {})
                )
                await self._upsert_additions(target, additions)
                await self._delete_disappeared_shadows(target, effective)
                reconciliation = await self._reconcile(effective)
                target_states[target.id] = next_state
                self.state.save(payload)
                result["synchronized"].append(
                    {
                        "target_id": target.id,
                        "additions": [item["key"] for item in additions],
                        "reconciliation": reconciliation,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - isolate each unavailable target
                logger.exception("Entitlement synchronization failed for %s", target.id)
                result["failed"][target.id] = str(exc)

        return result
