import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import httpx
import pytest

from app.config.entitlement_sync import EntitlementSyncConfig
from app.config.settings import settings
from app.config.target_catalog import TargetDefinition, target_catalog
from app.services.entitlement_sync_service import EntitlementSyncService
from app.services.entitlement_removal_service import EntitlementRemovalService
from app.services.midpoint_client import MidPointClient
from app.utils.enums import OperationType


def sync_config(tmp_path, missing_cycles=3):
    return EntitlementSyncConfig.model_validate({
        "poll_interval_seconds": 60,
        "startup_delay_seconds": 0,
        "request_timeout_seconds": 5,
        "decommission_timeout_seconds": 123,
        "gateway_http_url": "http://gateway-http:5100",
        "gateway_api_url": "http://gateway-api:8100",
        "missing_confirmation_cycles": missing_cycles,
        "state_file": str(tmp_path / "sync-state.json"),
        "midpoint": {
            "resource_oid": "resource-oid",
            "import_shadows_on_addition": True,
            "object_classes": {"postgresql": "CustomPostgresqlProfileObjectClass"},
        },
    })


def target_definition():
    return TargetDefinition.model_validate({
        "id": "postgresql-demo",
        "type": "postgresql",
        "display_name": "PostgreSQL Demo",
        "entitlements": {
            "provider": "postgresql_roles",
            "association_ref": "postgresqlProfile",
        },
    })


def entitlement(key="demo-readonly"):
    return {
        "key": key,
        "native_name": key,
        "role_oid": f"oid-{key}",
        "role_name": f"Gateway - PostgreSQL Demo - {key}",
        "association_ref": "postgresqlProfile",
        "association_value": f"postgresql-demo.{key}",
    }


@pytest.mark.asyncio
async def test_reconcile_purges_historical_postgresql_login_account_without_email():
    inventory = SimpleNamespace(
        id="inventory-login",
        target_id="postgresql-demo",
        entitlement_key="ilies-chibane",
        native_name="ilies.chibane",
        role_oid="bogus-role-oid",
    )
    request = SimpleNamespace(id="pending-request")
    db = SimpleNamespace(
        provisioningoperation=SimpleNamespace(
            find_many=AsyncMock(return_value=[]),
        ),
        entitlementinventory=SimpleNamespace(
            find_many=AsyncMock(side_effect=[[inventory], []]),
            find_unique=AsyncMock(),
            delete=AsyncMock(),
        ),
        entitlementremovalrequest=SimpleNamespace(
            find_many=AsyncMock(return_value=[request]),
            find_first=AsyncMock(),
            update=AsyncMock(),
        ),
    )
    midpoint = AsyncMock()
    email = AsyncMock()

    result = await EntitlementRemovalService(
        db, email=email, midpoint=midpoint
    ).reconcile([{
        "target_id": "postgresql-demo",
        "target_type": "postgresql",
        "entitlements": [],
        "excluded_native_names": ["ilies.chibane"],
    }])

    assert result == {"tracked": 0, "created_removal_requests": []}
    midpoint.remove_role_everywhere.assert_awaited_once_with("bogus-role-oid")
    db.entitlementremovalrequest.update.assert_awaited_once()
    update_data = db.entitlementremovalrequest.update.await_args.kwargs["data"]
    assert update_data["status"] == "CANCELLED"
    db.entitlementinventory.delete.assert_awaited_once_with(
        where={"id": "inventory-login"}
    )
    email.send_html.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_uses_history_for_login_account_already_deleted():
    inventory = SimpleNamespace(
        id="inventory-login",
        target_id="postgresql-demo",
        entitlement_key="ilies-chibane",
        native_name="ilies.chibane",
        role_oid="bogus-role-oid",
    )
    operation = SimpleNamespace(
        original_message={"target_id": "postgresql-demo"},
        user_data={"username": "ilies.chibane"},
    )
    db = SimpleNamespace(
        provisioningoperation=SimpleNamespace(
            find_many=AsyncMock(return_value=[operation]),
        ),
        entitlementinventory=SimpleNamespace(
            find_many=AsyncMock(side_effect=[[inventory], []]),
            find_unique=AsyncMock(),
            delete=AsyncMock(),
        ),
        entitlementremovalrequest=SimpleNamespace(
            find_many=AsyncMock(return_value=[]),
            find_first=AsyncMock(),
            update=AsyncMock(),
        ),
    )
    midpoint = AsyncMock()

    result = await EntitlementRemovalService(
        db, email=AsyncMock(), midpoint=midpoint
    ).reconcile([{
        "target_id": "postgresql-demo",
        "target_type": "postgresql",
        "entitlements": [],
        "excluded_native_names": [],
    }])

    assert result["created_removal_requests"] == []
    midpoint.remove_role_everywhere.assert_awaited_once_with("bogus-role-oid")
    db.entitlementinventory.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_last_disappeared_target_role_requests_user_deletion(monkeypatch):
    db = SimpleNamespace(
        entitlementinventory=SimpleNamespace(find_many=AsyncMock(return_value=[]))
    )
    process = AsyncMock(return_value="delete-operation")
    monkeypatch.setattr(
        "app.services.entitlement_removal_service.ProvisioningOrchestrator.process_message",
        process,
    )
    approval_repo = AsyncMock()
    approval_repo.get_user_state.return_value = {
        "username": "donald.trump",
        "email": "donald@example.test",
        "first_name": "Donald",
        "last_name": "Trump",
    }
    monkeypatch.setattr(
        "app.services.entitlement_removal_service.RedisClient.get_client",
        AsyncMock(return_value=AsyncMock()),
    )
    monkeypatch.setattr(
        "app.services.entitlement_removal_service.ApprovalRedisRepository",
        lambda _redis: approval_repo,
    )
    request = SimpleNamespace(
        id="removal-request",
        target_id="postgresql-demo",
        role_oid="test-role-oid",
    )
    target = target_catalog.get("postgresql-demo")
    result = {
        "affected_users": [{
            "oid": "donald-oid",
            "username": "donald.trump",
            "remaining_role_oids": [],
        }]
    }

    operations = await EntitlementRemovalService(
        db, email=AsyncMock(), midpoint=AsyncMock()
    )._request_orphan_account_deletions(request, target, result)

    assert operations == ["delete-operation"]
    message = process.await_args.args[0]
    assert message.operation_type == OperationType.DELETE_USER
    assert message.target_id == "postgresql-demo"
    assert message.user_data.username == "donald.trump"
    assert message.user_data.email == "donald@example.test"
    assert message.metadata["midpoint_user_oid"] == "donald-oid"


@pytest.mark.asyncio
async def test_entitlement_approval_returns_before_cleanup_execution():
    request = SimpleNamespace(
        id="request-id",
        status="PENDING",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        approvers=[{"name": "Manager", "email": "manager@example.test"}],
        current_approver_index=0,
    )
    db = SimpleNamespace(
        entitlementremovalrequest=SimpleNamespace(
            find_first=AsyncMock(return_value=request),
            update=AsyncMock(),
        )
    )
    midpoint = AsyncMock()
    service = EntitlementRemovalService(db, email=AsyncMock(), midpoint=midpoint)

    result = await service.decide("valid-token", approved=True)

    assert result == {"status": "processing", "request_id": "request-id"}
    assert db.entitlementremovalrequest.update.await_args_list[-1].kwargs[
        "data"
    ]["status"] == "APPROVED"
    midpoint.remove_role_everywhere.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_processing_uses_dedicated_timeout_and_is_isolated(tmp_path):
    client = AsyncMock()
    client.post.side_effect = httpx.ReadTimeout("cleanup still running")
    service = EntitlementSyncService(
        sync_config(tmp_path), midpoint=AsyncMock(), http_client=client
    )

    await service._process_decommissions()

    assert client.post.await_args.kwargs["timeout"] == 123


def test_disappearance_requires_configured_successful_cycles(tmp_path):
    service = EntitlementSyncService(
        sync_config(tmp_path, missing_cycles=3),
        midpoint=AsyncMock(),
        http_client=AsyncMock(),
    )
    item = entitlement()
    previous = {"entitlements": {item["key"]: item}, "missing_counts": {}}
    missing_manifest = {"target_id": "postgresql-demo", "entitlements": []}

    first, state, _, first_removals = service._confirmed_manifest(
        missing_manifest, previous
    )
    second, state, _, second_removals = service._confirmed_manifest(
        missing_manifest, state
    )
    third, state, _, third_removals = service._confirmed_manifest(
        missing_manifest, state
    )

    assert [value["key"] for value in first["entitlements"]] == [item["key"]]
    assert [value["key"] for value in second["entitlements"]] == [item["key"]]
    assert third["entitlements"] == []
    assert state["entitlements"] == {}
    assert first_removals == []
    assert second_removals == []
    assert third_removals == [item]


@pytest.mark.asyncio
async def test_confirmed_disappearance_deletes_only_exact_midpoint_shadow(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={
                "object": {
                    "object": [
                        {
                            "oid": "matching-shadow",
                            "name": "ldap-test.EXTERNE",
                            "objectClass": "ri:CustomLdapGroupObjectClass",
                            "resourceRef": {"oid": "resource-oid"},
                        },
                        {
                            "oid": "other-target-shadow",
                            "name": "ldap-test.EXTERNE",
                            "objectClass": "ri:CustomLdapGroupObjectClass",
                            "resourceRef": {"oid": "other-resource"},
                        },
                    ]
                }
            })
        if request.method == "GET":
            return httpx.Response(200, json={"object": {"list": []}})
        return httpx.Response(204)

    client = httpx.AsyncClient(
        base_url="http://midpoint", transport=httpx.MockTransport(handler)
    )
    midpoint = MidPointClient(base_url="http://midpoint")
    midpoint._client = client

    deleted = await midpoint.delete_stale_entitlement_shadows(
        "resource-oid",
        "CustomLdapGroupObjectClass",
        "ldap-test",
        {"ldap-test.admins"},
    )

    assert deleted == ["ldap-test.EXTERNE"]
    assert requests[0].url.params["options"] == "raw"
    query = json.loads(requests[0].content)["query"]["filter"]["text"]
    assert 'resourceRef matches (oid = "resource-oid")' in query
    assert 'objectClass = "ri:CustomLdapGroupObjectClass"' in query
    assert requests[1].method == "GET"
    assert requests[2].method == "DELETE"
    assert requests[2].url.path == "/ws/rest/shadows/matching-shadow"
    assert requests[2].url.params["options"] == "raw"
    await midpoint.close()


@pytest.mark.asyncio
async def test_shadow_cleanup_removes_account_reference_before_deletion():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={
                "object": {"object": [{
                    "oid": "entitlement-shadow",
                    "name": "postgresql-ui.gateway-base",
                    "objectClass": "ri:CustomPostgresqlProfileObjectClass",
                    "resourceRef": {"oid": "resource-oid"},
                }]}
            })
        if request.method == "GET" and request.url.path == "/ws/rest/shadows":
            return httpx.Response(200, json={
                "object": {"list": [{"oid": "account-shadow"}]}
            })
        if request.method == "GET":
            return httpx.Response(200, json={
                "shadow": {
                    "referenceAttributes": {
                        "postgresqlProfile": [
                            {"oid": "entitlement-shadow", "type": "c:ShadowType"},
                            {"oid": "other-shadow", "type": "c:ShadowType"},
                        ]
                    }
                }
            })
        return httpx.Response(204)

    client = httpx.AsyncClient(
        base_url="http://midpoint", transport=httpx.MockTransport(handler)
    )
    midpoint = MidPointClient(base_url="http://midpoint")
    midpoint._client = client

    await midpoint.delete_stale_entitlement_shadows(
        "resource-oid",
        "CustomPostgresqlProfileObjectClass",
        "postgresql-ui",
        set(),
    )

    patch = next(request for request in requests if request.method == "PATCH")
    delta = json.loads(patch.content)["objectModification"]["itemDelta"][0]
    assert delta["path"] == "referenceAttributes/postgresqlProfile"
    assert delta["value"] == [
        {"oid": "entitlement-shadow", "type": "c:ShadowType"}
    ]
    delete = next(request for request in requests if request.method == "DELETE")
    assert requests.index(patch) < requests.index(delete)
    await midpoint.close()


@pytest.mark.asyncio
async def test_new_entitlement_creates_midpoint_role_and_reconciles(
    tmp_path, monkeypatch
):
    target = target_definition()
    item = entitlement()
    manifest = {
        "target_id": target.id,
        "target_type": target.type,
        "display_name": target.display_name,
        "entitlements": [item],
    }
    reconciled = []

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith("/runtime-targets/decommissions/process"):
            return httpx.Response(200, json={"decommissions": []})
        if request.url.path.endswith("/entitlement-removals/process-approved"):
            return httpx.Response(200, json={"processed": []})
        reconciled.extend(json.loads(request.content))
        return httpx.Response(
            200, json={"tracked": 1, "created_removal_requests": []}
        )

    midpoint = AsyncMock()
    midpoint.upsert_role_xml.return_value = "created"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(target_catalog, "targets", lambda: [target])
    monkeypatch.setattr(settings, "ENTITLEMENT_RECONCILE_TOKEN", "test-token")
    service = EntitlementSyncService(
        sync_config(tmp_path), midpoint=midpoint, http_client=client
    )

    result = await service.run_cycle()

    assert result["failed"] == {}
    midpoint.upsert_role_xml.assert_awaited_once()
    generated_xml = midpoint.upsert_role_xml.await_args.args[1]
    ElementTree.fromstring(generated_xml)
    assert "postgresql-demo.demo-readonly" in generated_xml
    midpoint.import_resource_object_class.assert_awaited_once_with(
        "resource-oid", "CustomPostgresqlProfileObjectClass"
    )
    assert reconciled[0]["target_id"] == "postgresql-demo"
    assert reconciled[0]["entitlements"][0]["key"] == "demo-readonly"
    await client.aclose()


@pytest.mark.asyncio
async def test_cycle_defers_shadow_deletion_until_removal_is_approved(
    tmp_path, monkeypatch
):
    target = target_definition()
    item = entitlement()
    config = sync_config(tmp_path, missing_cycles=3)
    config.midpoint.object_classes[target.type] = "CustomPostgresqlProfileObjectClass"
    config_path = tmp_path / "sync-state.json"
    config_path.write_text(json.dumps({
        "version": 1,
        "targets": {
            target.id: {
                "entitlements": {item["key"]: item},
                "missing_counts": {item["key"]: 2},
            }
        },
    }))

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={
                "target_id": target.id,
                "target_type": target.type,
                "entitlements": [],
            })
        return httpx.Response(
            200, json={"tracked": 0, "created_removal_requests": ["request-id"]}
        )

    midpoint = AsyncMock()
    midpoint.delete_stale_entitlement_shadows.return_value = [
        "postgresql-demo.demo-readonly"
    ]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(target_catalog, "targets", lambda: [target])
    monkeypatch.setattr(settings, "ENTITLEMENT_RECONCILE_TOKEN", "test-token")
    service = EntitlementSyncService(config, midpoint=midpoint, http_client=client)

    result = await service.run_cycle()

    midpoint.delete_stale_entitlement_shadows.assert_not_awaited()
    assert result["synchronized"][0]["reconciliation"][
        "created_removal_requests"
    ] == ["request-id"]
    await client.aclose()
