"""Redis repository for approval workflow operations"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis

from app.config.target_catalog import target_catalog

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

    @staticmethod
    def _canonical_target_service(target_service: str) -> str:
        normalized = target_service.strip()
        if not normalized:
            return normalized
        try:
            return target_catalog.resolve(normalized).id
        except Exception:
            return normalized.lower()

    @classmethod
    def _target_variants(cls, target_service: str) -> list[str]:
        canonical = cls._canonical_target_service(target_service)
        raw = target_service.strip()
        variants = [
            canonical,
            raw,
            raw.lower(),
            raw.upper(),
        ]
        return list(dict.fromkeys(value for value in variants if value))

    async def _get_first_matching_key(
        self,
        prefix: str,
        target_service: str,
        suffix: str,
    ) -> tuple[str | None, str]:
        canonical_target = self._canonical_target_service(target_service)
        canonical_key = f"{prefix}:{canonical_target}:{suffix}"

        for candidate in self._target_variants(target_service):
            key = f"{prefix}:{candidate}:{suffix}"
            value = await self.redis.get(key)
            if value is not None:
                if key != canonical_key:
                    ttl = await self.redis.ttl(key)
                    if ttl and ttl > 0:
                        await self.redis.setex(canonical_key, ttl, value)
                    else:
                        await self.redis.set(canonical_key, value)
                    await self.redis.delete(key)
                return value, canonical_key

        return None, canonical_key

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
            target_service = self._canonical_target_service(
                operation_data["target_service"]
            )
            key = f"approval:pending:{operation_id}"
            value = json.dumps(
                {
                    "operation_id": operation_id,
                    "target_service": target_service,
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

    # --- Approval chain (multi-level email approval) ---

    async def store_chain(self, operation_id: str, chain_data: dict) -> bool:
        """Store the approval chain state (approvers, current level, context).

        Args:
            operation_id: Operation ID
            chain_data: Chain state dict (approvers, current_index, user_data, ...)

        Returns:
            True if stored successfully
        """
        try:
            key = f"approval:chain:{operation_id}"
            result = await self.redis.setex(key, self.ttl, json.dumps(chain_data))
            logger.info(f"Stored approval chain: {operation_id} (TTL: {self.ttl}s)")
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to store approval chain: {e}")
            return False

    async def get_chain(self, operation_id: str) -> Optional[dict]:
        """Get the approval chain state."""
        try:
            value = await self.redis.get(f"approval:chain:{operation_id}")
            return json.loads(value) if value else None
        except Exception as e:
            logger.error(f"Failed to get approval chain: {e}")
            return None

    async def delete_chain(self, operation_id: str) -> bool:
        """Delete the approval chain after a terminal decision."""
        try:
            result = await self.redis.delete(f"approval:chain:{operation_id}")
            return result > 0
        except Exception as e:
            logger.error(f"Failed to delete approval chain: {e}")
            return False

    async def store_decision_token(self, token: str, token_data: dict) -> bool:
        """Store a single-use decision token for an approver.

        Args:
            token: Unguessable token embedded in the email links
            token_data: {operation_id, approver_email, approver_name, level, level_index}

        Returns:
            True if stored successfully
        """
        try:
            key = f"approval:token:{token}"
            result = await self.redis.setex(key, self.ttl, json.dumps(token_data))
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to store decision token: {e}")
            return False

    async def consume_decision_token(self, token: str) -> Optional[dict]:
        """Atomically consume a decision token (GETDEL).

        A second call with the same token returns None, which enforces
        single-use semantics.
        """
        try:
            value = await self.redis.getdel(f"approval:token:{token}")
            return json.loads(value) if value else None
        except Exception as e:
            logger.error(f"Failed to consume decision token: {e}")
            return None

    async def delete_decision_token(self, token: str) -> bool:
        """Delete a token without consuming it (cleanup on terminal decision)."""
        try:
            result = await self.redis.delete(f"approval:token:{token}")
            return result > 0
        except Exception as e:
            logger.error(f"Failed to delete decision token: {e}")
            return False

    async def cancel_operation_approval(self, operation_id: str) -> int:
        """Invalidate every Redis approval artifact for one operation."""
        deleted = 0
        chain = await self.get_chain(operation_id)
        if chain and chain.get("active_token"):
            deleted += int(
                await self.delete_decision_token(chain["active_token"])
            )
        deleted += int(await self.remove_pending_approval(operation_id))
        deleted += int(await self.delete_chain(operation_id))

        # Also cover old/incomplete chains whose token was stored but never
        # copied into approval:chain (e.g. a process interruption).
        async for key in self.redis.scan_iter(match="approval:token:*"):
            value = await self.redis.get(key)
            if not value:
                continue
            try:
                token_data = json.loads(value)
            except json.JSONDecodeError:
                continue
            if token_data.get("operation_id") == operation_id:
                deleted += await self.redis.delete(key)
        return deleted

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
            canonical_target = self._canonical_target_service(target_service)
            key = f"rejected_create:{canonical_target}:{username}"
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
            result, _ = await self._get_first_matching_key(
                "rejected_create",
                target_service,
                username,
            )
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
            canonical_target = self._canonical_target_service(target_service)
            key = f"user_state:{canonical_target}:{username}"
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
            value, _ = await self._get_first_matching_key(
                "user_state",
                target_service,
                username,
            )
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Failed to get user state: {e}")
            return None

    async def delete_user_state(self, username: str, target_service: str) -> bool:
        """Delete every canonical or legacy state key after a successful DELETE."""
        try:
            deleted = 0
            for candidate in self._target_variants(target_service):
                deleted += await self.redis.delete(
                    f"user_state:{candidate}:{username}"
                )
            return deleted > 0
        except Exception as e:
            logger.error(f"Failed to delete user state: {e}")
            return False

    async def mark_user_deleted(
        self, username: str, target_service: str, operation_id: str
    ) -> bool:
        """Remember that the target account is absent until a CREATE succeeds."""
        try:
            canonical_target = self._canonical_target_service(target_service)
            return bool(await self.redis.set(
                f"deleted_user:{canonical_target}:{username}",
                json.dumps({
                    "operation_id": operation_id,
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                }),
            ))
        except Exception as e:
            logger.error(f"Failed to store deleted user marker: {e}")
            return False

    async def was_user_deleted(self, username: str, target_service: str) -> bool:
        """Return whether the account was successfully deleted on this target."""
        try:
            value, _ = await self._get_first_matching_key(
                "deleted_user", target_service, username
            )
            return value is not None
        except Exception as e:
            logger.error(f"Failed to check deleted user marker: {e}")
            return False

    async def clear_user_deleted(self, username: str, target_service: str) -> bool:
        """Clear the deletion marker after a successful target CREATE."""
        try:
            deleted = 0
            for candidate in self._target_variants(target_service):
                deleted += await self.redis.delete(
                    f"deleted_user:{candidate}:{username}"
                )
            return deleted > 0
        except Exception as e:
            logger.error(f"Failed to clear deleted user marker: {e}")
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
            deleted = 0
            for candidate in self._target_variants(target_service):
                key = f"rejected_create:{candidate}:{username}"
                deleted += await self.redis.delete(key)
            if deleted > 0:
                logger.info(
                    f"Cleared rejected CREATE marker: {username} on {target_service}"
                )
            return deleted > 0
        except Exception as e:
            logger.error(f"Failed to clear rejected CREATE marker: {e}")
            return False
