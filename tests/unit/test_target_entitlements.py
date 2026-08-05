import importlib.util
from pathlib import Path

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
