"""API endpoints for connector management - Gateway and MidPoint"""
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.core.connectors.factory import ConnectorFactory
from app.services.midpoint_client import midpoint_client
from app.utils.enums import TargetService

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


# ============================================================================
# Helper Functions
# ============================================================================

def get_gateway_connector_config(service: TargetService) -> dict[str, Any]:
    """Get current configuration for a Gateway connector (without sensitive data).

    Passwords are intentionally excluded — they are read from settings but never returned.
    """
    if service == TargetService.MYSQL:
        return {
            "host": settings.MYSQL_HOST,
            "port": settings.MYSQL_PORT,
            "user": settings.MYSQL_USER,
            "database": settings.MYSQL_DATABASE,
        }
    elif service == TargetService.POSTGRESQL:
        return {
            "host": settings.POSTGRESQL_HOST,
            "port": settings.POSTGRESQL_PORT,
            "user": settings.POSTGRESQL_USER,
            "database": settings.POSTGRESQL_DATABASE,
        }
    elif service == TargetService.ODOO:
        return {
            "url": settings.ODOO_URL,
            "database": settings.ODOO_DB,
            "user": settings.ODOO_USERNAME,
        }
    elif service == TargetService.LDAP:
        return {
            "host": settings.LDAP_HOST,
            "port": settings.LDAP_PORT,
            "use_ssl": settings.LDAP_USE_SSL,
            "base_dn": settings.LDAP_BASE_DN,
        }
    elif service == TargetService.MONGODB:
        return {
            "host": settings.MONGODB_HOST,
            "port": settings.MONGODB_PORT,
            "user": settings.MONGODB_USER,
            "database": settings.MONGODB_DATABASE,
            "auth_source": settings.MONGODB_AUTH_SOURCE,
        }
    return {}


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
    available_services = ConnectorFactory.get_available_services()
    for service in available_services:
        try:
            connector = ConnectorFactory.create(service)
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
            name=service.value,
            type="gateway",
            status=status_str,
            message=message,
            config=get_gateway_connector_config(service)
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

                # Extract connector type from connectorRef
                connector_ref = res_obj.get("connectorRef", {})
                connector_type = connector_ref.get("type", "Unknown")

                # Get operational state
                op_state = res_obj.get("operationalState", {})
                last_availability = op_state.get("lastAvailabilityStatus", "UNKNOWN")

                if last_availability == "UP":
                    status_str = "connected"
                elif last_availability == "DOWN":
                    status_str = "disconnected"
                else:
                    status_str = "unknown"

                # Sanitize config: removes passwords and credentials before returning to the client
                config = midpoint_client._sanitize_config(
                    res_obj.get("connectorConfiguration", {})
                )

                midpoint_connectors.append(MidPointConnector(
                    oid=oid,
                    name=name,
                    type="midpoint",
                    description=res_obj.get("description"),
                    connector_type=connector_type,
                    status=status_str,
                    config=config
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
    available_services = ConnectorFactory.get_available_services()

    for service in available_services:
        try:
            connector = ConnectorFactory.create(service)
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
            name=service.value,
            type="gateway",
            status=status_str,
            message=message,
            config=get_gateway_connector_config(service)
        ))

    return connectors


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
        service = TargetService(connector_name.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' not found"
        )

    if not ConnectorFactory.is_service_available(service):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' is not available"
        )

    try:
        connector = ConnectorFactory.create(service)
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
        name=service.value,
        type="gateway",
        status=status_str,
        message=message,
        config=get_gateway_connector_config(service)
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
        service = TargetService(connector_name.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' not found"
        )

    if not ConnectorFactory.is_service_available(service):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' is not available"
        )

    start_time = time.perf_counter()

    try:
        connector = ConnectorFactory.create(service)
        try:
            await connector.connect()
            is_healthy = await connector.health_check()
            latency_ms = (time.perf_counter() - start_time) * 1000

            if is_healthy:
                return ConnectorTestResponse(
                    name=service.value,
                    type="gateway",
                    success=True,
                    message="Connection successful",
                    latency_ms=round(latency_ms, 2)
                )
            else:
                return ConnectorTestResponse(
                    name=service.value,
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
            name=service.value,
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

        if result.get("success", False) or "error" not in result:
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
    Update .env file for permanent changes.
    """
    try:
        service = TargetService(connector_name.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' not found"
        )

    if not ConnectorFactory.is_service_available(service):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway connector '{connector_name}' is not available"
        )

    # Update settings in memory
    if service == TargetService.MYSQL:
        if config.host:
            settings.MYSQL_HOST = config.host
        if config.port:
            settings.MYSQL_PORT = config.port
        if config.user:
            settings.MYSQL_USER = config.user
        if config.password:
            settings.MYSQL_PASSWORD = config.password
        if config.database:
            settings.MYSQL_DATABASE = config.database
    elif service == TargetService.POSTGRESQL:
        if config.host:
            settings.POSTGRESQL_HOST = config.host
        if config.port:
            settings.POSTGRESQL_PORT = config.port
        if config.user:
            settings.POSTGRESQL_USER = config.user
        if config.password:
            settings.POSTGRESQL_PASSWORD = config.password
        if config.database:
            settings.POSTGRESQL_DATABASE = config.database
    elif service == TargetService.ODOO:
        if config.url:
            settings.ODOO_URL = config.url
        if config.database:
            settings.ODOO_DB = config.database
        if config.user:
            settings.ODOO_USERNAME = config.user
        if config.password:
            settings.ODOO_PASSWORD = config.password
    elif service == TargetService.LDAP:
        if config.host:
            settings.LDAP_HOST = config.host
        if config.port:
            settings.LDAP_PORT = config.port
    elif service == TargetService.MONGODB:
        if config.host:
            settings.MONGODB_HOST = config.host
        if config.port:
            settings.MONGODB_PORT = config.port
        if config.user:
            settings.MONGODB_USER = config.user
        if config.password:
            settings.MONGODB_PASSWORD = config.password
        if config.database:
            settings.MONGODB_DATABASE = config.database

    logger.info(f"Updated configuration for Gateway connector: {connector_name}")

    return await get_gateway_connector(connector_name)
