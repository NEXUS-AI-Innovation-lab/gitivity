#!/usr/bin/env python3
"""
Gateway HTTP Simple - Reçoit les opérations de Midpoint via HTTP
Supporte aussi les entitlements (groupes LDAP, profils SQL et rôles MongoDB)
"""

from flask import Flask, request, jsonify
import json
import os
import re
from datetime import datetime
import yaml

app = Flask(__name__)

# Charger la configuration
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
TARGET_CATALOG_FILE = os.getenv(
    'TARGET_CATALOG_PATH',
    os.path.join(os.path.dirname(__file__), 'config', 'targets.yaml'),
)
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

def load_config():
    """Charge la configuration depuis config.json"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur chargement config: {e}")
        return {}


def load_targets():
    """Load targets.yaml and resolve its ${VAR:-default} placeholders."""
    def interpolate(value):
        if isinstance(value, dict):
            return {key: interpolate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [interpolate(item) for item in value]
        if not isinstance(value, str):
            return value
        resolved = ENV_PATTERN.sub(
            lambda match: os.getenv(match.group(1), match.group(2) or ''),
            value,
        )
        return yaml.safe_load(resolved) if ENV_PATTERN.fullmatch(value) and resolved else resolved

    with open(TARGET_CATALOG_FILE, 'r', encoding='utf-8') as stream:
        document = interpolate(yaml.safe_load(stream) or {})
    return [target for target in document.get('targets', []) if target.get('enabled', True)]


def get_target(target_id=None, target_type=None):
    """Resolve a target instance, falling back to the first target of its type."""
    targets = load_targets()
    if target_id:
        normalized = target_id.lower()
        for target in targets:
            if normalized == target['id'] or normalized in target.get('aliases', []):
                return target
    return next((target for target in targets if target.get('type') == target_type), None)

def get_ldap_groups():
    """Discover groups from every enabled LDAP target in targets.yaml."""
    ldap_targets = [target for target in load_targets() if target.get('type') == 'ldap']
    all_groups = []
    for target in ldap_targets:
        all_groups.extend(_fetch_ldap_groups_for_target(target))
    return all_groups


def _fetch_ldap_groups_for_target(target):
    """Fetch one target's groups, namespacing additional LDAP instances."""
    target_id = target['id']
    connection = target.get('connection', {})
    prefix = '' if target_id == target.get('type') else f'{target_id}.'

    if not connection.get('host'):
        return []

    try:
        import ldap3
        ldap_host = connection.get('host', 'localhost')
        ldap_port = int(connection.get('port', 389))
        use_ssl = bool(connection.get('use_ssl', False))
        server = ldap3.Server(
            ldap_host,
            port=ldap_port,
            use_ssl=use_ssl,
            get_info=ldap3.ALL,
        )
        conn = ldap3.Connection(
            server,
            user=connection.get('bind_dn'),
            password=connection.get('bind_password'),
            auto_bind=True
        )

        groups_base = connection.get(
            'groups_base_dn',
            f"ou=Groups,{connection.get('base_dn', '')}",
        )
        group_object_classes = connection.get(
            'group_object_classes',
            ['groupOfNames', 'groupOfUniqueNames'],
        )
        group_filter = '(|{})'.format(
            ''.join(
                f'(objectClass={object_class})'
                for object_class in group_object_classes
            )
        )
        conn.search(
            search_base=groups_base,
            search_filter=group_filter,
            attributes=['cn', 'description', 'member']
        )

        groups = []
        for entry in conn.entries:
            cn = str(entry.cn) if hasattr(entry, 'cn') else ''
            groups.append({
                'dn': str(entry.entry_dn),
                'cn': f"{prefix}{cn}",
                'target': target_id,
                'description': str(entry.description) if hasattr(entry, 'description') else ''
            })

        conn.unbind()
        return groups

    except ImportError:
        print("Module ldap3 non installé - aucun groupe LDAP retourné")
        return []
    except Exception as e:
        print(f"Erreur LDAP ({target_id}): {e}")
        return []


def get_mongodb_roles(target_id=None):
    """Discover built-in and custom roles from the configured MongoDB database.

    The static configuration is only a resilience fallback, so MidPoint can
    still load the resource schema while MongoDB is restarting.
    """
    target = get_target(target_id, 'mongodb') or {}
    connection = target.get('connection', {})
    entitlements = target.get('entitlements', {})
    database = connection.get('database', 'target_db')
    fallback = [
        {
            'roleName': role,
            'database': database,
            'description': 'Rôle MongoDB de secours',
        }
        for role in entitlements.get('fallback', [])
    ]

    try:
        from pymongo import MongoClient

        client = MongoClient(
            host=connection.get('host', 'localhost'),
            port=int(connection.get('port', 27017)),
            username=connection.get('user', 'root'),
            password=connection.get('password', ''),
            authSource=connection.get('auth_source', 'admin'),
            serverSelectionTimeoutMS=5000,
        )
        client.admin.command('ping')
        result = client[database].command({
            'rolesInfo': 1,
            'showBuiltinRoles': True,
            'showPrivileges': False,
        })
        roles = []
        for role in result.get('roles', []):
            role_name = role.get('role')
            role_db = role.get('db', database)
            if not role_name or role_db != database:
                continue
            roles.append({
                'roleName': role_name,
                'database': role_db,
                'description': 'Rôle MongoDB intégré' if role.get('isBuiltin') else 'Rôle MongoDB personnalisé',
            })
        client.close()
        return sorted(roles, key=lambda item: item['roleName'].lower()) or fallback
    except Exception as exc:
        print(f"MongoDB indisponible pour la découverte des rôles: {exc}")
        return fallback

def print_separator():
    print("=" * 60)

@app.route('/create', methods=['POST'])
def create():
    try:
        data = request.get_json()

        print_separator()
        print("CREATE REÇU VIA HTTP")
        print_separator()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print_separator()
        print()

        return jsonify({
            "status": "success",
            "message": "User created",
            "uid": data.get("attributes", {}).get("username", "unknown")
        }), 201

    except Exception as e:
        print(f"❌ Erreur: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/update', methods=['POST'])
def update():
    try:
        data = request.get_json()

        print_separator()
        print("UPDATE REÇU VIA HTTP")
        print_separator()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print_separator()
        print()

        return jsonify({
            "status": "success",
            "message": "User updated",
            "uid": data.get("uid", "unknown")
        }), 200

    except Exception as e:
        print(f"❌ Erreur: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/delete', methods=['POST'])
def delete():
    try:
        data = request.get_json()

        print_separator()
        print("DELETE REÇU VIA HTTP")
        print_separator()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print_separator()
        print()

        return jsonify({
            "status": "success",
            "message": "User deleted",
            "uid": data.get("uid", "unknown")
        }), 200

    except Exception as e:
        print(f"❌ Erreur: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "service": "Gateway HTTP Midpoint",
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/targets', methods=['GET'])
def list_targets():
    """Expose non-secret target metadata for diagnostics and MidPoint tooling."""
    return jsonify([
        {
            'id': target['id'],
            'type': target['type'],
            'displayName': target.get('display_name', target['id']),
            'aliases': target.get('aliases', []),
        }
        for target in load_targets()
    ]), 200


# ============================================================
# ENDPOINTS ENTITLEMENTS
# ============================================================

@app.route('/entitlements/ldap-groups', methods=['GET'])
def get_entitlements_ldap_groups():
    """Retourne la liste des groupes LDAP disponibles"""
    try:
        groups = get_ldap_groups()
        print_separator()
        print(f"ENTITLEMENTS LDAP - {len(groups)} groupes trouvés")
        print_separator()
        return jsonify(groups), 200
    except Exception as e:
        print(f"Erreur: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/entitlements/postgresql-profiles', methods=['GET'])
def get_entitlements_postgresql_profiles():
    """Retourne les profils de droits PostgreSQL"""
    try:
        config = load_config()
        profiles = config.get('entitlements', {}).get('postgresql', {}).get('profiles', [])

        if not profiles:
            profiles = [
                {"profileName": "readonly", "grants": "SELECT", "description": "Lecture seule"},
                {"profileName": "readwrite", "grants": "SELECT, INSERT, UPDATE", "description": "Lecture et écriture"},
                {"profileName": "admin", "grants": "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP", "description": "Administrateur"}
            ]

        print_separator()
        print(f"ENTITLEMENTS PostgreSQL - {len(profiles)} profils")
        print_separator()
        return jsonify(profiles), 200
    except Exception as e:
        print(f"Erreur: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/entitlements/mysql-profiles', methods=['GET'])
def get_entitlements_mysql_profiles():
    """Retourne les profils de droits MySQL"""
    try:
        config = load_config()
        profiles = config.get('entitlements', {}).get('mysql', {}).get('profiles', [])

        if not profiles:
            profiles = [
                {"profileName": "readonly", "grants": "SELECT", "description": "Lecture seule"},
                {"profileName": "readwrite", "grants": "SELECT, INSERT, UPDATE", "description": "Lecture et écriture"},
                {"profileName": "admin", "grants": "ALL PRIVILEGES", "description": "Administrateur"}
            ]

        print_separator()
        print(f"ENTITLEMENTS MySQL - {len(profiles)} profils")
        print_separator()
        return jsonify(profiles), 200
    except Exception as e:
        print(f"Erreur: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/entitlements/mongodb-roles', methods=['GET'])
def get_entitlements_mongodb_roles():
    """Return roles discoverable on the target MongoDB database."""
    try:
        roles = get_mongodb_roles(request.args.get('target'))
        print_separator()
        print(f"ENTITLEMENTS MongoDB - {len(roles)} rôles")
        print_separator()
        return jsonify(roles), 200
    except Exception as e:
        print(f"Erreur: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("=" * 60)
    print("Gateway HTTP - Réception opérations Midpoint")
    print("=" * 60)
    print("Endpoints provisioning:")
    print("  - POST /create  → Création d'utilisateur")
    print("  - POST /update  → Modification d'utilisateur")
    print("  - POST /delete  → Suppression d'utilisateur")
    print("  - GET  /health  → Health check")
    print("")
    print("Endpoints entitlements:")
    print("  - GET /entitlements/ldap-groups         → Groupes LDAP")
    print("  - GET /entitlements/postgresql-profiles → Profils PostgreSQL")
    print("  - GET /entitlements/mysql-profiles      → Profils MySQL")
    print("  - GET /entitlements/mongodb-roles       → Rôles MongoDB")
    print("=" * 60)
    print("Serveur démarré sur http://localhost:5100")
    print("=" * 60)
    print()

    app.run(host='0.0.0.0', port=5100, debug=False)
