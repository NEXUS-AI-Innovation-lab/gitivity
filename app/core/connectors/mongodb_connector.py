"""MongoDB connector for native database-user provisioning."""

import asyncio
import logging
from typing import Any

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

from app.config.settings import settings
from app.core.connectors.base import ProvisioningConnector
from app.models.domain import ProvisioningResult
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorConnectionError, ProvisioningError

logger = logging.getLogger(__name__)

DEFAULT_MONGODB_ROLES = ["read", "readWrite", "dbAdmin", "dbOwner"]
SERVICE_ROLE_NAMES = {"mongodb", "mongo"}


class MongoDBConnector(ProvisioningConnector):
    """Provision MongoDB users and assign database-scoped roles."""

    def __init__(self) -> None:
        self._client: MongoClient | None = None

    @property
    def service_name(self) -> TargetService:
        return TargetService.MONGODB

    async def connect(self) -> None:
        try:
            self._client = MongoClient(
                host=self.target_setting("host", settings.MONGODB_HOST),
                port=self.target_setting("port", settings.MONGODB_PORT),
                username=self.target_setting("user", settings.MONGODB_USER),
                password=self.target_setting("password", settings.MONGODB_PASSWORD),
                authSource=self.target_setting(
                    "auth_source", settings.MONGODB_AUTH_SOURCE
                ),
                serverSelectionTimeoutMS=self.target_setting(
                    "connect_timeout", settings.MONGODB_CONNECT_TIMEOUT
                )
                * 1000,
                connectTimeoutMS=self.target_setting(
                    "connect_timeout", settings.MONGODB_CONNECT_TIMEOUT
                )
                * 1000,
            )
            await asyncio.to_thread(self._client.admin.command, "ping")
            logger.info("MongoDB connection established")
        except PyMongoError as exc:
            if self._client:
                self._client.close()
                self._client = None
            raise ConnectorConnectionError(
                target_service=TargetService.MONGODB,
                error_message=str(exc),
            ) from exc

    async def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            logger.info("MongoDB connection closed")

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            await asyncio.to_thread(self._client.admin.command, "ping")
            return True
        except PyMongoError:
            return False

    def _database_name(self, attributes: dict[str, Any] | None) -> str:
        return (attributes or {}).get("mongodbDatabase") or self.target_setting(
            "database", settings.MONGODB_DATABASE
        )

    def _normalize_roles(
        self,
        roles: list[str] | None,
        attributes: dict[str, Any] | None,
        database: str,
    ) -> list[dict[str, str]]:
        attrs = attributes or {}
        raw_roles = attrs.get("mongodbRoles")
        if raw_roles is None:
            raw_roles = attrs.get("mongodbRole")
        if raw_roles is None:
            raw_roles = roles or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]

        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_roles:
            if isinstance(item, dict):
                role_name = str(item.get("role", "")).strip()
                role_db = str(item.get("db") or database).strip()
            else:
                value = str(item).strip()
                if not value or value.lower() in SERVICE_ROLE_NAMES:
                    continue
                if "@" in value:
                    role_name, role_db = value.rsplit("@", 1)
                else:
                    role_name, role_db = value, database

            key = (role_name, role_db)
            if role_name and key not in seen:
                normalized.append({"role": role_name, "db": role_db})
                seen.add(key)

        return normalized

    async def _user_exists(self, username: str, database: str) -> bool:
        assert self._client is not None
        result = await asyncio.to_thread(
            self._client[database].command,
            {"usersInfo": {"user": username, "db": database}},
        )
        return bool(result.get("users"))

    async def provision_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        if not self._client:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MONGODB,
                error_message="Not connected to MongoDB",
                is_retriable=True,
            )
        if not password:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MONGODB,
                error_message="Password is required for MongoDB user creation",
                is_retriable=False,
            )

        database = self._database_name(attributes)
        mongo_roles = self._normalize_roles(roles, attributes, database)
        try:
            if await self._user_exists(username, database):
                return await self.update_user(username, password, email, roles, attributes)

            command: dict[str, Any] = {
                "createUser": username,
                "pwd": password,
                "roles": mongo_roles,
            }
            custom_data = {
                "email": email,
                "midpointUid": (attributes or {}).get("midpoint_uid"),
            }
            command["customData"] = {k: v for k, v in custom_data.items() if v}
            await asyncio.to_thread(self._client[database].command, command)

            return ProvisioningResult(
                success=True,
                service_user_id=f"{username}@{database}",
                message=f"MongoDB user {username} created in {database}",
                details={"username": username, "database": database, "roles": mongo_roles},
            )
        except PyMongoError as exc:
            raise self._provisioning_error(exc) from exc

    async def update_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        if not self._client:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MONGODB,
                error_message="Not connected to MongoDB",
                is_retriable=True,
            )

        database = self._database_name(attributes)
        try:
            if not await self._user_exists(username, database):
                return await self.provision_user(username, password, email, roles, attributes)

            command: dict[str, Any] = {"updateUser": username}
            if password:
                command["pwd"] = password
            if (
                roles is not None
                or (attributes or {}).get("mongodbRoles") is not None
                or (attributes or {}).get("mongodbRole") is not None
            ):
                command["roles"] = self._normalize_roles(roles, attributes, database)
            custom_data = {
                "email": email,
                "midpointUid": (attributes or {}).get("midpoint_uid"),
            }
            if any(custom_data.values()):
                command["customData"] = {k: v for k, v in custom_data.items() if v}

            await asyncio.to_thread(self._client[database].command, command)
            return ProvisioningResult(
                success=True,
                service_user_id=f"{username}@{database}",
                message=f"MongoDB user {username} updated in {database}",
                details={"username": username, "database": database},
            )
        except PyMongoError as exc:
            raise self._provisioning_error(exc) from exc

    async def delete_user(
        self,
        username: str,
        email: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        if not self._client:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MONGODB,
                error_message="Not connected to MongoDB",
                is_retriable=True,
            )

        database = self._database_name(attributes)
        try:
            if not await self._user_exists(username, database):
                return ProvisioningResult(
                    success=True,
                    service_user_id=f"{username}@{database}",
                    message=f"MongoDB user {username} already absent from {database}",
                )
            await asyncio.to_thread(
                self._client[database].command,
                {"dropUser": username},
            )
            return ProvisioningResult(
                success=True,
                service_user_id=f"{username}@{database}",
                message=f"MongoDB user {username} deleted from {database}",
            )
        except PyMongoError as exc:
            raise self._provisioning_error(exc) from exc

    @staticmethod
    def _provisioning_error(exc: PyMongoError) -> ProvisioningError:
        code = str(exc.code) if isinstance(exc, OperationFailure) and exc.code else None
        retriable = isinstance(exc, ConnectionFailure) or bool(
            isinstance(exc, OperationFailure) and exc.has_error_label("RetryableWriteError")
        )
        return ProvisioningError(
            operation_id="",
            target_service=TargetService.MONGODB,
            error_message=str(exc),
            error_code=code,
            is_retriable=retriable,
        )
