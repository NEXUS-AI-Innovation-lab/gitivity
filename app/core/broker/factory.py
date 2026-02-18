"""Factory for creating broker consumers

Supports RabbitMQ (default) and Kafka (optional).
The broker type is configured via BROKER_TYPE environment variable.
"""
import logging

from app.config.settings import settings
from app.core.broker.base import BrokerConsumer
from app.core.broker.kafka_consumer import KafkaConsumer
from app.core.broker.rabbitmq_consumer import RabbitMQConsumer
from app.core.orchestrator import ProvisioningOrchestrator
from app.core.retry_manager import RetryManager
from app.utils.enums import BrokerType
from app.utils.exceptions import BrokerConnectionError

logger = logging.getLogger(__name__)


class BrokerConsumerFactory:
    """Factory for creating message broker consumers"""

    @staticmethod
    def create(
        orchestrator: ProvisioningOrchestrator,
        retry_manager: RetryManager,
        broker_type: str | None = None,
    ) -> BrokerConsumer:
        """Create a broker consumer based on configuration

        Args:
            orchestrator: The orchestrator for message processing
            retry_manager: The retry manager for failed operations
            broker_type: Override broker type (defaults to settings.BROKER_TYPE)

        Returns:
            Appropriate BrokerConsumer instance

        Raises:
            BrokerConnectionError: If broker type is not supported
        """
        broker = broker_type or settings.BROKER_TYPE

        logger.info(f"Creating broker consumer for type: {broker}")

        if broker == BrokerType.KAFKA.value or broker.lower() == "kafka":
            return KafkaConsumer(
                orchestrator=orchestrator,
                retry_manager=retry_manager,
            )
        elif broker == BrokerType.RABBITMQ.value or broker.lower() == "rabbitmq":
            return RabbitMQConsumer(
                orchestrator=orchestrator,
                retry_manager=retry_manager,
            )
        else:
            raise BrokerConnectionError(
                broker_type=broker,
                error_message=f"Unsupported broker type: {broker}",
            )


def get_consumer(
    orchestrator: ProvisioningOrchestrator,
    retry_manager: RetryManager,
) -> BrokerConsumer:
    """Convenience function to get a broker consumer

    Args:
        orchestrator: The orchestrator for message processing
        retry_manager: The retry manager for failed operations

    Returns:
        Configured BrokerConsumer instance
    """
    return BrokerConsumerFactory.create(orchestrator, retry_manager)
