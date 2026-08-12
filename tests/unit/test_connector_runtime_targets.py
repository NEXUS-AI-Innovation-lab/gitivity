import yaml

from app.api.v1.endpoints.connectors import (
    RuntimeTargetRequest,
    _midpoint_test_succeeded,
    build_runtime_target,
    export_target_yaml,
)


def postgresql_request() -> RuntimeTargetRequest:
    return RuntimeTargetRequest(
        id="postgresql-sandbox",
        type="postgresql",
        display_name="PostgreSQL Sandbox",
        aliases=["pg-sandbox"],
        connection={
            "host": "db.example.test",
            "port": 5432,
            "user": "provisioner",
            "password": "super-secret",
            "database": "sandbox",
            "connect_timeout": 10,
        },
    )


def test_build_runtime_target_adds_postgresql_routing_and_entitlements():
    target = build_runtime_target(postgresql_request())

    assert target.routing.entitlement_attributes == [
        "postgresqlProfile",
        "postgresqlGrants",
        "postgresqlRole",
    ]
    assert target.entitlements.provider == "postgresql_roles"
    assert target.entitlements.association_ref == "postgresqlProfile"
    assert target.provisioning["managed_roles"] == [
        {"name": "admin", "privileges": ["ALL"]},
        {"name": "readonly", "privileges": ["SELECT"]},
    ]


def test_export_runtime_target_uses_environment_placeholders_for_connection():
    exported = yaml.safe_load(export_target_yaml(build_runtime_target(postgresql_request())))
    target = exported["targets"][0]

    assert target["connection"]["host"] == "${POSTGRESQL_SANDBOX_HOST}"
    assert target["connection"]["password"] == "${POSTGRESQL_SANDBOX_PASSWORD}"
    assert target["deployment"]["environment"] == {
        "POSTGRESQL_SANDBOX_HOST": "db.example.test",
        "POSTGRESQL_SANDBOX_PORT": 5432,
        "POSTGRESQL_SANDBOX_USER": "provisioner",
        "POSTGRESQL_SANDBOX_PASSWORD": "CHANGE_ME",
        "POSTGRESQL_SANDBOX_DATABASE": "sandbox",
        "POSTGRESQL_SANDBOX_CONNECT_TIMEOUT": 10,
    }
    assert "super-secret" not in yaml.safe_dump(exported)


def test_midpoint_test_status_normalizes_operation_result():
    assert _midpoint_test_succeeded({"operationResult": {"status": "success"}})
    assert not _midpoint_test_succeeded({
        "operationResult": {"status": "fatal_error"}
    })
    assert not _midpoint_test_succeeded({"success": False, "error": "down"})
