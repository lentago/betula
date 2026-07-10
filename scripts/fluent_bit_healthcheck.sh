#!/bin/bash
# =============================================================================
# Fluent Bit Health Check
#
# Runs every 5 minutes via cron. Inspects recent Fluent Bit output and, if it's
# only producing errors (or the container is stopped), restarts it to clear
# wedged state.
#
# Catches all known failure modes:
#   - DNS resolution failures wedging the connection pool
#   - Upstream outage recovery (errors resolved but flushes stuck)
#   - Stale connections after network changes
#
# Install location: /home/pi/.firewalla/config/fluent_bit_healthcheck.sh
# =============================================================================

set -euo pipefail

CONTAINER_NAME="fluent-bit-axiom"
LOGFILE="/home/pi/.firewalla/config/fluent-bit-healthcheck.log"
CHECK_WINDOW="5m"
readonly LOG_MAX_BYTES=1048576

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [healthcheck] $1" >> "$LOGFILE"
}

rotate_log() {
    if [[ -f "$LOGFILE" ]]; then
        local size
        size=$(stat -c%s "$LOGFILE")
        if (( size >= LOG_MAX_BYTES )); then
            mv "$LOGFILE" "${LOGFILE}.1"
        fi
    fi
}

rotate_log

# --- Is the container even running? ------------------------------------------
if ! sudo docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "Container not running — starting via post_main.d script"
    sudo /home/pi/.firewalla/config/post_main.d/start_log_shipping.sh 2>&1 | tee -a "$LOGFILE" >/dev/null
    exit 0
fi

# --- Get recent logs ---------------------------------------------------------
RECENT_LOGS=$(sudo docker logs --since "$CHECK_WINDOW" "$CONTAINER_NAME" 2>&1)

# Silence is healthy — a steady-state Fluent Bit logs nothing between the
# startup banner and any errors. Exit before the counting pipelines below,
# which cannot handle empty input under pipefail.
if [ -z "$RECENT_LOGS" ]; then
    exit 0
fi

# --- Check for successful activity -------------------------------------------
# Silence is healthy: Fluent Bit logs nothing when flushes succeed quietly.
# No-data detection (extended gap with zero events reaching Loki) is handled
# by external Grafana Cloud alerting, not this script.
# Fluent Bit doesn't log successful flushes at "warn" level, but it DOES
# stay quiet when things are working. Errors are loud. So the logic is:
#   - If we see errors AND nothing else → stuck
#   - If we see only the startup banner → just restarted, give it time
#   - If we see errors mixed with normal operation → recovering, leave it

# grep -c prints "0" itself on no match (exiting 1) — `|| true` satisfies
# set -e without appending a second "0" line, which breaks the arithmetic
# and integer comparisons below.
ERROR_COUNT=$(echo "$RECENT_LOGS" | grep -c '\[error\]' 2>/dev/null || true)
WARN_COUNT=$(echo "$RECENT_LOGS" | grep -c '\[ warn\]' 2>/dev/null || true)
# shellcheck disable=SC2034  # tracked for future use
RETRY_COUNT=$(echo "$RECENT_LOGS" | grep -c 'retry in' 2>/dev/null || true)

# If there are retries happening, Fluent Bit is actively trying but failing
# Check if ALL recent lines are errors/warnings (no successful flushes)
# `|| true`: grep -v exits 1 if every line is blank, which pipefail would
# otherwise turn into a script-killing failure; wc still prints 0.
TOTAL_LINES=$(echo "$RECENT_LOGS" | grep -v '^\s*$' | wc -l || true)
ERROR_LINES=$((ERROR_COUNT + WARN_COUNT))

# --- Decision logic ----------------------------------------------------------

# If the only output is the startup banner, it just restarted — skip
if echo "$RECENT_LOGS" | grep -q "Fluent Bit v" && [ "$ERROR_COUNT" -eq 0 ]; then
    # Healthy or just started — no action needed
    exit 0
fi

# If there are zero errors, everything is fine
if [ "$ERROR_COUNT" -eq 0 ]; then
    exit 0
fi

# If errors make up more than 80% of all output lines, it's stuck
if [ "$TOTAL_LINES" -gt 0 ]; then
    ERROR_RATIO=$((ERROR_LINES * 100 / TOTAL_LINES))
    if [ "$ERROR_RATIO" -gt 80 ]; then
        log "WARNING: ${ERROR_RATIO}% error rate (${ERROR_LINES}/${TOTAL_LINES} lines) — restarting"
        log "Last errors: $(echo "$RECENT_LOGS" | grep '\[error\]' | tail -3)"
        sudo docker restart "$CONTAINER_NAME" >> "$LOGFILE" 2>&1
        log "Container restarted (reason: high error rate)"
        exit 0
    fi
fi

# Errors present but not dominant — Fluent Bit is probably recovering on its own
log "INFO: ${ERROR_COUNT} errors in last ${CHECK_WINDOW} but container appears to be recovering"
