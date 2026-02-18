"""Unit tests for ProvisioningOrchestrator"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.orchestrator import ProvisioningOrchestrator
from app.models.domain import (
    MidPointMessage,
    UserData,
    ValidationResponse,
    ProvisioningResult,
)
from app.utils.enums import OperationType, OperationStatus, TargetService
from app.utils.exceptions import ValidationRejectedError, ProvisioningError


class TestProvisioningOrchestrator:
    """Tests for ProvisioningOrchestrator"""

    @pytest.fixture
    def mock_db(self):
        """Mock Prisma database"""
        return MagicMock()

    @pytest.fixture
    def mock_n8n_client(self):
        """Mock n8n client"""
        client = AsyncMock()
        client.validate = AsyncMock(
            return_value=ValidationResponse(
                approved=True,
                validation_id="val-123",
            )
        )
        client.notify_success = AsyncMock(return_value=True)
        client.notify_failure = AsyncMock(return_value=True)
        return client

    @pytest.fixture
    def orchestrator(self, mock_db, mock_n8n_client):
        """Orchestrator with mocked dependencies"""
        return ProvisioningOrchestrator(db=mock_db, n8n_client=mock_n8n_client)

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
        mock_n8n_client,
    ):
        """Test successful message processing"""
        mock_operation = MagicMock()
        mock_operation.id = "op-123"
        mock_operation.status = OperationStatus.PENDING

        with patch.object(orchestrator, "_repo") as mock_repo, patch.object(
            orchestrator, "_audit"
        ) as mock_audit, patch(
            "app.core.orchestrator.ConnectorFactory"
        ) as mock_factory:
            mock_repo.create_operation = AsyncMock(return_value=mock_operation)
            mock_repo.get_by_id = AsyncMock(return_value=mock_operation)
            mock_repo.update_status = AsyncMock()
            mock_repo.mark_notification_sent = AsyncMock()
            mock_audit.log_operation_created = AsyncMock()
            mock_audit.log_status_change = AsyncMock()
            mock_audit.log_validation_sent = AsyncMock()
            mock_audit.log_validation_response = AsyncMock()
            mock_audit.log_provisioning_started = AsyncMock()
            mock_audit.log_provisioning_completed = AsyncMock()
            mock_audit.log_notification_sent = AsyncMock()

            # Mock connector
            mock_connector = AsyncMock()
            mock_connector.provision_user = AsyncMock(
                return_value=ProvisioningResult(
                    success=True,
                    service_user_id="testuser@%",
                    message="Created",
                )
            )
            mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
            mock_connector.__aexit__ = AsyncMock()
            mock_factory.create.return_value = mock_connector

            operation_id = await orchestrator.process_message(sample_message)

            assert operation_id == "op-123"
            mock_repo.create_operation.assert_called_once()
            mock_n8n_client.validate.assert_called_once()
            mock_connector.provision_user.assert_called_once()
            mock_n8n_client.notify_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_message_validation_rejected(
        self,
        orchestrator,
        sample_message,
        mock_n8n_client,
    ):
        """Test message processing when validation is rejected"""
        mock_operation = MagicMock()
        mock_operation.id = "op-123"
        mock_operation.status = OperationStatus.PENDING

        mock_n8n_client.validate = AsyncMock(
            return_value=ValidationResponse(
                approved=False,
                validation_id="val-123",
                reason="User blocked",
            )
        )

        with patch.object(orchestrator, "_repo") as mock_repo, patch.object(
            orchestrator, "_audit"
        ) as mock_audit:
            mock_repo.create_operation = AsyncMock(return_value=mock_operation)
            mock_repo.update_status = AsyncMock()
            mock_audit.log_operation_created = AsyncMock()
            mock_audit.log_status_change = AsyncMock()
            mock_audit.log_validation_sent = AsyncMock()
            mock_audit.log_validation_response = AsyncMock()

            with pytest.raises(ValidationRejectedError):
                await orchestrator.process_message(sample_message)

    @pytest.mark.asyncio
    async def test_process_message_provisioning_error(
        self,
        orchestrator,
        sample_message,
        mock_n8n_client,
    ):
        """Test message processing when provisioning fails"""
        mock_operation = MagicMock()
        mock_operation.id = "op-123"
        mock_operation.status = OperationStatus.VALIDATED

        with patch.object(orchestrator, "_repo") as mock_repo, patch.object(
            orchestrator, "_audit"
        ) as mock_audit, patch(
            "app.core.orchestrator.ConnectorFactory"
        ) as mock_factory:
            mock_repo.create_operation = AsyncMock(return_value=mock_operation)
            mock_repo.get_by_id = AsyncMock(return_value=mock_operation)
            mock_repo.update_status = AsyncMock()
            mock_audit.log_operation_created = AsyncMock()
            mock_audit.log_status_change = AsyncMock()
            mock_audit.log_validation_sent = AsyncMock()
            mock_audit.log_validation_response = AsyncMock()
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
        return ProvisioningOrchestrator(db=MagicMock())

    @pytest.mark.asyncio
    async def test_execute_create_user(self, orchestrator):
        """Test CREATE_USER operation execution"""
        mock_connector = AsyncMock()
        mock_connector.provision_user = AsyncMock(
            return_value=ProvisioningResult(
                success=True,
            )
        )

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
        mock_connector.update_user = AsyncMock(
            return_value=ProvisioningResult(
                success=True,
            )
        )

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
        mock_connector.delete_user = AsyncMock(
            return_value=ProvisioningResult(
                success=True,
            )
        )

        user_data = UserData(username="test")

        result = await orchestrator._execute_operation(
            mock_connector,
            OperationType.DELETE_USER,
            user_data,
        )

        mock_connector.delete_user.assert_called_once()
        assert result.success is True
