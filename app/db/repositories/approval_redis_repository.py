"""Redis repository for approval workflow operations"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class ApprovalRedisRepository:
    """Repository for managing approval data in Redis"""

    def __init__(self, redis_client: redis.Redis):
        """Initialize repository with Redis client

        Args:
            redis_client: Async Redis client instance
        """
        self.redis = redis_client
        self.ttl = 7200  # 2 hours in seconds

    async def add_pending_approval(
        self, operation_id: str, operation_data: dict
    ) -> bool:
        """Store pending approval in Redis with TTL

        Args:
            operation_id: Unique operation ID
            operation_data: Dict containing:
                - target_service: Target service name
                - operation_type: Type of operation
                - user_data: Complete user data dict
                - midpoint_message: Complete MidPoint message dict

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            key = f"approval:pending:{operation_id}"
            value = json.dumps(
                {
                    "operation_id": operation_id,
                    "target_service": operation_data["target_service"],
                    "operation_type": operation_data["operation_type"],
                    "user_data": operation_data["user_data"],
                    "midpoint_message": operation_data["midpoint_message"],
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "timeout_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=self.ttl)
                    ).isoformat(),
                    "worker_id": None,
                    "status": "pending",
                }
            )

            result = await self.redis.setex(key, self.ttl, value)
            logger.info(
                f"Added pending approval to Redis: {operation_id} (TTL: {self.ttl}s)"
            )
            return bool(result)

        except Exception as e:
            logger.error(f"Failed to add pending approval to Redis: {e}")
            return False

    async def update_approval_status(
        self, operation_id: str, worker_id: str, status: str = "processing"
    ) -> bool:
        """Update approval status and worker_id in Redis

        Args:
            operation_id: Operation ID
            worker_id: ID of worker processing approval
            status: New status (default: "processing")

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            key = f"approval:pending:{operation_id}"
            data = await self.get_pending_approval(operation_id)

            if data:
                data["worker_id"] = worker_id
                data["status"] = status
                result = await self.redis.setex(key, self.ttl, json.dumps(data))
                logger.info(
                    f"Updated approval status: {operation_id} -> {status} (worker: {worker_id})"
                )
                return bool(result)

            logger.warning(f"Approval not found for update: {operation_id}")
            return False

        except Exception as e:
            logger.error(f"Failed to update approval status: {e}")
            return False

    async def get_pending_approval(self, operation_id: str) -> Optional[dict]:
        """Retrieve pending approval data from Redis

        Args:
            operation_id: Operation ID

        Returns:
            Dict with approval data if found, None otherwise
        """
        try:
            key = f"approval:pending:{operation_id}"
            value = await self.redis.get(key)

            if value:
                return json.loads(value)

            return None

        except Exception as e:
            logger.error(f"Failed to get pending approval: {e}")
            return None

    async def remove_pending_approval(self, operation_id: str) -> bool:
        """Delete approval from Redis after decision

        Args:
            operation_id: Operation ID

        Returns:
            True if deleted, False otherwise
        """
        try:
            key = f"approval:pending:{operation_id}"
            result = await self.redis.delete(key)
            logger.info(f"Removed pending approval from Redis: {operation_id}")
            return result > 0

        except Exception as e:
            logger.error(f"Failed to remove pending approval: {e}")
            return False

    async def list_all_pending(self) -> list[dict]:
        """List all pending approvals for monitoring

        Returns:
            List of approval data dicts
        """
        try:
            keys = await self.redis.keys("approval:pending:*")
            results = []

            for key in keys:
                value = await self.redis.get(key)
                if value:
                    results.append(json.loads(value))

            logger.info(f"Listed {len(results)} pending approvals from Redis")
            return results

        except Exception as e:
            logger.error(f"Failed to list pending approvals: {e}")
            return []

    async def get_pending_count(self) -> int:
        """Get count of pending approvals

        Returns:
            Number of pending approvals
        """
        try:
            keys = await self.redis.keys("approval:pending:*")
            return len(keys)
        except Exception as e:
            logger.error(f"Failed to get pending count: {e}")
            return 0

    # --- Rejected CREATE tracking ---

    async def store_rejected_create(
        self, username: str, target_service: str, operation_id: str, reason: str
    ) -> bool:
        """Store a rejected CREATE marker in Redis (TTL 30 days)

        When a CREATE_USER is rejected by the admin, we store a marker so that
        future UPDATE/DELETE for the same user+service can be handled correctly.

        Args:
            username: The username that was rejected
            target_service: The target service (e.g. "MYSQL", "ODOO")
            operation_id: The rejected operation ID
            reason: Rejection reason

        Returns:
            True if stored successfully
        """
        try:
            key = f"rejected_create:{target_service}:{username}"
            value = json.dumps({
                "operation_id": operation_id,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            })
            ttl = 30 * 24 * 3600  # 30 days
            result = await self.redis.setex(key, ttl, value)
            logger.info(
                f"Stored rejected CREATE marker: {username} on {target_service} "
                f"(operation: {operation_id})"
            )
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to store rejected CREATE marker: {e}")
            return False

    async def check_rejected_create(
        self, username: str, target_service: str
    ) -> bool:
        """Check if a CREATE was previously rejected for this user+service

        Args:
            username: The username to check
            target_service: The target service to check

        Returns:
            True if a rejected CREATE exists
        """
        try:
            key = f"rejected_create:{target_service}:{username}"
            result = await self.redis.get(key)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to check rejected CREATE marker: {e}")
            return False

    # --- User state tracking (for UPDATE diff) ---

    async def store_user_state(
        self, username: str, target_service: str, user_data: dict
    ) -> bool:
        """Store user state after successful provisioning for future diff detection

        Args:
            username: The username
            target_service: The target service
            user_data: The user data dict to store

        Returns:
            True if stored successfully
        """
        try:
            key = f"user_state:{target_service}:{username}"
            value = json.dumps(user_data)
            ttl = 90 * 24 * 3600  # 90 days
            result = await self.redis.setex(key, ttl, value)
            logger.debug(f"Stored user state: {username} on {target_service}")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to store user state: {e}")
            return False

    async def get_user_state(
        self, username: str, target_service: str
    ) -> dict | None:
        """Get stored user state for diff computation

        Args:
            username: The username
            target_service: The target service

        Returns:
            User data dict if found, None otherwise
        """
        try:
            key = f"user_state:{target_service}:{username}"
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Failed to get user state: {e}")
            return None

    # --- Approval group tracking (bulk email per user) ---

    async def create_or_join_approval_group(
        self, username: str, operation_type: str, operation_id: str, target_service: str
    ) -> dict:
        """Create a new approval group or join an existing one.

        The first operation for a given (username, operation_type) becomes the
        group leader and will send the single approval email for all services.
        Subsequent operations for the same user join as members.

        Args:
            username: The username being provisioned
            operation_type: Operation type (e.g. "CREATE_USER")
            operation_id: This operation's ID
            target_service: This operation's target service

        Returns:
            Dict with keys:
              - group_leader (bool): True if this operation is the leader
              - leader_operation_id (str): The leader's operation ID
              - members (list): Current members including this one
        """
        key = f"approval:group:{username}:{operation_type}"
        # Short TTL for the grouping window — long enough for all messages to arrive
        group_ttl = 30  # seconds

        try:
            existing = await self.redis.get(key)
            if existing:
                # Join existing group as a member
                group = json.loads(existing)
                group["members"].append({"operation_id": operation_id, "target_service": target_service})
                await self.redis.setex(key, group_ttl, json.dumps(group))
                logger.info(
                    f"Operation {operation_id} joined group for {username}/{operation_type} "
                    f"(leader: {group['leader_operation_id']})"
                )
                return {
                    "group_leader": False,
                    "leader_operation_id": group["leader_operation_id"],
                    "members": group["members"],
                }
            else:
                # Create new group — this operation is the leader
                group = {
                    "leader_operation_id": operation_id,
                    "username": username,
                    "operation_type": operation_type,
                    "members": [{"operation_id": operation_id, "target_service": target_service}],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "finalized": False,
                }
                await self.redis.setex(key, group_ttl, json.dumps(group))
                logger.info(f"Created approval group for {username}/{operation_type}, leader: {operation_id}")
                return {
                    "group_leader": True,
                    "leader_operation_id": operation_id,
                    "members": group["members"],
                }
        except Exception as e:
            logger.error(f"Failed to create/join approval group: {e}")
            # Fallback: behave as standalone leader (no grouping)
            return {
                "group_leader": True,
                "leader_operation_id": operation_id,
                "members": [{"operation_id": operation_id, "target_service": target_service}],
            }

    async def finalize_approval_group(self, username: str, operation_type: str) -> dict:
        """Mark the group as finalized and return its members.

        Called by the leader after the grouping window has elapsed.

        Args:
            username: The username
            operation_type: Operation type

        Returns:
            The group dict with all members collected so far
        """
        key = f"approval:group:{username}:{operation_type}"
        try:
            existing = await self.redis.get(key)
            if existing:
                group = json.loads(existing)
                group["finalized"] = True
                # Extend TTL so the group is available when the callback arrives
                await self.redis.setex(key, self.ttl, json.dumps(group))
                logger.info(
                    f"Finalized approval group for {username}/{operation_type}: "
                    f"{len(group['members'])} member(s)"
                )
                return group
            logger.warning(f"Approval group not found for finalization: {username}/{operation_type}")
            return {}
        except Exception as e:
            logger.error(f"Failed to finalize approval group: {e}")
            return {}

    async def get_group_by_leader(self, leader_operation_id: str) -> dict | None:
        """Find an approval group by its leader operation ID.

        Scans all group keys — only used during callback processing so
        performance is acceptable (small number of active groups).

        Args:
            leader_operation_id: The leader's operation ID

        Returns:
            The group dict if found, None otherwise
        """
        try:
            keys = await self.redis.keys("approval:group:*")
            for key in keys:
                value = await self.redis.get(key)
                if value:
                    group = json.loads(value)
                    if group.get("leader_operation_id") == leader_operation_id:
                        return group
            return None
        except Exception as e:
            logger.error(f"Failed to get group by leader {leader_operation_id}: {e}")
            return None

    async def remove_approval_group(self, username: str, operation_type: str) -> bool:
        """Delete the approval group after all members have been processed.

        Args:
            username: The username
            operation_type: Operation type

        Returns:
            True if deleted
        """
        try:
            key = f"approval:group:{username}:{operation_type}"
            result = await self.redis.delete(key)
            logger.info(f"Removed approval group: {username}/{operation_type}")
            return result > 0
        except Exception as e:
            logger.error(f"Failed to remove approval group: {e}")
            return False

    async def clear_rejected_create(
        self, username: str, target_service: str
    ) -> bool:
        """Clear rejected CREATE marker after successful provisioning

        Args:
            username: The username to clear
            target_service: The target service to clear

        Returns:
            True if deleted
        """
        try:
            key = f"rejected_create:{target_service}:{username}"
            result = await self.redis.delete(key)
            if result > 0:
                logger.info(
                    f"Cleared rejected CREATE marker: {username} on {target_service}"
                )
            return result > 0
        except Exception as e:
            logger.error(f"Failed to clear rejected CREATE marker: {e}")
            return False
