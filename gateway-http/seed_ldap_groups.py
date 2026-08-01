"""Idempotently seed LDAP groups declared in the target catalogue."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import ldap3
import yaml

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: interpolate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate(item) for item in value]
    if not isinstance(value, str):
        return value

    resolved = ENV_PATTERN.sub(
        lambda match: os.getenv(match.group(1), match.group(2) or ""),
        value,
    )
    if ENV_PATTERN.fullmatch(value) and resolved:
        return yaml.safe_load(resolved)
    return resolved


def load_target(path: Path, target_id: str) -> dict[str, Any]:
    document = interpolate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    for target in document.get("targets", []):
        if target.get("enabled", True) and target.get("id") == target_id:
            return target
    raise RuntimeError(f"Enabled LDAP target '{target_id}' not found")


def connect(connection: dict[str, Any]) -> ldap3.Connection:
    server = ldap3.Server(
        connection["host"],
        port=int(connection.get("port", 389)),
        use_ssl=bool(connection.get("use_ssl", False)),
        get_info=ldap3.NONE,
    )
    last_error: Exception | None = None
    for _ in range(30):
        try:
            return ldap3.Connection(
                server,
                user=connection["bind_dn"],
                password=connection["bind_password"],
                auto_bind=True,
            )
        except Exception as exc:  # pragma: no cover - depends on service startup
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"Cannot connect to LDAP: {last_error}")


def ensure_entry(
    connection: ldap3.Connection,
    dn: str,
    object_classes: list[str],
    attributes: dict[str, Any],
) -> bool:
    if connection.search(dn, "(objectClass=*)", search_scope=ldap3.BASE):
        return False
    if not connection.add(dn, object_class=object_classes, attributes=attributes):
        raise RuntimeError(f"Cannot create {dn}: {connection.result}")
    return True


def seed(target: dict[str, Any]) -> list[str]:
    connection_config = target.get("connection", {})
    provisioning = target.get("provisioning", {})
    groups = provisioning.get("seed_groups", [])
    if not groups:
        return []

    base_dn = connection_config["base_dn"]
    groups_base_dn = connection_config.get("groups_base_dn", f"ou=Groups,{base_dn}")
    object_class = provisioning.get("group_object_class", "groupOfNames")
    member_attribute = provisioning.get("group_member_attribute", "member")
    initial_member = provisioning.get(
        "group_initial_member",
        connection_config["bind_dn"],
    )

    connection = connect(connection_config)
    created: list[str] = []
    try:
        groups_ou = groups_base_dn.split(",", 1)[0].split("=", 1)[1]
        if ensure_entry(
            connection,
            groups_base_dn,
            ["top", "organizationalUnit"],
            {"ou": groups_ou},
        ):
            created.append(groups_base_dn)

        for group_name in groups:
            group_dn = f"cn={group_name},{groups_base_dn}"
            if ensure_entry(
                connection,
                group_dn,
                ["top", object_class],
                {"cn": group_name, member_attribute: initial_member},
            ):
                created.append(group_dn)
    finally:
        connection.unbind()
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--catalog",
        default=os.getenv("TARGET_CATALOG_PATH", "/app/config/targets.yaml"),
    )
    args = parser.parse_args()

    created = seed(load_target(Path(args.catalog), args.target))
    print(json.dumps({"changed": bool(created), "created": created}))


if __name__ == "__main__":
    main()
