#!/usr/bin/env bash
# =============================================================================
# Deploy firewalla-axiom-pipeline to a Firewalla Gold SE
#
# Usage:
#   ./deploy.sh <firewalla-ip>
#   ./deploy.sh 192.168.1.1
#
# Copies all config files to the Firewalla's persistent directory,
# sets permissions, starts the pipeline, and installs cron.
# Safe to re-run: only copies changed files and restarts the container
# when config actually changed.
#
# Prerequisites:
#   - SSH access to the Firewalla (pi user)
#   - .env file configured with your Axiom credentials
#   - Docker enabled on the Firewalla
# =============================================================================

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: ./deploy.sh <firewalla-ip>"
    echo "  e.g. ./deploy.sh 192.168.1.1"
    exit 1
fi

FW_IP="$1"
FW_USER="pi"
FW_CONFIG="/home/pi/.firewalla/config"

# --- Preflight checks --------------------------------------------------------
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Copy env.example to .env and configure it."
    exit 1
fi

if ! grep -qE '^AXIOM_API_TOKEN=.+' .env || ! grep -qE '^AXIOM_DATASET=.+' .env; then
    echo "ERROR: .env is missing required keys (AXIOM_API_TOKEN, AXIOM_DATASET). Check env.example."
    exit 1
fi

echo "=== Deploying firewalla-axiom-pipeline to ${FW_IP} ==="

# --- Helpers -----------------------------------------------------------------
# Copy a file only if its md5 differs from the remote copy.
# Prints status and returns 0 if the file was updated, 1 if already current.
copy_if_changed() {
    local src="$1"
    local dst="$2"
    local local_md5 remote_md5
    local_md5=$(md5sum "$src" | awk '{print $1}')
    remote_md5=$(ssh "${FW_USER}@${FW_IP}" \
        "[ -f '${dst}' ] && md5sum '${dst}' | awk '{print \$1}' || echo none")
    if [ "$local_md5" != "$remote_md5" ]; then
        scp "$src" "${FW_USER}@${FW_IP}:${dst}"
        echo "  updated: ${dst}"
        return 0
    else
        echo "  unchanged: ${dst}"
        return 1
    fi
}

# --- Ensure remote directories exist -----------------------------------------
echo "[1/5] Ensuring directories exist on Firewalla..."
ssh "${FW_USER}@${FW_IP}" "mkdir -p ${FW_CONFIG}/post_main.d ${FW_CONFIG}/fluent-bit-data"

# --- Sync files, tracking what changed ---------------------------------------
echo "[2/5] Syncing config files..."
fluent_restart_needed=false
cron_changed=false

if copy_if_changed fluent-bit/fluent-bit.conf "${FW_CONFIG}/fluent-bit.conf"; then
    fluent_restart_needed=true
fi
if copy_if_changed fluent-bit/parsers.conf "${FW_CONFIG}/parsers.conf"; then
    fluent_restart_needed=true
fi
copy_if_changed scripts/device_lookup_export.sh "${FW_CONFIG}/device_lookup_export.sh" || true
copy_if_changed scripts/system_metrics_export.sh "${FW_CONFIG}/system_metrics_export.sh" || true
copy_if_changed scripts/rotate_logs.sh "${FW_CONFIG}/rotate_logs.sh" || true
if copy_if_changed scripts/start_log_shipping.sh "${FW_CONFIG}/post_main.d/start_log_shipping.sh"; then
    fluent_restart_needed=true
fi
# .env synced every run — if it changed the container needs new env vars
if copy_if_changed .env "${FW_CONFIG}/log_shipping.env"; then
    fluent_restart_needed=true
fi
if copy_if_changed cron/user_crontab "${FW_CONFIG}/user_crontab"; then
    cron_changed=true
fi

# --- Set permissions ---------------------------------------------------------
echo "[3/5] Setting permissions..."
ssh "${FW_USER}@${FW_IP}" \
    "chmod +x ${FW_CONFIG}/post_main.d/start_log_shipping.sh ${FW_CONFIG}/device_lookup_export.sh ${FW_CONFIG}/system_metrics_export.sh ${FW_CONFIG}/rotate_logs.sh"

# --- Start or restart the pipeline conditionally -----------------------------
echo "[4/5] Managing Fluent Bit container..."
container_running=$(ssh "${FW_USER}@${FW_IP}" \
    "sudo docker inspect --format='{{.State.Running}}' fluent-bit-axiom 2>/dev/null || echo absent")

if [ "$container_running" != "true" ]; then
    echo "  Container not running — starting..."
    ssh "${FW_USER}@${FW_IP}" "sudo ${FW_CONFIG}/post_main.d/start_log_shipping.sh"
elif [ "$fluent_restart_needed" = "true" ]; then
    echo "  Config changed — restarting container..."
    ssh "${FW_USER}@${FW_IP}" "sudo ${FW_CONFIG}/post_main.d/start_log_shipping.sh"
else
    echo "  Container running, config unchanged — skipping restart."
fi

# --- Install cron and run initial device export ------------------------------
echo "[5/5] Installing cron and exporting device inventory..."
if [ "$cron_changed" = "true" ]; then
    # Merge via Firewalla's script — NEVER `crontab user_crontab`, which replaces
    # pi's whole crontab and wipes ~60 system jobs (#67). Run as pi (the SSH user),
    # never sudo (root empties the crontab on a tempfile permission error).
    ssh "${FW_USER}@${FW_IP}" \
        "test -x /home/pi/firewalla/scripts/update_crontab.sh || { echo 'ERROR: update_crontab.sh not found — refusing raw crontab fallback' >&2; exit 1; }; /home/pi/firewalla/scripts/update_crontab.sh"
    echo "  crontab merged via update_crontab.sh."
else
    echo "  crontab unchanged — skipping reload."
fi
ssh "${FW_USER}@${FW_IP}" "sudo ${FW_CONFIG}/device_lookup_export.sh"

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Verify:"
echo "  ssh ${FW_USER}@${FW_IP} 'sudo docker logs --tail 10 fluent-bit-axiom'"
echo ""
echo "Then check Axiom Stream view for incoming events."
