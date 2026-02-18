"""Base repository with common operations"""
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from prisma import Prisma

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """Abstract base repository providing common database operations

    All repositories should inherit from this class and implement
    the abstract methods for their specific model.
    """

    def __init__(self, db: Prisma) -> None:
        """Initialize repository with database client

        Args:
            db: Prisma client instance
        """
        self._db = db

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name for this repository"""
        ...

    @abstractmethod
    async def get_by_id(self, id: str) -> T | None:
        """Get a record by its ID

        Args:
            id: The unique identifier

        Returns:
            The record if found, None otherwise
        """
        ...
