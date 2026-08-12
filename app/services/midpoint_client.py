"""MidPoint API client for managing connectors"""

import copy
import logging
from typing import Any

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


class MidPointClient:
    """Client for interacting with MidPoint REST API

    Allows fetching and managing MidPoint connectors (resources).
    """

    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = (
            base_url
            or getattr(settings, "MIDPOINT_URL", "http://localhost:8080/midpoint")
        ).rstrip("/")
        self.username = username or getattr(
            settings, "MIDPOINT_USERNAME", "administrator"
        )
        self.password = password or getattr(settings, "MIDPOINT_PASSWORD", "5ecr3t")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=(self.username, self.password),
                timeout=self.timeout,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Check if MidPoint is reachable"""
        try:
            client = await self._get_client()
            response = await client.get("/ws/rest")
            return response.status_code in (
                200,
                401,
                403,
                404,
            )  # Any response means MidPoint is up
        except Exception as e:
            logger.debug(f"MidPoint health check failed: {e}")
            return False

    async def get_resources(self) -> list[dict[str, Any]]:
        """Get all resources (connectors) from MidPoint

        Returns:
            List of resource objects with their configuration
        """
        try:
            client = await self._get_client()
            response = await client.get(
                "/ws/rest/resources", headers={"Accept": "application/json"}
            )
            response.raise_for_status()

            data = response.json()
            # Depending on MidPoint version and collection size, REST returns
            # object.list, object.object, a direct object list, or one object.
            value: Any = data.get("object", data.get("objectList", {}))
            if isinstance(value, dict):
                value = value.get("object", value.get("list", []))
            if isinstance(value, dict):
                return [value]
            return value if isinstance(value, list) else []
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch MidPoint resources: {e.response.status_code}"
            )
            raise
        except Exception as e:
            logger.error(f"Error fetching MidPoint resources: {e}")
            raise

    async def get_resource(self, oid: str) -> dict[str, Any]:
        """Get a specific resource by OID

        Args:
            oid: The MidPoint object identifier

        Returns:
            Resource object with full configuration
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"/ws/rest/resources/{oid}", headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to fetch MidPoint resource {oid}: {e.response.status_code}"
            )
            raise
        except Exception as e:
            logger.error(f"Error fetching MidPoint resource {oid}: {e}")
            raise

    async def test_resource(self, oid: str) -> dict[str, Any]:
        """Test connection for a specific resource

        Args:
            oid: The MidPoint resource OID

        Returns:
            Test result with success status and details
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"/ws/rest/resources/{oid}/test", headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to test MidPoint resource {oid}: {e.response.status_code}"
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}",
                "message": str(e),
            }
        except Exception as e:
            logger.error(f"Error testing MidPoint resource {oid}: {e}")
            return {"success": False, "error": "connection_error", "message": str(e)}

    async def get_resource_capabilities(self, oid: str) -> dict[str, Any]:
        """Get capabilities of a resource

        Args:
            oid: The MidPoint resource OID

        Returns:
            Resource capabilities
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"/ws/rest/resources/{oid}/capabilities",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching capabilities for resource {oid}: {e}")
            return {}

    async def update_resource(
        self, oid: str, modifications: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a resource configuration

        Args:
            oid: The MidPoint resource OID
            modifications: Dictionary of modifications to apply

        Returns:
            Updated resource object
        """
        try:
            client = await self._get_client()
            # MidPoint uses PATCH with specific modification format
            response = await client.patch(
                f"/ws/rest/resources/{oid}",
                json=modifications,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            return await self.get_resource(oid)
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to update MidPoint resource {oid}: {e.response.status_code}"
            )
            raise
        except Exception as e:
            logger.error(f"Error updating MidPoint resource {oid}: {e}")
            raise

    async def get_connector_info(self, resource: dict[str, Any]) -> dict[str, Any]:
        """Extract connector information from a resource

        Args:
            resource: Full resource object from MidPoint

        Returns:
            Simplified connector info
        """
        # Extract relevant fields from MidPoint resource structure
        resource_obj = resource.get("resource", resource)

        return {
            "oid": resource_obj.get("oid", ""),
            "name": resource_obj.get("name", "Unknown"),
            "description": resource_obj.get("description", ""),
            "connectorRef": resource_obj.get("connectorRef", {}),
            "connectorConfiguration": self._sanitize_config(
                resource_obj.get("connectorConfiguration", {})
            ),
            "operationalState": resource_obj.get("operationalState", {}),
        }

    # ================================================================
    # User & Role management methods
    # ================================================================

    async def search_user(self, username: str) -> dict[str, Any] | None:
        """Search for a user by username in MidPoint

        Args:
            username: The username to search for

        Returns:
            User dict with oid and assignments if found, None otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"/ws/rest/users?name={username}",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            # Log raw response keys for debugging
            logger.debug(
                f"MidPoint search response for {username}: {list(data.keys())}"
            )

            # Format 1: {"user": {...}}
            user = data.get("user")
            if user and isinstance(user, dict):
                return user

            obj = data.get("object")

            # Format 2: {"object": {"user": {...}}}
            if isinstance(obj, dict) and "user" in obj:
                return obj["user"]

            # Format 3: {"object": {"oid": "...", ...}} (user directly in object)
            if isinstance(obj, dict) and "oid" in obj:
                return obj

            # Format 4: {"object": [{"oid": "...", ...}]} (list of objects)
            if isinstance(obj, list) and len(obj) > 0:
                return obj[0]

            # Format 5: {"object": {"object": [...]}} (nested list wrapper)
            if isinstance(obj, dict) and "object" in obj:
                nested = obj["object"]
                if isinstance(nested, list) and len(nested) > 0:
                    return nested[0]
                if isinstance(nested, dict):
                    return nested

            # Format 6: {"objectList": {"object": [...]}}
            obj_list = data.get("objectList", {})
            if isinstance(obj_list, dict):
                objs = obj_list.get("object", [])
                if isinstance(objs, list) and len(objs) > 0:
                    return objs[0]

            logger.info(
                f"User not found in MidPoint: {username} (response keys: {list(data.keys())})"
            )
            return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.error(
                f"Failed to search MidPoint user {username}: {e.response.status_code}"
            )
            return None
        except Exception as e:
            logger.error(f"Error searching MidPoint user {username}: {e}")
            return None

    async def get_user(self, oid: str) -> dict[str, Any] | None:
        """Get a user by OID from MidPoint

        Args:
            oid: The MidPoint user OID

        Returns:
            User dict if found, None otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"/ws/rest/users/{oid}",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("user", data)
        except Exception as e:
            logger.error(f"Error fetching MidPoint user {oid}: {e}")
            return None

    async def get_role(self, oid: str) -> dict[str, Any] | None:
        """Get a role by OID from MidPoint

        Args:
            oid: The MidPoint role OID

        Returns:
            Role dict if found, None otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"/ws/rest/roles/{oid}",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("role", data)
        except Exception as e:
            logger.error(f"Error fetching MidPoint role {oid}: {e}")
            return None

    async def upsert_role_xml(self, oid: str, xml: str) -> str:
        """Create or replace a role using its deterministic OID."""
        client = await self._get_client()
        headers = {"Accept": "application/json", "Content-Type": "application/xml"}
        existing = await client.get(f"/ws/rest/roles/{oid}")
        if existing.status_code == 200:
            response = await client.put(
                f"/ws/rest/roles/{oid}", content=xml, headers=headers
            )
            response.raise_for_status()
            return "updated"
        if existing.status_code != 404:
            existing.raise_for_status()

        response = await client.post("/ws/rest/roles", content=xml, headers=headers)
        if response.status_code == 409:
            response = await client.put(
                f"/ws/rest/roles/{oid}", content=xml, headers=headers
            )
            response.raise_for_status()
            return "updated"
        response.raise_for_status()
        return "created"

    async def import_resource_object_class(
        self, resource_oid: str, object_class: str
    ) -> None:
        """Ask MidPoint to refresh entitlement shadows for one object class."""
        client = await self._get_client()
        response = await client.post(
            f"/ws/rest/resources/{resource_oid}/import/{object_class}",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if response.status_code not in {200, 201, 202, 303}:
            response.raise_for_status()

    @staticmethod
    def _mql_string(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    async def delete_stale_entitlement_shadows(
        self,
        resource_oid: str,
        object_class: str,
        managed_prefix: str,
        active_names: set[str],
    ) -> list[str]:
        """Delete stale managed shadows without touching native objects."""
        resource = self._mql_string(resource_oid)
        object_type = self._mql_string(object_class)
        query = (
            f'resourceRef matches (oid = "{resource}") '
            f'and objectClass = "ri:{object_type}"'
        )
        client = await self._get_client()
        response = await client.post(
            "/ws/rest/shadows/search",
            params={"options": "raw"},
            json={"query": {"filter": {"text": query}}},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        objects = response.json().get("object", {}).get("object", [])
        if isinstance(objects, dict):
            objects = [objects]

        matching_shadows: list[tuple[str, str]] = []
        for shadow in objects:
            resource_ref = shadow.get("resourceRef", {})
            shadow_name = shadow.get("name")
            if (
                resource_ref.get("oid") == resource_oid
                and shadow.get("objectClass") == f"ri:{object_class}"
                and isinstance(shadow_name, str)
                and shadow_name.startswith(f"{managed_prefix}.")
                and shadow_name not in active_names
                and shadow.get("oid")
            ):
                matching_shadows.append((shadow["oid"], shadow_name))

        stale_oids = {oid for oid, _ in matching_shadows}
        if stale_oids:
            await self._remove_references_to_shadows(client, stale_oids)

        for shadow_oid, shadow_name in matching_shadows:
            deletion = await client.delete(
                f"/ws/rest/shadows/{shadow_oid}", params={"options": "raw"}
            )
            if deletion.status_code != 404:
                deletion.raise_for_status()
            logger.info(
                "Deleted stale MidPoint shadow: oid=%s name=%s",
                shadow_oid,
                shadow_name,
            )
        return [name for _, name in matching_shadows]

    async def _remove_references_to_shadows(
        self, client: httpx.AsyncClient, shadow_oids: set[str]
    ) -> int:
        """Remove account association references before deleting entitlement shadows."""
        listing = await client.get("/ws/rest/shadows", params={"options": "raw"})
        listing.raise_for_status()
        removed = 0
        for summary in self._object_list(listing.json()):
            oid = summary.get("oid")
            if not oid or oid in shadow_oids:
                continue
            detail = await client.get(
                f"/ws/rest/shadows/{oid}", params={"options": "raw"}
            )
            if detail.status_code == 404:
                continue
            detail.raise_for_status()
            shadow = detail.json().get("shadow", detail.json())
            references = shadow.get("referenceAttributes") or {}
            deltas = []
            for name, raw_values in references.items():
                if name.startswith("@"):
                    continue
                values = raw_values if isinstance(raw_values, list) else [raw_values]
                matching = [
                    value
                    for value in values
                    if isinstance(value, dict) and value.get("oid") in shadow_oids
                ]
                if matching:
                    deltas.append(
                        {
                            "modificationType": "delete",
                            "path": f"referenceAttributes/{name}",
                            "value": matching,
                        }
                    )
                    removed += len(matching)
            if deltas:
                patch = await client.patch(
                    f"/ws/rest/shadows/{oid}",
                    params={"options": "raw"},
                    json={"objectModification": {"itemDelta": deltas}},
                )
                patch.raise_for_status()
        return removed

    async def unassign_role(self, user_oid: str, role_oid: str) -> bool:
        """Remove a role assignment from a user in MidPoint

        Uses PATCH with a modification delta to delete the assignment.

        Args:
            user_oid: The MidPoint user OID
            role_oid: The MidPoint role OID to unassign

        Returns:
            True if successful, False otherwise
        """
        try:
            client = await self._get_client()
            modification = {
                "objectModification": {
                    "itemDelta": [
                        {
                            "modificationType": "delete",
                            "path": "assignment",
                            "value": [
                                {
                                    "targetRef": {
                                        "oid": role_oid,
                                        "type": "RoleType",
                                    }
                                }
                            ],
                        }
                    ]
                }
            }
            response = await client.patch(
                f"/ws/rest/users/{user_oid}",
                json=modification,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            logger.info(f"Successfully unassigned role {role_oid} from user {user_oid}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to unassign role {role_oid} from user {user_oid}: {e}"
            )
            return False

    @staticmethod
    def _object_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize the JSON collection formats returned by MidPoint."""
        value: Any = payload.get("object", payload.get("objectList", {}))
        if isinstance(value, dict):
            value = value.get("object", value.get("list", []))
        if isinstance(value, dict):
            return [value]
        return value if isinstance(value, list) else []

    @staticmethod
    def _references_role(value: Any, role_oid: str) -> bool:
        if isinstance(value, dict):
            reference = value.get("targetRef")
            if isinstance(reference, dict) and reference.get("oid") == role_oid:
                return True
        return False

    async def remove_role_everywhere(self, role_oid: str) -> dict[str, Any]:
        """Remove direct references to a role from all assignable objects, then delete it.

        Values returned by MidPoint are sent back verbatim in delete deltas. This also
        removes assignments carrying activation or relation metadata, not only the
        simplest ``targetRef`` form.
        """
        client = await self._get_client()
        role_response = await client.get(f"/ws/rest/roles/{role_oid}")
        if role_response.status_code == 404:
            return {
                "role_deleted": False,
                "already_absent": True,
                "references_removed": 0,
            }
        role_response.raise_for_status()

        references_removed = 0
        modified_objects = 0
        affected_users: list[dict[str, Any]] = []
        for endpoint in ("users", "roles", "orgs", "services"):
            response = await client.get(f"/ws/rest/{endpoint}")
            response.raise_for_status()
            for item in self._object_list(response.json()):
                oid = item.get("oid")
                if not oid or oid == role_oid:
                    continue
                detail_response = await client.get(f"/ws/rest/{endpoint}/{oid}")
                detail_response.raise_for_status()
                detail_payload = detail_response.json()
                singular = {
                    "users": "user",
                    "roles": "role",
                    "orgs": "org",
                    "services": "service",
                }[endpoint]
                item = detail_payload.get(singular, detail_payload)
                deltas = []
                for path in ("assignment", "inducement"):
                    values = item.get(path, [])
                    if isinstance(values, dict):
                        values = [values]
                    matching = [
                        value
                        for value in values
                        if self._references_role(value, role_oid)
                    ]
                    if matching:
                        # MidPoint GET responses include computed activation and
                        # value metadata that cannot be parsed back in a PATCH.
                        # Container value IDs identify the exact assignment and
                        # avoid resubmitting this read-only metadata.
                        deletions = [
                            {"@id": value["@id"]}
                            if isinstance(value, dict) and "@id" in value
                            else value
                            for value in matching
                        ]
                        deltas.append(
                            {
                                "modificationType": "delete",
                                "path": path,
                                "value": deletions,
                            }
                        )
                        references_removed += len(matching)
                if deltas:
                    if endpoint == "users":
                        name = item.get("name", "")
                        if isinstance(name, dict):
                            name = name.get("orig") or name.get("norm") or ""
                        assignments = item.get("assignment", [])
                        if isinstance(assignments, dict):
                            assignments = [assignments]
                        remaining_role_oids = [
                            value.get("targetRef", {}).get("oid")
                            for value in assignments
                            if isinstance(value, dict)
                            and not self._references_role(value, role_oid)
                            and value.get("targetRef", {}).get("oid")
                        ]
                        affected_users.append(
                            {
                                "oid": oid,
                                "username": str(name),
                                "email": item.get("email"),
                                "remaining_role_oids": remaining_role_oids,
                            }
                        )
                    patch_response = await client.patch(
                        f"/ws/rest/{endpoint}/{oid}",
                        json={"objectModification": {"itemDelta": deltas}},
                        params={"options": "raw"},
                    )
                    patch_response.raise_for_status()
                    modified_objects += 1

        delete_response = await client.delete(
            f"/ws/rest/roles/{role_oid}", params={"options": "raw"}
        )
        if delete_response.status_code != 404:
            delete_response.raise_for_status()
        return {
            "role_deleted": delete_response.status_code != 404,
            "already_absent": delete_response.status_code == 404,
            "references_removed": references_removed,
            "objects_modified": modified_objects,
            "affected_users": affected_users,
        }

    async def remove_user_resource_projection(
        self, user_oid: str, resource_oid: str
    ) -> dict[str, Any]:
        """Remove one orphan user projection without invoking its connector again."""
        client = await self._get_client()
        response = await client.get(
            f"/ws/rest/users/{user_oid}", params={"options": "raw"}
        )
        if response.status_code == 404:
            return {"projection_deleted": False, "user_absent": True}
        response.raise_for_status()
        user = response.json().get("user", response.json())
        links = user.get("linkRef", [])
        if isinstance(links, dict):
            links = [links]

        matching = []
        for link in links:
            shadow_oid = link.get("oid") if isinstance(link, dict) else None
            if not shadow_oid:
                continue
            shadow_response = await client.get(
                f"/ws/rest/shadows/{shadow_oid}", params={"options": "raw"}
            )
            if shadow_response.status_code == 404:
                continue
            shadow_response.raise_for_status()
            shadow = shadow_response.json().get("shadow", shadow_response.json())
            if shadow.get("resourceRef", {}).get("oid") == resource_oid:
                matching.append((link, shadow_oid))

        for link, shadow_oid in matching:
            unlink = await client.patch(
                f"/ws/rest/users/{user_oid}",
                json={
                    "objectModification": {
                        "itemDelta": [{
                            "modificationType": "delete",
                            "path": "linkRef",
                            "value": [link],
                        }]
                    }
                },
                params={"options": "raw"},
            )
            unlink.raise_for_status()
            deletion = await client.delete(
                f"/ws/rest/shadows/{shadow_oid}", params={"options": "raw"}
            )
            if deletion.status_code != 404:
                deletion.raise_for_status()
        return {
            "projection_deleted": bool(matching),
            "deleted_shadow_oids": [oid for _link, oid in matching],
        }

    async def replace_role_everywhere(
        self, old_role_oid: str, new_role_oid: str
    ) -> dict[str, Any]:
        """Replace every direct old-role reference while preserving assignment metadata."""
        if old_role_oid == new_role_oid:
            raise ValueError("Old and replacement role OIDs must differ")
        client = await self._get_client()
        replacement = await client.get(f"/ws/rest/roles/{new_role_oid}")
        replacement.raise_for_status()
        old = await client.get(f"/ws/rest/roles/{old_role_oid}")
        if old.status_code == 404:
            return {
                "old_role_absent": True,
                "references_replaced": 0,
                "objects_modified": 0,
            }
        old.raise_for_status()

        replaced = 0
        modified = 0
        for endpoint in ("users", "roles", "orgs", "services"):
            response = await client.get(f"/ws/rest/{endpoint}")
            response.raise_for_status()
            for summary in self._object_list(response.json()):
                oid = summary.get("oid")
                if not oid or oid in {old_role_oid, new_role_oid}:
                    continue
                detail = await client.get(f"/ws/rest/{endpoint}/{oid}")
                detail.raise_for_status()
                singular = {
                    "users": "user",
                    "roles": "role",
                    "orgs": "org",
                    "services": "service",
                }[endpoint]
                obj = detail.json().get(singular, detail.json())
                object_modified = False
                for path in ("assignment", "inducement"):
                    values = obj.get(path, [])
                    if isinstance(values, dict):
                        values = [values]
                    old_values = [
                        value
                        for value in values
                        if self._references_role(value, old_role_oid)
                    ]
                    if not old_values:
                        continue
                    has_new = any(
                        self._references_role(value, new_role_oid) for value in values
                    )
                    if not has_new:
                        writable_values = []
                        for old_value in old_values:
                            value = copy.deepcopy(old_value)
                            for metadata_key in ("@id", "@metadata", "@ns"):
                                value.pop(metadata_key, None)
                            value["targetRef"]["oid"] = new_role_oid
                            value["targetRef"].pop("targetName", None)
                            activation = value.get("activation")
                            if isinstance(activation, dict):
                                activation.pop("effectiveStatus", None)
                                activation.pop("validityStatus", None)
                                if not activation:
                                    value.pop("activation")
                            writable_values.append(value)
                        add = await client.patch(
                            f"/ws/rest/{endpoint}/{oid}",
                            json={
                                "objectModification": {
                                    "itemDelta": [
                                        {
                                            "modificationType": "add",
                                            "path": path,
                                            "value": writable_values,
                                        }
                                    ]
                                }
                            },
                        )
                        add.raise_for_status()
                    delete_selectors = []
                    for old_value in old_values:
                        container_id = old_value.get("@id")
                        if container_id is None:
                            raise ValueError(
                                f"MidPoint {path} referencing {old_role_oid} has no @id"
                            )
                        delete_selectors.append({"@id": container_id})
                    delete = await client.patch(
                        f"/ws/rest/{endpoint}/{oid}",
                        json={
                            "objectModification": {
                                "itemDelta": [
                                    {
                                        "modificationType": "delete",
                                        "path": path,
                                        "value": delete_selectors,
                                    }
                                ]
                            }
                        },
                    )
                    delete.raise_for_status()
                    object_modified = True
                    replaced += len(old_values)
                if object_modified:
                    modified += 1
        return {
            "old_role_absent": False,
            "references_replaced": replaced,
            "objects_modified": modified,
        }

    def _sanitize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive data from configuration

        Args:
            config: Raw connector configuration

        Returns:
            Configuration with passwords masked
        """
        sensitive_keys = {"password", "secret", "credential", "apiKey", "token"}
        sanitized = {}

        for key, value in config.items():
            if isinstance(value, dict):
                sanitized[key] = self._sanitize_config(value)
            elif any(s in key.lower() for s in sensitive_keys):
                sanitized[key] = "********"
            else:
                sanitized[key] = value

        return sanitized


# Global client instance
midpoint_client = MidPointClient()
