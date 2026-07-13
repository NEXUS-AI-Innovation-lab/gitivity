#!/usr/bin/env bash
# Setup script: deploys the Gateway IAM connector to a fresh MidPoint instance,
# then imports the resource and all roles via the MidPoint REST API.
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT="$SCRIPT_DIR/.."

MIDPOINT_URL="http://localhost:8080/midpoint"
MIDPOINT_USER="administrator"
MIDPOINT_PASS="Test5ecr3t"
MIDPOINT_CONTAINER="gitivity-midpoint"

CONNECTOR_DIR="$PROJECT_ROOT/midpoint-connector"
JAR_NAME="GateWay-IAM-1.2.0-SNAPSHOT.jar"
JAR_PATH="$CONNECTOR_DIR/build/libs/$JAR_NAME"
ICF_CONNECTORS_PATH="/opt/midpoint/var/icf-connectors/"

RESOURCE_XML="$PROJECT_ROOT/midpoint/ressource.xml"

CONNECTOR_BUNDLE="GateWay-IAM"
CONNECTOR_VERSION="1.2.0-SNAPSHOT"

# OID hardcoded in ressource.xml's connectorRef — must be replaced with the
# dynamically discovered OID after JAR deployment.
OLD_CONNECTOR_OID="84505b3d-5f90-4617-a160-6548be000a44"

# Stable OID declared in ressource.xml itself.
RESOURCE_OID="736ea741-2c73-4478-b5d1-07d84cdf860f"

WAIT_TIMEOUT=300
WAIT_INTERVAL=10

SKIP_BUILD=false
SKIP_DEPLOY=false

TEMP_DIR=""
CONNECTOR_OID=""

# ---------------------------------------------------------------------------
# Colors (disabled when not writing to a TTY)
# ---------------------------------------------------------------------------
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    BLUE=$(tput setaf 4)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
fi

# ---------------------------------------------------------------------------
# Logging helpers
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

Bootstraps a fresh MidPoint instance with the Gateway IAM connector
and resource.

Options:
  --skip-build    Skip Gradle build (JAR must already exist at $JAR_PATH)
  --skip-deploy   Skip docker cp + docker restart (connector already deployed)
  --help, -h      Show this help

Prerequisites:
  - curl, jq, Java 17+, and Docker are installed automatically if missing.
  - Supported package managers: Homebrew (macOS), apt, dnf/yum, pacman, zypper.
  - After auto-install, Docker must be running with the MidPoint profile:
      docker compose --profile midpoint up -d

EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-build)  SKIP_BUILD=true ;;
            --skip-deploy) SKIP_DEPLOY=true ;;
            --help|-h)     usage; exit 0 ;;
            *) log_error "Unknown argument: $1"; usage; exit 1 ;;
        esac
        shift
    done
}

# ---------------------------------------------------------------------------
# Detect OS and package manager
# ---------------------------------------------------------------------------
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        case "$ID" in
            ubuntu|debian|linuxmint) echo "debian" ;;
            fedora|rhel|centos|rocky|almalinux) echo "redhat" ;;
            arch|manjaro) echo "arch" ;;
            opensuse*|sles) echo "suse" ;;
            *) echo "unknown" ;;
        esac
    else
        echo "unknown"
    fi
}

# ---------------------------------------------------------------------------
# Install a single package via the appropriate package manager
# ---------------------------------------------------------------------------
pkg_install() {
    local pkg="$1"
    local os
    os=$(detect_os)

    log_info "Installing $pkg (OS: $os)..."

    case "$os" in
        macos)
            command -v brew >/dev/null 2>&1 || {
                log_info "Homebrew not found. Installing Homebrew first..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
                    || die "Homebrew installation failed"
            }
            brew install "$pkg" || die "Failed to install $pkg via Homebrew"
            ;;
        debian)
            sudo apt-get update -qq
            sudo apt-get install -y "$pkg" || die "Failed to install $pkg via apt"
            ;;
        redhat)
            if command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y "$pkg" || die "Failed to install $pkg via dnf"
            else
                sudo yum install -y "$pkg" || die "Failed to install $pkg via yum"
            fi
            ;;
        arch)
            sudo pacman -Sy --noconfirm "$pkg" || die "Failed to install $pkg via pacman"
            ;;
        suse)
            sudo zypper install -y "$pkg" || die "Failed to install $pkg via zypper"
            ;;
        *)
            die "Unsupported OS. Please install $pkg manually."
            ;;
    esac
    log_success "$pkg installed"
}

# ---------------------------------------------------------------------------
# Install Java 17 (LTS) if missing
# ---------------------------------------------------------------------------
install_java() {
    local os
    os=$(detect_os)
    log_info "Installing Java 17..."

    case "$os" in
        macos)
            command -v brew >/dev/null 2>&1 || {
                log_info "Homebrew not found. Installing Homebrew first..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
                    || die "Homebrew installation failed"
            }
            brew install --cask temurin@17 2>/dev/null \
                || brew install openjdk@17 \
                || die "Failed to install Java via Homebrew"
            # Add to PATH for this session if installed via formula
            local jdk_path
            jdk_path=$(brew --prefix openjdk@17 2>/dev/null || true)
            if [[ -d "$jdk_path/bin" ]]; then
                export PATH="$jdk_path/bin:$PATH"
            fi
            ;;
        debian)
            sudo apt-get update -qq
            sudo apt-get install -y openjdk-17-jdk || die "Failed to install OpenJDK 17 via apt"
            ;;
        redhat)
            if command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y java-17-openjdk-devel || die "Failed to install OpenJDK 17 via dnf"
            else
                sudo yum install -y java-17-openjdk-devel || die "Failed to install OpenJDK 17 via yum"
            fi
            ;;
        arch)
            sudo pacman -Sy --noconfirm jdk17-openjdk || die "Failed to install OpenJDK 17 via pacman"
            ;;
        suse)
            sudo zypper install -y java-17-openjdk-devel || die "Failed to install OpenJDK 17 via zypper"
            ;;
        *)
            die "Unsupported OS. Please install Java 17+ manually and ensure it is in PATH."
            ;;
    esac
    log_success "Java installed: $(java -version 2>&1 | head -1)"
}

# ---------------------------------------------------------------------------
# Install Docker if missing
# ---------------------------------------------------------------------------
install_docker() {
    local os
    os=$(detect_os)
    log_info "Installing Docker..."

    case "$os" in
        macos)
            command -v brew >/dev/null 2>&1 || {
                log_info "Homebrew not found. Installing Homebrew first..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
                    || die "Homebrew installation failed"
            }
            brew install --cask docker || die "Failed to install Docker Desktop via Homebrew"
            log_warn "Docker Desktop installed. Please open it from Applications to start the Docker daemon, then re-run this script."
            exit 0
            ;;
        debian)
            sudo apt-get update -qq
            sudo apt-get install -y ca-certificates curl gnupg lsb-release
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
                | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
                | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update -qq
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
                || die "Failed to install Docker via apt"
            sudo systemctl enable --now docker
            sudo usermod -aG docker "$USER"
            log_warn "Added $USER to the docker group. You may need to log out and back in."
            ;;
        redhat)
            sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null \
                || sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
                || sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
                || die "Failed to install Docker via dnf/yum"
            sudo systemctl enable --now docker
            sudo usermod -aG docker "$USER"
            log_warn "Added $USER to the docker group. You may need to log out and back in."
            ;;
        arch)
            sudo pacman -Sy --noconfirm docker docker-compose || die "Failed to install Docker via pacman"
            sudo systemctl enable --now docker
            sudo usermod -aG docker "$USER"
            log_warn "Added $USER to the docker group. You may need to log out and back in."
            ;;
        *)
            die "Unsupported OS. Please install Docker manually: https://docs.docker.com/get-docker/"
            ;;
    esac
    log_success "Docker installed"
}

# ---------------------------------------------------------------------------
# Install prerequisites automatically
# ---------------------------------------------------------------------------
install_prerequisites() {
    log_step "Install prerequisites"

    # curl
    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl not found — installing..."
        pkg_install curl
    else
        log_success "curl found: $(curl --version | head -1)"
    fi

    # jq
    if ! command -v jq >/dev/null 2>&1; then
        log_warn "jq not found — installing..."
        pkg_install jq
    else
        log_success "jq found: $(jq --version)"
    fi

    # Docker
    if ! command -v docker >/dev/null 2>&1; then
        log_warn "docker not found — installing..."
        install_docker
    else
        log_success "docker found: $(docker --version)"
    fi

    # Java (only needed when building)
    if [[ "$SKIP_BUILD" == false ]]; then
        if ! command -v java >/dev/null 2>&1; then
            log_warn "java not found — installing Java 17..."
            install_java
        else
            local java_ver
            java_ver=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}')
            local major
            major=$(echo "$java_ver" | awk -F'[._]' '{print ($1 == "1" ? $2 : $1)}')
            if [[ "$major" -lt 11 ]]; then
                log_warn "Java $java_ver detected but Java 11+ is required — installing Java 17..."
                install_java
            else
                log_success "java found: $java_ver (major $major)"
            fi
        fi
    fi
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
check_prerequisites() {
    log_step "Pre-flight checks"

    docker info >/dev/null 2>&1 \
        || die "Docker is not running. Start Docker and try again."
    log_success "Docker is running"

    local running
    running=$(docker inspect -f '{{.State.Running}}' "$MIDPOINT_CONTAINER" 2>/dev/null || echo "false")
    [[ "$running" == "true" ]] \
        || die "Container '$MIDPOINT_CONTAINER' is not running. Start with: docker compose --profile midpoint up -d"
    log_success "Container $MIDPOINT_CONTAINER is running"

    command -v jq >/dev/null 2>&1 \
        || die "jq is required but not installed. Install with: brew install jq  OR  apt install jq"
    log_success "jq found: $(jq --version)"

    command -v curl >/dev/null 2>&1 \
        || die "curl is required but not installed."
    log_success "curl found"

    if [[ "$SKIP_BUILD" == false ]]; then
        command -v java >/dev/null 2>&1 \
            || die "java is not in PATH. Java 11+ is required to build the connector."
        log_success "java found: $(java -version 2>&1 | head -1)"

        [[ -f "$CONNECTOR_DIR/gradlew" ]] \
            || die "Gradle wrapper not found at $CONNECTOR_DIR/gradlew"
        log_success "gradlew found"
    fi

    [[ -f "$RESOURCE_XML" ]] \
        || die "Resource XML not found: $RESOURCE_XML"
    log_success "Resource XML found"

    TEMP_DIR=$(mktemp -d)
    log_info "Temp directory: $TEMP_DIR"
}

# ---------------------------------------------------------------------------
# Build the Java connector JAR
# ---------------------------------------------------------------------------
build_connector() {
    log_step "Build Java connector"

    if [[ "$SKIP_BUILD" == true ]]; then
        log_warn "Skipping build (--skip-build)"
        [[ -f "$JAR_PATH" ]] \
            || die "JAR not found at $JAR_PATH and --skip-build was set. Build first or remove the flag."
        log_info "Using existing JAR: $JAR_PATH"
        return 0
    fi

    log_info "Running: ./gradlew clean jar  (in $CONNECTOR_DIR)"
    (
        cd "$CONNECTOR_DIR"
        chmod +x ./gradlew
        ./gradlew clean jar
    ) || die "Gradle build failed. Check output above."

    [[ -f "$JAR_PATH" ]] \
        || die "Build succeeded but JAR not found at expected path: $JAR_PATH"
    log_success "JAR built: $JAR_PATH"
}

# ---------------------------------------------------------------------------
# Copy JAR into MidPoint container and restart
# ---------------------------------------------------------------------------
deploy_connector() {
    log_step "Deploy connector to MidPoint"

    if [[ "$SKIP_DEPLOY" == true ]]; then
        log_warn "Skipping deployment (--skip-deploy)"
        return 0
    fi

    log_info "Copying JAR into container $MIDPOINT_CONTAINER..."
    docker cp "$JAR_PATH" "$MIDPOINT_CONTAINER:$ICF_CONNECTORS_PATH" \
        || die "docker cp failed. Is $MIDPOINT_CONTAINER running?"

    docker exec "$MIDPOINT_CONTAINER" ls -lh "${ICF_CONNECTORS_PATH}${JAR_NAME}" \
        || die "JAR copy verification failed — file not found inside container"
    log_success "JAR deployed to $ICF_CONNECTORS_PATH"

    log_info "Restarting $MIDPOINT_CONTAINER to trigger connector discovery..."
    docker restart "$MIDPOINT_CONTAINER" \
        || die "docker restart failed"
    log_success "Container restarted"
}

# ---------------------------------------------------------------------------
# Wait for MidPoint to be ready
# ---------------------------------------------------------------------------
wait_for_midpoint() {
    log_step "Wait for MidPoint to be ready"

    local elapsed=0
    local version_url="$MIDPOINT_URL/ws/rest/self"

    log_info "Polling $version_url (timeout: ${WAIT_TIMEOUT}s)..."
    printf "%s" "${BLUE}[INFO]${RESET}  "

    while true; do
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 5 --max-time 10 \
            -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
            "$version_url" 2>/dev/null || echo "000")

        if [[ "$http_code" == "200" ]]; then
            echo ""
            log_success "MidPoint HTTP is up (${elapsed}s elapsed)"
            break
        fi

        if [[ $elapsed -ge $WAIT_TIMEOUT ]]; then
            echo ""
            die "MidPoint did not respond within ${WAIT_TIMEOUT}s (last HTTP code: $http_code). Check: docker logs $MIDPOINT_CONTAINER"
        fi

        printf "."
        sleep "$WAIT_INTERVAL"
        elapsed=$((elapsed + WAIT_INTERVAL))
    done

    # Secondary wait: ICF connector scanner must have run before we query connectors.
    log_info "Waiting for connector framework to initialize (max 120s)..."
    local conn_elapsed=0
    local conn_count=0
    printf "%s" "${BLUE}[INFO]${RESET}  "

    while [[ $conn_count -eq 0 && $conn_elapsed -lt 120 ]]; do
        local resp
        resp=$(curl -s --connect-timeout 5 --max-time 15 \
            -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
            -H "Accept: application/json" \
            "$MIDPOINT_URL/ws/rest/connectors" 2>/dev/null || echo "{}")

        conn_count=$(echo "$resp" | \
            jq -r '(.object.object // .object // []) | length' 2>/dev/null || echo 0)

        if [[ "$conn_count" -gt 0 ]]; then
            echo ""
            log_success "Connector framework ready ($conn_count connector(s) visible)"
            break
        fi

        printf "."
        sleep 10
        conn_elapsed=$((conn_elapsed + 10))
    done

    if [[ $conn_count -eq 0 ]]; then
        echo ""
        log_warn "No connectors visible after 120s. Continuing anyway — the JAR connector may still appear."
    fi
}

# ---------------------------------------------------------------------------
# Query MidPoint to find the deployed connector's OID
# ---------------------------------------------------------------------------
discover_connector_oid() {
    log_step "Discover connector OID"

    local response
    response=$(curl -s \
        -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
        -H "Accept: application/json" \
        "$MIDPOINT_URL/ws/rest/connectors" 2>/dev/null) \
        || die "Failed to query MidPoint connectors API"

    # Try exact bundle + version match first, then fall back to bundle name only.
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
        log_error "Could not find connector with bundle: $CONNECTOR_BUNDLE"
        log_error "Available connectors:"
        echo "$response" | jq -r \
            '(.object.object // .object // [])[]
             | "  - \(.connectorBundle // "?") v\(.connectorVersion // "?")  oid=\(.oid // "?")"' \
            >&2 2>/dev/null || echo "$response" >&2
        die "Connector discovery failed. Did the JAR deploy correctly? Check: docker logs $MIDPOINT_CONTAINER"
    fi

    log_success "Connector OID: $CONNECTOR_OID"
}

# ---------------------------------------------------------------------------
# Patch connector OID in resource XML, then import via REST API
# ---------------------------------------------------------------------------
import_resource() {
    log_step "Import resource"

    local patched="$TEMP_DIR/ressource-patched.xml"

    sed "s/${OLD_CONNECTOR_OID}/${CONNECTOR_OID}/g" "$RESOURCE_XML" > "$patched"

    grep -q "$CONNECTOR_OID" "$patched" \
        || die "OID replacement failed in resource XML — $OLD_CONNECTOR_OID not found in source"

    log_info "Patched connectorRef OID: $OLD_CONNECTOR_OID -> $CONNECTOR_OID"

    local resp_file="$TEMP_DIR/resource_response.txt"
    local http_code

    # Try POST (create). If resource already exists (409/5xx with existing OID), use PUT (update).
    http_code=$(curl -s -o "$resp_file" -w "%{http_code}" \
        -X POST \
        -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
        -H "Content-Type: application/xml" \
        --data-binary "@$patched" \
        "$MIDPOINT_URL/ws/rest/resources" 2>/dev/null)

    if [[ "$http_code" =~ ^2 ]]; then
        log_success "Resource imported (HTTP $http_code)"
    else
        log_info "POST returned $http_code — resource may already exist, trying PUT..."
        http_code=$(curl -s -o "$resp_file" -w "%{http_code}" \
            -X PUT \
            -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
            -H "Content-Type: application/xml" \
            --data-binary "@$patched" \
            "$MIDPOINT_URL/ws/rest/resources/$RESOURCE_OID" 2>/dev/null)

        if [[ "$http_code" =~ ^2 ]]; then
            log_success "Resource updated via PUT (HTTP $http_code)"
        else
            log_error "Resource import failed (HTTP $http_code)"
            log_error "Response body:"
            cat "$resp_file" >&2
            die "Resource import failed"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Verify the setup
# ---------------------------------------------------------------------------
verify_setup() {
    log_step "Verify setup"

    # Check resource exists
    local resource_code
    resource_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -u "$MIDPOINT_USER:$MIDPOINT_PASS" \
        "$MIDPOINT_URL/ws/rest/resources/$RESOURCE_OID" 2>/dev/null || echo "000")

    if [[ "$resource_code" == "200" ]]; then
        log_success "Resource $RESOURCE_OID is accessible"
    else
        log_warn "Resource not accessible (HTTP $resource_code). Import may have failed."
    fi

    echo ""
    if [[ "$resource_code" == "200" ]]; then
        echo "${GREEN}${BOLD}============================================${RESET}"
        echo "${GREEN}${BOLD}  Gateway IAM MidPoint setup complete!${RESET}"
        echo "${GREEN}${BOLD}============================================${RESET}"
    else
        log_warn "Setup finished with warnings. Review output above."
    fi

    echo ""
    log_info "MidPoint UI : $MIDPOINT_URL"
    log_info "Credentials : $MIDPOINT_USER / $MIDPOINT_PASS"
    log_info "Resource OID: $RESOURCE_OID"
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
main() {
    local start_time=$SECONDS

    parse_args "$@"

    echo ""
    echo "${BOLD}${BLUE}======================================${RESET}"
    echo "${BOLD}${BLUE}  Gateway IAM — MidPoint Setup Script${RESET}"
    echo "${BOLD}${BLUE}======================================${RESET}"
    echo ""

    install_prerequisites
    check_prerequisites
    build_connector
    deploy_connector
    wait_for_midpoint
    discover_connector_oid
    import_resource
    verify_setup

    local elapsed=$((SECONDS - start_time))
    echo ""
    log_success "Total time: ${elapsed}s"
}

main "$@"
