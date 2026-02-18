"""Redis client singleton for approval workflow"""
import logging
from typing import Optional

import redis.asyncio as redis

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Singleton Redis client for async operations"""

    _instance: Optional[redis.Redis] = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        """Get or create Redis client instance

        Returns:
            Redis client instance

        Raises:
            redis.ConnectionError: If unable to connect to Redis
        """
        if cls._instance is None:
            try:
                cls._instance = redis.Redis(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    password=settings.REDIS_PASSWORD
                    if settings.REDIS_PASSWORD
                    else None,
                    db=settings.REDIS_DB,
                    decode_responses=True,  # Auto-decode bytes to strings
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )

                # Test connection
                await cls._instance.ping()
                logger.info(
                    f"Redis client connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}"
                )

            except redis.ConnectionError as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise

        return cls._instance

    @classmethod
    async def close(cls) -> None:
        """Close Redis connection"""
        if cls._instance:
            await cls._instance.aclose()
            cls._instance = None
            logger.info("Redis client connection closed")

    @classmethod
    async def health_check(cls) -> bool:
        """Check Redis connection health

        Returns:
            True if Redis is reachable, False otherwise
        """
        try:
            client = await cls.get_client()
            await client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False
