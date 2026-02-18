#!/usr/bin/env python3
"""Script to start the message broker consumer"""
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.logging import setup_logging
from app.config.settings import settings
from app.db import db
from app.core.orchestrator import ProvisioningOrchestrator
from app.core.retry_manager import RetryManager
from app.core.broker.factory import get_consumer

logger = logging.getLogger(__name__)

# Global consumer reference for signal handlers
consumer = None
shutdown_event = asyncio.Event()


async def run_retry_processor(retry_manager: RetryManager, orchestrator) -> None:
    """Background task to process pending retries"""
    logger.info("Starting retry processor...")

    while not shutdown_event.is_set():
        try:
            # Process pending retries every 30 seconds
            await asyncio.sleep(30)

            if shutdown_event.is_set():
                break

            processed = await retry_manager.process_pending_retries(orchestrator)
            if processed > 0:
                logger.info(f"Processed {processed} retry operations")

        except Exception as e:
            logger.error(f"Error in retry processor: {e}")

    logger.info("Retry processor stopped")


async def main() -> None:
    """Main entry point for the consumer"""
    global consumer

    # Setup logging
    setup_logging()
    logger.info(f"Starting Gateway IAM Consumer (broker: {settings.BROKER_TYPE})")

    # Connect to database
    logger.info("Connecting to database...")
    await db.connect()

    try:
        # Initialize components
        orchestrator = ProvisioningOrchestrator(db=db.client)
        retry_manager = RetryManager(db=db.client)

        # Create consumer
        consumer = get_consumer(
            orchestrator=orchestrator,
            retry_manager=retry_manager,
        )

        # Start retry processor in background
        retry_task = asyncio.create_task(
            run_retry_processor(retry_manager, orchestrator)
        )

        # Start consumer
        logger.info("Starting message consumer...")
        await consumer.start()

        # Wait for shutdown
        await shutdown_event.wait()

        # Cancel retry task
        retry_task.cancel()
        try:
            await retry_task
        except asyncio.CancelledError:
            pass

    except Exception as e:
        logger.exception(f"Consumer error: {e}")
        raise
    finally:
        # Stop consumer
        if consumer:
            await consumer.stop()

        # Disconnect from database
        logger.info("Disconnecting from database...")
        await db.disconnect()

        logger.info("Consumer shutdown complete")


def handle_signal(signum, frame) -> None:
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_event.set()

    if consumer:
        # Create task to stop consumer
        asyncio.create_task(consumer.stop())


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
