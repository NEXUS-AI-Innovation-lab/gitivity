"""Run continuous entitlement discovery and MidPoint role synchronization."""

import asyncio
import logging
import signal

from app.config.entitlement_sync import load_entitlement_sync_config
from app.config.logging import setup_logging
from app.config.settings import settings
from app.services.entitlement_sync_service import EntitlementSyncService

logger = logging.getLogger(__name__)
shutdown_event = asyncio.Event()


def handle_signal(signum, frame) -> None:
    logger.info("Received signal %s, stopping entitlement synchronization", signum)
    shutdown_event.set()


async def main() -> None:
    setup_logging()
    config = load_entitlement_sync_config(settings.ENTITLEMENT_SYNC_CONFIG_PATH)
    if not config.enabled:
        logger.info("Continuous entitlement synchronization is disabled")
        return
    if not settings.ENTITLEMENT_RECONCILE_TOKEN:
        raise RuntimeError("ENTITLEMENT_RECONCILE_TOKEN must be configured")

    service = EntitlementSyncService(config)
    try:
        if config.startup_delay_seconds:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=config.startup_delay_seconds
                )
                return
            except TimeoutError:
                pass

        while not shutdown_event.is_set():
            result = await service.run_cycle()
            logger.info("Entitlement synchronization cycle completed: %s", result)
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=config.poll_interval_seconds
                )
            except TimeoutError:
                pass
    finally:
        await service.close()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    asyncio.run(main())
