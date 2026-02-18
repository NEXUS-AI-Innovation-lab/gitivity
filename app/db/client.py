"""Prisma client singleton for database operations"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from prisma import Prisma


class DatabaseClient:
    """Singleton wrapper for Prisma client with connection management"""

    _instance: "DatabaseClient | None" = None
    _client: Prisma | None = None

    def __new__(cls) -> "DatabaseClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def client(self) -> Prisma:
        """Get the Prisma client instance"""
        if self._client is None:
            self._client = Prisma()
        return self._client

    async def connect(self) -> None:
        """Connect to the database"""
        if not self.client.is_connected():
            await self.client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the database"""
        if self._client is not None and self._client.is_connected():
            await self._client.disconnect()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Prisma, None]:
        """Context manager for database transactions

        Usage:
            async with db.transaction() as tx:
                await tx.provisioningoperation.create(...)
                await tx.auditlog.create(...)
        """
        async with self.client.tx() as tx:
            yield tx


# Global database client instance
db = DatabaseClient()


async def get_db() -> Prisma:
    """Dependency for getting the database client

    Ensures connection is established before returning client.
    """
    await db.connect()
    return db.client
