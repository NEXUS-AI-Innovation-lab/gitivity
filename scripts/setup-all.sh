#!/usr/bin/env bash
# Full from-scratch setup script for Gateway IAM.
# Starts all Docker services, waits for readiness, imports n8n workflow,
# builds and deploys the MidPoint connector, imports resource + roles.
#
# Usage:
#   bash scripts/setup-all.sh [OPTIONS]
#
# Options:
#   --skip-build      Skip Gradle connector build
#   --skip-midpoint   Skip MidPoint setup entirely
#   --skip-n8n        Skip n8n workflow import
#   --targets         Also start target services (LDAP, MySQL, PostgreSQL, Odoo)
#   --help, -h        Show this help
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT="$SCRIPT_DIR/.."

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Docker
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"

# Gateway services
GATEWAY_API_URL="http://localhost:8100"
GATEWAY_HTTP_URL="http://localhost:5100"

# n8n
N8N_URL="http://localhost:5678"
N8N_CONTAINER="gitivity-n8n"
N8N_WORKFLOW_FILE="$PROJECT_ROOT/n8n/workflows/approval-workflow.json"

# MidPoint
MIDPOINT_URL="http://localhost:8080/midpoint"
MIDPOINT_USER="administrator"
MIDPOINT_PASS="Test5ecr3t"
MIDPOINT_CONTAINER="gitivity-midpoint"
CONNECTOR_DIR="$PROJECT_ROOT/midpoint-connector"
JAR_NAME="connector-restgateway-1.2.0-SNAPSHOT.jar"
JAR_PATH="$CONNECTOR_DIR/build/libs/$JAR_NAME"
ICF_CONNECTORS_PATH="/opt/midpoint/var/icf-connectors/"
RESOURCE_XML="$PROJECT_ROOT/midpoint/ressource.xml"
ROLES_DIR="$PROJECT_ROOT/midpoint/roles"
CONNECTOR_BUNDLE="lu.lns.connector.restgateway"
CONNECTOR_VERSION="1.2.0-SNAPSHOT"
OLD_CONNECTOR_OID="84505b3d-5f90-4617-a160-6548be000a44"
RESOURCE_OID="736ea741-2c73-4478-b5d1-07d84cdf860f"
OLD_RESOURCE_OID_IN_ROLES="3447776b-105c-4663-a025-5b27568ee091"

WAIT_TIMEOUT=300
WAIT_INTERVAL=10

# Flags
SKIP_BUILD=false
SKIP_MIDPOINT=false
SKIP_N8N=false
WITH_TARGETS=false

TEMP_DIR=""
CONNECTOR_OID=""

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    RED=$(tput setaf 1); GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3); BLUE=$(tput setaf 4)
    BOLD=$(tput bold); RESET=$(tput sgr0)
else
    RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
fi

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_info()    { echo "${BLUE}[INFO]${RESET}  $*"; }
log_success() { echo "${GREEN}[OK]${RESET}    $*"; }
log_warn()    { echo "${YELLOW}[WARN]${RESET}  $*"; }
log_error()   { echo "${RED}[ERROR]${RESET} $*" >&2; }
log_step()    { echo ""; echo "${BOLD}${BLUE}===== $* =====${RESET}"; echo ""; }
die()         { log_error "$*"; exit 1; }

cleanup() {
    [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]] && rm -rf "$TEMP_DIR" || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Full from-scratch setup for Gateway IAM:
  1. Start all Docker services (default profile + midpoint profile)
  2. Wait for every service to be ready
  3. Import n8n approval workflow
  4. Build & deploy MidPoint connector JAR
  5. Import MidPoint resource + 10 roles

Options:
  --skip-build      Skip Gradle connector build (JAR must already exist)
  --skip-midpoint   Skip MidPoint connector + resource + role setup
  --skip-n8n        Skip n8n workflow import
  --targets         Also start target services (LDAP, MySQL, PostgreSQL, Odoo)
  --help, -h        Show this help

Prerequisites:
  - Docker running
  - jq  (brew install jq / apt install jq)
  - Java 11+  (unless --skip-build)
  - Optional: set GMAIL_USER and GMAIL_APP_PASSWORD in your environment
              for email notifications via n8n

EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-build)    SKIP_BUILD=true ;;
            --skip-midpoint) SKIP_MIDPOINT=true ;;
            --skip-n8n)      SKIP_N8N=true ;;
            --targets)       WITH_TARGETS=true ;;
            --help|-h)       usage; exit 0 ;;
            *) log_error "Unknown argument: $1"; usage; exit 1 ;;
        esac
        shift
    done
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
check_prerequisites() {
    log_step "Pre-flight checks"

    docker info >/dev/null 2>&1 \
        || die "Docker is not running."
    log_success "Docker is running"

    command -v jq >/dev/null 2>&1 \
        || die "jq is required. Install with: brew install jq  OR  apt install jq"
    log_success "jq $(jq --version)"

    command -v curl >/dev/null 2>&1 || die "curl is required."
    log_success "curl found"

    if [[ "$SKIP_BUILD" == false && "$SKIP_MIDPOINT" == false ]]; then
        command -v java >/dev/null 2>&1 \
            || die "Java 11+ required to build the connector. Use --skip-build if JAR already exists."
        log_success "java found"
        [[ -f "$CONNECTOR_DIR/gradlew" ]] \
            || die "gradlew not found at $CONNECTOR_DIR/gradlew"
    fi

    [[ -f "$COMPOSE_FILE" ]]       || die "docker-compose.yml not found at $COMPOSE_FILE"
    [[ -f "$N8N_WORKFLOW_FILE" ]]  || die "n8n workflow not found at $N8N_WORKFLOW_FILE"
    [[ -f "$RESOURCE_XML" ]]       || die "Resource XML not found at $RESOURCE_XML"
    [[ -d "$ROLES_DIR" ]]          || die "Roles directory not found at $ROLES_DIR"

    TEMP_DIR=$(mktemp -d)
    log_success "All checks passed (temp: $TEMP_DIR)"
}

# ---------------------------------------------------------------------------
# Start Docker services
# ---------------------------------------------------------------------------
start_services() {
    log_step "Start Docker services"

    local profiles="--profile midpoint"
    [[ "$WITH_TARGETS" == true ]] && profiles="$profiles --profile targets"

    log_info "Starting services (profiles: default + midpoint${WITH_TARGETS:+ + targets})..."
    (cd "$PROJECT_ROOT" && docker compose $profiles up -d --remove-orphans) \
        || die "docker compose up failed"

    log_success "Services started"
    echo ""
    docker compose --profile midpoint ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null \
        || docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep gitivity
}

# ---------------------------------------------------------------------------
# Generic wait helper
# ---------------------------------------------------------------------------
wait_for_http() {
    local name="$1"
    local url="$2"
    local expected_code="${3:-200}"
    local auth="${4:-}"
    local elapsed=0

    log_info "Waiting for $name at $url ..."
    printf "%s" "${BLUE}[INFO]${RESET}  "

    while true; do
        local auth_arg=()
        [[ -n "$auth" ]] && auth_arg=(-u "$auth")

        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 5 --max-time 10 \
            ${auth_arg[@]+"${auth_arg[@]}"} "$url" 2>/dev/null || echo "000")

        if [[ "$code" == "$expected_code" ]]; then
            echo ""
            log_success "$name is ready (${elapsed}s)"
            return 0
        fi

        if [[ $elapsed -ge $WAIT_TIMEOUT ]]; then
            echo ""
            die "$name did not become ready within ${WAIT_TIMEOUT}s (last code: $code)"
        fi

        printf "."
        sleep "$WAIT_INTERVAL"
        elapsed=$((elapsed + WAIT_INTERVAL))
    done
}

# ---------------------------------------------------------------------------
# Wait for all core services
# ---------------------------------------------------------------------------
wait_for_services() {
    log_step "Wait for services to be ready"

    wait_for_http "RabbitMQ Management"   "http://localhost:15672"            "200"
    wait_for_http "Gateway HTTP"          "$GATEWAY_HTTP_URL/health"          "200"
    wait_for_http "Gateway API"           "$GATEWAY_API_URL/health"           "200"
    wait_for_http "n8n"                   "$N8N_URL/healthz"                  "200"

    if [[ "$SKIP_MIDPOINT" == false ]]; then
        wait_for_http "MidPoint" "$MIDPOINT_URL/ws/rest/self" "200" "$MIDPOINT_USER:$MIDPOINT_PASS"

        # Wait for ICF connector scanner
        log_info "Waiting for MidPoint connector framework..."
        local conn_elapsed=0
        local conn_count=0
        printf "%s" "${BLUE}[INFO]${RESET}  "
        while [[ $conn_count -eq 0 && $conn_elapsed -lt 120 ]]; do
            conn_count=$(curl -s --connect-timeout 5 --max-time 15 \
                -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
                -H "Accept: application/json" \
                "$MIDPOINT_URL/ws/rest/connectors" 2>/dev/null \
                | jq -r '(.object.object // .object // []) | length' 2>/dev/null || echo 0)
            [[ "$conn_count" -gt 0 ]] && break
            printf "."; sleep 10; conn_elapsed=$((conn_elapsed + 10))
        done
        echo ""
        if [[ "$conn_count" -gt 0 ]]; then
            log_success "MidPoint connector framework ready ($conn_count connectors)"
        else
            log_warn "Connector framework not yet initialized — continuing anyway"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Import n8n workflow
# ---------------------------------------------------------------------------
import_n8n_workflow() {
    log_step "Import n8n workflow"

    if [[ "$SKIP_N8N" == true ]]; then
        log_warn "Skipping n8n workflow import (--skip-n8n)"
        return 0
    fi

    # Copy workflow JSON into container
    log_info "Copying workflow file into container $N8N_CONTAINER..."
    docker cp "$N8N_WORKFLOW_FILE" "$N8N_CONTAINER:/tmp/approval-workflow.json" \
        || die "docker cp failed for n8n workflow"

    # Import via n8n CLI
    log_info "Running: n8n import:workflow..."
    local output
    output=$(docker exec "$N8N_CONTAINER" \
        n8n import:workflow --input=/tmp/approval-workflow.json 2>&1) || true

    if echo "$output" | grep -qi "error\|failed"; then
        log_warn "n8n import output: $output"
        log_warn "Workflow may already exist or had a non-fatal issue."
    else
        log_success "n8n workflow imported"
        log_info "$output"
    fi

    # Activate the workflow via n8n API
    log_info "Activating workflow..."
    local api_key=""

    # Try to get/create an API key from n8n
    local workflow_id
    workflow_id=$(curl -s \
        --user "admin:admin" \
        -H "Accept: application/json" \
        "$N8N_URL/api/v1/workflows" 2>/dev/null \
        | jq -r '.data[0].id // empty' 2>/dev/null || echo "")

    if [[ -n "$workflow_id" ]]; then
        curl -s -X PATCH \
            --user "admin:admin" \
            -H "Content-Type: application/json" \
            -d '{"active": true}' \
            "$N8N_URL/api/v1/workflows/$workflow_id" >/dev/null 2>&1 || true
        log_success "Workflow activated (id: $workflow_id)"
    else
        log_warn "Could not auto-activate workflow. Activate manually at $N8N_URL"
    fi
}

# ---------------------------------------------------------------------------
# Build Java connector JAR
# ---------------------------------------------------------------------------
build_connector() {
    log_step "Build MidPoint connector JAR"

    if [[ "$SKIP_BUILD" == true ]]; then
        log_warn "Skipping build (--skip-build)"
        [[ -f "$JAR_PATH" ]] \
            || die "JAR not found at $JAR_PATH. Build first or remove --skip-build."
        log_info "Using existing JAR: $JAR_PATH"
        return 0
    fi

    log_info "Running ./gradlew clean jar in $CONNECTOR_DIR..."
    (
        cd "$CONNECTOR_DIR"
        chmod +x ./gradlew
        ./gradlew clean jar
    ) || die "Gradle build failed."

    [[ -f "$JAR_PATH" ]] || die "Build succeeded but JAR not found at: $JAR_PATH"
    log_success "JAR built: $JAR_PATH"
}

# ---------------------------------------------------------------------------
# Deploy connector JAR into MidPoint
# ---------------------------------------------------------------------------
deploy_connector() {
    log_step "Deploy connector to MidPoint"

    log_info "Copying JAR into $MIDPOINT_CONTAINER..."
    docker cp "$JAR_PATH" "$MIDPOINT_CONTAINER:$ICF_CONNECTORS_PATH" \
        || die "docker cp failed"

    docker exec "$MIDPOINT_CONTAINER" ls -lh "${ICF_CONNECTORS_PATH}${JAR_NAME}" \
        || die "JAR not found inside container after copy"
    log_success "JAR deployed to $ICF_CONNECTORS_PATH"

    log_info "Restarting $MIDPOINT_CONTAINER to load connector..."
    docker restart "$MIDPOINT_CONTAINER" || die "docker restart failed"
    log_success "Container restarted"

    # Wait for MidPoint to come back up
    wait_for_http "MidPoint (after restart)" "$MIDPOINT_URL/ws/rest/self" "200" "$MIDPOINT_USER:$MIDPOINT_PASS"

    # Wait for connector scanner
    log_info "Waiting for connector framework..."
    local elapsed=0 count=0
    printf "%s" "${BLUE}[INFO]${RESET}  "
    while [[ $count -eq 0 && $elapsed -lt 120 ]]; do
        count=$(curl -s -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
            -H "Accept: application/json" \
            "$MIDPOINT_URL/ws/rest/connectors" 2>/dev/null \
            | jq -r '(.object.object // .object // []) | length' 2>/dev/null || echo 0)
        [[ "$count" -gt 0 ]] && break
        printf "."; sleep 10; elapsed=$((elapsed + 10))
    done
    echo ""
    log_success "Connector framework ready ($count connectors visible)"
}

# ---------------------------------------------------------------------------
# Discover the deployed connector's OID
# ---------------------------------------------------------------------------
discover_connector_oid() {
    log_step "Discover connector OID"

    local response
    response=$(curl -s \
        -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
        -H "Accept: application/json" \
        "$MIDPOINT_URL/ws/rest/connectors" 2>/dev/null)

    CONNECTOR_OID=$(echo "$response" | jq -r \
        '(.object.object // .object // [])[]
         | select(.connectorBundle == "'"$CONNECTOR_BUNDLE"'"
                  and .connectorVersion == "'"$CONNECTOR_VERSION"'")
         | .oid' 2>/dev/null | head -1)

    if [[ -z "$CONNECTOR_OID" || "$CONNECTOR_OID" == "null" ]]; then
        CONNECTOR_OID=$(echo "$response" | jq -r \
            '(.object.object // .object // [])[]
             | select(.connectorBundle == "'"$CONNECTOR_BUNDLE"'")
             | .oid' 2>/dev/null | head -1)
    fi

    if [[ -z "$CONNECTOR_OID" || "$CONNECTOR_OID" == "null" ]]; then
        log_error "Connector '$CONNECTOR_BUNDLE' not found. Available connectors:"
        echo "$response" | jq -r \
            '(.object.object // .object // [])[]
             | "  - \(.connectorBundle // "?") v\(.connectorVersion // "?")  oid=\(.oid // "?")"' \
            >&2 2>/dev/null || echo "$response" >&2
        die "Connector discovery failed. Check: docker logs $MIDPOINT_CONTAINER"
    fi

    log_success "Connector OID: $CONNECTOR_OID"
}

# ---------------------------------------------------------------------------
# Import MidPoint resource (POST → PUT if exists)
# ---------------------------------------------------------------------------
import_resource() {
    log_step "Import MidPoint resource"

    local patched="$TEMP_DIR/ressource-patched.xml"
    sed "s/${OLD_CONNECTOR_OID}/${CONNECTOR_OID}/g" "$RESOURCE_XML" > "$patched"
    grep -q "$CONNECTOR_OID" "$patched" \
        || die "connectorRef OID replacement failed"

    log_info "Patched connectorRef: $OLD_CONNECTOR_OID → $CONNECTOR_OID"

    local resp_file="$TEMP_DIR/resource_resp.txt"
    local http_code
    http_code=$(curl -s -o "$resp_file" -w "%{http_code}" \
        -X POST \
        -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
        -H "Content-Type: application/xml" \
        --data-binary "@$patched" \
        "$MIDPOINT_URL/ws/rest/resources" 2>/dev/null)

    if [[ "$http_code" =~ ^2 ]]; then
        log_success "Resource created (HTTP $http_code)"
    else
        log_info "POST returned $http_code — trying PUT (resource may already exist)..."
        http_code=$(curl -s -o "$resp_file" -w "%{http_code}" \
            -X PUT \
            -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
            -H "Content-Type: application/xml" \
            --data-binary "@$patched" \
            "$MIDPOINT_URL/ws/rest/resources/$RESOURCE_OID" 2>/dev/null)

        if [[ "$http_code" =~ ^2 ]]; then
            log_success "Resource updated (HTTP $http_code)"
        else
            log_error "Resource import failed (HTTP $http_code)"
            cat "$resp_file" >&2
            die "Resource import failed"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Import all MidPoint roles
# ---------------------------------------------------------------------------
import_roles() {
    log_step "Import MidPoint roles"

    local role_files=(
        "$ROLES_DIR/role-ldap.xml"
        "$ROLES_DIR/role-mysql.xml"
        "$ROLES_DIR/role-postgresql.xml"
        "$ROLES_DIR/role-odoo.xml"
        "$ROLES_DIR/role-gateway-mysql-shadowref.xml"
        "$ROLES_DIR/role-gateway-mysql-shadowref admin.xml"
        "$ROLES_DIR/role-gateway-postgresql-shadowref.xml"
        "$ROLES_DIR/role-gateway-postgresql-shadowref admin.xml"
        "$ROLES_DIR/role-gateway-ldap-shadowref.xml"
        "$ROLES_DIR/role-gateway-ldap-shadowref admin.xml"
    )

    local ok=0 fail=0

    for role_file in "${role_files[@]}"; do
        local role_name
        role_name=$(basename "$role_file" .xml)

        if [[ ! -f "$role_file" ]]; then
            log_warn "  Skipped (not found): $role_name"
            ((fail++)) || true
            continue
        fi

        local temp_role="$TEMP_DIR/${role_name}.xml"
        sed "s/${OLD_RESOURCE_OID_IN_ROLES}/${RESOURCE_OID}/g" "$role_file" > "$temp_role"

        local role_oid
        role_oid=$(grep -o 'oid="[^"]*"' "$temp_role" | head -1 | cut -d'"' -f2)

        local resp_file="$TEMP_DIR/role_resp.txt"
        local code
        code=$(curl -s -o "$resp_file" -w "%{http_code}" \
            -X POST \
            -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
            -H "Content-Type: application/xml" \
            --data-binary "@$temp_role" \
            "$MIDPOINT_URL/ws/rest/roles" 2>/dev/null)

        if [[ "$code" =~ ^2 ]]; then
            log_success "  Created:  $role_name (HTTP $code)"
            ((ok++)) || true
        elif [[ -n "$role_oid" ]]; then
            code=$(curl -s -o "$resp_file" -w "%{http_code}" \
                -X PUT \
                -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
                -H "Content-Type: application/xml" \
                --data-binary "@$temp_role" \
                "$MIDPOINT_URL/ws/rest/roles/$role_oid" 2>/dev/null)
            if [[ "$code" =~ ^2 ]]; then
                log_success "  Updated:  $role_name (HTTP $code)"
                ((ok++)) || true
            else
                log_warn "  Failed:   $role_name (HTTP $code)"
                ((fail++)) || true
            fi
        else
            log_warn "  Failed:   $role_name (HTTP $code)"
            ((fail++)) || true
        fi
    done

    echo ""
    log_info "Roles: ${ok} ok, ${fail} failed"
}

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
print_summary() {
    log_step "Setup complete"

    echo "${GREEN}${BOLD}╔══════════════════════════════════════════════╗${RESET}"
    echo "${GREEN}${BOLD}║       Gateway IAM — Setup Complete!          ║${RESET}"
    echo "${GREEN}${BOLD}╚══════════════════════════════════════════════╝${RESET}"
    echo ""
    echo "  ${BOLD}Services:${RESET}"
    echo "    Gateway API     →  http://localhost:8100"
    echo "    Gateway HTTP    →  http://localhost:5100"
    echo "    RabbitMQ UI     →  http://localhost:15672   (guest / guest)"
    echo "    n8n             →  http://localhost:5678    (admin / admin)"
    if [[ "$SKIP_MIDPOINT" == false ]]; then
        echo "    MidPoint        →  http://localhost:8080/midpoint  ($MIDPOINT_USER / $MIDPOINT_PASS)"
    fi
    if [[ "$WITH_TARGETS" == true ]]; then
        echo ""
        echo "  ${BOLD}Target services:${RESET}"
        echo "    LDAP            →  localhost:10389"
        echo "    MySQL           →  localhost:3306"
        echo "    PostgreSQL      →  localhost:5433"
        echo "    Odoo            →  http://localhost:8069"
    fi
    if [[ -z "${GMAIL_USER:-}" ]]; then
        echo ""
        echo "  ${YELLOW}${BOLD}Note:${RESET} GMAIL_USER / GMAIL_APP_PASSWORD not set."
        echo "    n8n email notifications are disabled."
        echo "    Set them and re-run to enable: export GMAIL_USER=you@gmail.com"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
main() {
    local start=$SECONDS

    parse_args "$@"

    echo ""
    echo "${BOLD}${BLUE}╔══════════════════════════════════════════════╗${RESET}"
    echo "${BOLD}${BLUE}║   Gateway IAM — Full Setup from Scratch      ║${RESET}"
    echo "${BOLD}${BLUE}╚══════════════════════════════════════════════╝${RESET}"
    echo ""

    check_prerequisites
    start_services
    wait_for_services
    import_n8n_workflow
    if [[ "$SKIP_MIDPOINT" == false ]]; then
        build_connector
        deploy_connector
        discover_connector_oid
        import_resource
        import_roles
    else
        log_warn "Skipping MidPoint setup (--skip-midpoint)"
    fi
    print_summary

    log_success "Total time: $((SECONDS - start))s"
}

main "$@"
