"""Connector Dashboard - View connectors, create users and assign roles directly"""
import os
import json
import requests
import pika
from requests.auth import HTTPBasicAuth
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# RabbitMQ Configuration (for sending messages back to MidPoint flow)
# =============================================================================
RABBITMQ_CONFIG = {
    "host": os.getenv("RABBITMQ_HOST", "localhost"),
    "port": int(os.getenv("RABBITMQ_PORT", 5672)),
    "user": os.getenv("RABBITMQ_USER", "admin"),
    "password": os.getenv("RABBITMQ_PASSWORD", "admin123"),
    "queue": os.getenv("RABBITMQ_QUEUE", "midpoint-operations"),
}


def send_to_rabbitmq(message: dict) -> bool:
    """Send a message to RabbitMQ (simulates MidPoint operation)"""
    try:
        credentials = pika.PlainCredentials(
            RABBITMQ_CONFIG["user"],
            RABBITMQ_CONFIG["password"]
        )
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_CONFIG["host"],
                port=RABBITMQ_CONFIG["port"],
                credentials=credentials,
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue=RABBITMQ_CONFIG["queue"], durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=RABBITMQ_CONFIG["queue"],
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        connection.close()
        print(f"[RabbitMQ] Message sent: {message}")
        return True
    except Exception as e:
        print(f"[RabbitMQ] Failed to send message: {e}")
        return False

app = Flask(__name__)

# Configuration
MIDPOINT_URL = os.getenv("MIDPOINT_URL", "http://localhost:8080/midpoint")
MIDPOINT_USERNAME = os.getenv("MIDPOINT_USERNAME", "administrator")
MIDPOINT_PASSWORD = os.getenv("MIDPOINT_PASSWORD", "5ecr3t")

GATEWAY_API_URL = os.getenv("GATEWAY_API_URL", "http://localhost:8100")

# Target services configuration
TARGET_SERVICES = {
    "mysql": {
        "name": "MySQL",
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", 3306)),
    },
    "postgresql": {
        "name": "PostgreSQL",
        "host": os.getenv("POSTGRESQL_HOST", "localhost"),
        "port": int(os.getenv("POSTGRESQL_PORT", 5433)),
    },
    "odoo": {
        "name": "Odoo",
        "url": os.getenv("ODOO_URL", "http://localhost:8069"),
    },
    "ldap": {
        "name": "LDAP (ApacheDS)",
        "host": os.getenv("LDAP_HOST", "localhost"),
        "port": int(os.getenv("LDAP_PORT", 10389)),
    },
}


def get_midpoint_resources():
    """Fetch resources from MidPoint REST API"""
    try:
        url = f"{MIDPOINT_URL}/ws/rest/resources"
        response = requests.get(
            url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            timeout=10
        )

        if response.status_code == 401:
            return {"status": "error", "message": "Authentification echouee (401) - verifier credentials MidPoint"}

        if response.status_code == 404:
            return {"status": "error", "message": "API non trouvee (404) - MidPoint est-il demarre?"}

        if response.status_code != 200:
            return {"status": "error", "message": f"HTTP {response.status_code}: {response.text[:200]}"}

        # Try to parse JSON
        try:
            data = response.json()
        except ValueError:
            # MidPoint returned non-JSON (probably XML)
            return {"status": "error", "message": "MidPoint a retourne du XML au lieu de JSON. Verifier la version de MidPoint."}

        resources = []

        # Debug: log the full response structure
        import json
        print(f"=== MidPoint Response ===")
        print(json.dumps(data, indent=2, default=str)[:2000])
        print(f"=========================")

        # Parse MidPoint response - handle nested structure
        def parse_resource(obj):
            """Parse a single MidPoint resource object"""
            if not isinstance(obj, dict):
                return None

            name = obj.get("name", "Unknown")
            oid = obj.get("oid", "")

            # Get operational status
            op_state = obj.get("operationalState", {})
            status = op_state.get("lastAvailabilityStatus", "unknown")
            status_message = op_state.get("message", "")

            return {
                "oid": oid,
                "name": name,
                "status": status,  # "up" or "down"
                "status_message": status_message,
                "type": obj.get("@type", "").replace("c:", ""),
            }

        # MidPoint format: data["object"]["object"] contains the list
        if "object" in data:
            outer = data["object"]
            if isinstance(outer, dict) and "object" in outer:
                # Nested structure: data.object.object[]
                obj_list = outer["object"]
                if isinstance(obj_list, list):
                    for obj in obj_list:
                        parsed = parse_resource(obj)
                        if parsed:
                            resources.append(parsed)
                else:
                    parsed = parse_resource(obj_list)
                    if parsed:
                        resources.append(parsed)
            elif isinstance(outer, list):
                # Direct list: data.object[]
                for obj in outer:
                    parsed = parse_resource(obj)
                    if parsed:
                        resources.append(parsed)

        return {"status": "ok", "resources": resources, "count": len(resources), "raw_keys": list(data.keys()) if isinstance(data, dict) else str(type(data))}

    except requests.ConnectionError:
        return {"status": "error", "message": f"Connexion impossible a MidPoint ({MIDPOINT_URL}). Est-il demarre?"}
    except requests.Timeout:
        return {"status": "error", "message": "Timeout - MidPoint ne repond pas"}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def check_gateway_connectors():
    """Check status of gateway-iam target connectors"""
    connectors = []

    for key, config in TARGET_SERVICES.items():
        connector = {
            "id": key,
            "name": config["name"],
            "type": "outbound",
            "status": "unknown",
        }

        try:
            if key == "odoo":
                # Check Odoo via HTTP
                response = requests.get(f"{config['url']}/web/database/selector", timeout=5)
                connector["status"] = "online" if response.status_code == 200 else "offline"
                connector["details"] = config["url"]
            else:
                # Check other services via socket
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((config["host"], config["port"]))
                sock.close()
                connector["status"] = "online" if result == 0 else "offline"
                connector["details"] = f"{config['host']}:{config['port']}"
        except Exception as e:
            connector["status"] = "error"
            connector["details"] = str(e)

        connectors.append(connector)

    return connectors


@app.route("/")
def index():
    """Main dashboard page"""
    return render_template("index.html")


@app.route("/api/midpoint/resources")
def api_midpoint_resources():
    """API endpoint for MidPoint resources"""
    return jsonify(get_midpoint_resources())


@app.route("/api/midpoint/resources/<oid>")
def api_midpoint_resource_details(oid):
    """Get details of a specific MidPoint resource (connector configuration)"""
    try:
        url = f"{MIDPOINT_URL}/ws/rest/resources/{oid}"
        response = requests.get(
            url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=10
        )

        if response.status_code == 401:
            return jsonify({"status": "error", "message": "Authentification MidPoint echouee"}), 401
        if response.status_code == 404:
            return jsonify({"status": "error", "message": f"Ressource {oid} non trouvee"}), 404
        if response.status_code != 200:
            return jsonify({"status": "error", "message": f"Erreur MidPoint: {response.status_code}"}), 500

        data = response.json()

        # Parse resource details
        resource = data.get("object", data)
        if isinstance(resource, dict) and "object" in resource:
            resource = resource["object"]

        # Extract key configuration fields
        config = {
            "oid": resource.get("oid", oid),
            "name": resource.get("name", "Unknown"),
            "description": resource.get("description", ""),
            "connectorRef": None,
            "connectorConfiguration": {},
            "schemaHandling": [],
            "synchronization": [],
        }

        # Get connector reference
        connector_ref = resource.get("connectorRef", {})
        if connector_ref:
            config["connectorRef"] = {
                "oid": connector_ref.get("oid", ""),
                "type": connector_ref.get("type", "").replace("c:", ""),
            }

        # Get connector configuration (host, port, etc.)
        connector_config = resource.get("connectorConfiguration", {})
        if connector_config:
            # Parse configurationProperties
            config_props = connector_config.get("configurationProperties", {})
            if isinstance(config_props, dict):
                # Extract common properties
                for key in ["host", "port", "baseContext", "baseDn", "url", "database",
                           "user", "bindDn", "ssl", "useSSL", "connectionType"]:
                    if key in config_props:
                        value = config_props[key]
                        # Don't expose passwords
                        if "password" not in key.lower() and "secret" not in key.lower():
                            config["connectorConfiguration"][key] = value

        # Get schema handling (object types)
        schema_handling = resource.get("schemaHandling", {})
        if schema_handling:
            object_types = schema_handling.get("objectType", [])
            if not isinstance(object_types, list):
                object_types = [object_types]
            for obj_type in object_types:
                if isinstance(obj_type, dict):
                    config["schemaHandling"].append({
                        "kind": obj_type.get("kind", ""),
                        "intent": obj_type.get("intent", "default"),
                        "displayName": obj_type.get("displayName", ""),
                        "default": obj_type.get("default", False),
                    })

        # Get synchronization settings
        sync = resource.get("synchronization", {})
        if sync:
            obj_sync = sync.get("objectSynchronization", [])
            if not isinstance(obj_sync, list):
                obj_sync = [obj_sync]
            for s in obj_sync:
                if isinstance(s, dict):
                    config["synchronization"].append({
                        "name": s.get("name", ""),
                        "kind": s.get("kind", ""),
                        "intent": s.get("intent", ""),
                        "enabled": s.get("enabled", True),
                    })

        return jsonify({"status": "ok", "resource": config})

    except requests.exceptions.ConnectionError:
        return jsonify({"status": "error", "message": "Impossible de se connecter a MidPoint"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/gateway/connectors")
def api_gateway_connectors():
    """API endpoint for gateway-iam connectors"""
    return jsonify({"connectors": check_gateway_connectors()})


@app.route("/api/ldap/groups")
def api_ldap_groups():
    """API endpoint for LDAP groups"""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE

        ldap_host = os.getenv("LDAP_HOST", "localhost")
        ldap_port = int(os.getenv("LDAP_PORT", 10389))
        ldap_bind_dn = os.getenv("LDAP_BIND_DN", "uid=admin,ou=system")
        ldap_bind_password = os.getenv("LDAP_BIND_PASSWORD", "secret")
        ldap_base_dn = os.getenv("LDAP_BASE_DN", "dc=openmicroscopy,dc=org")

        server = Server(ldap_host, port=ldap_port, get_info=ALL)
        conn = Connection(server, user=ldap_bind_dn, password=ldap_bind_password, auto_bind=True)

        # Search for groups (groupOfNames, groupOfUniqueNames)
        conn.search(
            search_base=ldap_base_dn,
            search_filter="(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))",
            search_scope=SUBTREE,
            attributes=["cn", "description"]
        )

        groups = []
        for entry in conn.entries:
            groups.append({
                "dn": str(entry.entry_dn),
                "cn": str(entry.cn) if hasattr(entry, "cn") else "Unknown",
                "description": str(entry.description) if hasattr(entry, "description") else ""
            })

        conn.unbind()

        return jsonify({"status": "ok", "groups": groups, "count": len(groups)})

    except ImportError:
        return jsonify({"status": "error", "message": "ldap3 non installe. Faire: pip install ldap3"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/health")
def health():
    """Health check"""
    return jsonify({"status": "healthy", "service": "connector-dashboard"})


# =============================================================================
# User Provisioning - Direct connection to target services
# =============================================================================

def _get_mysql_connection():
    """Create a MySQL connection"""
    import pymysql
    config = TARGET_SERVICES["mysql"]
    return pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "mysql_root_secret"),
        database=os.getenv("MYSQL_DATABASE", "target_db"),
        connect_timeout=5,
    )


def _get_postgresql_connection():
    """Create a PostgreSQL connection"""
    import psycopg2
    config = TARGET_SERVICES["postgresql"]
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        user=os.getenv("POSTGRESQL_USER", "target_user"),
        password=os.getenv("POSTGRESQL_PASSWORD", "target_secret"),
        dbname=os.getenv("POSTGRESQL_DATABASE", "target_db"),
        connect_timeout=5,
    )


def _get_ldap_connection():
    """Create an LDAP connection"""
    from ldap3 import Server, Connection, ALL
    ldap_host = os.getenv("LDAP_HOST", "localhost")
    ldap_port = int(os.getenv("LDAP_PORT", 10389))
    ldap_bind_dn = os.getenv("LDAP_BIND_DN", "uid=admin,ou=system")
    ldap_bind_password = os.getenv("LDAP_BIND_PASSWORD", "secret")
    server = Server(ldap_host, port=ldap_port, get_info=ALL)
    conn = Connection(server, user=ldap_bind_dn, password=ldap_bind_password, auto_bind=True)
    return conn


def _get_odoo_connection():
    """Get Odoo XML-RPC connection info"""
    import xmlrpc.client
    odoo_url = os.getenv("ODOO_URL", "http://localhost:8069")
    odoo_db = os.getenv("ODOO_DB", "odoo")
    odoo_user = os.getenv("ODOO_USERNAME", "admin")
    odoo_password = os.getenv("ODOO_PASSWORD", "admin")

    common = xmlrpc.client.ServerProxy(f"{odoo_url}/xmlrpc/2/common")
    uid = common.authenticate(odoo_db, odoo_user, odoo_password, {})
    models = xmlrpc.client.ServerProxy(f"{odoo_url}/xmlrpc/2/object")
    return models, odoo_db, uid, odoo_password


@app.route("/api/users/<service>", methods=["GET"])
def api_list_users(service):
    """List users for a given target service"""
    try:
        if service == "mysql":
            conn = _get_mysql_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT User, Host FROM mysql.user WHERE User NOT IN ('root', 'mysql.sys', 'mysql.session', 'mysql.infoschema', 'debian-sys-maint')")
            users = [{"username": row[0], "host": row[1]} for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            return jsonify({"status": "ok", "users": users})

        elif service == "postgresql":
            conn = _get_postgresql_connection()
            conn.autocommit = True
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.rolname, r.rolcanlogin, r.rolsuper,
                       ARRAY(SELECT b.rolname FROM pg_catalog.pg_auth_members m
                             JOIN pg_catalog.pg_roles b ON m.roleid = b.oid
                             WHERE m.member = r.oid) as member_of
                FROM pg_catalog.pg_roles r
                WHERE r.rolname NOT LIKE 'pg_%%'
                  AND r.rolname NOT IN ('postgres', 'target_user')
                ORDER BY r.rolname
            """)
            users = []
            for row in cursor.fetchall():
                users.append({
                    "username": row[0],
                    "can_login": row[1],
                    "is_superuser": row[2],
                    "roles": row[3] if row[3] else [],
                })
            cursor.close()
            conn.close()
            return jsonify({"status": "ok", "users": users})

        elif service == "ldap":
            from ldap3 import SUBTREE
            conn = _get_ldap_connection()
            base_dn = os.getenv("LDAP_BASE_DN", "dc=openmicroscopy,dc=org")

            # 1. Fetch all users
            conn.search(
                search_base=f"ou=Users,{base_dn}",
                search_filter="(objectClass=inetOrgPerson)",
                search_scope=SUBTREE,
                attributes=["uid", "cn", "mail", "employeeNumber"],
            )

            users_by_dn = {}
            for entry in conn.entries:
                user_dn = str(entry.entry_dn)
                users_by_dn[user_dn] = {
                    "username": str(entry.uid) if hasattr(entry, "uid") else "",
                    "cn": str(entry.cn) if hasattr(entry, "cn") else "",
                    "email": str(entry.mail) if hasattr(entry, "mail") else "",
                    "employeeNumber": str(entry.employeeNumber) if hasattr(entry, "employeeNumber") else "",
                    "groups": [],
                }

            # 2. Fetch groups and their members (reverse lookup)
            conn.search(
                search_base=base_dn,
                search_filter="(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))",
                search_scope=SUBTREE,
                attributes=["cn", "member", "uniqueMember"],
            )

            for group in conn.entries:
                group_cn = str(group.cn) if hasattr(group, "cn") else ""
                members = []
                if hasattr(group, "member") and group.member.values:
                    members.extend(group.member.values)
                if hasattr(group, "uniqueMember") and group.uniqueMember.values:
                    members.extend(group.uniqueMember.values)

                for member_dn in members:
                    if member_dn in users_by_dn:
                        users_by_dn[member_dn]["groups"].append(group_cn)

            users = list(users_by_dn.values())
            conn.unbind()
            return jsonify({"status": "ok", "users": users})

        elif service == "odoo":
            models, db, uid, password = _get_odoo_connection()
            user_ids = models.execute_kw(db, uid, password, "res.users", "search", [[["share", "=", False]]])
            users_data = models.execute_kw(db, uid, password, "res.users", "read", [user_ids], {"fields": ["login", "name", "email", "active", "groups_id"]})
            users = []
            for u in users_data:
                users.append({
                    "username": u.get("login", ""),
                    "name": u.get("name", ""),
                    "email": u.get("email", ""),
                    "active": u.get("active", False),
                    "id": u.get("id"),
                })
            return jsonify({"status": "ok", "users": users})

        else:
            return jsonify({"status": "error", "message": f"Unknown service: {service}"}), 400

    except ImportError as e:
        return jsonify({"status": "error", "message": f"Missing dependency: {e}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/users/<service>", methods=["POST"])
def api_create_user(service):
    """Create a user on a target service (via RabbitMQ for approval workflow)"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    email = data.get("email", "").strip()
    roles = data.get("roles", [])

    if not username:
        return jsonify({"status": "error", "message": "Username is required"}), 400

    # Map service to role name for RabbitMQ message
    service_role_map = {
        "mysql": "sql",
        "postgresql": "postgresql",
        "ldap": "ldap",
        "odoo": "odoo",
    }

    if service not in service_role_map:
        return jsonify({"status": "error", "message": f"Unknown service: {service}"}), 400

    try:
        # Build attributes for RabbitMQ message
        attributes = {
            "username": username,
            "roles": [service_role_map[service]],
        }

        # Add optional attributes
        if password:
            attributes["password"] = password
        if email:
            attributes["email"] = email

        # Add service-specific attributes
        if service == "mysql":
            # Include role/privilege level
            role = roles[0] if roles else "readonly"
            attributes["mysqlRole"] = role

        elif service == "postgresql":
            role = roles[0] if roles else "readonly"
            attributes["postgresqlRole"] = role

        elif service == "ldap":
            # Include full name and groups
            attributes["fullName"] = data.get("fullName", username)
            attributes["lastName"] = data.get("lastName", username)
            if roles:
                # roles contains group DNs for LDAP
                attributes["ldapGroups"] = roles

        elif service == "odoo":
            attributes["fullName"] = data.get("fullName", username)
            if roles:
                # roles contains Odoo group xmlids
                attributes["odooGroups"] = roles

        # Send CREATE message to RabbitMQ - gateway-iam will handle after approval
        rabbitmq_message = {
            "operation": "CREATE",
            "entityType": "User",
            "uid": username,
            "attributes": attributes,
        }

        success = send_to_rabbitmq(rabbitmq_message)

        if success:
            return jsonify({
                "status": "ok",
                "message": f"Create request for {username} sent to approval workflow",
                "pending_approval": True,
                "service": service,
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to send message to RabbitMQ"
            }), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/users/<service>/<username>", methods=["PUT"])
def api_update_user(service, username):
    """Update a user on a target service (via RabbitMQ for approval workflow)"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data"}), 400

    # Map service to role name for RabbitMQ message
    service_role_map = {
        "mysql": "sql",
        "postgresql": "postgresql",
        "ldap": "ldap",
        "odoo": "odoo",
    }

    if service not in service_role_map:
        return jsonify({"status": "error", "message": f"Unknown service: {service}"}), 400

    try:
        # Build attributes for RabbitMQ message
        attributes = {
            "username": username,
            "roles": [service_role_map[service]],
        }

        # Add optional attributes from request
        if data.get("password"):
            attributes["password"] = data["password"]
        if data.get("email"):
            attributes["email"] = data["email"]

        # Add service-specific attributes
        if service == "mysql":
            if data.get("mysqlRole"):
                attributes["mysqlRole"] = data["mysqlRole"]

        elif service == "postgresql":
            if data.get("postgresqlRole"):
                attributes["postgresqlRole"] = data["postgresqlRole"]

        elif service == "ldap":
            if data.get("fullName"):
                attributes["fullName"] = data["fullName"]
            if data.get("lastName"):
                attributes["lastName"] = data["lastName"]
            if data.get("ldapGroups"):
                attributes["ldapGroups"] = data["ldapGroups"]

        elif service == "odoo":
            if data.get("fullName"):
                attributes["fullName"] = data["fullName"]
            if data.get("odooGroups"):
                attributes["odooGroups"] = data["odooGroups"]

        # Send UPDATE message to RabbitMQ - gateway-iam will handle after approval
        rabbitmq_message = {
            "operation": "UPDATE",
            "entityType": "User",
            "uid": username,
            "attributes": attributes,
        }

        success = send_to_rabbitmq(rabbitmq_message)

        if success:
            return jsonify({
                "status": "ok",
                "message": f"Update request for {username} sent to approval workflow",
                "pending_approval": True,
                "service": service,
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to send message to RabbitMQ"
            }), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/users/<service>/<username>", methods=["DELETE"])
def api_delete_user(service, username):
    """Delete a user from a target service (via RabbitMQ for approval workflow)"""

    # Map service to role name for RabbitMQ message
    service_role_map = {
        "mysql": "sql",
        "postgresql": "postgresql",
        "ldap": "ldap",
        "odoo": "odoo",
    }

    if service not in service_role_map:
        return jsonify({"status": "error", "message": f"Unknown service: {service}"}), 400

    try:
        # Send DELETE message to RabbitMQ - gateway-iam will handle after approval
        rabbitmq_message = {
            "operation": "DELETE",
            "entityType": "User",
            "uid": username,
            "attributes": {
                "username": username,
                "roles": [service_role_map[service]],
            }
        }

        success = send_to_rabbitmq(rabbitmq_message)

        if success:
            return jsonify({
                "status": "ok",
                "message": f"Delete request for {username} sent to approval workflow",
                "pending_approval": True,
                "service": service,
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to send message to RabbitMQ"
            }), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/roles/<service>", methods=["GET"])
def api_list_roles(service):
    """List available roles for a service"""
    try:
        if service == "mysql":
            return jsonify({"status": "ok", "roles": [
                {"id": "readonly", "name": "Lecture seule (SELECT)"},
                {"id": "readwrite", "name": "Lecture/Ecriture (SELECT, INSERT, UPDATE, DELETE)"},
                {"id": "admin", "name": "Admin (ALL PRIVILEGES)"},
            ]})

        elif service == "postgresql":
            return jsonify({"status": "ok", "roles": [
                {"id": "readonly", "name": "Lecture seule (pg_read_all_data)"},
                {"id": "readwrite", "name": "Lecture/Ecriture (pg_read_all_data + pg_write_all_data)"},
                {"id": "admin", "name": "Admin (all data roles)"},
            ]})

        elif service == "ldap":
            # Return actual LDAP groups
            from ldap3 import SUBTREE
            ldap_conn = _get_ldap_connection()
            base_dn = os.getenv("LDAP_BASE_DN", "dc=openmicroscopy,dc=org")
            ldap_conn.search(
                search_base=base_dn,
                search_filter="(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))",
                search_scope=SUBTREE,
                attributes=["cn", "description"],
            )
            roles = []
            for entry in ldap_conn.entries:
                roles.append({
                    "id": str(entry.entry_dn),
                    "name": str(entry.cn) if hasattr(entry, "cn") else str(entry.entry_dn),
                })
            ldap_conn.unbind()
            return jsonify({"status": "ok", "roles": roles})

        elif service == "odoo":
            return jsonify({"status": "ok", "roles": [
                {"id": "base.group_user", "name": "Utilisateur interne"},
                {"id": "base.group_portal", "name": "Portail"},
                {"id": "base.group_public", "name": "Public"},
                {"id": "sales_team.group_sale_salesman", "name": "Commercial"},
                {"id": "sales_team.group_sale_manager", "name": "Responsable commercial"},
                {"id": "account.group_account_user", "name": "Comptable"},
                {"id": "account.group_account_manager", "name": "Responsable comptable"},
                {"id": "hr.group_hr_user", "name": "RH"},
                {"id": "hr.group_hr_manager", "name": "Responsable RH"},
            ]})

        else:
            return jsonify({"status": "error", "message": f"Unknown service: {service}"}), 400

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# MidPoint User Management
# =============================================================================

@app.route("/api/midpoint/status", methods=["GET"])
def api_midpoint_status():
    """Check MidPoint connectivity"""
    try:
        url = f"{MIDPOINT_URL}/ws/rest/self"
        response = requests.get(
            url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=5
        )
        return jsonify({
            "status": "ok" if response.status_code == 200 else "error",
            "midpoint_url": MIDPOINT_URL,
            "http_status": response.status_code,
            "message": "Connected" if response.status_code == 200 else f"Error: {response.status_code}",
        })
    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "error",
            "midpoint_url": MIDPOINT_URL,
            "message": f"Cannot connect to MidPoint at {MIDPOINT_URL}. Is MidPoint running?",
        }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "midpoint_url": MIDPOINT_URL,
            "message": str(e),
        }), 500


@app.route("/api/midpoint/users", methods=["GET"])
def api_midpoint_users():
    """List users from MidPoint"""
    try:
        url = f"{MIDPOINT_URL}/ws/rest/users"
        print(f"[MidPoint] Fetching users from: {url}")
        response = requests.get(
            url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=10
        )

        print(f"[MidPoint] Response status: {response.status_code}")
        print(f"[MidPoint] Response content-type: {response.headers.get('content-type', 'unknown')}")

        if response.status_code == 401:
            return jsonify({"status": "error", "message": "MidPoint authentication failed (401). Check MIDPOINT_USERNAME and MIDPOINT_PASSWORD"}), 500

        if response.status_code == 404:
            return jsonify({"status": "error", "message": f"MidPoint REST API not found (404). URL: {url}"}), 500

        if response.status_code != 200:
            return jsonify({"status": "error", "message": f"MidPoint error: {response.status_code} - {response.text[:200]}"}), 500

        # Check if response is JSON
        content_type = response.headers.get('content-type', '')
        if 'application/json' not in content_type and 'application/xml' not in content_type:
            return jsonify({"status": "error", "message": f"MidPoint returned non-JSON response. Content-Type: {content_type}. Response: {response.text[:200]}"}), 500

        try:
            data = response.json()
        except Exception as json_err:
            return jsonify({"status": "error", "message": f"Failed to parse MidPoint JSON response: {json_err}. Response: {response.text[:200]}"}), 500

        users = []

        # Parse MidPoint users
        if "object" in data:
            obj_list = data["object"]
            if isinstance(obj_list, dict) and "object" in obj_list:
                obj_list = obj_list["object"]
            if isinstance(obj_list, list):
                for obj in obj_list:
                    users.append({
                        "oid": obj.get("oid", ""),
                        "name": obj.get("name", ""),
                        "fullName": obj.get("fullName", ""),
                        "email": obj.get("emailAddress", ""),
                    })

        return jsonify({"status": "ok", "users": users})

    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "error",
            "message": f"Impossible de se connecter a MidPoint ({MIDPOINT_URL}). Verifiez que MidPoint est demarre et accessible."
        }), 500
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "MidPoint ne repond pas (timeout). Verifiez que le service est operationnel."
        }), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur inattendue: {str(e)}"}), 500


@app.route("/api/midpoint/users", methods=["POST"])
def api_create_midpoint_user():
    """Create a user in MidPoint"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    email = data.get("email", "").strip()
    full_name = data.get("fullName", username).strip()
    given_name = data.get("givenName", full_name.split()[0] if full_name else username).strip()
    family_name = data.get("familyName", full_name.split()[-1] if full_name else username).strip()

    if not username:
        return jsonify({"status": "error", "message": "Username is required"}), 400

    try:
        # First check if user already exists
        check_url = f"{MIDPOINT_URL}/ws/rest/users?name={username}"
        check_response = requests.get(
            check_url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=10
        )

        if check_response.status_code == 200:
            check_data = check_response.json()
            # Check if user was found
            if "object" in check_data:
                obj = check_data["object"]
                if isinstance(obj, dict) and "object" in obj:
                    obj = obj["object"]
                if (isinstance(obj, list) and len(obj) > 0) or (isinstance(obj, dict) and obj.get("oid")):
                    return jsonify({
                        "status": "error",
                        "message": f"L'utilisateur '{username}' existe deja dans MidPoint. Utilisez la liste des utilisateurs pour le modifier."
                    }), 409

        # Build MidPoint user XML
        user_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<user xmlns="http://midpoint.evolveum.com/xml/ns/public/common/common-3">
    <name>{username}</name>
    <fullName>{full_name}</fullName>
    <givenName>{given_name}</givenName>
    <familyName>{family_name}</familyName>
    <emailAddress>{email}</emailAddress>
    <credentials>
        <password>
            <value>
                <clearValue>{password}</clearValue>
            </value>
        </password>
    </credentials>
</user>"""

        url = f"{MIDPOINT_URL}/ws/rest/users"
        print(f"[MidPoint] Creating user: {username}")

        response = requests.post(
            url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={
                "Content-Type": "application/xml",
                "Accept": "application/json"
            },
            data=user_xml,
            timeout=15
        )

        print(f"[MidPoint] Create response: {response.status_code}")

        if response.status_code in [200, 201]:
            return jsonify({
                "status": "ok",
                "message": f"Utilisateur '{username}' cree avec succes dans MidPoint!",
                "midpoint": True,
            })
        elif response.status_code == 409:
            return jsonify({
                "status": "error",
                "message": f"L'utilisateur '{username}' existe deja dans MidPoint. Rafraichissez la liste pour le voir."
            }), 409
        else:
            # Try to extract error message from response
            error_msg = response.text[:300]
            try:
                # MidPoint sometimes returns JSON with error details
                err_data = response.json()
                if "error" in err_data:
                    error_msg = err_data.get("error", {}).get("message", error_msg)
            except:
                pass
            return jsonify({
                "status": "error",
                "message": f"Erreur MidPoint {response.status_code}: {error_msg}"
            }), 500

    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "error",
            "message": f"Impossible de se connecter a MidPoint ({MIDPOINT_URL}). Verifiez que MidPoint est demarre."
        }), 500
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "Timeout lors de la creation. L'utilisateur a peut-etre ete cree - rafraichissez la liste pour verifier."
        }), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/midpoint/roles", methods=["GET"])
def api_midpoint_roles():
    """List roles from MidPoint"""
    try:
        url = f"{MIDPOINT_URL}/ws/rest/roles"
        response = requests.get(
            url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"status": "error", "message": f"MidPoint error: {response.status_code}"}), 500

        data = response.json()
        roles = []

        # Parse MidPoint roles
        if "object" in data:
            obj_list = data["object"]
            if isinstance(obj_list, dict) and "object" in obj_list:
                obj_list = obj_list["object"]
            if isinstance(obj_list, list):
                for obj in obj_list:
                    roles.append({
                        "oid": obj.get("oid", ""),
                        "name": obj.get("name", ""),
                        "description": obj.get("description", ""),
                    })

        return jsonify({"status": "ok", "roles": roles})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/midpoint/users/<username>/roles", methods=["POST"])
def api_assign_midpoint_role(username):
    """Assign a role to a user in MidPoint (triggers provisioning)"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON data"}), 400

    role_oid = data.get("roleOid", "").strip()
    if not role_oid:
        return jsonify({"status": "error", "message": "Role OID is required"}), 400

    try:
        # First, find the user by name
        search_url = f"{MIDPOINT_URL}/ws/rest/users?name={username}"
        search_response = requests.get(
            search_url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=10
        )

        if search_response.status_code != 200:
            return jsonify({"status": "error", "message": "User not found in MidPoint"}), 404

        search_data = search_response.json()
        user_oid = None

        if "object" in search_data:
            obj = search_data["object"]
            if isinstance(obj, dict):
                if "object" in obj:
                    obj = obj["object"]
                if isinstance(obj, list) and len(obj) > 0:
                    user_oid = obj[0].get("oid")
                elif isinstance(obj, dict):
                    user_oid = obj.get("oid")

        if not user_oid:
            return jsonify({"status": "error", "message": f"User {username} not found in MidPoint"}), 404

        # Assign role using modify operation - correct MidPoint REST API format
        # Note: path should NOT have c: prefix, and targetRef uses RoleType without c: prefix
        modify_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objectModification xmlns="http://midpoint.evolveum.com/xml/ns/public/common/api-types-3"
                    xmlns:c="http://midpoint.evolveum.com/xml/ns/public/common/common-3"
                    xmlns:t="http://prism.evolveum.com/xml/ns/public/types-3">
    <itemDelta>
        <t:modificationType>add</t:modificationType>
        <t:path>assignment</t:path>
        <t:value>
            <c:targetRef oid="{role_oid}" type="RoleType"/>
        </t:value>
    </itemDelta>
</objectModification>"""

        modify_url = f"{MIDPOINT_URL}/ws/rest/users/{user_oid}"
        print(f"[MidPoint] Assigning role {role_oid} to user {user_oid}")
        print(f"[MidPoint] URL: {modify_url}")
        print(f"[MidPoint] XML: {modify_xml}")

        modify_response = requests.post(
            modify_url,
            auth=HTTPBasicAuth(MIDPOINT_USERNAME, MIDPOINT_PASSWORD),
            headers={
                "Content-Type": "application/xml",
                "Accept": "application/json"
            },
            data=modify_xml,
            timeout=30
        )

        print(f"[MidPoint] Response status: {modify_response.status_code}")
        print(f"[MidPoint] Response: {modify_response.text[:500]}")

        if modify_response.status_code in [200, 204]:
            return jsonify({
                "status": "ok",
                "message": f"Role assigne a {username} dans MidPoint (provisioning en cours)",
            })
        elif modify_response.status_code == 409:
            return jsonify({
                "status": "error",
                "message": f"Ce role est deja assigne a {username}"
            }), 409
        else:
            # Parse MidPoint error message if possible
            error_msg = modify_response.text[:300]
            try:
                error_data = modify_response.json()
                if "error" in error_data:
                    error_msg = error_data.get("error", {}).get("message", error_msg)
            except:
                pass
            return jsonify({
                "status": "error",
                "message": f"Erreur MidPoint {modify_response.status_code}: {error_msg}"
            }), 500

    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "error",
            "message": f"Impossible de se connecter a MidPoint ({MIDPOINT_URL})"
        }), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 5002))
    print(f"Starting Connector Dashboard on port {port}")
    print(f"MidPoint URL: {MIDPOINT_URL}")
    print(f"Gateway API URL: {GATEWAY_API_URL}")
    app.run(host="0.0.0.0", port=port, debug=True)
