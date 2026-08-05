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

from target_entitlements import synchronize

app = Flask(__name__)

# Charger la configuration
TARGET_CATALOG_FILE = os.getenv(
    'TARGET_CATALOG_PATH',
    os.path.join(os.path.dirname(__file__), 'config', 'targets.yaml'),
)
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

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
    prefix = f'{target_id}.'

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

    except Exception as e:
        raise RuntimeError(f"LDAP entitlement discovery failed for {target_id}: {e}") from e


def get_manifests(target_type):
    """Discover real entitlements for every enabled target of a family."""
    return [
        synchronize(target)
        for target in load_targets()
        if target.get('type') == target_type
    ]

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


@app.route('/entitlements/manifest', methods=['GET'])
def entitlement_manifest():
    """Return one fail-closed normalized target entitlement manifest."""
    target_id = request.args.get('target')
    if not target_id:
        return jsonify({'error': 'target query parameter is required'}), 400
    try:
        target = get_target(target_id)
        if not target:
            return jsonify({'error': f'unknown target: {target_id}'}), 404
        return jsonify(synchronize(target)), 200
    except Exception as exc:
        return jsonify({'error': str(exc)}), 503


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
    """Return discovered PostgreSQL native roles without fictional defaults."""
    try:
        profiles = [
            {
                'profileName': role['association_value'],
                'grants': role['native_name'],
                'description': role['description'],
            }
            for manifest in get_manifests('postgresql')
            for role in manifest['entitlements']
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
    """Return discovered MySQL native roles without fictional defaults."""
    try:
        profiles = [
            {
                'profileName': role['association_value'],
                'grants': role['native_name'],
                'description': role['description'],
            }
            for manifest in get_manifests('mysql')
            for role in manifest['entitlements']
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
    """Return discovered MongoDB roles without static resilience fallbacks."""
    try:
        target_id = request.args.get('target')
        targets = [
            target for target in load_targets()
            if target.get('type') == 'mongodb'
            and (not target_id or target.get('id') == target_id)
        ]
        roles = [
            {
                'roleName': role['association_value'].split('@', 1)[0],
                'database': target['connection']['database'],
                'description': role['description'],
            }
            for target in targets
            for role in synchronize(target)['entitlements']
        ]
        print_separator()
        print(f"ENTITLEMENTS MongoDB - {len(roles)} rôles")
        print_separator()
        return jsonify(roles), 200
    except Exception as e:
        print(f"Erreur: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/entitlements/odoo-groups', methods=['GET'])
def get_entitlements_odoo_groups():
    """Return discovered Odoo groups."""
    try:
        groups = [
            {
                'groupName': role['association_value'],
                'description': role['description'],
            }
            for manifest in get_manifests('odoo')
            for role in manifest['entitlements']
        ]
        return jsonify(groups), 200
    except Exception as e:
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
