"""RabbitMQ consumer implementation (default broker)

This is the primary message broker for Gateway IAM.
RabbitMQ handles messages from MidPoint for user provisioning operations.
"""
import asyncio
import json
import logging
import secrets
import string
import time
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.config.settings import settings
from app.core.broker.base import BrokerConsumer
from app.core.orchestrator import ProvisioningOrchestrator
from app.core.retry_manager import RetryManager
from app.models.domain import MidPointMessage, UserData
from app.utils.enums import OperationType, TargetService
from app.utils.exceptions import MessageParsingError

logger = logging.getLogger(__name__)

# In-memory cache to track which services each user is provisioned to
# Key: midpoint_uid, Value: set of TargetService
_user_services_cache: dict[str, set[TargetService]] = {}


def generate_random_password(length: int = 12) -> str:
    """Generate a secure random password

    Args:
        length: Password length (default 12)

    Returns:
        A random password with uppercase, lowercase, digits and special chars
    """
    # Ensure at least one of each type
    uppercase = secrets.choice(string.ascii_uppercase)
    lowercase = secrets.choice(string.ascii_lowercase)
    digit = secrets.choice(string.digits)
    special = secrets.choice("!@#$%^&*")

    # Fill the rest with random characters
    remaining_length = length - 4
    all_chars = string.ascii_letters + string.digits + "!@#$%^&*"
    remaining = "".join(secrets.choice(all_chars) for _ in range(remaining_length))

    # Combine and shuffle
    password_list = list(uppercase + lowercase + digit + special + remaining)
    secrets.SystemRandom().shuffle(password_list)

    return "".join(password_list)


class RabbitMQConsumer(BrokerConsumer):
    """RabbitMQ consumer using aio_pika"""

    def __init__(
        self,
        orchestrator: ProvisioningOrchestrator,
        retry_manager: RetryManager,
    ) -> None:
        """Initialize RabbitMQ consumer

        Args:
            orchestrator: The orchestrator to delegate message processing
            retry_manager: The retry manager for failed operations
        """
        super().__init__()
        self._orchestrator = orchestrator
        self._retry_manager = retry_manager
        self._connection: aio_pika.Connection | None = None
        self._channel: aio_pika.Channel | None = None

    async def start(self) -> None:
        """Start consuming messages from RabbitMQ"""
        logger.info(
            "Starting RabbitMQ consumer",
            extra={
                "host": settings.RABBITMQ_HOST,
                "port": settings.RABBITMQ_PORT,
                "queue": settings.RABBITMQ_QUEUE,
            },
        )

        # Connect to RabbitMQ
        self._connection = await aio_pika.connect_robust(
            host=settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            login=settings.RABBITMQ_USER,
            password=settings.RABBITMQ_PASSWORD,
        )

        self._channel = await self._connection.channel()

        # Set prefetch count for fair dispatch
        await self._channel.set_qos(prefetch_count=1)

        # Simple queue mode (for MidPoint connector compatibility)
        # No exchange/routing key needed - just declare and consume from queue
        if settings.RABBITMQ_EXCHANGE:
            # Use exchange mode if configured
            exchange = await self._channel.declare_exchange(
                settings.RABBITMQ_EXCHANGE,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            queue = await self._channel.declare_queue(
                settings.RABBITMQ_QUEUE,
                durable=True,
            )
            await queue.bind(exchange, routing_key=settings.RABBITMQ_ROUTING_KEY)
        else:
            # Simple queue mode (MidPoint connector)
            queue = await self._channel.declare_queue(
                settings.RABBITMQ_QUEUE,
                durable=True,
            )

        self._running = True
        logger.info("RabbitMQ consumer started, waiting for messages...")

        # Start consuming
        await queue.consume(self._on_message)

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the RabbitMQ consumer"""
        logger.info("Stopping RabbitMQ consumer...")
        self._running = False

        if self._channel:
            await self._channel.close()
            self._channel = None

        if self._connection:
            await self._connection.close()
            self._connection = None

        logger.info("RabbitMQ consumer stopped")

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Callback for incoming messages"""
        async with message.process():
            logger.debug(
                "Received RabbitMQ message",
                extra={
                    "message_id": message.message_id,
                    "routing_key": message.routing_key,
                },
            )

            try:
                await self._process_message(message.body)
                # Message will be auto-acked by context manager

                logger.debug(
                    "Message processed and acknowledged",
                    extra={"message_id": message.message_id},
                )

            except Exception as e:
                logger.error(
                    f"Error processing RabbitMQ message: {e}",
                    extra={"message_id": message.message_id},
                )
                # Message will be nacked and requeued by aio_pika
                raise

    async def _process_message(self, message: bytes) -> None:
        """Process a RabbitMQ message"""
        try:
            # Deserialize JSON
            data = json.loads(message.decode("utf-8"))

            logger.info(f"Received message: {json.dumps(data, indent=2)}")

            # Parse into domain model(s) - may return multiple messages
            # if user has multiple roles (one per target service)
            midpoint_messages = await self._parse_message(data)

            # Process each message through orchestrator
            for midpoint_message in midpoint_messages:
                operation_id = await self._orchestrator.process_message(
                    midpoint_message
                )

                logger.info(
                    "Message processed successfully",
                    extra={
                        "operation_id": operation_id,
                        "request_id": midpoint_message.request_id,
                        "target_service": midpoint_message.target_service.value,
                    },
                )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
            raise MessageParsingError(
                error_message=f"Invalid JSON: {e}",
                raw_message=message.decode("utf-8", errors="replace"),
            )
        except Exception as e:
            logger.error(f"Failed to process message: {e}")
            raise

    # Mapping from role name to target service
    ROLE_TO_SERVICE: dict[str, TargetService] = {
        # Odoo aliases
        "odoo": TargetService.ODOO,
        "odoo1": TargetService.ODOO,
        "odoo2": TargetService.ODOO,
        "odoo3": TargetService.ODOO,
        "odoo4": TargetService.ODOO,
        "erp": TargetService.ODOO,
        # MySQL aliases
        "sql": TargetService.MYSQL,
        "mysql": TargetService.MYSQL,
        "mysql1": TargetService.MYSQL,
        "mysql2": TargetService.MYSQL,
        "mysql3": TargetService.MYSQL,
        "mysql4": TargetService.MYSQL,
        "mariadb": TargetService.MYSQL,
        # PostgreSQL aliases
        "postgresql": TargetService.POSTGRESQL,
        "postgresql1": TargetService.POSTGRESQL,
        "postgresql2": TargetService.POSTGRESQL,
        "postgresql3": TargetService.POSTGRESQL,
        "postgresql4": TargetService.POSTGRESQL,
        "postgres": TargetService.POSTGRESQL,
        "postgres1": TargetService.POSTGRESQL,
        "postgres2": TargetService.POSTGRESQL,
        "postgres3": TargetService.POSTGRESQL,
        "postgres4": TargetService.POSTGRESQL,
        "pg": TargetService.POSTGRESQL,
        # LDAP aliases
        "ldap": TargetService.LDAP,
        "ldap1": TargetService.LDAP,
        "ldap2": TargetService.LDAP,
        "ldap3": TargetService.LDAP,
        "ldap4": TargetService.LDAP,
        "activedirectory": TargetService.LDAP,
        "ad": TargetService.LDAP,
    }

    # Mapping from MidPoint operation to OperationType
    OPERATION_MAPPING: dict[str, OperationType] = {
        "CREATE": OperationType.CREATE_USER,
        "UPDATE": OperationType.UPDATE_USER,
        "DELETE": OperationType.DELETE_USER,
    }

    async def _parse_message(self, data: dict[str, Any]) -> list[MidPointMessage]:
        """Parse raw message data into MidPointMessage(s)

        Supports both gateway-iam format and MidPoint connector format.
        For MidPoint format, creates one message per target service based on roles.

        Args:
            data: Parsed JSON data

        Returns:
            List of MidPointMessage instances (one per target service)

        Raises:
            MessageParsingError: If message format is invalid
        """
        try:
            # Detect message format: MidPoint connector or gateway-iam native
            # MidPoint format has "operation" field (CREATE, UPDATE, DELETE)
            # DELETE messages may not have "attributes" field
            if "operation" in data and (
                "attributes" in data or data.get("operation") == "DELETE"
            ):
                return await self._parse_midpoint_format(data)
            else:
                return [self._parse_native_format(data)]

        except MessageParsingError:
            raise
        except Exception as e:
            raise MessageParsingError(
                error_message=f"Failed to parse message: {e}",
                raw_message=str(data)[:500],
            )

    async def _parse_midpoint_format(
        self, data: dict[str, Any]
    ) -> list[MidPointMessage]:
        """Parse MidPoint connector format message

        Format:
        {
            "operation": "CREATE",
            "entityType": "User",
            "uid": "xxx-xxx",
            "attributes": {
                "username": "jean",
                "firstName": "Jean",
                "lastName": "Dupont",
                "email": "jean@example.com",
                "roles": ["odoo", "ldap"],
                "ldapGroups": ["cn=Users,ou=Groups,dc=example,dc=com"]
            }
        }
        """
        # Extract operation
        operation_str = data.get("operation", "").upper()
        if operation_str not in self.OPERATION_MAPPING:
            raise MessageParsingError(
                error_message=f"Invalid operation: {operation_str}. Expected CREATE, UPDATE, or DELETE"
            )
        operation_type = self.OPERATION_MAPPING[operation_str]

        # Extract request ID (use uid from MidPoint)
        request_id = data.get("uid") or data.get("requestId") or data.get("request_id")
        if not request_id:
            raise MessageParsingError(error_message="Missing required field: uid")

        # Extract attributes
        attributes = data.get("attributes", {})

        # Get roles to determine target services
        roles = attributes.get("roles", [])
        if isinstance(roles, str):
            roles = [roles]

        # Get ldapGroups for LDAP provisioning
        ldap_groups = attributes.get("ldapGroups", [])
        if isinstance(ldap_groups, str):
            ldap_groups = [ldap_groups]

        # Get odooGroups for Odoo group assignment
        odoo_groups = attributes.get("odooGroups", [])
        if isinstance(odoo_groups, str):
            odoo_groups = [odoo_groups]

        # Build user data - include all key attributes for connectors
        first_name = attributes.get("firstName")
        last_name = attributes.get("lastName")
        email = attributes.get("email")

        # Generate password for CREATE operations if not provided
        password = attributes.get("password")
        username = attributes.get("username") or attributes.get("__NAME__", "")

        # === DELETE enrichment: fetch user info from MidPoint/Redis ===
        if operation_type == OperationType.DELETE_USER and not username:
            logger.info(
                f"DELETE without attributes - enriching from MidPoint (uid={request_id})"
            )
            # Try MidPoint API first
            try:
                from app.services.midpoint_client import midpoint_client

                mp_user = await midpoint_client.get_user(request_id)
                if mp_user:
                    # Extract username
                    name_field = mp_user.get("name", {})
                    if isinstance(name_field, dict):
                        username = name_field.get("orig", "")
                    elif isinstance(name_field, str):
                        username = name_field

                    # Extract email
                    email_field = mp_user.get("emailAddress")
                    if isinstance(email_field, dict):
                        email = email_field.get("orig", email)
                    elif isinstance(email_field, str):
                        email = email_field

                    # Extract names
                    gn = mp_user.get("givenName", {})
                    first_name = (
                        gn.get("orig", "") if isinstance(gn, dict) else (gn or "")
                    )
                    fn = mp_user.get("familyName", {})
                    last_name = (
                        fn.get("orig", "") if isinstance(fn, dict) else (fn or "")
                    )

                    # Determine services from role assignments
                    assignments = mp_user.get("assignment", [])
                    if not isinstance(assignments, list):
                        assignments = [assignments]
                    for assignment in assignments:
                        target_ref = assignment.get("targetRef", {})
                        ref_oid = target_ref.get("oid")
                        ref_type = target_ref.get("type", "")
                        if ref_oid and "RoleType" in ref_type:
                            try:
                                role = await midpoint_client.get_role(ref_oid)
                                if role:
                                    role_name = role.get("name", "")
                                    if isinstance(role_name, dict):
                                        role_name = role_name.get("orig", "")
                                    if role_name:
                                        roles.append(role_name)
                            except Exception:
                                pass

                    logger.info(
                        f"DELETE enriched from MidPoint: username={username}, roles={roles}"
                    )
            except Exception as e:
                logger.warning(f"Failed to enrich DELETE from MidPoint: {e}")

            # Fallback: search Redis user states
            if not username:
                try:
                    from app.db.redis_client import RedisClient

                    redis_client = await RedisClient.get_client()
                    for svc in ["MYSQL", "POSTGRESQL", "LDAP", "ODOO"]:
                        keys = await redis_client.keys(f"user_state:{svc}:*")
                        for k in keys:
                            value = await redis_client.get(k)
                            if value:
                                state = json.loads(value)
                                if (
                                    state.get("attributes", {}).get("midpoint_uid")
                                    == request_id
                                ):
                                    username = state.get("username", "")
                                    email = email or state.get("email")
                                    first_name = first_name or state.get("first_name")
                                    last_name = last_name or state.get("last_name")
                                    logger.info(
                                        f"DELETE enriched from Redis: username={username}"
                                    )
                                    break
                        if username:
                            break
                except Exception as e:
                    logger.warning(f"Failed to enrich DELETE from Redis: {e}")

        if operation_type == OperationType.CREATE_USER and not password:
            password = generate_random_password()
            logger.info("=" * 60)
            logger.info(f"GENERATED PASSWORD FOR USER: {username}")
            logger.info(f"PASSWORD: {password}")
            logger.info("=" * 60)

        user_data = UserData(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            roles=roles,
            attributes={
                "midpoint_uid": request_id,  # MidPoint unique ID for user identification
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "fullName": attributes.get("fullName"),
                "description": attributes.get("description"),
                "telephoneNumber": attributes.get("telephoneNumber"),
                "mobile": attributes.get("mobile"),
                "title": attributes.get("title"),
                "organization": attributes.get("organization"),
                "organizationalUnit": attributes.get("organizationalUnit"),
                "department": attributes.get("department"),
                "locality": attributes.get("locality"),
                "costCenter": attributes.get("costCenter"),
                "preferredLanguage": attributes.get("preferredLanguage"),
                "locale": attributes.get("locale"),
                "timezone": attributes.get("timezone"),
                "ldapGroups": ldap_groups,
                "odooGroups": odoo_groups,
                "enabled": attributes.get("enabled", True),
                # Odoo-specific: control which models to provision
                "odooCreateUser": attributes.get("odooCreateUser", True),
                "odooCreateEmployee": attributes.get("odooCreateEmployee", False),
                # MySQL-specific: direct SQL grants (comma-separated) or role
                "mysqlGrants": attributes.get("mysqlGrants"),
                "mysqlRole": attributes.get("mysqlRole"),
                # PostgreSQL-specific: direct SQL grants (comma-separated) or role
                "postgresqlGrants": attributes.get("postgresqlGrants"),
                "postgresqlRole": attributes.get("postgresqlRole"),
                # Employee-specific attributes
                "employeeNumber": attributes.get("employeeNumber"),
                "personalNumber": attributes.get("personalNumber"),
            },
        )

        # Check for removed roles (sent by MidPoint when a role is unassigned)
        removed_roles = attributes.get("removedRoles", [])
        if isinstance(removed_roles, str):
            removed_roles = [removed_roles]

        # Create one message per target service based on roles
        messages: list[MidPointMessage] = []
        target_services_processed: set[TargetService] = set()

        # For DELETE operations, determine which services to delete from
        if operation_type == OperationType.DELETE_USER:
            target_services = []

            # First check the cache for previously provisioned services
            cached_services = _user_services_cache.get(request_id, set())
            if cached_services:
                target_services = list(cached_services)
                logger.info(
                    f"DELETE: Using cached services for user {request_id}: {[s.value for s in target_services]}"
                )
                # Clear cache after DELETE
                del _user_services_cache[request_id]

            # If roles are specified, add those services too
            if roles:
                for role in roles:
                    role_lower = role.lower()
                    if role_lower in self.ROLE_TO_SERVICE:
                        service = self.ROLE_TO_SERVICE[role_lower]
                        if service not in target_services:
                            target_services.append(service)

            # Also check ldapGroups - if present, include LDAP
            if ldap_groups and TargetService.LDAP not in target_services:
                target_services.append(TargetService.LDAP)
                logger.info("DELETE: Including LDAP based on ldapGroups presence")

            # Also check odooGroups - if present, include Odoo
            if odoo_groups and TargetService.ODOO not in target_services:
                target_services.append(TargetService.ODOO)

            # If still no services (no cache, no roles, no groups), try ALL services
            if not target_services:
                logger.info(
                    "DELETE operation without any hints - attempting deletion from ALL services"
                )
                target_services = [
                    TargetService.MYSQL,
                    TargetService.POSTGRESQL,
                    TargetService.LDAP,
                    TargetService.ODOO,
                ]

        # Handle role removal (removedRoles attribute from Java connector)
        elif removed_roles:
            logger.info(f"Role removal detected via removedRoles: {removed_roles}")
            for role in removed_roles:
                role_lower = role.lower()
                if role_lower in self.ROLE_TO_SERVICE:
                    target_service = self.ROLE_TO_SERVICE[role_lower]
                    if target_service not in target_services_processed:
                        target_services_processed.add(target_service)
                        timestamp_ms = int(time.time() * 1000)
                        if target_service == TargetService.LDAP:
                            # For LDAP: UPDATE to remove group memberships (not DELETE the user)
                            messages.append(
                                MidPointMessage(
                                    request_id=f"{request_id}-ldap-cleanup-{timestamp_ms}",
                                    operation_type=OperationType.UPDATE_USER,
                                    target_service=TargetService.LDAP,
                                    user_data=user_data,
                                    metadata={
                                        "source": "midpoint",
                                        "entityType": data.get("entityType", "User"),
                                        "original_uid": request_id,
                                        "removed_role": role,
                                        "reason": "ldap role removed - group cleanup",
                                    },
                                )
                            )
                            logger.info(
                                "Created UPDATE operation for removed LDAP role (group cleanup)"
                            )
                        else:
                            messages.append(
                                MidPointMessage(
                                    request_id=f"{request_id}-{target_service.value.lower()}-delete-{timestamp_ms}",
                                    operation_type=OperationType.DELETE_USER,
                                    target_service=target_service,
                                    user_data=user_data,
                                    metadata={
                                        "source": "midpoint",
                                        "entityType": data.get("entityType", "User"),
                                        "original_uid": request_id,
                                        "removed_role": role,
                                    },
                                )
                            )
                            logger.info(
                                f"Created DELETE operation for removed role '{role}' -> {target_service.value}"
                            )

            # Map remaining roles to target services for UPDATE
            target_services = []
            for role in roles:
                role_lower = role.lower()
                if role_lower in self.ROLE_TO_SERVICE:
                    target_services.append(self.ROLE_TO_SERVICE[role_lower])

        # Handle UPDATE with partial roles - detect missing services that should be deleted
        elif operation_type == OperationType.UPDATE_USER:
            # Map current roles to target services
            current_services: set[TargetService] = set()
            for role in roles:
                role_lower = role.lower()
                if role_lower in self.ROLE_TO_SERVICE:
                    current_services.add(self.ROLE_TO_SERVICE[role_lower])

            # Get previously provisioned services from cache
            previous_services = _user_services_cache.get(request_id, set())

            # Services that were removed (in cache but not in current roles)
            removed_services = previous_services - current_services

            if removed_services:
                logger.info(
                    f"UPDATE: Detected removed services for user {request_id}: {[s.value for s in removed_services]}"
                )

            # Create operations for removed services
            for service in removed_services:
                if service == TargetService.LDAP:
                    # For LDAP: use UPDATE (not DELETE) so the connector removes group memberships
                    # without deleting the entire LDAP user account
                    logger.info(
                        "UPDATE: 'ldap' was removed - creating UPDATE for LDAP group cleanup"
                    )
                    timestamp_ms = int(time.time() * 1000)
                    messages.append(
                        MidPointMessage(
                            request_id=f"{request_id}-ldap-cleanup-{timestamp_ms}",
                            operation_type=OperationType.UPDATE_USER,
                            target_service=TargetService.LDAP,
                            user_data=user_data,
                            metadata={
                                "source": "midpoint",
                                "entityType": data.get("entityType", "User"),
                                "original_uid": request_id,
                                "reason": "ldap role removed - group cleanup",
                            },
                        )
                    )
                else:
                    logger.info(
                        f"UPDATE: '{service.value}' was removed - creating DELETE"
                    )
                    timestamp_ms = int(time.time() * 1000)
                    messages.append(
                        MidPointMessage(
                            request_id=f"{request_id}-{service.value.lower()}-delete-{timestamp_ms}",
                            operation_type=OperationType.DELETE_USER,
                            target_service=service,
                            user_data=user_data,
                            metadata={
                                "source": "midpoint",
                                "entityType": data.get("entityType", "User"),
                                "original_uid": request_id,
                                "reason": f"{service.value} role removed",
                            },
                        )
                    )
                target_services_processed.add(service)

            # Update cache with current services
            _user_services_cache[request_id] = current_services.copy()

            # UPDATE for remaining services that are still in roles
            target_services = list(current_services)

        else:
            # Map roles to target services for CREATE
            target_services = []
            for role in roles:
                role_lower = role.lower()
                if role_lower in self.ROLE_TO_SERVICE:
                    target_services.append(self.ROLE_TO_SERVICE[role_lower])

            # Store in cache for future UPDATE/DELETE tracking
            if operation_type == OperationType.CREATE_USER and target_services:
                _user_services_cache[request_id] = set(target_services)
                logger.info(
                    f"CREATE: Cached services for user {request_id}: {[s.value for s in target_services]}"
                )

        for target_service in target_services:
            # Avoid duplicate provisioning to same service
            if target_service in target_services_processed:
                continue
            target_services_processed.add(target_service)

            # Add timestamp to make request_id unique per operation
            timestamp_ms = int(time.time() * 1000)
            messages.append(
                MidPointMessage(
                    request_id=f"{request_id}-{target_service.value.lower()}-{timestamp_ms}",
                    operation_type=operation_type,
                    target_service=target_service,
                    user_data=user_data,
                    metadata={
                        "source": "midpoint",
                        "entityType": data.get("entityType", "User"),
                        "original_uid": request_id,
                        "roles": roles,
                    },
                )
            )

        if not messages:
            logger.warning(
                f"No target services found for roles: {roles}. "
                f"Supported roles: {list(self.ROLE_TO_SERVICE.keys())}"
            )

        return messages

    def _parse_native_format(self, data: dict[str, Any]) -> MidPointMessage:
        """Parse native gateway-iam format message

        Format:
        {
            "request_id": "xxx",
            "operation_type": "CREATE_USER",
            "target_service": "ODOO",
            "user_data": {...}
        }
        """
        # Extract required fields
        request_id = data.get("request_id") or data.get("requestId")
        if not request_id:
            raise MessageParsingError(
                error_message="Missing required field: request_id"
            )

        operation_type_str = data.get("operation_type") or data.get("operationType")
        if not operation_type_str:
            raise MessageParsingError(
                error_message="Missing required field: operation_type"
            )

        target_service_str = data.get("target_service") or data.get("targetService")
        if not target_service_str:
            raise MessageParsingError(
                error_message="Missing required field: target_service"
            )

        user_data_raw = data.get("user_data") or data.get("userData") or {}

        # Parse enums
        try:
            operation_type = OperationType(operation_type_str.upper())
        except ValueError:
            raise MessageParsingError(
                error_message=f"Invalid operation_type: {operation_type_str}"
            )

        try:
            target_service = TargetService(target_service_str.upper())
        except ValueError:
            raise MessageParsingError(
                error_message=f"Invalid target_service: {target_service_str}"
            )

        # Parse user data
        user_data = UserData(
            username=user_data_raw.get("username", ""),
            email=user_data_raw.get("email"),
            first_name=user_data_raw.get("first_name")
            or user_data_raw.get("firstName"),
            last_name=user_data_raw.get("last_name") or user_data_raw.get("lastName"),
            password=user_data_raw.get("password"),
            roles=user_data_raw.get("roles", []),
            attributes=user_data_raw.get("attributes", {}),
        )

        return MidPointMessage(
            request_id=request_id,
            operation_type=operation_type,
            target_service=target_service,
            user_data=user_data,
            metadata=data.get("metadata", {}),
        )
