"""Bootstrap and discover native entitlements for one catalogue target.

The command fails closed: connection/discovery errors are never replaced with
invented roles. Its JSON output is consumed by Ansible to generate MidPoint
roles uniformly for default and additional target instances.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
import xmlrpc.client
from pathlib import Path
from typing import Any

import ldap3
import yaml

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
ANSIBLE_UUID_NAMESPACE = uuid.UUID("361e6d51-faec-444a-9079-341386da8e2e")


def interpolate(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: interpolate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate(item) for item in value]
    if not isinstance(value, str):
        return value
    resolved = ENV_PATTERN.sub(
        lambda match: os.getenv(match.group(1), match.group(2) or ""), value
    )
    return (
        yaml.safe_load(resolved)
        if ENV_PATTERN.fullmatch(value) and resolved
        else resolved
    )


def load_targets(paths: list[Path]) -> list[dict[str, Any]]:
    """Merge enabled targets from persistent and optional runtime catalogues."""
    targets: list[dict[str, Any]] = []
    identifiers: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        document = interpolate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        for target in document.get("targets", []):
            if not target.get("enabled", True):
                continue
            target_id = str(target.get("id", "")).strip().lower()
            if not target_id:
                raise RuntimeError(f"Target without id in {path}")
            for identifier in [target_id, *target.get("aliases", [])]:
                normalized = str(identifier).strip().lower()
                owner = identifiers.get(normalized)
                if owner and owner != target_id:
                    raise RuntimeError(
                        f"Target identifier {normalized!r} is shared by "
                        f"{owner!r} and {target_id!r}"
                    )
                identifiers[normalized] = target_id
            targets.append(target)
    return targets


def load_target(
    path: Path,
    target_id: str,
    runtime_path: Path | None = None,
) -> dict[str, Any]:
    paths = [path, *([runtime_path] if runtime_path is not None else [])]
    for target in load_targets(paths):
        if target.get("id") == target_id:
            return target
    raise RuntimeError(f"Enabled target {target_id!r} not found")


def role_key(name: str, base_name: str) -> str:
    if name.casefold() == base_name.casefold():
        return "base"
    key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not key:
        raise RuntimeError(f"Entitlement name {name!r} cannot form a stable key")
    return key


def normalize(
    target: dict[str, Any], names: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    config = target["entitlements"]
    base_name = config.get("base_name", "gateway-base")
    seen: set[str] = set()
    result = []
    for name, description in sorted(names, key=lambda item: item[0].casefold()):
        key = role_key(name, base_name)
        if key in seen:
            raise RuntimeError(
                f"Entitlement key collision on target {target['id']!r}: {key!r}"
            )
        seen.add(key)
        value = config.get("identifier", "{name}").format(
            target=target["id"],
            name=name,
            role=name,
            database=target.get("connection", {}).get("database", ""),
        )
        result.append(
            {
                "key": key,
                "label": "Base" if key == "base" else name,
                "native_name": name,
                "association_ref": config["association_ref"],
                "association_value": value,
                "description": description,
                "is_base": key == "base",
                "role_oid": str(
                    uuid.uuid5(
                        ANSIBLE_UUID_NAMESPACE,
                        f"gateway-iam:{target['id']}:{key}",
                    )
                ),
                "role_name": (
                    f"Gateway - {target.get('display_name', target['id'])} - "
                    f"{'Base' if key == 'base' else name}"
                ),
            }
        )
    if "base" not in seen:
        raise RuntimeError(
            f"Native base entitlement {base_name!r} was not discovered on {target['id']!r}"
        )
    return result


def ldap_entitlements(target: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    config = target["connection"]
    provisioning = target.get("provisioning", {})
    entitlement = target["entitlements"]
    server = ldap3.Server(
        config["host"],
        port=int(config.get("port", 389)),
        use_ssl=bool(config.get("use_ssl", False)),
        get_info=ldap3.NONE,
        connect_timeout=int(config.get("connect_timeout", 10)),
    )
    connection = ldap3.Connection(
        server,
        user=config["bind_dn"],
        password=config["bind_password"],
        auto_bind=True,
    )
    changed = False
    try:
        groups_base = config["groups_base_dn"]
        base_name = entitlement.get("base_name", "gateway-base")
        group_dn = f"cn={base_name},{groups_base}"
        if not connection.search(group_dn, "(objectClass=*)", search_scope=ldap3.BASE):
            object_class = provisioning.get("group_object_class", "groupOfNames")
            member_attribute = provisioning.get("group_member_attribute", "member")
            initial_member = provisioning.get("group_initial_member", config["bind_dn"])
            attributes: dict[str, Any] = {"cn": base_name}
            if object_class.casefold() in {"groupofnames", "groupofuniquenames"}:
                attributes[member_attribute] = initial_member
            if not connection.add(
                group_dn,
                object_class=["top", object_class],
                attributes=attributes,
            ):
                raise RuntimeError(f"Cannot create {group_dn}: {connection.result}")
            changed = True

        classes = config.get("group_object_classes", ["groupOfNames"])
        group_filter = "(|{})".format(
            "".join(f"(objectClass={item})" for item in classes)
        )
        if not connection.search(
            groups_base,
            group_filter,
            search_scope=ldap3.SUBTREE,
            attributes=["cn", "description"],
        ):
            if connection.result.get("result") != 0:
                raise RuntimeError(f"LDAP group discovery failed: {connection.result}")
        names = [
            (
                str(entry.cn),
                str(entry.description) if hasattr(entry, "description") else "",
            )
            for entry in connection.entries
            if hasattr(entry, "cn")
        ]
        return names, changed
    finally:
        connection.unbind()


def mongodb_entitlements(target: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    from pymongo import MongoClient

    config = target["connection"]
    database = config["database"]
    base_name = target["entitlements"].get("base_name", "gateway-base")
    client = MongoClient(
        host=config["host"],
        port=int(config.get("port", 27017)),
        username=config.get("user"),
        password=config.get("password"),
        authSource=config.get("auth_source", "admin"),
        serverSelectionTimeoutMS=int(config.get("connect_timeout", 10)) * 1000,
    )
    changed = False
    try:
        client.admin.command("ping")
        existing = (
            client[database]
            .command({"rolesInfo": base_name, "showPrivileges": False})
            .get("roles", [])
        )
        if not existing:
            client[database].command(
                {"createRole": base_name, "privileges": [], "roles": []}
            )
            changed = True
        roles = (
            client[database]
            .command(
                {"rolesInfo": 1, "showBuiltinRoles": True, "showPrivileges": False}
            )
            .get("roles", [])
        )
        return [
            (
                role["role"],
                "MongoDB built-in role"
                if role.get("isBuiltin")
                else "MongoDB custom role",
            )
            for role in roles
            if role.get("role") and role.get("db") == database
        ], changed
    finally:
        client.close()


def postgresql_entitlements(
    target: dict[str, Any],
) -> tuple[list[tuple[str, str]], bool, list[str]]:
    import psycopg2
    from psycopg2 import sql

    config = target["connection"]
    base_name = target["entitlements"].get("base_name", "gateway-base")
    connection = psycopg2.connect(
        host=config["host"],
        port=int(config.get("port", 5432)),
        user=config["user"],
        password=config["password"],
        dbname=config["database"],
        connect_timeout=int(config.get("connect_timeout", 10)),
    )
    connection.autocommit = True
    changed = False
    try:
        with connection.cursor() as cursor:
            managed = [
                {"name": base_name, "privileges": []},
                *target.get("provisioning", {}).get("managed_roles", []),
            ]
            for role in managed:
                name = role["name"]
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"
                        ).format(sql.Identifier(name))
                    )
                    changed = True
                privileges = {value.upper() for value in role.get("privileges", [])}
                if "ALL" in privileges:
                    cursor.execute(
                        sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                            sql.Identifier(config["database"]), sql.Identifier(name)
                        )
                    )
                    cursor.execute(
                        sql.SQL("GRANT ALL PRIVILEGES ON SCHEMA public TO {}").format(
                            sql.Identifier(name)
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {}"
                        ).format(sql.Identifier(name))
                    )
                    cursor.execute(
                        sql.SQL(
                            "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {}"
                        ).format(sql.Identifier(name))
                    )
                elif "SELECT" in privileges:
                    cursor.execute(
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                            sql.Identifier(config["database"]), sql.Identifier(name)
                        )
                    )
                    cursor.execute(
                        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                            sql.Identifier(name)
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}"
                        ).format(sql.Identifier(name))
                    )
            # PostgreSQL stores both login accounts and group/privilege roles in
            # pg_roles. Only NOLOGIN roles are native entitlements; exposing a
            # LOGIN account here would create a bogus MidPoint application role
            # and a disappearance approval when that account is deleted.
            cursor.execute(
                "SELECT rolname FROM pg_roles "
                "WHERE NOT rolcanlogin ORDER BY rolname"
            )
            entitlements = [
                (row[0], "PostgreSQL native role") for row in cursor.fetchall()
            ]
            cursor.execute(
                "SELECT rolname FROM pg_roles "
                "WHERE rolcanlogin ORDER BY rolname"
            )
            login_accounts = [row[0] for row in cursor.fetchall()]
            return entitlements, changed, login_accounts
    finally:
        connection.close()


def mysql_entitlements(target: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    import pymysql

    config = target["connection"]
    base_name = target["entitlements"].get("base_name", "gateway-base")
    connection = pymysql.connect(
        host=config["host"],
        port=int(config.get("port", 3306)),
        user=config["user"],
        password=config["password"],
        database=config.get("database"),
        connect_timeout=int(config.get("connect_timeout", 10)),
        autocommit=True,
    )
    changed = False
    try:
        with connection.cursor() as cursor:
            managed = [
                {"name": base_name, "privileges": []},
                *target.get("provisioning", {}).get("managed_roles", []),
            ]
            database = str(config["database"]).replace("`", "``")
            for role in managed:
                name = role["name"]
                escaped = name.replace("`", "``")
                cursor.execute(
                    "SELECT 1 FROM mysql.user WHERE User=%s AND Host='%%'", (name,)
                )
                if cursor.fetchone() is None:
                    cursor.execute(f"CREATE ROLE `{escaped}`")
                    changed = True
                privileges = {value.upper() for value in role.get("privileges", [])}
                if "ALL" in privileges:
                    cursor.execute(
                        f"GRANT ALL PRIVILEGES ON `{database}`.* TO `{escaped}`"
                    )
                elif "SELECT" in privileges:
                    cursor.execute(f"GRANT SELECT ON `{database}`.* TO `{escaped}`")
            cursor.execute(
                "SELECT User FROM mysql.user "
                "WHERE account_locked='Y' AND authentication_string='' ORDER BY User"
            )
            return [(row[0], "MySQL native role") for row in cursor.fetchall()], changed
    finally:
        connection.close()


def odoo_entitlements(target: dict[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    config = target["connection"]
    url = str(config["url"]).rstrip("/")
    database = config["database"]
    username = config["username"]
    password = config["password"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(database, username, password, {})
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    base_name = target["entitlements"].get("base_name", "gateway-base")
    ids = models.execute_kw(
        database, uid, password, "res.groups", "search", [[("name", "=", base_name)]]
    )
    changed = False
    if not ids:
        models.execute_kw(
            database, uid, password, "res.groups", "create", [{"name": base_name}]
        )
        changed = True
    groups = models.execute_kw(
        database,
        uid,
        password,
        "res.groups",
        "search_read",
        [[]],
        {"fields": ["name", "comment"], "order": "name"},
    )
    return [
        (item["name"], item.get("comment") or "Odoo group") for item in groups
    ], changed


PROVIDERS = {
    "ldap_groups": ldap_entitlements,
    "mongodb_roles": mongodb_entitlements,
    "postgresql_roles": postgresql_entitlements,
    "mysql_roles": mysql_entitlements,
    "odoo_groups": odoo_entitlements,
}


def synchronize(target: dict[str, Any]) -> dict[str, Any]:
    entitlement = target.get("entitlements")
    if not entitlement:
        raise RuntimeError(f"Target {target['id']!r} has no entitlements configuration")
    provider_name = entitlement["provider"]
    if provider_name not in PROVIDERS:
        raise RuntimeError(f"Unsupported entitlement provider {provider_name!r}")
    last_error: Exception | None = None
    for attempt in range(30):
        try:
            provider_result = PROVIDERS[provider_name](target)
            names, changed = provider_result[:2]
            manifest = {
                "target_id": target["id"],
                "target_type": target["type"],
                "display_name": target.get("display_name", target["id"]),
                "changed": changed,
                "entitlements": normalize(target, names),
            }
            if len(provider_result) > 2:
                manifest["excluded_native_names"] = provider_result[2]
            return manifest
        except Exception as exc:  # pragma: no cover - service startup timing
            last_error = exc
            if attempt == 29:
                break
            time.sleep(2)
    raise RuntimeError(
        f"Entitlement synchronization failed for {target['id']}: {last_error}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--catalog",
        default=os.getenv("TARGET_CATALOG_PATH", "/app/config/targets.yaml"),
    )
    args = parser.parse_args()
    target = load_target(Path(args.catalog), args.target)
    print(json.dumps(synchronize(target), ensure_ascii=False))


if __name__ == "__main__":
    main()
