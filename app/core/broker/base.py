"""Abstract base class for message broker consumers"""
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class BrokerConsumer(ABC):
    """Abstract base class for message broker consumers

    All broker implementations (Kafka, RabbitMQ, etc.) must inherit
    from this class and implement the required methods.
    """

    def __init__(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if the consumer is currently running"""
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Start consuming messages from the broker

        This method should:
        1. Connect to the broker
        2. Subscribe to the configured topic/queue
        3. Start processing messages in a loop
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop consuming messages and disconnect

        This method should:
        1. Stop the message processing loop
        2. Commit any pending offsets/acks
        3. Close the connection gracefully
        """
        ...

    @abstractmethod
    async def _process_message(self, message: bytes) -> None:
        """Process a single message from the broker

        Args:
            message: Raw message bytes from the broker

        This method should:
        1. Deserialize the message
        2. Validate the message format
        3. Delegate to the orchestrator for processing
        """
        ...
