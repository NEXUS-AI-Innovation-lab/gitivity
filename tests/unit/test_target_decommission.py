import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.target_decommission_service import TargetDecommissionService


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value
        return True

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def scan_iter(self, match):
        prefix = match.removesuffix("*")
        for key in list(self.values):
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_begin_uses_redis_and_durable_history_without_duplicates(monkeypatch):
    target = SimpleNamespace(id="postgresql-ui", type="postgresql")
    monkeypatch.setattr(
        "app.services.target_decommission_service.target_catalog.get",
        lambda _target_id: target,
    )
    monkeypatch.setattr(
        "app.services.target_decommission_service.target_catalog.source",
        lambda _target_id: "runtime",
    )
    redis = FakeRedis(
        {
            "user_state:postgresql-ui:alice": json.dumps(
                {"username": "alice", "email": "alice@example.test"}
            )
        }
    )
    db = SimpleNamespace(
        provisioningoperation=SimpleNamespace(
            find_many=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        operation_type="UPDATE_USER",
                        status="SUCCESS",
                        original_message={"target_id": "postgresql-ui"},
                        user_data={"username": "alice"},
                    ),
                    SimpleNamespace(
                        operation_type="CREATE_USER",
                        status="SUCCESS",
                        original_message={"target_id": "postgresql-ui"},
                        user_data={"username": "bob"},
                    ),
                ]
            )
        ),
        entitlementremovalrequest=SimpleNamespace(
            find_many=AsyncMock(return_value=[]), update=AsyncMock()
        ),
    )
    process = AsyncMock(side_effect=["delete-alice", "delete-bob"])
    monkeypatch.setattr(
        "app.services.target_decommission_service.ProvisioningOrchestrator.process_message",
        process,
    )
    service = TargetDecommissionService(db)
    service._redis = AsyncMock(return_value=redis)

    result = await service.begin("postgresql-ui")

    assert result["status"] == "WAITING_APPROVALS"
    assert result["operation_ids"] == ["delete-alice", "delete-bob"]
    assert [call.args[0].user_data.username for call in process.await_args_list] == [
        "alice",
        "bob",
    ]


@pytest.mark.asyncio
async def test_process_waits_when_a_delete_is_not_successful():
    db = SimpleNamespace(
        provisioningoperation=SimpleNamespace(
            find_many=AsyncMock(
                return_value=[SimpleNamespace(status="APPROVAL_PENDING")]
            )
        )
    )
    redis = FakeRedis()
    service = TargetDecommissionService(db)
    service._redis = AsyncMock(return_value=redis)

    result = await service._process(
        {"target_id": "postgresql-ui", "operation_ids": ["delete-alice"]}
    )

    assert result["status"] == "WAITING_APPROVALS"


@pytest.mark.asyncio
async def test_begin_cancels_existing_approval_and_invalidates_token(monkeypatch):
    target = SimpleNamespace(id="postgresql-ui", type="postgresql")
    monkeypatch.setattr(
        "app.services.target_decommission_service.target_catalog.get",
        lambda _target_id: target,
    )
    monkeypatch.setattr(
        "app.services.target_decommission_service.target_catalog.source",
        lambda _target_id: "runtime",
    )
    operation = SimpleNamespace(
        id="pending-update",
        operation_type="UPDATE_USER",
        status="APPROVAL_PENDING",
        original_message={
            "target_id": "postgresql-ui",
            "metadata": {"source": "midpoint"},
        },
        user_data={"username": "alice"},
    )
    update = AsyncMock()
    entitlement_update = AsyncMock()
    db = SimpleNamespace(
        provisioningoperation=SimpleNamespace(
            find_many=AsyncMock(return_value=[operation]),
            update=update,
        ),
        entitlementremovalrequest=SimpleNamespace(
            find_many=AsyncMock(
                return_value=[SimpleNamespace(id="entitlement-removal")]
            ),
            update=entitlement_update,
        ),
    )
    redis = FakeRedis(
        {
            "approval:pending:pending-update": "{}",
            "approval:chain:pending-update": json.dumps(
                {"active_token": "old-token"}
            ),
            "approval:token:old-token": json.dumps(
                {"operation_id": "pending-update"}
            ),
        }
    )
    process = AsyncMock()
    monkeypatch.setattr(
        "app.services.target_decommission_service.ProvisioningOrchestrator.process_message",
        process,
    )
    service = TargetDecommissionService(db)
    service._redis = AsyncMock(return_value=redis)

    result = await service.begin("postgresql-ui")

    assert result["cancelled_operation_ids"] == ["pending-update"]
    assert result["cancelled_entitlement_request_ids"] == [
        "entitlement-removal"
    ]
    assert result["operation_ids"] == []
    assert not any(key.startswith("approval:") for key in redis.values)
    process.assert_not_awaited()
    assert update.await_args.kwargs["data"]["status"] == "FAILED"
    assert entitlement_update.await_args.kwargs["data"]["status"] == "CANCELLED"
    assert (
        entitlement_update.await_args.kwargs["data"]["decision_token_hash"]
        is None
    )
