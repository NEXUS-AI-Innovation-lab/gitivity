"""Pytest fixtures and configuration"""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.models.domain import (
    MidPointMessage,
    UserData,
    ValidationResponse,
    ProvisioningResult,
)
from app.utils.enums import OperationType, TargetService


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_user_data() -> UserData:
    """Sample user data for testing"""
    return UserData(
        username="testuser",
        email="testuser@example.com",
        first_name="Test",
        last_name="User",
        password="SecureP@ss123",
        roles=["read", "write"],
        attributes={"department": "IT"},
    )


@pytest.fixture
def sample_midpoint_message(sample_user_data: UserData) -> MidPointMessage:
    """Sample MidPoint message for testing"""
    return MidPointMessage(
        request_id="mp-req-12345",
        operation_type=OperationType.CREATE_USER,
        target_service=TargetService.MYSQL,
        user_data=sample_user_data,
        timestamp=datetime.utcnow(),
        metadata={"source": "midpoint", "priority": "normal"},
    )


@pytest.fixture
def sample_validation_response_approved() -> ValidationResponse:
    """Approved validation response"""
    return ValidationResponse(
        approved=True,
        validation_id="val-12345",
        reason=None,
        modified_data=None,
        metadata={},
    )


@pytest.fixture
def sample_validation_response_rejected() -> ValidationResponse:
    """Rejected validation response"""
    return ValidationResponse(
        approved=False,
        validation_id="val-12345",
        reason="User already exists in target system",
        modified_data=None,
        metadata={"rejection_code": "USER_EXISTS"},
    )


@pytest.fixture
def sample_provisioning_result_success() -> ProvisioningResult:
    """Successful provisioning result"""
    return ProvisioningResult(
        success=True,
        service_user_id="testuser@%",
        message="User created successfully",
        details={"host": "%", "database": "*"},
        error_code=None,
    )


@pytest.fixture
def sample_provisioning_result_failure() -> ProvisioningResult:
    """Failed provisioning result"""
    return ProvisioningResult(
        success=False,
        service_user_id=None,
        message="Connection refused",
        details={},
        error_code="CONNECTION_ERROR",
    )


@pytest.fixture
def mock_n8n_client() -> AsyncMock:
    """Mock n8n client"""
    client = AsyncMock()
    client.validate = AsyncMock(
        return_value=ValidationResponse(
            approved=True,
            validation_id="val-mock",
        )
    )
    client.notify_success = AsyncMock(return_value=True)
    client.notify_failure = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_connector() -> AsyncMock:
    """Mock provisioning connector"""
    connector = AsyncMock()
    connector.service_name = TargetService.MYSQL
    connector.connect = AsyncMock()
    connector.disconnect = AsyncMock()
    connector.health_check = AsyncMock(return_value=True)
    connector.provision_user = AsyncMock(
        return_value=ProvisioningResult(
            success=True,
            service_user_id="testuser@%",
            message="User created",
        )
    )
    connector.update_user = AsyncMock(
        return_value=ProvisioningResult(
            success=True,
            service_user_id="testuser@%",
            message="User updated",
        )
    )
    connector.delete_user = AsyncMock(
        return_value=ProvisioningResult(
            success=True,
            message="User deleted",
        )
    )
    connector.__aenter__ = AsyncMock(return_value=connector)
    connector.__aexit__ = AsyncMock(return_value=None)
    return connector


@pytest.fixture
def sample_kafka_message() -> bytes:
    """Sample Kafka message bytes"""
    return json.dumps(
        {
            "request_id": "mp-req-12345",
            "operation_type": "CREATE_USER",
            "target_service": "MYSQL",
            "user_data": {
                "username": "testuser",
                "email": "testuser@example.com",
                "password": "SecureP@ss123",
                "roles": ["read"],
            },
            "metadata": {"source": "test"},
        }
    ).encode("utf-8")


@pytest.fixture
def sample_operation_dict() -> dict:
    """Sample operation as dict (simulating DB record)"""
    return {
        "id": "op-12345",
        "midpoint_request_id": "mp-req-12345",
        "broker_message_id": None,
        "operation_type": "CREATE_USER",
        "target_service": "MYSQL",
        "status": "PENDING",
        "user_data": {"username": "testuser", "email": "test@example.com"},
        "original_message": {},
        "retry_count": 0,
        "max_retries": 3,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
