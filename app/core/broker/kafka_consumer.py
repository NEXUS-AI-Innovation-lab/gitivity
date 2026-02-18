"""Kafka consumer implementation (optional - RabbitMQ is the default broker)

This module provides Kafka support for environments that require it.
To use Kafka instead of RabbitMQ, set BROKER_TYPE=kafka in your .env file.
"""
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer

from app.config.settings import settings
from app.core.broker.base import BrokerConsumer
from app.core.orchestrator import ProvisioningOrchestrator
from app.core.retry_manager import RetryManager
from app.models.domain import MidPointMessage, UserData
from app.utils.enums import OperationType, TargetService
from app.utils.exceptions import MessageParsingError

logger = logging.getLogger(__name__)


class KafkaConsumer(BrokerConsumer):
    """Kafka consumer using aiokafka"""

    def __init__(
        self,
        orchestrator: ProvisioningOrchestrator,
        retry_manager: RetryManager,
    ) -> None:
        """Initialize Kafka consumer

        Args:
            orchestrator: The orchestrator to delegate message processing
            retry_manager: The retry manager for failed operations
        """
        super().__init__()
        self._orchestrator = orchestrator
        self._retry_manager = retry_manager
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        """Start consuming messages from Kafka"""
        logger.info(
            "Starting Kafka consumer",
            extra={
                "bootstrap_servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "topic": settings.KAFKA_TOPIC,
                "consumer_group": settings.KAFKA_CONSUMER_GROUP,
            },
        )

        self._consumer = AIOKafkaConsumer(
            settings.KAFKA_TOPIC,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            auto_offset_reset=settings.KAFKA_AUTO_OFFSET_RESET,
            enable_auto_commit=settings.KAFKA_ENABLE_AUTO_COMMIT,
            value_deserializer=lambda m: m,  # Keep as bytes, we'll deserialize ourselves
        )

        await self._consumer.start()
        self._running = True

        logger.info("Kafka consumer started, waiting for messages...")

        try:
            async for msg in self._consumer:
                if not self._running:
                    break

                logger.debug(
                    "Received Kafka message",
                    extra={
                        "topic": msg.topic,
                        "partition": msg.partition,
                        "offset": msg.offset,
                    },
                )

                try:
                    await self._process_message(msg.value)
                except Exception as e:
                    logger.error(
                        f"Error processing Kafka message: {e}",
                        extra={
                            "topic": msg.topic,
                            "partition": msg.partition,
                            "offset": msg.offset,
                        },
                    )
                    # Message will be reprocessed based on commit strategy

        except Exception as e:
            logger.error(f"Kafka consumer error: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Kafka consumer"""
        logger.info("Stopping Kafka consumer...")
        self._running = False

        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

        logger.info("Kafka consumer stopped")

    async def _process_message(self, message: bytes) -> None:
        """Process a Kafka message"""
        try:
            # Deserialize JSON
            data = json.loads(message.decode("utf-8"))

            # Parse into domain model
            midpoint_message = self._parse_message(data)

            # Process through orchestrator
            operation_id = await self._orchestrator.process_message(midpoint_message)

            logger.info(
                "Message processed successfully",
                extra={
                    "operation_id": operation_id,
                    "request_id": midpoint_message.request_id,
                },
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
            raise MessageParsingError(
                error_message=f"Invalid JSON: {e}",
                raw_message=message.decode("utf-8", errors="replace"),
            )
        except Exception as e:
            logger.error(f"Failed to process message: {e}")
            # Let the caller handle retry logic
            raise

    def _parse_message(self, data: dict[str, Any]) -> MidPointMessage:
        """Parse raw message data into MidPointMessage

        Args:
            data: Parsed JSON data

        Returns:
            MidPointMessage instance

        Raises:
            MessageParsingError: If message format is invalid
        """
        try:
            # Extract required fields
            request_id = data.get("request_id") or data.get("requestId")
            if not request_id:
                raise MessageParsingError(
                    error_message="Missing required field: request_id"
                )

            operation_type_str = data.get("operation_type") or data.get("operationType")
            if not operation_type_str:
                raise MessageParsingError(
                    error_message="Missing required field: operation_type"
                )

            target_service_str = data.get("target_service") or data.get("targetService")
            if not target_service_str:
                raise MessageParsingError(
                    error_message="Missing required field: target_service"
                )

            user_data_raw = data.get("user_data") or data.get("userData") or {}

            # Parse enums
            try:
                operation_type = OperationType(operation_type_str.upper())
            except ValueError:
                raise MessageParsingError(
                    error_message=f"Invalid operation_type: {operation_type_str}"
                )

            try:
                target_service = TargetService(target_service_str.upper())
            except ValueError:
                raise MessageParsingError(
                    error_message=f"Invalid target_service: {target_service_str}"
                )

            # Parse user data
            user_data = UserData(
                username=user_data_raw.get("username", ""),
                email=user_data_raw.get("email"),
                first_name=user_data_raw.get("first_name")
                or user_data_raw.get("firstName"),
                last_name=user_data_raw.get("last_name")
                or user_data_raw.get("lastName"),
                password=user_data_raw.get("password"),
                roles=user_data_raw.get("roles", []),
                attributes=user_data_raw.get("attributes", {}),
            )

            return MidPointMessage(
                request_id=request_id,
                operation_type=operation_type,
                target_service=target_service,
                user_data=user_data,
                metadata=data.get("metadata", {}),
            )

        except MessageParsingError:
            raise
        except Exception as e:
            raise MessageParsingError(
                error_message=f"Failed to parse message: {e}",
                raw_message=str(data)[:500],
            )
