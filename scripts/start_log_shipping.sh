#!/bin/bash
# =============================================================================
# Firewalla Gold SE — Log Shipping Persistence Script
#
# Install location: /home/pi/.firewalla/config/post_main.d/start_log_shipping.sh
#
# Firewalla runs all scripts in post_main.d/ after every boot and firmware
# update. This ensures the Fluent Bit container is always running.
#
# Boot race (#86): post_main.d can fire before Zeek has recreated its logs on
# the /bspool tmpfs. A tail input started against missing/stale files can wedge
# without ever erroring, so we wait (bounded) for a freshly-written Zeek log
# before launching the container.
# =============================================================================

set -euo pipefail

CONFIG_DIR="/home/pi/.firewalla/config"
ENV_FILE="${CONFIG_DIR}/log_shipping.env"
CONTAINER_NAME="fluent-bit-axiom"
IMAGE="fluent/fluent-bit:latest"

# --- Load environment variables ----------------------------------------------
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
else
    echo "[log-shipping] WARNING: $ENV_FILE not found. Create it with:"
    echo "  GRAFANA_CLOUD_LOGS_HOST=logs-prod-XXX.grafana.net"
    echo "  GRAFANA_CLOUD_LOGS_USER=000000"
    echo "  GRAFANA_CLOUD_LOGS_TOKEN=glc-your-token-here"
    exit 1
fi

# --- Validate required env vars ----------------------------------------------
if [ -z "${GRAFANA_CLOUD_LOGS_HOST:-}" ] || [ -z "${GRAFANA_CLOUD_LOGS_USER:-}" ] || [ -z "${GRAFANA_CLOUD_LOGS_TOKEN:-}" ]; then
    echo "[log-shipping] ERROR: GRAFANA_CLOUD_LOGS_{HOST,USER,TOKEN} must all be set in $ENV_FILE"
    exit 1
fi

# --- Wait for Docker ---------------------------------------------------------
echo "[log-shipping] Waiting for Docker daemon..."
for _i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! docker info >/dev/null 2>&1; then
    echo "[log-shipping] ERROR: Docker not available after 60 seconds."
    exit 1
fi

# --- Wait for Zeek logs (boot race, #86) -------------------------------------
# Bounded: after ZEEK_WAIT_MAX_SECS we proceed anyway so a quiet or broken Zeek
# can never block log shipping (the healthcheck's delivery check is the backstop).
readonly ZEEK_WAIT_MAX_SECS=300
readonly ZEEK_FRESH_MIN=2
zeek_logs_fresh() {
    [ -n "$(find /bspool/manager -maxdepth 1 -name '*.log' -mmin "-${ZEEK_FRESH_MIN}" -print -quit 2>/dev/null)" ]
}

echo "[log-shipping] Waiting for fresh Zeek logs in /bspool/manager (max ${ZEEK_WAIT_MAX_SECS}s)..."
_waited=0
until zeek_logs_fresh; do
    if [ "$_waited" -ge "$ZEEK_WAIT_MAX_SECS" ]; then
        echo "[log-shipping] WARNING: no Zeek log modified in the last ${ZEEK_FRESH_MIN} min after ${ZEEK_WAIT_MAX_SECS}s — starting anyway"
        break
    fi
    sleep 5
    _waited=$((_waited + 5))
done

# --- Create data directory for position tracking -----------------------------
mkdir -p "${CONFIG_DIR}/fluent-bit-data"

# --- Wipe stale position tracking data ---------------------------------------
# Zeek logs live on a tmpfs that's recreated on every reboot. If Fluent Bit's
# position tracker (*.db files) references byte offsets in files that no longer
# exist, it silently reads nothing. This was the #1 cause of "data stopped
# flowing" in production — hit it 3 times before adding this fix.
echo "[log-shipping] Clearing stale position tracking data..."
rm -f "${CONFIG_DIR}/fluent-bit-data"/*.db
rm -f "${CONFIG_DIR}/fluent-bit-data"/*.offset

# --- Pull image if needed ----------------------------------------------------
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[log-shipping] Pulling Fluent Bit image (first run)..."
    docker pull "$IMAGE"
fi

# --- Stop existing container -------------------------------------------------
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "[log-shipping] Stopping existing ${CONTAINER_NAME} container..."
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

# --- Start Fluent Bit --------------------------------------------------------
echo "[log-shipping] Starting Fluent Bit → Grafana Cloud Loki pipeline..."
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart always \
    --network host \
    -e GRAFANA_CLOUD_LOGS_HOST="${GRAFANA_CLOUD_LOGS_HOST}" \
    -e GRAFANA_CLOUD_LOGS_USER="${GRAFANA_CLOUD_LOGS_USER}" \
    -e GRAFANA_CLOUD_LOGS_TOKEN="${GRAFANA_CLOUD_LOGS_TOKEN}" \
    -v "${CONFIG_DIR}/fluent-bit.conf:/fluent-bit/etc/fluent-bit.conf:ro" \
    -v "${CONFIG_DIR}/parsers.conf:/fluent-bit/etc/parsers.conf:ro" \
    -v "${CONFIG_DIR}/fluent-bit-data:/fluent-bit/data" \
    -v "/bspool/manager:/logs/zeek:ro" \
    -v "/alog:/logs/alog:ro" \
    "$IMAGE"

echo "[log-shipping] Fluent Bit running → Grafana Cloud Loki (${GRAFANA_CLOUD_LOGS_HOST})"
echo "[log-shipping] Check status: docker logs ${CONTAINER_NAME}"
