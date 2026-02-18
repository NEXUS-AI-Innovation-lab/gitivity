"""Odoo connector for user provisioning via XML-RPC"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from functools import partial

import xmlrpc.client

from app.config.settings import settings
from app.core.connectors.base import ProvisioningConnector
from app.models.domain import ProvisioningResult
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorConnectionError, ProvisioningError

logger = logging.getLogger(__name__)

# Mapping from role names to Odoo group XML IDs
ROLE_TO_ODOO_GROUPS: dict[str, list[str]] = {
    "user": ["base.group_user"],
    "admin": ["base.group_system"],
    "portal": ["base.group_portal"],
    "public": ["base.group_public"],
    "sales": ["sales_team.group_sale_salesman"],
    "sales_manager": ["sales_team.group_sale_manager"],
    "hr": ["hr.group_hr_user"],
    "hr_manager": ["hr.group_hr_manager"],
    "accounting": ["account.group_account_user"],
    "accounting_manager": ["account.group_account_manager"],
}

# Mapping from short language codes to Odoo language codes
LANG_CODE_MAPPING: dict[str, str] = {
    "fr": "fr_FR",
    "en": "en_US",
    "es": "es_ES",
    "de": "de_DE",
    "it": "it_IT",
    "pt": "pt_PT",
    "nl": "nl_NL",
    "pl": "pl_PL",
    "ru": "ru_RU",
    "zh": "zh_CN",
    "ja": "ja_JP",
    "ko": "ko_KR",
    "ar": "ar_001",
}


class OdooConnector(ProvisioningConnector):
    """Connector for Odoo user provisioning via XML-RPC

    Uses synchronous xmlrpc.client wrapped in asyncio executor
    since odoorpc doesn't have native async support.
    """

    def __init__(self) -> None:
        self._common: xmlrpc.client.ServerProxy | None = None
        self._models: xmlrpc.client.ServerProxy | None = None
        self._uid: int | None = None
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._available_langs: set[str] | None = None  # Cache for available languages

    @property
    def service_name(self) -> TargetService:
        return TargetService.ODOO

    async def connect(self) -> None:
        """Establish connection to Odoo via XML-RPC"""
        try:
            url = settings.ODOO_URL.rstrip("/")

            # Create XML-RPC proxies
            self._common = xmlrpc.client.ServerProxy(
                f"{url}/xmlrpc/2/common",
                allow_none=True,
            )
            self._models = xmlrpc.client.ServerProxy(
                f"{url}/xmlrpc/2/object",
                allow_none=True,
            )

            # Authenticate
            loop = asyncio.get_event_loop()
            self._uid = await loop.run_in_executor(
                self._executor,
                partial(
                    self._common.authenticate,
                    settings.ODOO_DB,
                    settings.ODOO_USERNAME,
                    settings.ODOO_PASSWORD,
                    {},
                ),
            )

            if not self._uid:
                raise ConnectorConnectionError(
                    target_service=TargetService.ODOO,
                    error_message="Authentication failed: invalid credentials",
                )

            logger.info(f"Connected to Odoo as uid={self._uid}")

        except xmlrpc.client.Fault as e:
            logger.error(f"Odoo XML-RPC fault: {e}")
            raise ConnectorConnectionError(
                target_service=TargetService.ODOO,
                error_message=f"XML-RPC fault: {e.faultString}",
            )
        except Exception as e:
            logger.error(f"Failed to connect to Odoo: {e}")
            raise ConnectorConnectionError(
                target_service=TargetService.ODOO,
                error_message=str(e),
            )

    async def disconnect(self) -> None:
        """Close Odoo connection"""
        self._common = None
        self._models = None
        self._uid = None
        self._executor.shutdown(wait=False)
        self._executor = ThreadPoolExecutor(max_workers=3)
        logger.info("Odoo connection closed")

    async def health_check(self) -> bool:
        """Check Odoo connectivity"""
        if not self._common:
            return False
        try:
            loop = asyncio.get_event_loop()
            version = await loop.run_in_executor(
                self._executor,
                self._common.version,
            )
            return version is not None
        except Exception as e:
            logger.warning(f"Odoo health check failed: {e}")
            return False

    async def _get_available_languages(self) -> set[str]:
        """Get list of installed languages in Odoo"""
        if self._available_langs is not None:
            return self._available_langs

        try:
            # Query res.lang for active languages
            # Use search + read instead of search_read for better compatibility
            lang_ids = await self._execute(
                "res.lang",
                "search",
                [["active", "=", True]],
            )
            if lang_ids:
                langs = await self._execute(
                    "res.lang",
                    "read",
                    lang_ids,
                    ["code"],
                )
                self._available_langs = {lang["code"] for lang in langs}
            else:
                self._available_langs = set()
            logger.info(f"Available Odoo languages: {self._available_langs}")
            return self._available_langs
        except Exception as e:
            logger.warning(f"Failed to get available languages: {e}")
            return set()

    async def _convert_language_code(self, lang_value: str) -> str | None:
        """Convert language code to Odoo format and validate it's available"""
        if not lang_value:
            return None

        # Try to convert short code to full code
        if len(lang_value) == 2:
            lang_value = LANG_CODE_MAPPING.get(lang_value.lower(), f"{lang_value}_{lang_value.upper()}")

        # Check if language is available in Odoo
        available = await self._get_available_languages()
        if lang_value in available:
            return lang_value

        # Try base language (fr_FR -> fr_*)
        base_lang = lang_value.split("_")[0]
        for avail_lang in available:
            if avail_lang.startswith(base_lang + "_"):
                logger.info(f"Language {lang_value} not found, using {avail_lang}")
                return avail_lang

        logger.warning(f"Language {lang_value} not available in Odoo, skipping")
        return None

    async def _execute(
        self, model: str, method: str, *args, **kwargs
    ) -> Any:
        """Execute an Odoo model method asynchronously"""
        if not self._models or not self._uid:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message="Not connected to Odoo",
                is_retriable=True,
            )

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(
                self._models.execute_kw,
                settings.ODOO_DB,
                self._uid,
                settings.ODOO_PASSWORD,
                model,
                method,
                args,
                kwargs,
            ),
        )

    async def provision_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Create an Odoo user (res.users) and optionally an employee (hr.employee)

        Controlled by attributes:
        - odooCreateUser: bool (default True) - create res.users record
        - odooCreateEmployee: bool (default False) - create hr.employee record
        """
        logger.info(f"DEBUG provision_user called with: username={username}, email={email}, roles={roles}")
        attrs = attributes or {}

        # Check which models to provision to (default: user only)
        create_user = attrs.get("odooCreateUser", True)
        create_employee = attrs.get("odooCreateEmployee", False)

        # Convert string "true"/"false" to boolean if needed
        if isinstance(create_user, str):
            create_user = create_user.lower() == "true"
        if isinstance(create_employee, str):
            create_employee = create_employee.lower() == "true"

        if not create_user and not create_employee:
            logger.warning("Both odooCreateUser and odooCreateEmployee are false - nothing to create")
            return ProvisioningResult(
                success=True,
                message="No Odoo records created (odooCreateUser=False, odooCreateEmployee=False)",
                details={"username": username, "skipped": True},
            )

        # Build display name from MidPoint attributes
        display_name = (
            attrs.get("fullName")
            or attrs.get("name")
            or f"{attrs.get('firstName', '')} {attrs.get('lastName', '')}".strip()
            or username
        )

        # Use email as login (unique identifier) if available, otherwise fallback to username
        login = email if email else username

        # Build user values
        user_vals: dict[str, Any] = {
            "login": login,
            "name": display_name,
            "email": email,
        }

        if password:
            user_vals["password"] = password

        # Map MidPoint attributes to Odoo fields
        # MidPoint -> Odoo mapping
        midpoint_to_odoo = {
            "telephoneNumber": "phone",
            "phone": "phone",
            "mobile": "mobile",
            "timezone": "tz",
            "tz": "tz",
            "company_id": "company_id",
            "title": "function",  # Job title in Odoo
        }

        for midpoint_attr, odoo_field in midpoint_to_odoo.items():
            if midpoint_attr in attrs and attrs[midpoint_attr] is not None:
                user_vals[odoo_field] = attrs[midpoint_attr]

        # Never deactivate during creation - new users must start active
        # If MidPoint sends enabled=False, ignore it for CREATE (user can be disabled via UPDATE later)
        deactivate_after_create = False

        user_id = None
        employee_id = None

        try:
            # Handle language code conversion (fr -> fr_FR) with validation
            lang_value = attrs.get("locale") or attrs.get("preferredLanguage") or attrs.get("lang")
            if lang_value:
                validated_lang = await self._convert_language_code(lang_value)
                if validated_lang:
                    user_vals["lang"] = validated_lang

            # Create res.users if requested
            if create_user:
                # Check if user already exists - search including archived users
                midpoint_uid = attrs.get("midpoint_uid")
                existing_user_id = None
                _ctx = {"context": {"active_test": False}}

                if midpoint_uid:
                    partner_ids = await self._execute(
                        "res.partner", "search", [["ref", "=", midpoint_uid]], **_ctx,
                    )
                    if partner_ids:
                        found_users = await self._execute(
                            "res.users", "search", [["partner_id", "=", partner_ids[0]]], **_ctx,
                        )
                        if found_users:
                            existing_user_id = found_users[0]
                            logger.info(f"Found existing Odoo user by midpoint_uid {midpoint_uid} (id={existing_user_id})")

                # Fallback: check by login (email)
                if not existing_user_id:
                    existing = await self._execute(
                        "res.users", "search", [["login", "=", login]], **_ctx,
                    )
                    if existing:
                        existing_user_id = existing[0]
                        logger.info(f"Found existing Odoo user by login {login} (id={existing_user_id})")

                # Fallback: check by username as login
                if not existing_user_id and username != login:
                    existing = await self._execute(
                        "res.users", "search", [["login", "=", username]], **_ctx,
                    )
                    if existing:
                        existing_user_id = existing[0]
                        logger.info(f"Found existing Odoo user by username {username} (id={existing_user_id})")

                # If user already exists, UPDATE instead of creating a duplicate
                if existing_user_id:
                    logger.info(f"User already exists (id={existing_user_id}), updating instead of creating")
                    update_vals = {"name": display_name, "active": True}  # Reactivate if archived
                    if email:
                        update_vals["login"] = login
                        update_vals["email"] = email
                    if password:
                        update_vals["password"] = password
                    for midpoint_attr, odoo_field in midpoint_to_odoo.items():
                        if midpoint_attr in attrs and attrs[midpoint_attr] is not None:
                            update_vals[odoo_field] = attrs[midpoint_attr]
                    await self._execute("res.users", "write", [existing_user_id], update_vals)
                    user_id = existing_user_id
                    logger.info(f"Updated existing Odoo user: {username} (id={user_id})")

                    if create_employee:
                        employee_id = await self._create_employee(
                            username=username, display_name=display_name,
                            email=email, user_id=user_id, attrs=attrs,
                        )

                    return ProvisioningResult(
                        success=True,
                        service_user_id=str(user_id),
                        message=f"User {username} already existed, updated successfully",
                        details={"user_id": user_id, "action": "updated"},
                    )

                # Get group IDs for roles (from role mapping)
                all_group_ids: list[int] = []
                if roles:
                    role_group_ids = await self._get_group_ids(roles)
                    all_group_ids.extend(role_group_ids)

                # Get group IDs for odooGroups (by name search)
                odoo_groups = attrs.get("odooGroups", [])
                if odoo_groups:
                    named_group_ids = await self._get_group_ids_by_name(odoo_groups)
                    all_group_ids.extend(named_group_ids)

                # Assign all groups to user
                if all_group_ids:
                    # Remove duplicates
                    all_group_ids = list(set(all_group_ids))
                    user_vals["groups_id"] = [(6, 0, all_group_ids)]
                    logger.info(f"Assigning groups to user: {all_group_ids}")

                # Create user
                logger.info(f"Creating Odoo user with values: {user_vals}")
                user_id = await self._execute("res.users", "create", user_vals)
                logger.info(f"Created Odoo user: {username} (id={user_id})")

                # Store MidPoint UID in the partner's ref field for future lookups
                midpoint_uid = attrs.get("midpoint_uid")
                if midpoint_uid:
                    user_data = await self._execute("res.users", "read", [user_id], ["partner_id"])
                    if user_data and user_data[0].get("partner_id"):
                        partner_id = user_data[0]["partner_id"][0]
                        await self._execute("res.partner", "write", [partner_id], {"ref": midpoint_uid})
                        logger.info(f"Stored MidPoint UID {midpoint_uid} in partner ref (partner_id={partner_id})")

                # Deactivate user after creation if enabled=False
                if deactivate_after_create:
                    await self._execute("res.users", "write", [user_id], {"active": False})
                    logger.info(f"Deactivated Odoo user: {username} (id={user_id})")

            # Create hr.employee if requested
            if create_employee:
                employee_id = await self._create_employee(
                    username=username,
                    display_name=display_name,
                    email=email,
                    user_id=user_id,
                    attrs=attrs,
                )

            return ProvisioningResult(
                success=True,
                service_user_id=str(user_id) if user_id else str(employee_id),
                message=f"User {username} created successfully",
                details={
                    "user_id": user_id,
                    "employee_id": employee_id,
                    "login": username,
                    "email": email,
                    "roles": roles or [],
                    "created_user": create_user and user_id is not None,
                    "created_employee": create_employee and employee_id is not None,
                },
            )

        except ProvisioningError:
            raise
        except xmlrpc.client.Fault as e:
            logger.error(f"Odoo provisioning fault: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message=e.faultString,
                is_retriable=True,
            )
        except Exception as e:
            logger.error(f"Odoo provisioning error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message=str(e),
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
        """Update an Odoo user"""
        attrs = attributes or {}

        # Build display name from MidPoint attributes
        display_name = (
            attrs.get("fullName")
            or attrs.get("name")
            or f"{attrs.get('firstName', '')} {attrs.get('lastName', '')}".strip()
        )

        # Get MidPoint UID for searching
        midpoint_uid = attrs.get("midpoint_uid")

        try:
            # Try multiple search strategies to find the user
            user_ids = None
            search_method = None

            # Search including archived users (active=False) to avoid duplicates
            _ctx = {"context": {"active_test": False}}

            # Strategy 1: Search by MidPoint UID (stored in partner's ref field) - MOST RELIABLE
            if midpoint_uid:
                # Find partner with this ref, then get the user
                partner_ids = await self._execute(
                    "res.partner",
                    "search",
                    [["ref", "=", midpoint_uid]],
                    **_ctx,
                )
                if partner_ids:
                    # Find user linked to this partner
                    user_ids = await self._execute(
                        "res.users",
                        "search",
                        [["partner_id", "=", partner_ids[0]]],
                        **_ctx,
                    )
                    if user_ids:
                        search_method = f"midpoint_uid: {midpoint_uid}"

            # Strategy 2: Search by email (login)
            if not user_ids and email:
                user_ids = await self._execute(
                    "res.users",
                    "search",
                    [["login", "=", email]],
                    **_ctx,
                )
                if user_ids:
                    search_method = f"email/login: {email}"

            # Strategy 3: Search by display name (fullName)
            if not user_ids and display_name:
                user_ids = await self._execute(
                    "res.users",
                    "search",
                    [["name", "=", display_name]],
                    **_ctx,
                )
                if user_ids:
                    search_method = f"name: {display_name}"

            # Strategy 4: Search by username in login field
            if not user_ids and username:
                user_ids = await self._execute(
                    "res.users",
                    "search",
                    [["login", "=", username]],
                    **_ctx,
                )
                if user_ids:
                    search_method = f"username: {username}"

            if not user_ids:
                # User doesn't exist in Odoo - create it first (upsert behavior)
                logger.info(f"Odoo user {username} not found, creating first...")
                return await self.provision_user(
                    username=username,
                    password=password,
                    email=email,
                    roles=roles,
                    attributes=attributes,
                )

            user_id = user_ids[0]
            logger.info(f"Found Odoo user (id={user_id}) via {search_method}")

            # Build update values
            update_vals: dict[str, Any] = {}

            if password:
                update_vals["password"] = password

            # Update email and login (login = email for uniqueness)
            if email:
                update_vals["email"] = email
                update_vals["login"] = email  # Keep login in sync with email

            if display_name:
                update_vals["name"] = display_name

            # Map MidPoint attributes to Odoo fields
            midpoint_to_odoo = {
                "telephoneNumber": "phone",
                "phone": "phone",
                "mobile": "mobile",
                "timezone": "tz",
                "tz": "tz",
                "company_id": "company_id",
                "title": "function",
            }

            for midpoint_attr, odoo_field in midpoint_to_odoo.items():
                if midpoint_attr in attrs and attrs[midpoint_attr] is not None:
                    update_vals[odoo_field] = attrs[midpoint_attr]

            # Handle language code conversion (fr -> fr_FR) with validation
            lang_value = attrs.get("locale") or attrs.get("preferredLanguage") or attrs.get("lang")
            if lang_value:
                validated_lang = await self._convert_language_code(lang_value)
                if validated_lang:
                    update_vals["lang"] = validated_lang

            # Handle enabled/active status
            if "enabled" in attrs:
                update_vals["active"] = bool(attrs["enabled"])

            # Update groups (from roles and odooGroups)
            all_group_ids: list[int] = []

            # Get group IDs for roles (from role mapping)
            if roles is not None:
                role_group_ids = await self._get_group_ids(roles)
                all_group_ids.extend(role_group_ids)

            # Get group IDs for odooGroups (by name search)
            odoo_groups = attrs.get("odooGroups", [])
            if odoo_groups:
                named_group_ids = await self._get_group_ids_by_name(odoo_groups)
                all_group_ids.extend(named_group_ids)

            # Assign all groups if any were found
            if all_group_ids:
                all_group_ids = list(set(all_group_ids))  # Remove duplicates
                update_vals["groups_id"] = [(6, 0, all_group_ids)]
                logger.info(f"Updating user groups to: {all_group_ids}")

            if update_vals:
                await self._execute("res.users", "write", [user_id], update_vals)
                logger.info(f"Updated Odoo user: {username} (id={user_id})")

            # Also update linked employee if exists
            employee_id = await self._update_employee(
                user_id=user_id,
                display_name=display_name,
                email=email,
                attrs=attrs,
            )

            return ProvisioningResult(
                success=True,
                service_user_id=str(user_id),
                message=f"User {username} updated successfully",
                details={
                    "user_id": user_id,
                    "employee_id": employee_id,
                    "login": username,
                    "fields_updated": list(update_vals.keys()),
                },
            )

        except ProvisioningError:
            raise
        except xmlrpc.client.Fault as e:
            logger.error(f"Odoo update fault: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message=e.faultString,
                is_retriable=True,
            )
        except Exception as e:
            logger.error(f"Odoo update error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message=str(e),
                is_retriable=True,
            )

    async def delete_user(
        self,
        username: str,
        email: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Delete (archive) an Odoo user

        Note: Odoo typically archives users instead of deleting them
        to preserve data integrity.
        """
        attrs = attributes or {}
        midpoint_uid = attrs.get("midpoint_uid")

        try:
            # Try multiple search strategies to find the user (including archived)
            user_ids = None
            search_method = None
            _ctx = {"context": {"active_test": False}}

            # Strategy 1: Search by MidPoint UID (stored in partner's ref field)
            if midpoint_uid:
                partner_ids = await self._execute(
                    "res.partner",
                    "search",
                    [["ref", "=", midpoint_uid]],
                    **_ctx,
                )
                if partner_ids:
                    user_ids = await self._execute(
                        "res.users",
                        "search",
                        [["partner_id", "=", partner_ids[0]]],
                        **_ctx,
                    )
                    if user_ids:
                        search_method = f"midpoint_uid: {midpoint_uid}"

            # Strategy 2: Search by email (login)
            if not user_ids and email:
                user_ids = await self._execute(
                    "res.users",
                    "search",
                    [["login", "=", email]],
                    **_ctx,
                )
                if user_ids:
                    search_method = f"email: {email}"

            # Strategy 3: Search by username
            if not user_ids and username:
                user_ids = await self._execute(
                    "res.users",
                    "search",
                    [["login", "=", username]],
                )
                if user_ids:
                    search_method = f"username: {username}"

            if not user_ids:
                return ProvisioningResult(
                    success=True,
                    message=f"User not found (already deleted). Tried midpoint_uid={midpoint_uid}, email={email}, username={username}",
                    details={"already_deleted": True},
                )

            user_id = user_ids[0]
            logger.info(f"Found Odoo user to delete (id={user_id}) via {search_method}")

            # Archive linked employee first (if exists)
            employee_archived = await self._archive_employee(user_id)

            # Archive the user (set active=False)
            await self._execute("res.users", "write", [user_id], {"active": False})
            logger.info(f"Archived Odoo user: {username} (id={user_id})")

            return ProvisioningResult(
                success=True,
                service_user_id=str(user_id),
                message=f"User {username} archived successfully",
                details={
                    "user_id": user_id,
                    "login": username,
                    "action": "archived",
                    "employee_archived": employee_archived,
                },
            )

        except xmlrpc.client.Fault as e:
            logger.error(f"Odoo delete fault: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message=e.faultString,
                is_retriable=True,
            )
        except Exception as e:
            logger.error(f"Odoo delete error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.ODOO,
                error_message=str(e),
                is_retriable=True,
            )

    async def _get_group_ids(self, roles: list[str]) -> list[int]:
        """Convert role names to Odoo group IDs"""
        group_xml_ids: set[str] = set()

        for role in roles:
            role_lower = role.lower()
            if role_lower in ROLE_TO_ODOO_GROUPS:
                group_xml_ids.update(ROLE_TO_ODOO_GROUPS[role_lower])
            # Skip unknown roles - don't try to resolve them as XML IDs

        group_ids: list[int] = []
        for xml_id in group_xml_ids:
            try:
                # Parse module.name format
                parts = xml_id.split(".")
                if len(parts) == 2:
                    module, name = parts
                else:
                    module, name = "base", xml_id

                # Resolve XML ID to database ID using search + read
                data_ids = await self._execute(
                    "ir.model.data",
                    "search",
                    [["module", "=", module], ["name", "=", name]],
                )
                if data_ids:
                    data_records = await self._execute(
                        "ir.model.data",
                        "read",
                        data_ids[:1],
                        ["res_id"],
                    )
                    if data_records:
                        group_ids.append(data_records[0]["res_id"])
                else:
                    logger.warning(f"Odoo group not found: {xml_id}")
            except Exception as e:
                logger.warning(f"Failed to resolve Odoo group {xml_id}: {e}")

        return group_ids

    async def _get_group_ids_by_name(self, group_names: list[str]) -> list[int]:
        """Search for Odoo groups by their display name

        Args:
            group_names: List of group names to search for (e.g., ["Administrateur", "User"])

        Returns:
            List of group IDs found
        """
        group_ids: list[int] = []

        for name in group_names:
            if not name:
                continue
            try:
                # Search for group by name in res_groups
                found_ids = await self._execute(
                    "res.groups",
                    "search",
                    [["name", "ilike", name]],
                )
                if found_ids:
                    group_ids.append(found_ids[0])
                    logger.info(f"Found Odoo group '{name}' with id={found_ids[0]}")
                else:
                    logger.warning(f"Odoo group '{name}' not found - skipping")
            except Exception as e:
                logger.warning(f"Failed to search for Odoo group '{name}': {e}")

        return group_ids

    async def _create_employee(
        self,
        username: str,
        display_name: str,
        email: str | None,
        user_id: int | None,
        attrs: dict[str, Any],
    ) -> int | None:
        """Create an hr.employee record

        Args:
            username: The username
            display_name: The display name for the employee
            email: The employee email
            user_id: The res.users ID to link (optional)
            attrs: Additional attributes from MidPoint

        Returns:
            The employee ID if created, None otherwise
        """
        try:
            # Build employee values
            employee_vals: dict[str, Any] = {
                "name": display_name,
            }

            # Link to user if we created one
            if user_id:
                employee_vals["user_id"] = user_id

            # Map MidPoint attributes to hr.employee fields
            if email:
                employee_vals["work_email"] = email

            # Phone numbers
            if attrs.get("telephoneNumber"):
                employee_vals["work_phone"] = attrs["telephoneNumber"]
            if attrs.get("mobile"):
                employee_vals["mobile_phone"] = attrs["mobile"]

            # Job title
            if attrs.get("title"):
                employee_vals["job_title"] = attrs["title"]

            # Department (by name search)
            department_name = attrs.get("organizationalUnit") or attrs.get("department")
            if department_name:
                dept_ids = await self._execute(
                    "hr.department",
                    "search",
                    [["name", "ilike", department_name]],
                )
                if dept_ids:
                    employee_vals["department_id"] = dept_ids[0]

            # Work location
            if attrs.get("locality"):
                employee_vals["work_location_id"] = False  # Reset first
                # Try to find or just set as string in notes
                employee_vals["notes"] = f"Location: {attrs['locality']}"

            # Employee identification
            employee_id_attr = attrs.get("employeeNumber") or attrs.get("personalNumber")
            if employee_id_attr:
                employee_vals["identification_id"] = employee_id_attr

            logger.info(f"Creating Odoo employee with values: {employee_vals}")
            employee_id = await self._execute("hr.employee", "create", employee_vals)
            logger.info(f"Created Odoo employee: {display_name} (id={employee_id})")

            return employee_id

        except Exception as e:
            logger.error(f"Failed to create hr.employee for {username}: {e}")
            # Don't fail the whole operation if employee creation fails
            return None

    async def _update_employee(
        self,
        user_id: int,
        display_name: str | None,
        email: str | None,
        attrs: dict[str, Any],
    ) -> int | None:
        """Update an hr.employee record linked to a user

        Args:
            user_id: The res.users ID to find the employee
            display_name: The new display name (if changed)
            email: The new email (if changed)
            attrs: Additional attributes from MidPoint

        Returns:
            The employee ID if updated, None if not found
        """
        try:
            # Find employee linked to this user
            employee_ids = await self._execute(
                "hr.employee",
                "search",
                [["user_id", "=", user_id]],
            )

            if not employee_ids:
                logger.info(f"No employee linked to user {user_id}")
                return None

            employee_id = employee_ids[0]
            update_vals: dict[str, Any] = {}

            if display_name:
                update_vals["name"] = display_name

            if email:
                update_vals["work_email"] = email

            if attrs.get("telephoneNumber"):
                update_vals["work_phone"] = attrs["telephoneNumber"]
            if attrs.get("mobile"):
                update_vals["mobile_phone"] = attrs["mobile"]
            if attrs.get("title"):
                update_vals["job_title"] = attrs["title"]

            if update_vals:
                await self._execute("hr.employee", "write", [employee_id], update_vals)
                logger.info(f"Updated Odoo employee: {employee_id}")

            return employee_id

        except Exception as e:
            logger.error(f"Failed to update hr.employee for user {user_id}: {e}")
            return None

    async def _archive_employee(self, user_id: int) -> bool:
        """Archive an hr.employee record linked to a user

        Args:
            user_id: The res.users ID to find the employee

        Returns:
            True if archived, False if not found
        """
        try:
            # Find employee linked to this user
            employee_ids = await self._execute(
                "hr.employee",
                "search",
                [["user_id", "=", user_id]],
            )

            if not employee_ids:
                return False

            # Archive the employee
            await self._execute("hr.employee", "write", employee_ids, {"active": False})
            logger.info(f"Archived Odoo employee(s): {employee_ids}")
            return True

        except Exception as e:
            logger.error(f"Failed to archive hr.employee for user {user_id}: {e}")
            return False
