import json
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import httpx
import pytest

from app.config.entitlement_sync import EntitlementSyncConfig
from app.config.settings import settings
from app.config.target_catalog import TargetDefinition, target_catalog
from app.services.entitlement_sync_service import EntitlementSyncService
from app.services.midpoint_client import MidPointClient


def sync_config(tmp_path, missing_cycles=3):
    return EntitlementSyncConfig.model_validate({
        "poll_interval_seconds": 60,
        "startup_delay_seconds": 0,
        "request_timeout_seconds": 5,
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
    assert requests[1].method == "DELETE"
    assert requests[1].url.path == "/ws/rest/shadows/matching-shadow"
    assert requests[1].url.params["options"] == "raw"
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
async def test_cycle_deletes_shadow_after_confirmed_disappearance(
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

    midpoint.delete_stale_entitlement_shadows.assert_awaited_once_with(
        "resource-oid",
        "CustomPostgresqlProfileObjectClass",
        "postgresql-demo",
        set(),
    )
    assert result["synchronized"][0]["reconciliation"][
        "created_removal_requests"
    ] == ["request-id"]
    await client.aclose()
