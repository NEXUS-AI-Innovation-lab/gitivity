"""PostgreSQL connector for user provisioning"""
import logging
from typing import Any

import asyncpg

from app.config.settings import settings
from app.core.connectors.base import ProvisioningConnector
from app.models.domain import ProvisioningResult
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorConnectionError, ProvisioningError

logger = logging.getLogger(__name__)

# Mapping from MidPoint role names to built-in PostgreSQL roles (pg_read_all_data etc.)
# These are PostgreSQL 14+ system roles — not user-created roles.
# "superuser" maps to [] because the SUPERUSER attribute is set via ALTER ROLE, not GRANT.
ROLE_TO_PG_ROLES: dict[str, list[str]] = {
    "read": ["pg_read_all_data"],
    "write": ["pg_write_all_data"],
    "admin": ["pg_read_all_data", "pg_write_all_data"],
    "readonly": ["pg_read_all_data"],
    "readwrite": ["pg_read_all_data", "pg_write_all_data"],
    "superuser": [],  # Will use SUPERUSER attribute instead
    # Default mapping for MidPoint service role
    "postgresql": ["pg_read_all_data", "pg_write_all_data"],
}


class PostgreSQLConnector(ProvisioningConnector):
    """Connector for PostgreSQL user provisioning"""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    @property
    def service_name(self) -> TargetService:
        return TargetService.POSTGRESQL

    async def connect(self) -> None:
        """Establish connection pool to PostgreSQL"""
        try:
            self._pool = await asyncpg.create_pool(
                host=self.target_setting("host", settings.POSTGRESQL_HOST),
                port=self.target_setting("port", settings.POSTGRESQL_PORT),
                user=self.target_setting("user", settings.POSTGRESQL_USER),
                password=self.target_setting("password", settings.POSTGRESQL_PASSWORD),
                database=self.target_setting("database", settings.POSTGRESQL_DATABASE),
                timeout=self.target_setting(
                    "connect_timeout", settings.POSTGRESQL_CONNECT_TIMEOUT
                ),
                min_size=1,
                max_size=5,
            )
            logger.info("PostgreSQL connection pool established")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise ConnectorConnectionError(
                target_service=TargetService.POSTGRESQL,
                error_message=str(e),
            )

    async def disconnect(self) -> None:
        """Close PostgreSQL connection pool"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connection pool closed")

    async def health_check(self) -> bool:
        """Check PostgreSQL connectivity"""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                return True
        except Exception as e:
            logger.warning(f"PostgreSQL health check failed: {e}")
            return False

    async def _role_exists(self, username: str) -> bool:
        """Check if PostgreSQL role already exists"""
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = $1", username
            )
            return result is not None

    async def provision_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Create a PostgreSQL role (user) with specified privileges

        If role already exists, updates their privileges instead.
        """
        if not self._pool:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message="Not connected to PostgreSQL",
                is_retriable=True,
            )

        if not password:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message="Password is required for PostgreSQL user creation",
                is_retriable=False,
            )

        # Get attributes
        attrs = attributes or {}
        can_login = attrs.get("can_login", True)
        is_superuser = "superuser" in (roles or [])
        database = attrs.get("database", "target_db")  # Default database

        # Get postgresqlGrants from attributes (comma-separated privileges like "SELECT, INSERT, UPDATE")
        postgresql_grants = attrs.get("postgresqlGrants")
        # Get postgresqlRole from attributes (e.g., "readonly", "readwrite", "admin")
        postgresql_role = attrs.get("postgresqlRole")

        try:
            # Check if role already exists - if so, update instead
            if await self._role_exists(username):
                logger.info(f"PostgreSQL role {username} already exists, updating...")
                return await self.update_user(
                    username=username,
                    password=password,
                    email=email,
                    roles=roles,
                    attributes=attributes,
                )

            async with self._pool.acquire() as conn:
                # Build CREATE ROLE statement
                role_options = ["LOGIN"] if can_login else ["NOLOGIN"]
                if is_superuser:
                    role_options.append("SUPERUSER")

                # Create role with password
                # Note: asyncpg doesn't support parameterized DDL, so we need to escape
                escaped_username = username.replace('"', '""')
                escaped_password = password.replace("'", "''")

                create_sql = f"""
                    CREATE ROLE "{escaped_username}"
                    WITH {' '.join(role_options)}
                    PASSWORD '{escaped_password}'
                """
                await conn.execute(create_sql)
                logger.info(f"Created PostgreSQL role: {username}")

                # Priority 1: Use postgresqlGrants if provided
                if postgresql_grants:
                    # Check if grants contain profile names (admin, readonly, readwrite)
                    pg_roles = self._resolve_profile_roles(postgresql_grants)
                    if pg_roles:
                        # Profile name detected → use PostgreSQL role membership
                        logger.info(f"Using postgresqlGrants profile: {pg_roles}")
                        for pg_role in pg_roles:
                            try:
                                await conn.execute(
                                    f'GRANT {pg_role} TO "{escaped_username}"'
                                )
                                logger.debug(f"Granted {pg_role} to {username}")
                            except asyncpg.UndefinedObjectError:
                                logger.warning(f"PostgreSQL role {pg_role} does not exist")
                    else:
                        # Direct SQL privileges → grant on tables
                        privileges = self._parse_grants(postgresql_grants)
                        logger.info(f"Using postgresqlGrants attribute: {privileges}")
                        for privilege in privileges:
                            try:
                                await conn.execute(
                                    f'GRANT {privilege} ON ALL TABLES IN SCHEMA public TO "{escaped_username}"'
                                )
                                await conn.execute(
                                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {privilege} ON TABLES TO "{escaped_username}"'
                                )
                                logger.debug(f"Granted {privilege} on tables to {username}")
                            except Exception as e:
                                logger.warning(f"Failed to grant {privilege}: {e}")

                # Priority 2: Use postgresqlRole attribute (e.g., "readonly", "readwrite", "admin")
                elif postgresql_role:
                    pg_roles = self._roles_to_pg_roles([postgresql_role])
                    logger.info(f"Using postgresqlRole attribute '{postgresql_role}': {pg_roles}")
                    for pg_role in pg_roles:
                        try:
                            await conn.execute(
                                f'GRANT "{pg_role}" TO "{escaped_username}"'
                            )
                            logger.debug(f"Granted {pg_role} to {username}")
                        except asyncpg.UndefinedObjectError:
                            logger.warning(f"PostgreSQL role {pg_role} does not exist")

                # Priority 3: Grant predefined roles based on role mapping
                elif roles:
                    pg_roles = self._roles_to_pg_roles(roles)
                    for pg_role in pg_roles:
                        try:
                            await conn.execute(
                                f'GRANT "{pg_role}" TO "{escaped_username}"'
                            )
                            logger.debug(f"Granted {pg_role} to {username}")
                        except asyncpg.UndefinedObjectError:
                            logger.warning(f"PostgreSQL role {pg_role} does not exist")

                # Grant database access
                escaped_db = database.replace('"', '""')
                try:
                    await conn.execute(
                        f'GRANT CONNECT ON DATABASE "{escaped_db}" TO "{escaped_username}"'
                    )
                except Exception as e:
                    logger.warning(f"Failed to grant CONNECT on database: {e}")

                # Add comment with email if provided
                if email:
                    escaped_email = email.replace("'", "''")
                    await conn.execute(
                        f"COMMENT ON ROLE \"{escaped_username}\" IS 'Email: {escaped_email}'"
                    )

            return ProvisioningResult(
                success=True,
                service_user_id=username,
                message=f"Role {username} created successfully",
                details={
                    "username": username,
                    "can_login": can_login,
                    "is_superuser": is_superuser,
                    "roles": roles or [],
                    "database": database,
                },
            )

        except asyncpg.DuplicateObjectError:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message=f"Role {username} already exists",
                error_code="42710",
                is_retriable=False,
            )
        except asyncpg.PostgresError as e:
            logger.error(f"PostgreSQL provisioning error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message=str(e),
                error_code=e.sqlstate if hasattr(e, "sqlstate") else None,
                is_retriable=True,
            )

    async def update_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Update a PostgreSQL role's password and/or privileges

        If role doesn't exist, creates it first.
        """
        if not self._pool:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message="Not connected to PostgreSQL",
                is_retriable=True,
            )

        # Check if role exists - if not, create it first
        if not await self._role_exists(username):
            logger.info(f"PostgreSQL role {username} does not exist, creating first...")
            # Create the role with basic settings, then continue with update
            escaped_username = username.replace('"', '""')
            escaped_password = password.replace("'", "''") if password else "changeme"
            async with self._pool.acquire() as conn:
                await conn.execute(
                    f'CREATE ROLE "{escaped_username}" WITH LOGIN PASSWORD \'{escaped_password}\''
                )
            logger.info(f"Created PostgreSQL role: {username}")

        escaped_username = username.replace('"', '""')

        try:
            async with self._pool.acquire() as conn:
                # Update password if provided
                if password:
                    escaped_password = password.replace("'", "''")
                    await conn.execute(
                        f"ALTER ROLE \"{escaped_username}\" PASSWORD '{escaped_password}'"
                    )
                    logger.info(f"Updated password for PostgreSQL role: {username}")

                # Handle enable/disable (LOGIN/NOLOGIN)
                attrs_enable = attributes or {}
                if "enabled" in attrs_enable:
                    if attrs_enable["enabled"]:
                        await conn.execute(f'ALTER ROLE "{escaped_username}" LOGIN')
                        logger.info(f"Enabled login for PostgreSQL role: {username}")
                    else:
                        await conn.execute(f'ALTER ROLE "{escaped_username}" NOLOGIN')
                        logger.info(f"Disabled login for PostgreSQL role: {username}")

                # Get postgresqlGrants and postgresqlRole from attributes
                attrs = attributes or {}
                postgresql_grants = attrs.get("postgresqlGrants")
                postgresql_role = attrs.get("postgresqlRole")

                # Update privileges if postgresqlGrants, postgresqlRole, or roles provided
                if postgresql_grants is not None or postgresql_role is not None or roles is not None:
                    # Revoke all current role memberships before re-granting to avoid stale privileges
                    current_roles = await conn.fetch(
                        """
                        SELECT r.rolname
                        FROM pg_roles r
                        JOIN pg_auth_members m ON r.oid = m.roleid
                        JOIN pg_roles u ON u.oid = m.member
                        WHERE u.rolname = $1
                        """,
                        username,
                    )

                    for row in current_roles:
                        role_name = row["rolname"]
                        escaped_role = role_name.replace('"', '""')
                        try:
                            await conn.execute(
                                f'REVOKE "{escaped_role}" FROM "{escaped_username}"'
                            )
                        except asyncpg.PostgresError:
                            pass

                    # Priority 1: Use postgresqlGrants if provided
                    if postgresql_grants:
                        # Check if grants contain profile names (admin, readonly, readwrite)
                        pg_roles = self._resolve_profile_roles(postgresql_grants)
                        if pg_roles:
                            # Profile name detected → use PostgreSQL role membership
                            logger.info(f"Updating with postgresqlGrants profile: {pg_roles}")
                            for pg_role in pg_roles:
                                try:
                                    await conn.execute(
                                        f'GRANT {pg_role} TO "{escaped_username}"'
                                    )
                                    logger.debug(f"Granted {pg_role} to {username}")
                                except asyncpg.UndefinedObjectError:
                                    logger.warning(f"PostgreSQL role {pg_role} does not exist")
                        else:
                            # Direct SQL privileges → grant on tables
                            privileges = self._parse_grants(postgresql_grants)
                            logger.info(f"Updating with postgresqlGrants: {privileges}")
                            try:
                                await conn.execute(
                                    f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{escaped_username}"'
                                )
                            except Exception:
                                pass
                            for privilege in privileges:
                                try:
                                    await conn.execute(
                                        f'GRANT {privilege} ON ALL TABLES IN SCHEMA public TO "{escaped_username}"'
                                    )
                                    await conn.execute(
                                        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {privilege} ON TABLES TO "{escaped_username}"'
                                    )
                                    logger.debug(f"Granted {privilege} on tables to {username}")
                                except Exception as e:
                                    logger.warning(f"Failed to grant {privilege}: {e}")

                    # Priority 2: Use postgresqlRole attribute (e.g., "readonly", "readwrite", "admin")
                    elif postgresql_role:
                        pg_roles = self._roles_to_pg_roles([postgresql_role])
                        logger.info(f"Updating with postgresqlRole '{postgresql_role}': {pg_roles}")
                        for pg_role in pg_roles:
                            try:
                                await conn.execute(
                                    f'GRANT "{pg_role}" TO "{escaped_username}"'
                                )
                                logger.debug(f"Granted {pg_role} to {username}")
                            except asyncpg.UndefinedObjectError:
                                logger.warning(f"PostgreSQL role {pg_role} does not exist")

                    # Priority 3: Grant predefined roles based on role mapping
                    elif roles is not None:
                        pg_roles = self._roles_to_pg_roles(roles)
                        for pg_role in pg_roles:
                            try:
                                await conn.execute(
                                    f'GRANT "{pg_role}" TO "{escaped_username}"'
                                )
                            except asyncpg.UndefinedObjectError:
                                logger.warning(f"PostgreSQL role {pg_role} does not exist")

                # Update comment with email if provided
                if email:
                    escaped_email = email.replace("'", "''")
                    await conn.execute(
                        f"COMMENT ON ROLE \"{escaped_username}\" IS 'Email: {escaped_email}'"
                    )

            return ProvisioningResult(
                success=True,
                service_user_id=username,
                message=f"Role {username} updated successfully",
                details={
                    "username": username,
                    "password_updated": password is not None,
                    "roles_updated": roles is not None,
                    "email_updated": email is not None,
                },
            )

        except asyncpg.UndefinedObjectError:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message=f"Role {username} does not exist",
                error_code="42704",
                is_retriable=False,
            )
        except asyncpg.PostgresError as e:
            logger.error(f"PostgreSQL update error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message=str(e),
                is_retriable=True,
            )

    async def delete_user(
        self,
        username: str,
        email: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Delete a PostgreSQL role

        Performs complete cleanup before DROP ROLE:
        1. Revoke all role memberships
        2. Revoke table/sequence privileges
        3. Revoke default privileges
        4. Revoke database-level privileges
        5. Reassign owned objects to admin
        6. Drop owned objects
        7. Drop the role
        """
        if not self._pool:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message="Not connected to PostgreSQL",
                is_retriable=True,
            )

        escaped_username = username.replace('"', '""')

        try:
            async with self._pool.acquire() as conn:
                # Check if role exists
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_roles WHERE rolname = $1", username
                )

                if not exists:
                    return ProvisioningResult(
                        success=True,
                        message=f"Role {username} does not exist (already deleted)",
                        details={"username": username, "already_deleted": True},
                    )

                # Step 1: Revoke all role memberships granted TO this user
                granted_roles = await conn.fetch(
                    """
                    SELECT r.rolname
                    FROM pg_roles r
                    JOIN pg_auth_members m ON r.oid = m.roleid
                    JOIN pg_roles u ON u.oid = m.member
                    WHERE u.rolname = $1
                    """,
                    username,
                )
                for row in granted_roles:
                    role_name = row["rolname"]
                    escaped_role = role_name.replace('"', '""')
                    try:
                        await conn.execute(
                            f'REVOKE "{escaped_role}" FROM "{escaped_username}"'
                        )
                        logger.debug(f"Revoked role {role_name} from {username}")
                    except Exception as e:
                        logger.warning(f"Failed to revoke role {role_name}: {e}")

                # Step 2: Revoke all privileges on tables in public schema
                try:
                    await conn.execute(
                        f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{escaped_username}"'
                    )
                except Exception as e:
                    logger.warning(f"Failed to revoke table privileges: {e}")

                # Step 3: Revoke all privileges on sequences
                try:
                    await conn.execute(
                        f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "{escaped_username}"'
                    )
                except Exception as e:
                    logger.warning(f"Failed to revoke sequence privileges: {e}")

                # Step 4: Revoke default privileges
                try:
                    await conn.execute(
                        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM "{escaped_username}"'
                    )
                except Exception as e:
                    logger.warning(f"Failed to revoke default privileges: {e}")

                # Step 5: Revoke privileges on all databases
                databases = await conn.fetch(
                    "SELECT datname FROM pg_database WHERE datistemplate = false"
                )
                for db_row in databases:
                    db_name = db_row["datname"]
                    escaped_db = db_name.replace('"', '""')
                    try:
                        await conn.execute(
                            f'REVOKE ALL PRIVILEGES ON DATABASE "{escaped_db}" FROM "{escaped_username}"'
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to revoke privileges on database {db_name}: {e}"
                        )

                # Step 6: Reassign owned objects to admin user
                try:
                    admin_user = str(
                        self.target_setting("user", settings.POSTGRESQL_USER)
                    ).replace('"', '""')
                    await conn.execute(
                        f'REASSIGN OWNED BY "{escaped_username}" TO "{admin_user}"'
                    )
                except Exception as e:
                    logger.warning(f"Failed to reassign owned objects: {e}")

                # Step 7: Drop remaining owned objects in current database
                try:
                    await conn.execute(
                        f'DROP OWNED BY "{escaped_username}" CASCADE'
                    )
                except Exception as e:
                    logger.warning(f"Failed to drop owned objects: {e}")

                # Step 8: Drop the role
                await conn.execute(f'DROP ROLE "{escaped_username}"')
                logger.info(f"Deleted PostgreSQL role: {username}")

            return ProvisioningResult(
                success=True,
                message=f"Role {username} deleted successfully",
                details={"username": username},
            )

        except asyncpg.DependentObjectsStillExistError as e:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message=f"Cannot delete role {username}: dependent objects still exist after cleanup: {e}",
                error_code="2BP01",
                is_retriable=False,
            )
        except asyncpg.PostgresError as e:
            logger.error(f"PostgreSQL delete error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.POSTGRESQL,
                error_message=str(e),
                is_retriable=True,
            )

    def _roles_to_pg_roles(self, roles: list[str]) -> list[str]:
        """Convert role names to PostgreSQL roles

        Only uses roles that are explicitly mapped in ROLE_TO_PG_ROLES.
        Unmapped roles (like MidPoint role IDs or other service roles) are ignored.
        """
        pg_roles: set[str] = set()
        for role in roles:
            role_lower = role.lower()
            if role_lower in ROLE_TO_PG_ROLES:
                pg_roles.update(ROLE_TO_PG_ROLES[role_lower])
            else:
                pg_roles.add(role)
        return list(pg_roles)

    # Mapping from profile names to PostgreSQL built-in roles
    PROFILE_TO_PG_ROLES: dict[str, list[str]] = {
        "readonly": ["pg_read_all_data"],
        "readwrite": ["pg_read_all_data", "pg_write_all_data"],
        "admin": ["pg_read_all_data", "pg_write_all_data"],
    }

    def _resolve_profile_roles(self, grants: str | list[str]) -> list[str] | None:
        """Check if grants contain profile names and resolve to PG roles.

        Returns list of pg role names if profile detected, None otherwise.
        """
        items = grants if isinstance(grants, list) else [grants]
        all_roles: set[str] = set()
        for item in items:
            item_lower = item.strip().lower()
            if item_lower in self.PROFILE_TO_PG_ROLES:
                all_roles.update(self.PROFILE_TO_PG_ROLES[item_lower])
            else:
                # Any unrecognized item means the entire grants value is raw SQL, not a profile
                return None  # Fall back to _parse_grants() for direct SQL privileges
        return list(all_roles) if all_roles else None

    # Mapping from profile names to SQL privileges
    PROFILE_TO_PRIVILEGES: dict[str, list[str]] = {
        "readonly": ["SELECT"],
        "readwrite": ["SELECT", "INSERT", "UPDATE"],
        "admin": ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP"],
    }

    def _parse_grants(self, grants: str | list[str]) -> list[str]:
        """Parse postgresqlGrants attribute into list of SQL privileges

        Args:
            grants: Either a comma-separated string ("SELECT, INSERT, UPDATE"),
                   a list of privileges, or a profile name ("readonly", "readwrite", "admin")

        Returns:
            List of uppercase privilege names
        """
        if isinstance(grants, list):
            # Check if list items are profile names
            resolved = []
            for item in grants:
                item_lower = item.strip().lower()
                if item_lower in self.PROFILE_TO_PRIVILEGES:
                    resolved.extend(self.PROFILE_TO_PRIVILEGES[item_lower])
                else:
                    resolved.append(item.strip().upper())
            return list(set(resolved)) if resolved else []

        # Single string: check if it's a profile name
        grants_lower = grants.strip().lower()
        if grants_lower in self.PROFILE_TO_PRIVILEGES:
            return self.PROFILE_TO_PRIVILEGES[grants_lower]

        # Split by comma and clean up
        privileges: list[str] = []
        for grant in grants.split(","):
            grant = grant.strip().upper()
            if grant:
                privileges.append(grant)

        return privileges
