import re
import time
from pathlib import Path

import pytest

from app.config.target_catalog import TargetCatalog
from app.utils.enums import TargetService


def test_catalog_associations_exist_in_midpoint_resource_schema():
    root = Path(__file__).parents[2]
    catalog = TargetCatalog(root / "config" / "targets.yaml")
    resource = (root / "midpoint" / "ressource.xml").read_text(encoding="utf-8")
    association_refs = set(
        re.findall(r"<association>\s*<ref>ri:([^<]+)</ref>", resource)
    )

    for target in catalog.targets():
        if target.entitlements:
            assert target.entitlements.association_ref in association_refs


def write_catalog(path, *, host="${TEST_TARGET_HOST:-localhost}", second=""):
    path.write_text(
        f"""
version: 1
targets:
  - id: reporting
    type: postgresql
    display_name: Reporting
    aliases: [reports]
    connection:
      host: {host}
      port: ${{TEST_TARGET_PORT:-5432}}
    routing:
      entitlement_attributes: [reportingRole]
{second}
""",
        encoding="utf-8",
    )


def test_catalog_resolves_aliases_and_environment(tmp_path, monkeypatch):
    path = tmp_path / "targets.yaml"
    monkeypatch.setenv("TEST_TARGET_HOST", "db.internal")
    monkeypatch.setenv("TEST_TARGET_PORT", "5544")
    write_catalog(path)

    target = TargetCatalog(path).resolve("reports")

    assert target.id == "reporting"
    assert target.family == TargetService.POSTGRESQL
    assert target.connection["host"] == "db.internal"
    assert target.connection["port"] == 5544


def test_catalog_parses_deployment_metadata(tmp_path):
    path = tmp_path / "targets.yaml"
    write_catalog(
        path,
        second="""
""",
    )
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content
        + """
    deployment:
      environment:
        REPORTING_DB_HOST: reporting-db.internal
        REPORTING_DB_PORT: 5432
""",
        encoding="utf-8",
    )

    target = TargetCatalog(path).get("reporting")

    assert target.deployment.environment == {
        "REPORTING_DB_HOST": "reporting-db.internal",
        "REPORTING_DB_PORT": 5432,
    }


def test_catalog_hot_reloads_after_file_change(tmp_path):
    path = tmp_path / "targets.yaml"
    write_catalog(path, host="old-host")
    catalog = TargetCatalog(path)
    assert catalog.get("reporting").connection["host"] == "old-host"

    time.sleep(0.002)
    write_catalog(path, host="new-host")

    assert catalog.get("reporting").connection["host"] == "new-host"


def test_catalog_rejects_duplicate_aliases(tmp_path):
    path = tmp_path / "targets.yaml"
    write_catalog(
        path,
        second="""
  - id: analytics
    type: mongodb
    display_name: Analytics
    aliases: [reports]
""",
    )

    with pytest.raises(ValueError, match="shared"):
        TargetCatalog(path).reload()


def test_catalog_merges_and_hot_reloads_runtime_targets(tmp_path):
    path = tmp_path / "targets.yaml"
    runtime_path = tmp_path / "runtime-targets.yaml"
    write_catalog(path)
    catalog = TargetCatalog(path, runtime_path)

    catalog.add_runtime(
        catalog.get("reporting").model_copy(
            update={"id": "sandbox", "display_name": "Sandbox", "aliases": []}
        )
    )

    assert catalog.get("sandbox").connection["host"] == "localhost"
    assert catalog.source("reporting") == "persistent"
    assert catalog.source("sandbox") == "runtime"
    assert "sandbox" in runtime_path.read_text(encoding="utf-8")


def test_catalog_rejects_runtime_identifier_collision(tmp_path):
    path = tmp_path / "targets.yaml"
    runtime_path = tmp_path / "runtime-targets.yaml"
    write_catalog(path)
    catalog = TargetCatalog(path, runtime_path)

    with pytest.raises(ValueError, match="already exist"):
        catalog.add_runtime(
            catalog.get("reporting").model_copy(
                update={"id": "sandbox", "aliases": ["reports"]}
            )
        )


def test_catalog_only_removes_runtime_targets(tmp_path):
    path = tmp_path / "targets.yaml"
    runtime_path = tmp_path / "runtime-targets.yaml"
    write_catalog(path)
    catalog = TargetCatalog(path, runtime_path)
    runtime_target = catalog.get("reporting").model_copy(
        update={"id": "sandbox", "aliases": []}
    )
    catalog.add_runtime(runtime_target)

    catalog.remove_runtime("sandbox")

    with pytest.raises(KeyError):
        catalog.get("sandbox")
    with pytest.raises(ValueError, match="Persistent targets"):
        catalog.remove_runtime("reporting")


def test_persistent_export_overrides_preserved_runtime_entry(tmp_path):
    path = tmp_path / "targets.yaml"
    runtime_path = tmp_path / "runtime-targets.yaml"
    write_catalog(path)
    catalog = TargetCatalog(path, runtime_path)
    catalog.add_runtime(
        catalog.get("reporting").model_copy(
            update={"id": "sandbox", "display_name": "Runtime", "aliases": []}
        )
    )

    persistent = path.read_text(encoding="utf-8").replace(
        "id: reporting", "id: sandbox"
    ).replace("display_name: Reporting", "display_name: Persistent")
    path.write_text(persistent, encoding="utf-8")
    catalog.reload(force=True)

    assert catalog.get("sandbox").display_name == "Persistent"
    assert catalog.source("sandbox") == "persistent"
