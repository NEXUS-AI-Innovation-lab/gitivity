import sys
import types

import pytest

from app.config.target_catalog import target_catalog
from app.core.broker.rabbitmq_consumer import RabbitMQConsumer
from app.utils.enums import OperationType, TargetService


def consumer_without_dependencies() -> RabbitMQConsumer:
    return object.__new__(RabbitMQConsumer)


@pytest.fixture(autouse=True)
def no_durable_target_state(monkeypatch):
    async def empty_state(_username):
        return set()

    monkeypatch.setattr(
        RabbitMQConsumer,
        "_previous_targets_from_redis",
        staticmethod(empty_state),
    )


def test_native_message_resolves_catalog_alias():
    message = consumer_without_dependencies()._parse_native_format({
        "request_id": "native-1",
        "operation_type": "CREATE_USER",
        "target_service": "mongo",
        "user_data": {"username": "alice", "password": "A7!vQ2#kL9@z"},
    })

    assert message.target_id == "mongodb"
    assert message.target_service == TargetService.MONGODB
    assert message.target_key == "mongodb"


@pytest.mark.asyncio
async def test_midpoint_roles_route_to_configured_target_ids():
    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "CREATE",
        "uid": "user-oid-1",
        "attributes": {
            "username": "alice",
            "password": "A7!vQ2#kL9@z",
            "roles": ["mongo", "pg"],
            "mongodbRoles": ["readWrite", "dbAdmin"],
        },
    })

    assert {message.target_id for message in messages} == {"mongodb", "postgresql"}
    assert all(message.operation_type == OperationType.CREATE_USER for message in messages)
    mongodb = next(message for message in messages if message.target_id == "mongodb")
    assert mongodb.user_data.attributes["mongodbRoles"] == ["readWrite", "dbAdmin"]


@pytest.mark.asyncio
async def test_entitlement_attribute_routes_without_role_alias():
    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "CREATE",
        "uid": "user-oid-2",
        "attributes": {
            "username": "bob",
            "password": "G4!sN8#qP2@x",
            "mongodbRoles": ["read"],
        },
    })

    assert [message.target_id for message in messages] == ["mongodb"]


@pytest.mark.asyncio
async def test_namespaced_entitlements_are_native_for_selected_target():
    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "CREATE",
        "uid": "user-oid-namespaced",
        "attributes": {
            "username": "alice",
            "password": "A7!vQ2#kL9@z",
            "roles": ["mongodb"],
            "mongodbRoles": ["mongodb.readWrite@target_db"],
        },
    })

    assert len(messages) == 1
    assert messages[0].user_data.attributes["mongodbRoles"] == ["readWrite@target_db"]


@pytest.mark.asyncio
async def test_update_projects_roles_and_entitlements_per_target():
    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "UPDATE",
        "uid": "user-oid-multi-target",
        "attributes": {
            "username": "alice",
            "roles": ["ldap-test", "postgresql", "postgresql-demo"],
            "ldapGroups": ["ldap-test.test-group"],
            "postgresqlRole": [
                "postgresql-demo.gateway-base",
                "postgresql.gateway-base",
            ],
        },
    })

    by_target = {message.target_id: message for message in messages}
    assert set(by_target) >= {"ldap-test", "postgresql", "postgresql-demo"}

    ldap = by_target["ldap-test"].user_data
    assert ldap.roles == ["ldap-test"]
    assert ldap.attributes["roles"] == ["ldap-test"]
    assert ldap.attributes["ldapGroups"] == ["test-group"]
    assert "postgresqlRole" not in ldap.attributes

    postgresql_demo = by_target["postgresql-demo"].user_data
    assert postgresql_demo.roles == ["postgresql-demo"]
    assert postgresql_demo.attributes["postgresqlRole"] == ["gateway-base"]
    assert "ldapGroups" not in postgresql_demo.attributes

    postgresql = by_target["postgresql"].user_data
    assert postgresql.roles == ["postgresql"]
    assert postgresql.attributes["postgresqlRole"] == ["gateway-base"]


@pytest.mark.asyncio
async def test_adding_another_target_does_not_change_existing_target_snapshot():
    consumer = consumer_without_dependencies()
    common_attributes = {
        "username": "alice",
        "email": "alice@example.test",
        "ldapGroups": ["ldap-test.test-group"],
    }
    before = await consumer._parse_midpoint_format({
        "operation": "UPDATE",
        "uid": "user-oid-target-snapshot",
        "attributes": {
            **common_attributes,
            "roles": ["ldap-test"],
        },
    })
    after = await consumer._parse_midpoint_format({
        "operation": "UPDATE",
        "uid": "user-oid-target-snapshot",
        "attributes": {
            **common_attributes,
            "roles": ["ldap-test", "postgresql-demo"],
            "postgresqlRole": ["postgresql-demo.gateway-base"],
        },
    })

    ldap_before = next(message for message in before if message.target_id == "ldap-test")
    ldap_after = next(message for message in after if message.target_id == "ldap-test")

    assert ldap_before.user_data.model_dump() == ldap_after.user_data.model_dump()


@pytest.mark.asyncio
async def test_explicit_target_role_wins_over_shared_family_entitlement():
    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "CREATE",
        "uid": "user-oid-ldap-test",
        "attributes": {
            "username": "dave",
            "password": "H7!mR4#kP9@w",
            "roles": ["ldap-test"],
            "ldapGroups": ["cn=Developers,ou=Groups,dc=lissi,dc=fr"],
        },
    })

    assert [message.target_id for message in messages] == ["ldap-test"]
    assert messages[0].user_data.attributes["ldapGroups"] == [
        "cn=Developers,ou=Groups,dc=lissi,dc=fr"
    ]


@pytest.mark.asyncio
async def test_ldap_role_removal_uses_configured_cleanup_mode():
    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "UPDATE",
        "uid": "user-oid-3",
        "attributes": {
            "username": "carol",
            "removedRoles": ["ldap"],
            "ldapGroups": [],
        },
    })

    assert len(messages) == 1
    assert messages[0].target_id == "ldap"
    expected = (
        OperationType.UPDATE_USER
        if target_catalog.get("ldap").routing.delete_mode == "update"
        else OperationType.DELETE_USER
    )
    assert messages[0].operation_type == expected


@pytest.mark.asyncio
async def test_last_assignment_removal_uses_durable_state_after_restart(monkeypatch):
    async def previous_targets(_username):
        return {"postgresql-demo"}

    monkeypatch.setattr(
        RabbitMQConsumer,
        "_previous_targets_from_redis",
        staticmethod(previous_targets),
    )

    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "UPDATE",
        "uid": "user-oid-after-restart",
        "attributes": {
            "username": "alice",
            "roles": [],
            "postgresqlRole": [],
        },
    })

    assert len(messages) == 1
    assert messages[0].target_id == "postgresql-demo"
    assert messages[0].operation_type == OperationType.DELETE_USER


@pytest.mark.asyncio
async def test_delete_enriches_roles_from_midpoint_assignments(monkeypatch):
    class FakeMidPointClient:
        async def get_user(self, request_id):
            assert request_id == "user-oid-delete"
            return {
                "assignment": [
                    {
                        "targetRef": {
                            "oid": "role-1",
                            "type": "c:RoleType",
                            "targetName": {"orig": "mongodb"},
                        }
                    }
                ]
            }

        async def get_role(self, oid):
            raise AssertionError("targetName should avoid role lookup")

    fake_module = types.SimpleNamespace(midpoint_client=FakeMidPointClient())
    monkeypatch.setitem(sys.modules, "app.services.midpoint_client", fake_module)

    messages = await consumer_without_dependencies()._parse_midpoint_format({
        "operation": "DELETE",
        "uid": "user-oid-delete",
        "attributes": {
            "username": "MongoDB_TEST",
        },
    })

    assert [message.target_id for message in messages] == ["mongodb"]
    assert messages[0].operation_type == OperationType.DELETE_USER
    assert messages[0].user_data.roles == ["mongodb"]
