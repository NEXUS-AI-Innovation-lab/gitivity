import pytest

from app.core.broker.rabbitmq_consumer import RabbitMQConsumer
from app.utils.enums import OperationType, TargetService


def consumer_without_dependencies() -> RabbitMQConsumer:
    return object.__new__(RabbitMQConsumer)


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
    assert messages[0].operation_type == OperationType.UPDATE_USER
