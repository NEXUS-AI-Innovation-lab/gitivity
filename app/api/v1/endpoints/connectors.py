"""API endpoints for connector management - Gateway and MidPoint"""
import logging
import re
import time
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import Response
from prisma import Prisma
from pydantic import BaseModel, Field

from app.config.target_catalog import TargetDefinition, target_catalog
from app.config.settings import settings
from app.core.connectors.factory import ConnectorFactory
from app.db import get_db
from app.services.midpoint_client import midpoint_client
from app.services.target_decommission_service import TargetDecommissionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["connectors"])


# ============================================================================
# Models
# ============================================================================

class ConnectorStatus(BaseModel):
    """Status of a single Gateway connector"""
    name: str
    type: str = "gateway"  # "gateway" or "midpoint"
    status: str  # "connected", "disconnected", "error"
    message: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    source: str = "persistent"


class MidPointConnector(BaseModel):
    """MidPoint connector (resource) information"""
    oid: str
    name: str
    type: str = "midpoint"
    description: str | None = None
    connector_type: str | None = None
    status: str  # "connected", "disconnected", "error", "unknown"
    message: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


def _midpoint_test_succeeded(result: dict[str, Any]) -> bool:
    """Normalize the operation-result variants returned by MidPoint."""
    if result.get("success") is False or result.get("error"):
        return False
    status_value = str(
        result.get("status")
        or result.get("operationResult", {}).get("status")
        or "success"
    ).lower()
    return status_value not in {"fatal_error", "partial_error", "error", "failed"}


class AllConnectorsResponse(BaseModel):
    """Response containing all connectors"""
    gateway_connectors: list[ConnectorStatus]
    midpoint_connectors: list[MidPointConnector]
    midpoint_available: bool


class ConnectorConfigUpdate(BaseModel):
    """Request to update connector configuration"""
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    url: str | None = None


class ConnectorTestResponse(BaseModel):
    """Response for connector test"""
    name: str
    type: str  # "gateway" or "midpoint"
    success: bool
    message: str
    latency_ms: float | None = None


class RuntimeTargetRequest(BaseModel):
    """A target candidate created from the connectors dashboard."""

    id: str
    type: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    connection: dict[str, Any]


# ============================================================================
# Helper Functions
# ============================================================================

def get_gateway_connector_config(target: TargetDefinition) -> dict[str, Any]:
    """Return the declarative connection settings without secrets."""
    return target.public_connection()


_TARGET_DEFAULTS = {
    "mysql": {
        "routing": {"entitlement_attributes": ["mysqlProfile", "mysqlGrants", "mysqlRole"]},
        "entitlements": {"provider": "mysql_roles", "identifier": "{target}.{name}", "association_ref": "mysqlProfile"},
        "provisioning": {
            "managed_roles": [
                {"name": "admin", "privileges": ["ALL"]},
                {"name": "readonly", "privileges": ["SELECT"]},
            ]
        },
    },
    "postgresql": {
        "routing": {"entitlement_attributes": ["postgresqlProfile", "postgresqlGrants", "postgresqlRole"]},
        "entitlements": {"provider": "postgresql_roles", "identifier": "{target}.{name}", "association_ref": "postgresqlProfile"},
        "provisioning": {
            "managed_roles": [
                {"name": "admin", "privileges": ["ALL"]},
                {"name": "readonly", "privileges": ["SELECT"]},
            ]
        },
    },
    "mongodb": {
        "routing": {"entitlement_attributes": ["mongodbRoles", "mongodbRole"]},
        "entitlements": {"provider": "mongodb_roles", "identifier": "{target}.{role}@{database}", "association_ref": "mongodbRole"},
    },
    "ldap": {
        "routing": {"entitlement_attributes": ["ldapGroups", "ldapGroup"], "delete_mode": "update"},
        "entitlements": {"provider": "ldap_groups", "identifier": "{target}.{name}", "association_ref": "ldapGroup"},
        "provisioning": {"group_object_class": "groupOfNames", "group_member_attribute": "member"},
    },
    "odoo": {
        "routing": {"entitlement_attributes": ["odooGroups", "odooGroup"]},
        "entitlements": {"provider": "odoo_groups", "identifier": "{target}.{name}", "association_ref": "odooGroup"},
    },
}

_REQUIRED_CONNECTION_KEYS = {
    "mysql": {"host", "port", "user", "database"},
    "postgresql": {"host", "port", "user", "database"},
    "mongodb": {"host", "port", "user", "database"},
    "ldap": {"host", "port", "bind_dn", "base_dn", "users_base_dn", "groups_base_dn"},
    "odoo": {"url", "database", "username"},
}


def build_runtime_target(request: RuntimeTargetRequest) -> TargetDefinition:
    if not request.id.strip():
        raise ValueError("Target id is required")
    if not request.display_name.strip():
        raise ValueError("Display name is required")
    family = request.type.strip().lower()
    defaults = _TARGET_DEFAULTS.get(family)
    if defaults is None:
        raise ValueError(f"Unsupported connector type '{request.type}'")
    missing = sorted(
        key for key in _REQUIRED_CONNECTION_KEYS[family]
        if request.connection.get(key) in (None, "")
    )
    if missing:
        raise ValueError(f"Missing connection fields: {', '.join(missing)}")
    payload = request.model_dump()
    payload.update(defaults)
    if family == "ldap":
        payload["provisioning"] = {
            **defaults["provisioning"],
            "group_initial_member": request.connection["bind_dn"],
        }
    return TargetDefinition.model_validate(payload)


async def test_target(target: TargetDefinition) -> ConnectorTestResponse:
    start_time = time.perf_counter()
    try:
        connector = ConnectorFactory.create(target)
        try:
            await connector.connect()
            healthy = await connector.health_check()
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass
        return ConnectorTestResponse(
            name=target.id,
            type="gateway",
            success=healthy,
            message="Connection successful" if healthy else "Health check failed",
            latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
        )
    except Exception as exc:
        return ConnectorTestResponse(
            name=target.id,
            type="gateway",
            success=False,
            message=f"Connection failed: {exc}",
            latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
        )


def export_target_yaml(target: TargetDefinition) -> str:
    """Return an Ansible-ready catalogue fragment without clear-text secrets."""
    exported = target.model_dump(mode="json", exclude_defaults=True)
    prefix = re.sub(r"[^A-Z0-9]+", "_", target.id.upper()).strip("_")
    deployment_environment: dict[str, Any] = {}
    for key, value in target.connection.items():
        upper_key = key.upper()
        environment_key = f"{prefix}_{upper_key}"
        exported["connection"][key] = f"${{{prefix}_{upper_key}}}"
        deployment_environment[environment_key] = (
            "CHANGE_ME" if "password" in key.lower() else value
        )
    exported["deployment"] = {"environment": deployment_environment}
    return yaml.safe_dump(
        {"version": 1, "targets": [exported]},
        sort_keys=False,
        allow_unicode=True,
    )


# ============================================================================
# Endpoints
# ============================================================================

@router.get("", response_model=AllConnectorsResponse)
async def list_all_connectors():
    """List all connectors - both Gateway and MidPoint"""
    gateway_connectors = []
    midpoint_connectors = []
    midpoint_available = False

    # Get Gateway connectors — each one is connect()-checked then closed
    available_targets = ConnectorFactory.get_available_targets()
    for target in available_targets:
        try:
            connector = ConnectorFactory.create(target)
            try:
                await connector.connect()
                is_healthy = await connector.health_check()
                status_str = "connected" if is_healthy else "disconnected"
                message = None
            except Exception as e:
                status_str = "error"
                message = str(e)
            finally:
                # Always disconnect even if health_check failed, to release the connection
                try:
                    await connector.disconnect()
                except Exception:
                    pass
        except Exception as e:
            status_str = "error"
            message = f"Failed to create connector: {str(e)}"

        gateway_connectors.append(ConnectorStatus(
            name=target.id,
            type="gateway",
            status=status_str,
            message=message,
            config=get_gateway_connector_config(target),
            source=target_catalog.source(target.id),
        ))

    # Get MidPoint connectors
    try:
        if await midpoint_client.health_check():
            midpoint_available = True
            resources = await midpoint_client.get_resources()

            for resource in resources:
                res_obj = resource.get("resource", resource)
                oid = res_obj.get("oid", "")
                name = res_obj.get("name", "Unknown")

                # connectorRef.targetName is the useful connector identity;
                # its `type` merely says that the reference points to a ConnectorType.
                connector_ref = res_obj.get("connectorRef", {})
                connector_identity = (
                    connector_ref.get("targetName")
                    or connector_ref.get("name")
                    or connector_ref.get("oid")
                    or "Connecteur non renseigné"
                )
                if isinstance(connector_identity, dict):
                    connector_identity = (
                        connector_identity.get("orig")
                        or connector_identity.get("norm")
                        or connector_ref.get("oid")
                        or "Connecteur non renseigné"
                    )
                connector_type = str(connector_identity)

                # List responses often omit operationalState. Run the same real
                # resource test as the card button so the displayed state is reliable.
                test_result = await midpoint_client.test_resource(oid)
                status_str = (
                    "connected"
                    if _midpoint_test_succeeded(test_result)
                    else "disconnected"
                )

                midpoint_connectors.append(MidPointConnector(
                    oid=oid,
                    name=name,
                    type="midpoint",
                    description=res_obj.get("description"),
                    connector_type=connector_type,
                    status=status_str,
                ))
    except Exception as e:
        logger.warning(f"Could not fetch MidPoint connectors: {e}")

    return AllConnectorsResponse(
        gateway_connectors=gateway_connectors,
        midpoint_connectors=midpoint_connectors,
        midpoint_available=midpoint_available
    )


@router.get("/gateway", response_model=list[ConnectorStatus])
async def list_gateway_connectors():
    """List all Gateway connectors"""
    connectors = []
    available_targets = ConnectorFactory.get_available_targets()

    for target in available_targets:
        try:
            connector = ConnectorFactory.create(target)
            try:
                await connector.connect()
                is_healthy = await connector.health_check()
                status_str = "connected" if is_healthy else "disconnected"
                message = None
            except Exception as e:
                status_str = "error"
                message = str(e)
            finally:
                try:
                    await connector.disconnect()
                except Exception:
                    pass
        except Exception as e:
            status_str = "error"
            message = f"Failed to create connector: {str(e)}"

        connectors.append(ConnectorStatus(
            name=target.id,
            type="gateway",
            status=status_str,
            message=message,
            config=get_gateway_connector_config(target),
            source=target_catalog.source(target.id),
        ))

    return connectors


@router.post("/runtime-targets/test", response_model=ConnectorTestResponse)
async def test_runtime_target(request: RuntimeTargetRequest):
    """Test a target candidate without changing the effective catalogue."""
    try:
        target = build_runtime_target(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return await test_target(target)


@router.post("/runtime-targets", response_model=ConnectorStatus, status_code=201)
async def create_runtime_target(request: RuntimeTargetRequest):
    """Test then activate a target in the shared runtime catalogue."""
    try:
        target = build_runtime_target(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    test_result = await test_target(target)
    if not test_result.success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Target was not activated: {test_result.message}",
        )
    try:
        target_catalog.add_runtime(target)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    logger.info("Activated runtime target %s (%s)", target.id, target.type)
    return ConnectorStatus(
        name=target.id,
        status="connected",
        message="Runtime target activated",
        config=get_gateway_connector_config(target),
        source="runtime",
    )


@router.delete("/runtime-targets/{target_id}", status_code=202)
async def delete_runtime_target(target_id: str, db: Prisma = Depends(get_db)):
    """Start safe removal; retain connection data until cleanup completes."""
    try:
        result = await TargetDecommissionService(db).begin(target_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return result


def _require_reconcile_token(token: str | None) -> None:
    if not settings.ENTITLEMENT_RECONCILE_TOKEN or token != settings.ENTITLEMENT_RECONCILE_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid reconcile token")


@router.post("/runtime-targets/decommission-unpersisted")
async def decommission_unpersisted_targets(
    db: Prisma = Depends(get_db),
    reconcile_token: str | None = Header(default=None, alias="X-Gateway-Reconcile-Token"),
):
    """Start decommissioning every target that was not exported to Ansible."""
    _require_reconcile_token(reconcile_token)
    return {"decommissions": await TargetDecommissionService(db).begin_all_runtime()}


@router.post("/runtime-targets/decommissions/process")
async def process_target_decommissions(
    db: Prisma = Depends(get_db),
    reconcile_token: str | None = Header(default=None, alias="X-Gateway-Reconcile-Token"),
):
    """Advance durable decommissions whose user deletions were approved."""
    _require_reconcile_token(reconcile_token)
    return {"decommissions": await TargetDecommissionService(db).process_all()}


@router.get("/runtime-targets/{target_id}/export")
async def export_runtime_target(target_id: str):
    """Download a secret-free targets.yaml fragment for Ansible persistence."""
    try:
        target = target_catalog.get(target_id)
        if target_catalog.source(target.id) != "runtime":
            raise ValueError("Only runtime targets can be exported from this endpoint")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return Response(
        content=export_target_yaml(target),
        media_type="application/yaml",
        headers={
            "Content-Disposition": f'attachment; filename="target-{target.id}.yaml"'
        },
    )


@router.get("/midpoint", response_model=list[MidPointConnector])
async def list_midpoint_connectors():
    """List all MidPoint connectors (resources)"""
    connectors = []

    try:
        if not await midpoint_client.health_check():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MidPoint is not available"
            )

        resources = await midpoint_client.get_resources()

        for resource in resources:
            res_obj = resource.get("resource", resource)
            oid = res_obj.get("oid", "")
            name = res_obj.get("name", "Unknown")

            connector_ref = res_obj.get("connectorRef", {})
            connector_type = connector_ref.get("type", "Unknown")

            op_state = res_obj.get("operationalState", {})
            last_availability = op_state.get("lastAvailabilityStatus", "UNKNOWN")

            if last_availability == "UP":
                status_str = "connected"
            elif last_availability == "DOWN":
                status_str = "disconnected"
            else:
                status_str = "unknown"

            config = midpoint_client._sanitize_config(
                res_obj.get("connectorConfiguration", {})
            )

            connectors.append(MidPointConnector(
                oid=oid,
                name=name,
                type="midpoint",
                description=res_obj.get("description"),
                connector_type=connector_type,
                status=status_str,
                config=config
            ))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching MidPoint connectors: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch MidPoint connectors: {str(e)}"
        )

    return connectors


@router.get("/gateway/{connector_name}", response_model=ConnectorStatus)
async def get_gateway_connector(connector_name: str):
    """Get details of a specific Gateway connector"""
    try:
        target = target_catalog.resolve(connector_name)
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' not found"
        )

    if not ConnectorFactory.is_service_available(target.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' is not available"
        )

    try:
        connector = ConnectorFactory.create(target)
        try:
            await connector.connect()
            is_healthy = await connector.health_check()
            status_str = "connected" if is_healthy else "disconnected"
            message = None
        except Exception as e:
            status_str = "error"
            message = str(e)
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass
    except Exception as e:
        status_str = "error"
        message = f"Failed to create connector: {str(e)}"

    return ConnectorStatus(
        name=target.id,
        type="gateway",
        status=status_str,
        message=message,
        config=get_gateway_connector_config(target),
        source=target_catalog.source(target.id),
    )


@router.get("/midpoint/{oid}", response_model=MidPointConnector)
async def get_midpoint_connector(oid: str):
    """Get details of a specific MidPoint connector"""
    try:
        if not await midpoint_client.health_check():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MidPoint is not available"
            )

        resource = await midpoint_client.get_resource(oid)
        res_obj = resource.get("resource", resource)

        connector_ref = res_obj.get("connectorRef", {})
        connector_type = connector_ref.get("type", "Unknown")

        op_state = res_obj.get("operationalState", {})
        last_availability = op_state.get("lastAvailabilityStatus", "UNKNOWN")

        if last_availability == "UP":
            status_str = "connected"
        elif last_availability == "DOWN":
            status_str = "disconnected"
        else:
            status_str = "unknown"

        config = midpoint_client._sanitize_config(
            res_obj.get("connectorConfiguration", {})
        )

        return MidPointConnector(
            oid=oid,
            name=res_obj.get("name", "Unknown"),
            type="midpoint",
            description=res_obj.get("description"),
            connector_type=connector_type,
            status=status_str,
            config=config
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching MidPoint connector {oid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch MidPoint connector: {str(e)}"
        )


@router.post("/gateway/{connector_name}/test", response_model=ConnectorTestResponse)
async def test_gateway_connector(connector_name: str):
    """Test connection to a Gateway connector"""
    try:
        target = target_catalog.resolve(connector_name)
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' not found"
        )

    if not ConnectorFactory.is_service_available(target.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' is not available"
        )

    start_time = time.perf_counter()

    try:
        connector = ConnectorFactory.create(target)
        try:
            await connector.connect()
            is_healthy = await connector.health_check()
            latency_ms = (time.perf_counter() - start_time) * 1000

            if is_healthy:
                return ConnectorTestResponse(
                    name=target.id,
                    type="gateway",
                    success=True,
                    message="Connection successful",
                    latency_ms=round(latency_ms, 2)
                )
            else:
                return ConnectorTestResponse(
                    name=target.id,
                    type="gateway",
                    success=False,
                    message="Health check failed",
                    latency_ms=round(latency_ms, 2)
                )
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return ConnectorTestResponse(
            name=target.id,
            type="gateway",
            success=False,
            message=f"Connection failed: {str(e)}",
            latency_ms=round(latency_ms, 2)
        )


@router.post("/midpoint/{oid}/test", response_model=ConnectorTestResponse)
async def test_midpoint_connector(oid: str):
    """Test connection for a MidPoint connector"""
    start_time = time.perf_counter()

    try:
        if not await midpoint_client.health_check():
            return ConnectorTestResponse(
                name=oid,
                type="midpoint",
                success=False,
                message="MidPoint is not available",
                latency_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )

        # Get resource name first
        resource = await midpoint_client.get_resource(oid)
        name = resource.get("resource", resource).get("name", oid)

        # Test the resource
        result = await midpoint_client.test_resource(oid)
        latency_ms = (time.perf_counter() - start_time) * 1000

        if _midpoint_test_succeeded(result):
            return ConnectorTestResponse(
                name=name,
                type="midpoint",
                success=True,
                message="Connection successful",
                latency_ms=round(latency_ms, 2)
            )
        else:
            return ConnectorTestResponse(
                name=name,
                type="midpoint",
                success=False,
                message=result.get("message", "Test failed"),
                latency_ms=round(latency_ms, 2)
            )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return ConnectorTestResponse(
            name=oid,
            type="midpoint",
            success=False,
            message=f"Test failed: {str(e)}",
            latency_ms=round(latency_ms, 2)
        )


@router.put("/gateway/{connector_name}", response_model=ConnectorStatus)
async def update_gateway_connector_config(connector_name: str, config: ConnectorConfigUpdate):
    """Update Gateway connector configuration (runtime only)

    Note: Changes are in memory only and won't persist after restart.
    Update config/targets.yaml for permanent changes.
    """
    try:
        target = target_catalog.resolve(connector_name)
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' not found"
        )

    if not ConnectorFactory.is_service_available(target.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' is not available"
        )

    for key, value in config.model_dump(exclude_none=True).items():
        connection_key = (
            "username"
            if key == "user" and "username" in target.connection
            else key
        )
        target.connection[connection_key] = value

    logger.info(f"Updated configuration for Gateway connector: {connector_name}")

    return await get_gateway_connector(connector_name)
