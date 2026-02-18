#!/usr/bin/env python3
"""
Gateway HTTP Simple - Reçoit les opérations de Midpoint via HTTP
Supporte aussi les entitlements (groupes LDAP, profils PostgreSQL/MySQL)
"""

from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

# Charger la configuration
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')

def load_config():
    """Charge la configuration depuis config.json"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur chargement config: {e}")
        return {}

def get_ldap_groups():
    """Récupère les groupes LDAP depuis le serveur LDAP"""
    config = load_config()
    ldap_config = config.get('configs', {}).get('LDAP', {})

    if not ldap_config.get('host'):
        return []

    try:
        import ldap3
        ldap_host = os.environ.get('LDAP_HOST', ldap_config.get('host', 'localhost'))
        ldap_port = int(os.environ.get('LDAP_PORT', ldap_config.get('port', 389)))
        server = ldap3.Server(
            ldap_host,
            port=ldap_port,
            get_info=ldap3.ALL
        )
        conn = ldap3.Connection(
            server,
            user=ldap_config.get('bindDn'),
            password=ldap_config.get('password'),
            auto_bind=True
        )

        groups_base = ldap_config.get('groupsBaseDn', f"ou=Groups,{ldap_config.get('baseDn', '')}")
        conn.search(
            search_base=groups_base,
            search_filter='(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)(objectClass=posixGroup))',
            attributes=['cn', 'description', 'member']
        )

        groups = []
        for entry in conn.entries:
            groups.append({
                'dn': str(entry.entry_dn),
                'cn': str(entry.cn) if hasattr(entry, 'cn') else '',
                'description': str(entry.description) if hasattr(entry, 'description') else ''
            })

        conn.unbind()
        return groups

    except ImportError:
        print("Module ldap3 non installé - retour groupes de test")
        return [
            {'dn': 'cn=Admins,ou=Groups,dc=openmicroscopy,dc=org', 'cn': 'Admins', 'description': 'Groupe Administrateurs'},
            {'dn': 'cn=Users,ou=Groups,dc=openmicroscopy,dc=org', 'cn': 'Users', 'description': 'Groupe Utilisateurs'},
            {'dn': 'cn=Developers,ou=Groups,dc=openmicroscopy,dc=org', 'cn': 'Developers', 'description': 'Groupe Développeurs'}
        ]
    except Exception as e:
        print(f"Erreur LDAP: {e}")
        return [
            {'dn': 'cn=Admins,ou=Groups,dc=openmicroscopy,dc=org', 'cn': 'Admins', 'description': 'Groupe Administrateurs'},
            {'dn': 'cn=Users,ou=Groups,dc=openmicroscopy,dc=org', 'cn': 'Users', 'description': 'Groupe Utilisateurs'},
            {'dn': 'cn=Developers,ou=Groups,dc=openmicroscopy,dc=org', 'cn': 'Developers', 'description': 'Groupe Développeurs'}
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
    print("=" * 60)
    print("Serveur démarré sur http://localhost:5100")
    print("=" * 60)
    print()

    app.run(host='0.0.0.0', port=5100, debug=False)
