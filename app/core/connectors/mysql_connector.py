"""MySQL connector for user provisioning"""
import logging
from typing import Any

import aiomysql

from app.config.settings import settings
from app.core.connectors.base import ProvisioningConnector
from app.models.domain import ProvisioningResult
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorConnectionError, ProvisioningError

logger = logging.getLogger(__name__)

# Mapping from MidPoint role names to MySQL GRANT privileges.
# These become GRANT <privilege> ON <database>.* TO <user>@<host>.
ROLE_TO_PRIVILEGES: dict[str, list[str]] = {
    "read": ["SELECT"],
    "write": ["SELECT", "INSERT", "UPDATE", "DELETE"],
    "admin": ["ALL PRIVILEGES"],
    "readonly": ["SELECT"],
    "readwrite": ["SELECT", "INSERT", "UPDATE", "DELETE"],
    "dba": ["ALL PRIVILEGES WITH GRANT OPTION"],
    # Fallback: service role name from MidPoint (shadowRef takes priority via mysqlProfiles)
    "mysql": ["SELECT", "INSERT", "UPDATE", "DELETE"],
}


class MySQLConnector(ProvisioningConnector):
    """Connector for MySQL user provisioning"""

    def __init__(self) -> None:
        self._pool: aiomysql.Pool | None = None

    @property
    def service_name(self) -> TargetService:
        return TargetService.MYSQL

    async def connect(self) -> None:
        """Establish connection pool to MySQL"""
        try:
            self._pool = await aiomysql.create_pool(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                db=settings.MYSQL_DATABASE,
                connect_timeout=settings.MYSQL_CONNECT_TIMEOUT,
                autocommit=True,
                minsize=1,
                maxsize=5,
            )
            logger.info("MySQL connection pool established")
        except Exception as e:
            logger.error(f"Failed to connect to MySQL: {e}")
            raise ConnectorConnectionError(
                target_service=TargetService.MYSQL,
                error_message=str(e),
            )

    async def disconnect(self) -> None:
        """Close MySQL connection pool"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQL connection pool closed")

    async def health_check(self) -> bool:
        """Check MySQL connectivity"""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    return True
        except Exception as e:
            logger.warning(f"MySQL health check failed: {e}")
            return False

    async def _user_exists(self, username: str, host: str = "%") -> bool:
        """Check if MySQL user already exists"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT 1 FROM mysql.user WHERE User = %s AND Host = %s",
                    (username, host)
                )
                result = await cursor.fetchone()
                return result is not None

    async def provision_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Create a MySQL user with specified privileges

        If user already exists, updates their privileges instead.
        """
        if not self._pool:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message="Not connected to MySQL",
                is_retriable=True,
            )

        if not password:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message="Password is required for MySQL user creation",
                is_retriable=False,
            )

        # host='%' means the user can connect from any host (MySQL wildcard)
        # database='*' means privileges apply to all databases
        host = attributes.get("host", "%") if attributes else "%"
        database = attributes.get("database", "*") if attributes else "*"

        # Get mysqlGrants from attributes (comma-separated privileges like "SELECT, INSERT, UPDATE")
        mysql_grants = attributes.get("mysqlGrants") if attributes else None

        # Get mysqlProfiles from attributes (multi-value, for shadowRef: e.g., ["admin"])
        mysql_profiles = attributes.get("mysqlProfiles") if attributes else None
        if isinstance(mysql_profiles, str):
            mysql_profiles = [mysql_profiles]

        # Get mysqlRole from attributes (e.g., "readonly", "readwrite", "admin")
        mysql_role = attributes.get("mysqlRole") if attributes else None

        try:
            # Check if user already exists - if so, update instead
            if await self._user_exists(username, host):
                logger.info(f"MySQL user {username}@{host} already exists, updating...")
                return await self.update_user(
                    username=username,
                    password=password,
                    email=email,
                    roles=roles,
                    attributes=attributes,
                )

            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # Create user
                    create_sql = "CREATE USER %s@%s IDENTIFIED BY %s"
                    await cursor.execute(create_sql, (username, host, password))
                    logger.info(f"Created MySQL user: {username}@{host}")

                    # MySQL 8.0: GRANT/REVOKE DDL does not support parameterized user@host
                    # Use direct string interpolation with manual escaping
                    esc_user = username.replace("'", "''")
                    esc_host = host.replace("'", "''")

                    # Priority 1: Use mysqlGrants if provided (direct privileges)
                    if mysql_grants:
                        privileges = self._parse_grants(mysql_grants)
                        logger.info(f"Using mysqlGrants attribute: {privileges}")
                        for privilege in privileges:
                            grant_sql = f"GRANT {privilege} ON {database}.* TO '{esc_user}'@'{esc_host}'"
                            await cursor.execute(grant_sql)
                            logger.debug(f"Granted {privilege} to {username}@{host}")
                    # Priority 2: Use mysqlProfiles (multi-value, shadowRef)
                    elif mysql_profiles:
                        privileges = self._roles_to_privileges(mysql_profiles)
                        logger.info(f"Using mysqlProfiles (shadowRef) '{mysql_profiles}': {privileges}")
                        for privilege in privileges:
                            grant_sql = f"GRANT {privilege} ON {database}.* TO '{esc_user}'@'{esc_host}'"
                            await cursor.execute(grant_sql)
                            logger.debug(f"Granted {privilege} to {username}@{host}")
                    # Priority 3: Use mysqlRole attribute (e.g., "readonly", "readwrite", "admin")
                    elif mysql_role:
                        privileges = self._roles_to_privileges([mysql_role])
                        logger.info(f"Using mysqlRole attribute '{mysql_role}': {privileges}")
                        for privilege in privileges:
                            grant_sql = f"GRANT {privilege} ON {database}.* TO '{esc_user}'@'{esc_host}'"
                            await cursor.execute(grant_sql)
                            logger.debug(f"Granted {privilege} to {username}@{host}")
                    # Priority 4: Use role-based privileges from roles array
                    elif roles:
                        privileges = self._roles_to_privileges(roles)
                        for privilege in privileges:
                            grant_sql = f"GRANT {privilege} ON {database}.* TO '{esc_user}'@'{esc_host}'"
                            await cursor.execute(grant_sql)
                            logger.debug(f"Granted {privilege} to {username}@{host}")

                    # Disable account on create if enabled=false
                    if not (attributes or {}).get("enabled", True):
                        await cursor.execute(
                            "ALTER USER %s@%s ACCOUNT LOCK", (username, host)
                        )
                        logger.info(f"Locked MySQL user on create: {username}@{host}")

                    await cursor.execute("FLUSH PRIVILEGES")

            return ProvisioningResult(
                success=True,
                service_user_id=f"{username}@{host}",
                message=f"User {username}@{host} created successfully",
                details={
                    "username": username,
                    "host": host,
                    "database": database,
                    "roles": roles or [],
                },
            )

        except aiomysql.Error as e:
            error_code = e.args[0] if e.args else None
            error_msg = str(e)

            # Check if user already exists (error 1396)
            is_retriable = error_code not in [1396]

            logger.error(f"MySQL provisioning error: {error_msg}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message=error_msg,
                error_code=str(error_code) if error_code else None,
                is_retriable=is_retriable,
            )

    async def update_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Update a MySQL user's password and/or privileges

        If user doesn't exist, creates it first.
        """
        if not self._pool:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message="Not connected to MySQL",
                is_retriable=True,
            )

        host = attributes.get("host", "%") if attributes else "%"
        database = attributes.get("database", "*") if attributes else "*"

        # Check if user exists - if not, try rename from old_username or create
        if not await self._user_exists(username, host):
            old_username = (attributes or {}).get("old_username")
            if old_username and await self._user_exists(old_username, host):
                # Username changed: rename the existing user
                async with self._pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        esc_old = old_username.replace("'", "''")
                        esc_new = username.replace("'", "''")
                        await cursor.execute(
                            f"RENAME USER '{esc_old}'@'{host}' TO '{esc_new}'@'{host}'"
                        )
                        await cursor.execute("FLUSH PRIVILEGES")
                logger.info(f"Renamed MySQL user: {old_username} → {username}@{host}")
            else:
                logger.info(f"MySQL user {username}@{host} does not exist, creating first...")
                async with self._pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        create_password = password if password else "changeme"
                        await cursor.execute(
                            "CREATE USER %s@%s IDENTIFIED BY %s",
                            (username, host, create_password)
                        )
                        await cursor.execute("FLUSH PRIVILEGES")
                logger.info(f"Created MySQL user: {username}@{host}")

        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # Update password if provided
                    if password:
                        alter_sql = "ALTER USER %s@%s IDENTIFIED BY %s"
                        await cursor.execute(alter_sql, (username, host, password))
                        logger.info(f"Updated password for MySQL user: {username}@{host}")

                    # Handle enable/disable (ACCOUNT LOCK/UNLOCK)
                    attrs = attributes or {}
                    if "enabled" in attrs:
                        if attrs["enabled"]:
                            await cursor.execute(
                                "ALTER USER %s@%s ACCOUNT UNLOCK", (username, host)
                            )
                            logger.info(f"Unlocked MySQL user: {username}@{host}")
                        else:
                            await cursor.execute(
                                "ALTER USER %s@%s ACCOUNT LOCK", (username, host)
                            )
                            logger.info(f"Locked MySQL user: {username}@{host}")

                    # Get mysqlGrants, mysqlProfiles and mysqlRole from attributes
                    mysql_grants = attributes.get("mysqlGrants") if attributes else None
                    mysql_profiles = attributes.get("mysqlProfiles") if attributes else None
                    if isinstance(mysql_profiles, str):
                        mysql_profiles = [mysql_profiles]
                    mysql_role = attributes.get("mysqlRole") if attributes else None

                    # Update privileges if mysqlGrants, mysqlProfiles, mysqlRole, or roles provided
                    if mysql_grants is not None or mysql_profiles is not None or mysql_role is not None or roles is not None:
                        # MySQL 8.0: GRANT/REVOKE DDL does not support parameterized user@host
                        esc_user = username.replace("'", "''")
                        esc_host = host.replace("'", "''")

                        # Revoke all existing privileges first to avoid accumulating stale grants
                        revoke_sql = f"REVOKE ALL PRIVILEGES ON *.* FROM '{esc_user}'@'{esc_host}'"
                        try:
                            await cursor.execute(revoke_sql)
                        except aiomysql.Error:
                            pass  # User might not have any privileges yet

                        # Priority 1: Use mysqlGrants if provided
                        if mysql_grants:
                            privileges = self._parse_grants(mysql_grants)
                            logger.info(f"Updating with mysqlGrants: {privileges}")
                        # Priority 2: Use mysqlProfiles (multi-value, shadowRef)
                        elif mysql_profiles:
                            privileges = self._roles_to_privileges(mysql_profiles)
                            logger.info(f"Updating with mysqlProfiles (shadowRef) '{mysql_profiles}': {privileges}")
                        # Priority 3: Use mysqlRole attribute (e.g., "readonly", "readwrite", "admin")
                        elif mysql_role:
                            privileges = self._roles_to_privileges([mysql_role])
                            logger.info(f"Updating with mysqlRole '{mysql_role}': {privileges}")
                        # Priority 4: Use role-based privileges from roles array
                        elif roles is not None:
                            privileges = self._roles_to_privileges(roles)
                        else:
                            privileges = []

                        for privilege in privileges:
                            grant_sql = f"GRANT {privilege} ON {database}.* TO '{esc_user}'@'{esc_host}'"
                            await cursor.execute(grant_sql)

                    await cursor.execute("FLUSH PRIVILEGES")

            return ProvisioningResult(
                success=True,
                service_user_id=f"{username}@{host}",
                message=f"User {username}@{host} updated successfully",
                details={
                    "username": username,
                    "host": host,
                    "password_updated": password is not None,
                    "roles_updated": roles is not None,
                },
            )

        except aiomysql.Error as e:
            logger.error(f"MySQL update error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message=str(e),
                is_retriable=True,
            )

    async def delete_user(
        self,
        username: str,
        email: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Delete a MySQL user"""
        if not self._pool:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message="Not connected to MySQL",
                is_retriable=True,
            )

        # If username not found, try old_username (in case of rename before delete)
        effective_username = username
        old_username = (attributes or {}).get("old_username")

        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # Find all hosts for this user
                    await cursor.execute(
                        "SELECT Host FROM mysql.user WHERE User = %s", (effective_username,)
                    )
                    hosts = await cursor.fetchall()

                    # Fallback to old_username if not found
                    if not hosts and old_username:
                        await cursor.execute(
                            "SELECT Host FROM mysql.user WHERE User = %s", (old_username,)
                        )
                        hosts = await cursor.fetchall()
                        if hosts:
                            logger.info(f"MySQL delete: using old username {old_username} (current {username} not found)")
                            effective_username = old_username

                    if not hosts:
                        return ProvisioningResult(
                            success=True,
                            message=f"User {effective_username} does not exist (already deleted)",
                            details={"username": effective_username, "already_deleted": True},
                        )

                    # Drop user for each host
                    for (host,) in hosts:
                        drop_sql = "DROP USER %s@%s"
                        await cursor.execute(drop_sql, (effective_username, host))
                        logger.info(f"Deleted MySQL user: {effective_username}@{host}")

                    await cursor.execute("FLUSH PRIVILEGES")

            return ProvisioningResult(
                success=True,
                message=f"User {username} deleted successfully",
                details={
                    "username": username,
                    "hosts_deleted": [h[0] for h in hosts],
                },
            )

        except aiomysql.Error as e:
            logger.error(f"MySQL delete error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.MYSQL,
                error_message=str(e),
                is_retriable=True,
            )

    def _roles_to_privileges(self, roles: list[str]) -> list[str]:
        """Convert role names to MySQL privileges"""
        privileges: set[str] = set()
        for role in roles:
            role_lower = role.lower()
            if role_lower in ROLE_TO_PRIVILEGES:
                privileges.update(ROLE_TO_PRIVILEGES[role_lower])
            else:
                privileges.add(role.upper())
        return list(privileges)

    def _parse_grants(self, grants: str | list[str]) -> list[str]:
        """Parse mysqlGrants attribute into list of SQL privileges

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
                if item_lower in ROLE_TO_PRIVILEGES:
                    resolved.extend(ROLE_TO_PRIVILEGES[item_lower])
                else:
                    resolved.append(item.strip().upper())
            return list(set(resolved)) if resolved else []

        # Single string: check if it's a profile name
        grants_lower = grants.strip().lower()
        if grants_lower in ROLE_TO_PRIVILEGES:
            return ROLE_TO_PRIVILEGES[grants_lower]

        # Split by comma and clean up
        privileges: list[str] = []
        for grant in grants.split(","):
            grant = grant.strip().upper()
            if grant:
                privileges.append(grant)

        return privileges
