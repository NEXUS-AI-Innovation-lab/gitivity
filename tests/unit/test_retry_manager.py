"""Unit tests for RetryManager"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.retry_manager import RetryManager


class TestRetryManager:
    """Tests for RetryManager"""

    @pytest.fixture
    def mock_db(self):
        """Mock Prisma database"""
        return MagicMock()

    @pytest.fixture
    def retry_manager(self, mock_db):
        """RetryManager instance with mocked dependencies"""
        return RetryManager(mock_db)

    def test_calculate_next_retry_first_attempt(self, retry_manager):
        """Test retry delay calculation for first attempt"""
        with patch("app.core.retry_manager.settings") as mock_settings:
            mock_settings.RETRY_INITIAL_DELAY = 5
            mock_settings.RETRY_BACKOFF_MULTIPLIER = 2.0
            mock_settings.RETRY_MAX_DELAY = 300

            next_retry, delay = retry_manager.calculate_next_retry(0)

            assert delay == 5  # 5 * 2^0 = 5
            assert next_retry > datetime.utcnow()

    def test_calculate_next_retry_second_attempt(self, retry_manager):
        """Test retry delay calculation for second attempt"""
        with patch("app.core.retry_manager.settings") as mock_settings:
            mock_settings.RETRY_INITIAL_DELAY = 5
            mock_settings.RETRY_BACKOFF_MULTIPLIER = 2.0
            mock_settings.RETRY_MAX_DELAY = 300

            next_retry, delay = retry_manager.calculate_next_retry(1)

            assert delay == 10  # 5 * 2^1 = 10

    def test_calculate_next_retry_third_attempt(self, retry_manager):
        """Test retry delay calculation for third attempt"""
        with patch("app.core.retry_manager.settings") as mock_settings:
            mock_settings.RETRY_INITIAL_DELAY = 5
            mock_settings.RETRY_BACKOFF_MULTIPLIER = 2.0
            mock_settings.RETRY_MAX_DELAY = 300

            next_retry, delay = retry_manager.calculate_next_retry(2)

            assert delay == 20  # 5 * 2^2 = 20

    def test_calculate_next_retry_max_delay_cap(self, retry_manager):
        """Test that retry delay is capped at max delay"""
        with patch("app.core.retry_manager.settings") as mock_settings:
            mock_settings.RETRY_INITIAL_DELAY = 5
            mock_settings.RETRY_BACKOFF_MULTIPLIER = 2.0
            mock_settings.RETRY_MAX_DELAY = 60  # Cap at 60 seconds

            next_retry, delay = retry_manager.calculate_next_retry(10)

            assert delay == 60  # Capped at max delay

    @pytest.mark.asyncio
    async def test_schedule_retry_success(self, retry_manager, mock_db):
        """Test successful retry scheduling"""
        mock_operation = MagicMock()
        mock_operation.retry_count = 0
        mock_operation.max_retries = 3

        with patch.object(retry_manager, "_repo") as mock_repo, \
             patch.object(retry_manager, "_audit") as mock_audit, \
             patch("app.core.retry_manager.settings") as mock_settings:

            mock_settings.RETRY_INITIAL_DELAY = 5
            mock_settings.RETRY_BACKOFF_MULTIPLIER = 2.0
            mock_settings.RETRY_MAX_DELAY = 300

            mock_repo.get_by_id = AsyncMock(return_value=mock_operation)
            mock_repo.reset_for_retry = AsyncMock()
            mock_audit.log_retry_scheduled = AsyncMock()

            result = await retry_manager.schedule_retry("op-123")

            assert result is True
            mock_repo.reset_for_retry.assert_called_once()
            mock_audit.log_retry_scheduled.assert_called_once()

    @pytest.mark.asyncio
    async def test_schedule_retry_max_retries_exceeded(self, retry_manager, mock_db):
        """Test that operation goes to DLQ when max retries exceeded"""
        mock_operation = MagicMock()
        mock_operation.retry_count = 3
        mock_operation.max_retries = 3

        with patch.object(retry_manager, "_repo") as mock_repo, \
             patch("app.core.retry_manager.DLQManager") as mock_dlq_class:

            mock_repo.get_by_id = AsyncMock(return_value=mock_operation)
            mock_dlq = AsyncMock()
            mock_dlq_class.return_value = mock_dlq

            result = await retry_manager.schedule_retry("op-123")

            assert result is False
            mock_dlq.send_to_dlq.assert_called_once()

    @pytest.mark.asyncio
    async def test_schedule_retry_operation_not_found(self, retry_manager, mock_db):
        """Test retry scheduling when operation not found"""
        with patch.object(retry_manager, "_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=None)

            result = await retry_manager.schedule_retry("op-nonexistent")

            assert result is False
