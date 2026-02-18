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
