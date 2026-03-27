"""LDAP connector for user provisioning"""
import logging
from typing import Any

from ldap3 import (
    Server,
    Connection,
    ALL,
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
    SUBTREE,
)
from ldap3.core.exceptions import LDAPException

from app.config.settings import settings
from app.core.connectors.base import ProvisioningConnector
from app.models.domain import ProvisioningResult
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorConnectionError, ProvisioningError

logger = logging.getLogger(__name__)


class LDAPConnector(ProvisioningConnector):
    """Connector for LDAP user provisioning

    Creates users in ou=Users under the base DN, then adds them as members
    to LDAP groups specified in ldapGroups attribute.
    """

    def __init__(self) -> None:
        self._server: Server | None = None
        self._connection: Connection | None = None

    @property
    def service_name(self) -> TargetService:
        return TargetService.LDAP

    async def connect(self) -> None:
        """Establish connection to LDAP server"""
        try:
            # Build LDAP URL
            protocol = "ldaps" if settings.LDAP_USE_SSL else "ldap"
            url = f"{protocol}://{settings.LDAP_HOST}:{settings.LDAP_PORT}"

            logger.info(f"Connecting to LDAP server: {url}")

            self._server = Server(
                settings.LDAP_HOST,
                port=settings.LDAP_PORT,
                use_ssl=settings.LDAP_USE_SSL,
                get_info=ALL,
                connect_timeout=settings.LDAP_CONNECT_TIMEOUT,
            )

            self._connection = Connection(
                self._server,
                user=settings.LDAP_BIND_DN,
                password=settings.LDAP_BIND_PASSWORD,
                auto_bind=True,
            )

            logger.info(f"Connected to LDAP server as {settings.LDAP_BIND_DN}")

        except LDAPException as e:
            logger.error(f"Failed to connect to LDAP: {e}")
            raise ConnectorConnectionError(
                target_service=TargetService.LDAP,
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"Unexpected LDAP connection error: {e}")
            raise ConnectorConnectionError(
                target_service=TargetService.LDAP,
                error_message=str(e),
            )

    async def disconnect(self) -> None:
        """Close LDAP connection"""
        if self._connection:
            self._connection.unbind()
            self._connection = None
        self._server = None
        logger.info("LDAP connection closed")

    async def health_check(self) -> bool:
        """Check LDAP connectivity"""
        if not self._connection:
            return False
        try:
            return self._connection.bound
        except Exception as e:
            logger.warning(f"LDAP health check failed: {e}")
            return False

    def _get_user_dn(self, username: str) -> str:
        """Get the user DN in the users OU"""
        return f"uid={username},ou=Users,{settings.LDAP_BASE_DN}"

    def _extract_base_dn_from_group(self, group_dn: str) -> str:
        """Extract base DN from a group DN

        Example: cn=Users,ou=Groups,dc=example,dc=com -> dc=example,dc=com

        The base DN is inferred from the group DN so we don't need a separate
        config per group. Falls back to LDAP_BASE_DN if no dc= component found.
        """
        parts = group_dn.split(",")
        # Collect all dc= components — they form the root of the directory tree
        dc_parts = [p for p in parts if p.lower().startswith("dc=")]
        if dc_parts:
            return ",".join(dc_parts)
        return settings.LDAP_BASE_DN

    async def _ensure_users_ou_exists(self, base_dn: str) -> None:
        """Ensure the ou=Users container exists"""
        users_ou_dn = f"ou=Users,{base_dn}"

        # Check if ou=Users exists
        self._connection.search(
            search_base=base_dn,
            search_filter="(ou=Users)",
            search_scope=SUBTREE,
        )

        if not self._connection.entries:
            # Create ou=Users
            logger.info(f"Creating ou=Users under {base_dn}")
            self._connection.add(
                users_ou_dn,
                attributes={
                    "objectClass": ["organizationalUnit", "top"],
                    "ou": "Users",
                },
            )

    async def _find_user_by_employee_number(self, employee_number: str, base_dn: str) -> str | None:
        """Find existing user by employeeNumber and return their DN"""
        if not employee_number:
            return None

        self._connection.search(
            search_base=f"ou=Users,{base_dn}",
            search_filter=f"(employeeNumber={employee_number})",
            search_scope=SUBTREE,
        )

        if self._connection.entries:
            return self._connection.entries[0].entry_dn
        return None

    async def provision_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Create user in LDAP and add to groups

        1. Checks if user exists by employeeNumber (MidPoint UID)
        2. If exists: updates the user
        3. If not: creates user in ou=Users,{base_dn}
        4. Adds user DN to each group's 'member' or 'uniqueMember' attribute
        """
        if not self._connection:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
                error_message="Not connected to LDAP",
                is_retriable=True,
            )

        attrs = attributes or {}

        # LDAP ne supporte pas le disable — supprimer si enabled=false
        if not attrs.get("enabled", True):
            logger.info(f"LDAP: enabled=false for {username}, deleting instead of disabling")
            return await self.delete_user(username=username, attributes=attributes)

        ldap_groups = attrs.get("ldapGroups", [])
        if isinstance(ldap_groups, str):
            ldap_groups = [ldap_groups]
        midpoint_uid = attrs.get("midpoint_uid", "")
        employee_number = attrs.get("personalNumber", "") or midpoint_uid

        # Build user attributes
        first_name = attrs.get("firstName") or username
        last_name = attrs.get("lastName") or ""
        full_name = attrs.get("fullName") or f"{first_name} {last_name}".strip() or username

        # Determine base DN from first group or use settings
        if ldap_groups:
            base_dn = self._extract_base_dn_from_group(ldap_groups[0])
        else:
            base_dn = settings.LDAP_BASE_DN

        # User will be created in ou=Users,{base_dn}
        user_dn = f"uid={username},ou=Users,{base_dn}"

        try:
            # Ensure ou=Users exists
            await self._ensure_users_ou_exists(base_dn)

            # FIRST: Check if user exists by employeeNumber (unique identifier)
            existing_dn = await self._find_user_by_employee_number(employee_number, base_dn)

            if existing_dn:
                # User exists - update instead of create
                logger.info(f"User with employeeNumber={employee_number} found at {existing_dn}, updating...")
                return await self.update_user(
                    username=username,
                    password=password,
                    email=email,
                    roles=roles,
                    attributes=attributes,
                )

            # SECOND: Check if user exists by uid (username)
            self._connection.search(
                search_base=f"ou=Users,{base_dn}",
                search_filter=f"(uid={username})",
                search_scope=SUBTREE,
            )

            user_created = False
            if not self._connection.entries:
                # Build user attributes
                user_attributes = {
                    "objectClass": ["inetOrgPerson", "organizationalPerson", "person", "top"],
                    "cn": full_name or username,
                    "sn": last_name or username,
                    "uid": username,
                }

                # Add optional attributes
                if first_name:
                    user_attributes["givenName"] = first_name
                if email:
                    user_attributes["mail"] = email
                if password:
                    user_attributes["userPassword"] = password
                if attrs.get("telephoneNumber"):
                    user_attributes["telephoneNumber"] = attrs["telephoneNumber"]
                if attrs.get("title"):
                    user_attributes["title"] = attrs["title"]
                if attrs.get("description"):
                    user_attributes["description"] = attrs["description"]
                if midpoint_uid:
                    user_attributes["employeeNumber"] = midpoint_uid

                # Create the user entry
                logger.info(f"Creating LDAP user: {user_dn}")
                success = self._connection.add(user_dn, attributes=user_attributes)

                if not success:
                    error_msg = str(self._connection.result)
                    logger.error(f"Failed to create LDAP user: {error_msg}")
                    raise ProvisioningError(
                        operation_id="",
                        target_service=TargetService.LDAP,
                        error_message=f"Failed to create LDAP user: {error_msg}",
                        is_retriable=True,
                    )

                logger.info(f"Created LDAP user {username} at {user_dn}")
                user_created = True
            else:
                logger.info(f"User {username} already exists at {user_dn}")

            # Add user to groups
            groups_added = []
            errors = []

            for group_dn in ldap_groups:
                if not group_dn:
                    continue

                try:
                    # Check if group exists
                    self._connection.search(
                        search_base=group_dn,
                        search_filter="(objectClass=*)",
                        attributes=["objectClass", "member", "uniqueMember"],
                    )

                    if not self._connection.entries:
                        logger.warning(f"Group {group_dn} does not exist - skipping")
                        errors.append(f"{group_dn}: group does not exist")
                        continue

                    group_entry = self._connection.entries[0]
                    object_classes = [oc.lower() for oc in group_entry.objectClass.values]

                    # groupOfUniqueNames uses 'uniqueMember'; groupOfNames uses 'member'
                    if "groupofuniquenames" in object_classes:
                        member_attr = "uniqueMember"
                    else:
                        member_attr = "member"

                    # Check if user is already a member
                    current_members = []
                    if hasattr(group_entry, member_attr):
                        current_members = getattr(group_entry, member_attr).values or []

                    if user_dn.lower() in [m.lower() for m in current_members]:
                        logger.info(f"User {username} already member of {group_dn}")
                        groups_added.append(group_dn)
                        continue

                    # Add user to group
                    logger.info(f"Adding {user_dn} to group {group_dn} using {member_attr}")
                    success = self._connection.modify(
                        group_dn,
                        {member_attr: [(MODIFY_ADD, [user_dn])]}
                    )

                    if success:
                        logger.info(f"Added user {username} to group {group_dn}")
                        groups_added.append(group_dn)
                    else:
                        error_msg = str(self._connection.result)
                        logger.error(f"Failed to add user to {group_dn}: {error_msg}")
                        errors.append(f"{group_dn}: {error_msg}")

                except LDAPException as e:
                    logger.error(f"LDAP error adding user to {group_dn}: {e}")
                    errors.append(f"{group_dn}: {str(e)}")

            return ProvisioningResult(
                success=True,
                service_user_id=username,
                message=f"User {username} created and added to {len(groups_added)} group(s)",
                details={
                    "username": username,
                    "user_dn": user_dn,
                    "user_created": user_created,
                    "groups_added": groups_added,
                    "errors": errors if errors else None,
                },
            )

        except ProvisioningError:
            raise
        except LDAPException as e:
            logger.error(f"LDAP error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
                error_message=str(e),
                is_retriable=True,
            )
        except Exception as e:
            logger.error(f"Error creating LDAP user: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
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
        """Update user in LDAP and manage group memberships

        Searches by employeeNumber first, then by uid (username)
        """
        if not self._connection:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
                error_message="Not connected to LDAP",
                is_retriable=True,
            )

        attrs = attributes or {}

        # LDAP ne supporte pas le disable — supprimer si enabled=false
        if not attrs.get("enabled", True):
            logger.info(f"LDAP: enabled=false for {username}, deleting instead of disabling")
            return await self.delete_user(username=username, attributes=attributes)

        ldap_groups = attrs.get("ldapGroups", [])
        if isinstance(ldap_groups, str):
            ldap_groups = [ldap_groups]
        midpoint_uid = attrs.get("midpoint_uid", "")
        employee_number = attrs.get("personalNumber", "") or midpoint_uid

        first_name = attrs.get("firstName") or ""
        last_name = attrs.get("lastName") or ""
        full_name = attrs.get("fullName") or f"{first_name} {last_name}".strip()

        # Determine base DN
        if ldap_groups:
            base_dn = self._extract_base_dn_from_group(ldap_groups[0])
        else:
            base_dn = settings.LDAP_BASE_DN

        try:
            # FIRST: Search for user by employeeNumber (unique identifier)
            existing_dn = await self._find_user_by_employee_number(employee_number, base_dn)

            if not existing_dn:
                # SECOND: Search by uid (username)
                self._connection.search(
                    search_base=f"ou=Users,{base_dn}",
                    search_filter=f"(uid={username})",
                    search_scope=SUBTREE,
                )
                if self._connection.entries:
                    existing_dn = self._connection.entries[0].entry_dn

            if not existing_dn:
                # User doesn't exist in LDAP
                if not ldap_groups:
                    # No groups to assign and user doesn't exist - nothing to do
                    logger.info(f"User {username} not found in LDAP and no groups to assign - skipping")
                    return ProvisioningResult(
                        success=True,
                        message=f"User {username} not in LDAP, no cleanup needed",
                        details={"username": username, "skipped": True},
                    )
                # User doesn't exist but has groups to assign - create it
                logger.info(f"User {username} not found, creating...")
                return await self.provision_user(
                    username=username,
                    password=password,
                    email=email,
                    roles=roles,
                    attributes=attributes,
                )

            # Detect uid rename — LDAP RDN can't be modified in place, must delete + recreate
            self._connection.search(
                search_base=existing_dn,
                search_filter="(objectClass=*)",
                attributes=["uid"],
            )
            if self._connection.entries:
                existing_entry = self._connection.entries[0]
                uid_values = existing_entry.uid.values if hasattr(existing_entry, "uid") else []
                existing_uid = uid_values[0] if uid_values else None
                if isinstance(existing_uid, bytes):
                    existing_uid = existing_uid.decode()
                if existing_uid and existing_uid != username:
                    logger.info(f"LDAP uid rename: {existing_uid} → {username}, deleting and recreating")
                    await self.delete_user(username=existing_uid, attributes=attributes)
                    return await self.provision_user(
                        username=username,
                        password=password,
                        email=email,
                        roles=roles,
                        attributes=attributes,
                    )

            # Update user attributes
            changes = {}
            if full_name:
                changes["cn"] = [(MODIFY_REPLACE, [full_name])]
            if last_name:
                changes["sn"] = [(MODIFY_REPLACE, [last_name])]
            if first_name:
                changes["givenName"] = [(MODIFY_REPLACE, [first_name])]
            if email:
                changes["mail"] = [(MODIFY_REPLACE, [email])]
            if password:
                changes["userPassword"] = [(MODIFY_REPLACE, [password])]
            if attrs.get("telephoneNumber"):
                changes["telephoneNumber"] = [(MODIFY_REPLACE, [attrs["telephoneNumber"]])]

            if changes:
                self._connection.modify(existing_dn, changes)
                logger.info(f"Updated LDAP user attributes for {username} at {existing_dn}")

            # Update group memberships - now handles both ADD and REMOVE
            groups_added = []
            groups_removed = []
            errors = []

            # FIRST: Get all current groups the user is a member of
            # Search both 'member' and 'uniqueMember' to handle different schema types
            current_user_groups = []
            self._connection.search(
                search_base=base_dn,
                search_filter=f"(|(member={existing_dn})(uniqueMember={existing_dn}))",
                search_scope=SUBTREE,
                attributes=["cn"],
            )
            for entry in self._connection.entries:
                current_user_groups.append(entry.entry_dn)
            logger.info(f"User {username} is currently member of: {current_user_groups}")
            logger.info(f"Desired groups: {ldap_groups}")

            # Normalize ldapGroups for case-insensitive comparison
            ldap_groups_lower = [g.lower() for g in ldap_groups if g]

            # SECOND: Remove user from groups that are not in ldapGroups
            for current_group_dn in current_user_groups:
                if current_group_dn.lower() not in ldap_groups_lower:
                    try:
                        # Get group details to determine member attribute
                        self._connection.search(
                            search_base=current_group_dn,
                            search_filter="(objectClass=*)",
                            attributes=["objectClass"],
                        )
                        if self._connection.entries:
                            group_entry = self._connection.entries[0]
                            object_classes = [oc.lower() for oc in group_entry.objectClass.values]
                            member_attr = "uniqueMember" if "groupofuniquenames" in object_classes else "member"

                            # Remove user from group
                            logger.info(f"Removing {existing_dn} from group {current_group_dn}")
                            success = self._connection.modify(
                                current_group_dn,
                                {member_attr: [(MODIFY_DELETE, [existing_dn])]}
                            )
                            if success:
                                groups_removed.append(current_group_dn)
                                logger.info(f"Removed user {username} from group {current_group_dn}")
                            else:
                                error_msg = str(self._connection.result)
                                logger.warning(f"Failed to remove from {current_group_dn}: {error_msg}")
                                errors.append(f"{current_group_dn}: {error_msg}")
                    except Exception as e:
                        logger.warning(f"Error removing from {current_group_dn}: {e}")
                        errors.append(f"{current_group_dn}: {str(e)}")

            # THIRD: Add user to groups in ldapGroups that they're not already a member of
            for group_dn in ldap_groups:
                if not group_dn:
                    continue

                try:
                    # Check if group exists
                    self._connection.search(
                        search_base=group_dn,
                        search_filter="(objectClass=*)",
                        attributes=["objectClass", "member", "uniqueMember"],
                    )

                    if not self._connection.entries:
                        errors.append(f"{group_dn}: group does not exist")
                        continue

                    group_entry = self._connection.entries[0]
                    object_classes = [oc.lower() for oc in group_entry.objectClass.values]

                    if "groupofuniquenames" in object_classes:
                        member_attr = "uniqueMember"
                    else:
                        member_attr = "member"

                    # Check if user is already a member
                    current_members = []
                    if hasattr(group_entry, member_attr):
                        current_members = getattr(group_entry, member_attr).values or []

                    # Use existing_dn (actual DN found by search) rather than the computed
                    # user_dn, because the user may have been renamed or moved in the tree
                    if existing_dn.lower() not in [m.lower() for m in current_members]:
                        # Add user to group using existing_dn
                        logger.info(f"Adding {existing_dn} to group {group_dn}")
                        success = self._connection.modify(
                            group_dn,
                            {member_attr: [(MODIFY_ADD, [existing_dn])]}
                        )
                        if success:
                            groups_added.append(group_dn)
                            logger.info(f"Added user {username} to group {group_dn}")
                        else:
                            error_msg = str(self._connection.result)
                            errors.append(f"{group_dn}: {error_msg}")

                except Exception as e:
                    errors.append(f"{group_dn}: {str(e)}")

            return ProvisioningResult(
                success=True,
                service_user_id=username,
                message=f"User {username} updated at {existing_dn}",
                details={
                    "username": username,
                    "user_dn": existing_dn,
                    "attributes_updated": list(changes.keys()),
                    "groups_added": groups_added,
                    "groups_removed": groups_removed,
                    "errors": errors if errors else None,
                },
            )

        except ProvisioningError:
            raise
        except LDAPException as e:
            logger.error(f"LDAP error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
                error_message=str(e),
                is_retriable=True,
            )

    async def delete_user(
        self,
        username: str,
        email: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> ProvisioningResult:
        """Delete user from LDAP and remove from groups"""
        if not self._connection:
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
                error_message="Not connected to LDAP",
                is_retriable=True,
            )

        attrs = attributes or {}
        ldap_groups = attrs.get("ldapGroups", [])
        if isinstance(ldap_groups, str):
            ldap_groups = [ldap_groups]

        # Determine base DN
        if ldap_groups:
            base_dn = self._extract_base_dn_from_group(ldap_groups[0])
        else:
            base_dn = settings.LDAP_BASE_DN

        user_dn = f"uid={username},ou=Users,{base_dn}"
        groups_removed = []
        errors = []

        try:
            # First, remove user from all groups
            for group_dn in ldap_groups:
                if not group_dn:
                    continue

                try:
                    # Check if group exists
                    self._connection.search(
                        search_base=group_dn,
                        search_filter="(objectClass=*)",
                        attributes=["objectClass", "member", "uniqueMember"],
                    )

                    if not self._connection.entries:
                        continue

                    group_entry = self._connection.entries[0]
                    object_classes = [oc.lower() for oc in group_entry.objectClass.values]

                    if "groupofuniquenames" in object_classes:
                        member_attr = "uniqueMember"
                    else:
                        member_attr = "member"

                    # Remove user from group
                    success = self._connection.modify(
                        group_dn,
                        {member_attr: [(MODIFY_DELETE, [user_dn])]}
                    )

                    if success:
                        logger.info(f"Removed {username} from group {group_dn}")
                        groups_removed.append(group_dn)

                except Exception as e:
                    logger.warning(f"Error removing user from {group_dn}: {e}")

            # Then delete the user entry
            if username:
                _delete_filter = f"(uid={username})"
            else:
                midpoint_uid = attrs.get("midpoint_uid", "")
                if midpoint_uid:
                    _delete_filter = f"(employeeNumber={midpoint_uid})"
                else:
                    logger.warning("Cannot identify LDAP user for deletion: no uid or midpoint_uid")
                    return ProvisioningResult(
                        success=True,
                        message="No identifier for LDAP deletion",
                        details={"skipped": True},
                    )
            self._connection.search(
                search_base=f"ou=Users,{base_dn}",
                search_filter=_delete_filter,
                search_scope=SUBTREE,
            )

            user_deleted = False
            if self._connection.entries:
                actual_user_dn = self._connection.entries[0].entry_dn
                logger.info(f"Deleting LDAP user: {actual_user_dn}")

                success = self._connection.delete(actual_user_dn)
                if success:
                    logger.info(f"Deleted LDAP user {username}")
                    user_deleted = True
                else:
                    error_msg = str(self._connection.result)
                    logger.error(f"Failed to delete user: {error_msg}")
                    errors.append(f"Delete user: {error_msg}")
            else:
                logger.info(f"User {username} not found in LDAP (already deleted)")

            return ProvisioningResult(
                success=True,
                service_user_id=username,
                message=f"User {username} deleted from LDAP",
                details={
                    "username": username,
                    "user_dn": user_dn,
                    "user_deleted": user_deleted,
                    "groups_removed": groups_removed,
                    "errors": errors if errors else None,
                },
            )

        except LDAPException as e:
            logger.error(f"LDAP error: {e}")
            raise ProvisioningError(
                operation_id="",
                target_service=TargetService.LDAP,
                error_message=str(e),
                is_retriable=True,
            )
