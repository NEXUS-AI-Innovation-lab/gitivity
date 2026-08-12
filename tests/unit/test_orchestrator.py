"""Unit tests for ProvisioningOrchestrator"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.orchestrator import ProvisioningOrchestrator
from app.models.domain import MidPointMessage, ProvisioningResult, UserData
from app.utils.enums import OperationStatus, OperationType, TargetService
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
    async def test_update_after_successful_delete_is_recorded_as_create(
        self, orchestrator
    ):
        message = MidPointMessage(
            request_id="mp-recreate",
            operation_type=OperationType.UPDATE_USER,
            target_service=TargetService.POSTGRESQL,
            target_id="postgresql-demo",
            user_data=UserData(
                username="donald.trump", email="donald@example.test"
            ),
        )
        operation = MagicMock(id="recreate-op", status=OperationStatus.PENDING)
        approval_repo = AsyncMock()
        approval_repo.check_rejected_create.return_value = False
        approval_repo.was_user_deleted.return_value = True

        with patch.object(orchestrator, "_repo") as repo, \
             patch.object(orchestrator, "_audit") as audit, \
             patch.object(orchestrator, "_get_approval_repo", AsyncMock(return_value=approval_repo)), \
             patch.object(orchestrator, "_request_approval", AsyncMock()) as request_approval, \
             patch("app.core.orchestrator.settings") as mocked_settings:
            mocked_settings.APPROVAL_ENABLED = True
            repo.create_operation = AsyncMock(return_value=operation)
            repo.update_status = AsyncMock()
            audit.log_operation_created = AsyncMock()
            audit.log_status_change = AsyncMock()

            await orchestrator.process_message(message)

        assert message.operation_type == OperationType.CREATE_USER
        assert repo.create_operation.await_args.kwargs["operation_type"] == (
            OperationType.CREATE_USER
        )
        assert request_approval.await_args.args[1].operation_type == (
            OperationType.CREATE_USER
        )

    @pytest.mark.asyncio
    async def test_failed_provisioning_after_approval_marks_operation_failed(
        self,
        orchestrator,
        sample_message,
    ):
        pending_operation = MagicMock()
        pending_operation.id = "op-approval-failure"
        pending_operation.status = OperationStatus.APPROVAL_PENDING
        processing_operation = MagicMock()
        processing_operation.id = pending_operation.id
        processing_operation.status = OperationStatus.PROCESSING
        approval_repo = AsyncMock()

        with patch.object(orchestrator, "_repo") as mock_repo, \
             patch.object(orchestrator, "_audit") as mock_audit, \
             patch.object(
                 orchestrator,
                 "_get_approval_repo",
                 new_callable=AsyncMock,
                 return_value=approval_repo,
             ), \
             patch.object(
                 orchestrator,
                 "_reconstruct_midpoint_message",
                 new_callable=AsyncMock,
                 return_value=sample_message,
             ), \
             patch.object(
                 orchestrator,
                 "_provision_to_target",
                 new_callable=AsyncMock,
                 side_effect=ProvisioningError(
                     operation_id=pending_operation.id,
                     target_service=TargetService.POSTGRESQL,
                     error_message="invalid PostgreSQL role payload",
                     is_retriable=False,
                 ),
             ), \
             patch.object(orchestrator, "_update_status", new_callable=AsyncMock):
            mock_repo.get_by_id = AsyncMock(
                side_effect=[pending_operation, processing_operation]
            )
            mock_repo.update_status = AsyncMock()
            mock_audit.log_approval_response = AsyncMock()
            mock_audit.log_status_change = AsyncMock()
            mock_audit.log_error = AsyncMock()

            with pytest.raises(ProvisioningError):
                await orchestrator.process_approval_response(
                    pending_operation.id,
                    approved=True,
                    reason="Approved",
                    worker_id="approver",
                )

            mock_repo.update_status.assert_awaited_once()
            assert mock_repo.update_status.await_args.kwargs["status"] == (
                OperationStatus.FAILED
            )

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

    @pytest.mark.asyncio
    async def test_finds_identical_pending_update_with_reordered_roles(
        self,
        orchestrator,
    ):
        message = MidPointMessage(
            request_id="mp-mongodb-2",
            operation_type=OperationType.UPDATE_USER,
            target_service=TargetService.MONGODB,
            target_id="mongodb",
            user_data=UserData(
                username="Mario",
                email="mario@example.com",
                roles=["mongodb", "EXTERNE"],
                attributes={
                    "mongodbRoles": [
                        "readWrite@target_db",
                        "dbAdmin@target_db",
                    ]
                },
            ),
        )
        approval_repo = AsyncMock()
        approval_repo.list_all_pending.return_value = [
            {
                "operation_id": "pending-1",
                "operation_type": OperationType.UPDATE_USER.value,
                "target_service": "mongodb",
                "user_data": {
                    "username": "Mario",
                    "email": "mario@example.com",
                    "first_name": None,
                    "last_name": None,
                    "password": None,
                    "roles": ["EXTERNE", "mongodb"],
                    "attributes": {
                        "mongodbRoles": [
                            "dbAdmin@target_db",
                            "readWrite@target_db",
                        ]
                    },
                },
            }
        ]
        orchestrator._approval_redis_repo = approval_repo

        duplicate = await orchestrator._find_duplicate_pending_update(message)

        assert duplicate == "pending-1"

    @pytest.mark.asyncio
    async def test_does_not_match_pending_update_with_different_payload(
        self,
        orchestrator,
    ):
        message = MidPointMessage(
            request_id="mp-mongodb-3",
            operation_type=OperationType.UPDATE_USER,
            target_service=TargetService.MONGODB,
            target_id="mongodb",
            user_data=UserData(
                username="Mario",
                attributes={"mongodbRoles": ["readWrite@target_db"]},
            ),
        )
        approval_repo = AsyncMock()
        approval_repo.list_all_pending.return_value = [
            {
                "operation_id": "pending-1",
                "operation_type": OperationType.UPDATE_USER.value,
                "target_service": "mongodb",
                "user_data": {
                    "username": "Mario",
                    "email": None,
                    "first_name": None,
                    "last_name": None,
                    "password": None,
                    "roles": [],
                    "attributes": {"mongodbRoles": ["dbAdmin@target_db"]},
                },
            }
        ]
        orchestrator._approval_redis_repo = approval_repo

        duplicate = await orchestrator._find_duplicate_pending_update(message)

        assert duplicate is None

    def test_diff_ignores_global_roles_from_legacy_snapshot(self, orchestrator):
        old_state = {
            "username": "alice",
            "email": "alice@example.test",
            "roles": ["ldap-test", "postgresql-demo"],
            "attributes": {
                "roles": ["ldap-test", "postgresql-demo"],
                "ldapGroups": ["test-group"],
                "postgresqlRole": ["postgresql-demo.gateway-base"],
            },
        }
        projected_ldap_state = {
            "username": "alice",
            "email": "alice@example.test",
            "roles": ["ldap-test"],
            "attributes": {
                "roles": ["ldap-test"],
                "ldapGroups": ["test-group"],
            },
        }

        assert orchestrator._compute_user_diff(old_state, projected_ldap_state) == []

    def test_diff_detects_target_entitlement_change(self, orchestrator):
        old_state = {
            "username": "alice",
            "attributes": {"ldapGroups": ["test-group"]},
        }
        new_state = {
            "username": "alice",
            "attributes": {"ldapGroups": ["admins", "test-group"]},
        }

        changes = orchestrator._compute_user_diff(old_state, new_state)

        assert [change["field"] for change in changes] == ["ldapGroups"]

    def test_diff_ignores_scalar_vs_singleton_entitlement_encoding(
        self, orchestrator
    ):
        old_state = {
            "username": "alice",
            "attributes": {"postgresqlRole": "gateway-base"},
        }
        new_state = {
            "username": "alice",
            "attributes": {"postgresqlRole": ["gateway-base"]},
        }

        assert orchestrator._compute_user_diff(old_state, new_state) == []

    def test_diff_detects_removal_of_last_target_entitlement(self, orchestrator):
        old_state = {
            "username": "alice",
            "attributes": {"ldapGroups": ["test-group"]},
        }
        new_state = {
            "username": "alice",
            "attributes": {"ldapGroups": []},
        }

        changes = orchestrator._compute_user_diff(old_state, new_state)

        assert changes == [
            {"field": "ldapGroups", "old": "['test-group']", "new": "[]"}
        ]


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
