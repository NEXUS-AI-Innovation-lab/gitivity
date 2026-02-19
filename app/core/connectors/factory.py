"""Factory for creating provisioning connectors"""
import logging
from typing import Type

from app.core.connectors.base import ProvisioningConnector
from app.core.connectors.ldap_connector import LDAPConnector
from app.core.connectors.mysql_connector import MySQLConnector
from app.core.connectors.postgresql_connector import PostgreSQLConnector
from app.core.connectors.odoo_connector import OdooConnector
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorNotFoundError

logger = logging.getLogger(__name__)


class ConnectorFactory:
    """Factory for creating provisioning connectors

    Uses the Factory pattern to instantiate the appropriate connector
    based on the target service type.
    """

    # Registry of target service to connector class
    _connectors: dict[TargetService, Type[ProvisioningConnector]] = {
        TargetService.LDAP: LDAPConnector,
        TargetService.MYSQL: MySQLConnector,
        TargetService.POSTGRESQL: PostgreSQLConnector,
        TargetService.ODOO: OdooConnector,
    }

    @classmethod
    def create(cls, target_service: TargetService) -> ProvisioningConnector:
        """Create a connector instance for the given target service

        Args:
            target_service: The target service type

        Returns:
            A new connector instance

        Raises:
            ConnectorNotFoundError: If no connector is registered for the service
        """
        connector_class = cls._connectors.get(target_service)

        if connector_class is None:
            logger.error(f"No connector found for target service: {target_service}")
            raise ConnectorNotFoundError(target_service)

        logger.debug(f"Creating connector for {target_service}")
        return connector_class()

    @classmethod
    def register_connector(
        cls,
        target_service: TargetService,
        connector_class: Type[ProvisioningConnector],
    ) -> None:
        """Register a new connector class for a target service

        Args:
            target_service: The target service type
            connector_class: The connector class to register
        """
        cls._connectors[target_service] = connector_class
        logger.info(f"Registered connector {connector_class.__name__} for {target_service}")

    @classmethod
    def unregister_connector(cls, target_service: TargetService) -> None:
        """Unregister a connector for a target service

        Args:
            target_service: The target service type to unregister
        """
        if target_service in cls._connectors:
            del cls._connectors[target_service]
            logger.info(f"Unregistered connector for {target_service}")

    @classmethod
    def get_available_services(cls) -> list[TargetService]:
        """Get list of available target services

        Returns:
            List of target services with registered connectors
        """
        return list(cls._connectors.keys())

    @classmethod
    def is_service_available(cls, target_service: TargetService) -> bool:
        """Check if a connector is available for the target service

        Args:
            target_service: The target service type to check

        Returns:
            True if a connector is registered, False otherwise
        """
        return target_service in cls._connectors


# Convenience function for creating connectors
def get_connector(target_service: TargetService) -> ProvisioningConnector:
    """Get a connector instance for the given target service

    This is a convenience wrapper around ConnectorFactory.create()

    Args:
        target_service: The target service type

    Returns:
        A new connector instance
    """
    return ConnectorFactory.create(target_service)
