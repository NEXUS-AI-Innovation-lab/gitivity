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
            # MidPoint returns resources in object.list format
            resources = data.get("object", {}).get("list", [])
            if not resources and isinstance(data.get("object"), list):
                resources = data["object"]
            return resources
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
                        deltas.append(
                            {
                                "modificationType": "delete",
                                "path": path,
                                "value": matching,
                            }
                        )
                        references_removed += len(matching)
                if deltas:
                    patch_response = await client.patch(
                        f"/ws/rest/{endpoint}/{oid}",
                        json={"objectModification": {"itemDelta": deltas}},
                    )
                    patch_response.raise_for_status()
                    modified_objects += 1

        delete_response = await client.delete(f"/ws/rest/roles/{role_oid}")
        if delete_response.status_code != 404:
            delete_response.raise_for_status()
        return {
            "role_deleted": delete_response.status_code != 404,
            "already_absent": delete_response.status_code == 404,
            "references_removed": references_removed,
            "objects_modified": modified_objects,
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
