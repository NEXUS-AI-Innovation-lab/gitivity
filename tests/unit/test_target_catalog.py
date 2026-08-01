import time

import pytest

from app.config.target_catalog import TargetCatalog
from app.utils.enums import TargetService


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
      midpoint_database: reporting
""",
        encoding="utf-8",
    )

    target = TargetCatalog(path).get("reporting")

    assert target.deployment.environment == {
        "REPORTING_DB_HOST": "reporting-db.internal",
        "REPORTING_DB_PORT": 5432,
    }
    assert target.deployment.midpoint_database == "reporting"


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
