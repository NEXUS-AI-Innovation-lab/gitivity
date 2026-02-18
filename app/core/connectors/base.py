"""Abstract base class for provisioning connectors"""
from abc import ABC, abstractmethod
from typing import Any

from app.models.domain import ProvisioningResult
from app.utils.enums import TargetService


class ProvisioningConnector(ABC):
    """Abstract base class for all provisioning connectors

    Each target service (MySQL, PostgreSQL, Odoo, etc.) must implement
    this interface to handle user provisioning operations.
    """

    @property
    @abstractmethod
    def service_name(self) -> TargetService:
        """Return the target service this connector handles"""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the target service

        Raises:
            ConnectorConnectionError: If connection fails
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the target service"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the target service is healthy and reachable

        Returns:
            True if service is healthy, False otherwise
        """
        ...

    @abstractmethod
    async def provision_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Create a new user in the target service

        Args:
            username: Username to create
            password: Optional password (may be auto-generated)
            email: Optional email address
            roles: Optional list of roles to assign
            attributes: Optional additional attributes

        Returns:
            ProvisioningResult with success status and details

        Raises:
            ProvisioningError: If user creation fails
        """
        ...

    @abstractmethod
    async def update_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Update an existing user in the target service

        Args:
            username: Username to update
            password: Optional new password
            email: Optional new email
            roles: Optional new roles (replaces existing)
            attributes: Optional new attributes

        Returns:
            ProvisioningResult with success status and details

        Raises:
            ProvisioningError: If user update fails
        """
        ...

    @abstractmethod
    async def delete_user(
        self,
        username: str,
        email: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Delete a user from the target service

        Args:
            username: Username to delete
            email: Optional email (used as login identifier for some services like Odoo)
            attributes: Optional attributes (contains midpoint_uid for reliable lookup)

        Returns:
            ProvisioningResult with success status and details

        Raises:
            ProvisioningError: If user deletion fails
        """
        ...

    async def __aenter__(self) -> "ProvisioningConnector":
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.disconnect()
