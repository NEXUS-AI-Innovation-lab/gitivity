import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def load_module():
    path = Path(__file__).parents[2] / "gateway-http" / "target_entitlements.py"
    spec = importlib.util.spec_from_file_location("target_entitlements", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_creates_stable_uniform_role_metadata():
    module = load_module()
    target = {
        "id": "ldap-test",
        "type": "ldap",
        "display_name": "LDAP_TEST",
        "entitlements": {
            "identifier": "{target}.{name}",
            "association_ref": "ldapGroup",
            "base_name": "gateway-base",
        },
    }

    roles = module.normalize(
        target,
        [("Comptabilite", "Equipe comptable"), ("gateway-base", "Base")],
    )

    base = next(role for role in roles if role["key"] == "base")
    accounting = next(role for role in roles if role["key"] == "comptabilite")
    assert base["association_value"] == "ldap-test.gateway-base"
    assert base["role_name"] == "Gateway - LDAP_TEST - Base"
    assert accounting["role_name"] == "Gateway - LDAP_TEST - Comptabilite"
    assert accounting["role_oid"] == module.normalize(
        target, [("Comptabilite", ""), ("gateway-base", "")]
    )[0]["role_oid"]


def test_normalize_refuses_manifest_without_native_base_role():
    module = load_module()
    target = {
        "id": "postgresql",
        "type": "postgresql",
        "entitlements": {
            "identifier": "{target}.{name}",
            "association_ref": "postgresqlRole",
            "base_name": "gateway-base",
        },
    }

    with pytest.raises(RuntimeError, match="gateway-base"):
        module.normalize(target, [("pg_read_all_data", "")])


def test_load_targets_merges_persistent_and_runtime_catalogues(tmp_path):
    module = load_module()
    persistent = tmp_path / "targets.yaml"
    runtime = tmp_path / "runtime-targets.yaml"
    persistent.write_text(
        "version: 1\ntargets:\n  - id: postgresql\n    type: postgresql\n",
        encoding="utf-8",
    )
    runtime.write_text(
        "version: 1\ntargets:\n  - id: postgresql-test\n    type: postgresql\n",
        encoding="utf-8",
    )

    targets = module.load_targets([persistent, runtime])

    assert [target["id"] for target in targets] == [
        "postgresql",
        "postgresql-test",
    ]


def test_load_targets_rejects_alias_collision_across_catalogues(tmp_path):
    module = load_module()
    persistent = tmp_path / "targets.yaml"
    runtime = tmp_path / "runtime-targets.yaml"
    persistent.write_text(
        "version: 1\ntargets:\n  - id: postgresql\n    aliases: [pg]\n",
        encoding="utf-8",
    )
    runtime.write_text(
        "version: 1\ntargets:\n  - id: postgresql-test\n    aliases: [pg]\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="is shared"):
        module.load_targets([persistent, runtime])


def test_postgresql_discovery_excludes_login_accounts(monkeypatch):
    module = load_module()
    executed = []

    class Cursor:
        def execute(self, query, params=None):
            executed.append((str(query), params))

        def fetchone(self):
            return (1,)

        def fetchall(self):
            # This is the result PostgreSQL would return after applying the
            # NOT rolcanlogin predicate: ilies.chibane is intentionally absent.
            return [("gateway-base",), ("readonly",)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    connection = SimpleNamespace(
        autocommit=False,
        cursor=lambda: Cursor(),
        close=lambda: None,
    )
    psycopg2 = ModuleType("psycopg2")
    psycopg2.connect = lambda **_kwargs: connection
    psycopg2.sql = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.sql", psycopg2.sql)

    entitlements, changed, login_accounts = module.postgresql_entitlements({
        "connection": {
            "host": "postgres",
            "port": 5432,
            "user": "provisioner",
            "password": "secret",
            "database": "demo",
        },
        "entitlements": {"base_name": "gateway-base"},
        "provisioning": {"managed_roles": []},
    })

    assert changed is False
    assert entitlements == [
        ("gateway-base", "PostgreSQL native role"),
        ("readonly", "PostgreSQL native role"),
    ]
    assert login_accounts == ["gateway-base", "readonly"]
    discovery_query = executed[-2][0]
    assert "WHERE NOT rolcanlogin" in discovery_query
    assert "WHERE rolcanlogin" in executed[-1][0]
