"""Unit tests for ProvisioningOrchestrator"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.orchestrator import ProvisioningOrchestrator
from app.models.domain import MidPointMessage, UserData, ProvisioningResult
from app.utils.enums import OperationType, OperationStatus, TargetService
from app.utils.exceptions import ProvisioningError


class TestProvisioningOrchestrator:
    """Tests for ProvisioningOrchestrator"""

    @pytest.fixture
    def mock_db(self):
        """Mock Prisma database"""
        return AsyncMock()

    @pytest.fixture
    def orchestrator(self, mock_db):
        """Orchestrator with mocked dependencies"""
        return ProvisioningOrchestrator(db=mock_db)

    @pytest.fixture
    def sample_message(self) -> MidPointMessage:
        """Sample MidPoint message"""
        return MidPointMessage(
            request_id="mp-123",
            operation_type=OperationType.CREATE_USER,
            target_service=TargetService.MYSQL,
            user_data=UserData(
                username="testuser",
                password="pass123",
                email="test@example.com",
            ),
        )

    @pytest.mark.asyncio
    async def test_process_message_success(
        self,
        orchestrator,
        sample_message,
    ):
        """Test successful message processing (approval bypassed)"""
        mock_operation = MagicMock()
        mock_operation.id = "op-123"
        mock_operation.status = OperationStatus.PENDING

        with patch.object(orchestrator, "_repo") as mock_repo, \
             patch.object(orchestrator, "_audit") as mock_audit, \
             patch.object(orchestrator, "_request_approval", new_callable=AsyncMock) as mock_approval, \
             patch("app.core.orchestrator.settings") as mock_settings, \
             patch("app.core.orchestrator.ConnectorFactory") as mock_factory:

            mock_settings.APPROVAL_ENABLED = False

            mock_repo.create_operation = AsyncMock(return_value=mock_operation)
            mock_repo.get_by_id = AsyncMock(return_value=mock_operation)
            mock_repo.update_status = AsyncMock()
            mock_repo.mark_notification_sent = AsyncMock()
            mock_audit.log_operation_created = AsyncMock()
            mock_audit.log_status_change = AsyncMock()
            mock_audit.log_provisioning_started = AsyncMock()
            mock_audit.log_provisioning_completed = AsyncMock()
            mock_audit.log_notification_sent = AsyncMock()

            # Mock connector
            mock_connector = AsyncMock()
            mock_connector.provision_user = AsyncMock(return_value=ProvisioningResult(
                success=True,
                service_user_id="testuser@%",
                message="Created",
            ))
            mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
            mock_connector.__aexit__ = AsyncMock()
            mock_factory.create.return_value = mock_connector

            operation_id = await orchestrator.process_message(sample_message)

            assert operation_id == "op-123"
            mock_repo.create_operation.assert_called_once()
            mock_approval.assert_not_called()
            mock_connector.provision_user.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_message_approval_requested(
        self,
        orchestrator,
        sample_message,
    ):
        """Test that approval is requested when APPROVAL_ENABLED is True"""
        mock_operation = MagicMock()
        mock_operation.id = "op-123"
        mock_operation.status = OperationStatus.PENDING

        with patch.object(orchestrator, "_repo") as mock_repo, \
             patch.object(orchestrator, "_audit") as mock_audit, \
             patch.object(orchestrator, "_request_approval", new_callable=AsyncMock) as mock_approval, \
             patch("app.core.orchestrator.settings") as mock_settings:

            mock_settings.APPROVAL_ENABLED = True

            mock_repo.create_operation = AsyncMock(return_value=mock_operation)
            mock_repo.update_status = AsyncMock()
            mock_audit.log_operation_created = AsyncMock()
            mock_audit.log_status_change = AsyncMock()

            operation_id = await orchestrator.process_message(sample_message)

            assert operation_id == "op-123"
            mock_repo.create_operation.assert_called_once()
            mock_approval.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_message_provisioning_error(
        self,
        orchestrator,
        sample_message,
    ):
        """Test message processing when provisioning fails"""
        mock_operation = MagicMock()
        mock_operation.id = "op-123"
        mock_operation.status = OperationStatus.VALIDATED

        with patch.object(orchestrator, "_repo") as mock_repo, \
             patch.object(orchestrator, "_audit") as mock_audit, \
             patch.object(orchestrator, "_request_approval", new_callable=AsyncMock), \
             patch("app.core.orchestrator.settings") as mock_settings, \
             patch("app.core.orchestrator.ConnectorFactory") as mock_factory:

            mock_settings.APPROVAL_ENABLED = False

            mock_repo.create_operation = AsyncMock(return_value=mock_operation)
            mock_repo.get_by_id = AsyncMock(return_value=mock_operation)
            mock_repo.update_status = AsyncMock()
            mock_audit.log_operation_created = AsyncMock()
            mock_audit.log_status_change = AsyncMock()
            mock_audit.log_provisioning_started = AsyncMock()
            mock_audit.log_error = AsyncMock()

            # Mock connector that raises error
            mock_connector = AsyncMock()
            mock_connector.provision_user = AsyncMock(
                side_effect=ProvisioningError(
                    operation_id="op-123",
                    target_service=TargetService.MYSQL,
                    error_message="Connection refused",
                    is_retriable=True,
                )
            )
            mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
            mock_connector.__aexit__ = AsyncMock()
            mock_factory.create.return_value = mock_connector

            with pytest.raises(ProvisioningError):
                await orchestrator.process_message(sample_message)

            mock_audit.log_error.assert_called()


class TestExecuteOperation:
    """Tests for _execute_operation method"""

    @pytest.fixture
    def orchestrator(self):
        """Orchestrator instance"""
        return ProvisioningOrchestrator(db=AsyncMock())

    @pytest.mark.asyncio
    async def test_execute_create_user(self, orchestrator):
        """Test CREATE_USER operation execution"""
        mock_connector = AsyncMock()
        mock_connector.provision_user = AsyncMock(return_value=ProvisioningResult(
            success=True,
        ))

        user_data = UserData(username="test", password="pass")

        result = await orchestrator._execute_operation(
            mock_connector,
            OperationType.CREATE_USER,
            user_data,
        )

        mock_connector.provision_user.assert_called_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_update_user(self, orchestrator):
        """Test UPDATE_USER operation execution"""
        mock_connector = AsyncMock()
        mock_connector.update_user = AsyncMock(return_value=ProvisioningResult(
            success=True,
        ))

        user_data = UserData(username="test")

        result = await orchestrator._execute_operation(
            mock_connector,
            OperationType.UPDATE_USER,
            user_data,
        )

        mock_connector.update_user.assert_called_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_delete_user(self, orchestrator):
        """Test DELETE_USER operation execution"""
        mock_connector = AsyncMock()
        mock_connector.delete_user = AsyncMock(return_value=ProvisioningResult(
            success=True,
        ))

        user_data = UserData(username="test")

        result = await orchestrator._execute_operation(
            mock_connector,
            OperationType.DELETE_USER,
            user_data,
        )

        mock_connector.delete_user.assert_called_once()
        assert result.success is True
