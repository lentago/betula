#!/usr/bin/env bash
set -euo pipefail

# device_inventory_publish.sh — the Firewalla device-inventory collector.
# Runs ON the Firewalla (pi user), hourly from cron/user_crontab, straight from
# the gitops clone (like gitops-sync.sh) so a merge to main is the deploy.
# Reads the box's own device inventory from local redis and pushes it to Loki
# as the log_source="device_inventory" stream.
#
# Moved from lentago/drosera (#115; drosera#151, drosera#243): capture is
# betula's side of the boundary. drosera consumes the stream — its dashboards
# resolve raw LAN IPs to device names by joining it in Grafana transformations
# and a label_values() template variable — so the stream contract below is an
# interface drosera depends on. Change it only with a matching drosera change.
#
# Stream contract (one Loki stream per (device, ip) pair):
#   labels    { log_source="device_inventory", dev="<name>|<ip>" }
#   line      {"name":"…","ip":"…","mac":"…","family":"4"|"6","source":"firewalla-redis"}
#   The dev label's "<name>|<ip>" shape is load-bearing: drosera populates a
#   dashboard variable with label_values({log_source="device_inventory"}, dev)
#   and regex /(?<text>[^|]+)\|(?<value>.+)/, so any '|' is stripped from names.
#   cluster="lentago-lab" is added by the central Alloy's external_labels, not
#   here — egress goes through that Alloy's Loki receiver (drosera ADR-0006;
#   whether to push direct to Cloud Loki instead is drosera#243).
#
# Redis model (Firewalla): one hash per device under `host:mac:<MAC>` with
# fields `name` (user label), `bname` (discovered/best name), `mac`, `ipv4Addr`
# (single string), `ipv6Addr` (JSON-encoded array of addresses).
#
# Deps: redis-cli, jq, curl (all present on the box). No sudo.
#
# Env knobs (optionally from /home/pi/.firewalla/config/device_inventory.env):
#   ALLOY_HOST   central Alloy host (default 192.168.139.20 — the Loki receiver)
#   ALLOY_PORT   Loki push port (default 3100)
#   REDIS_CLI    redis-cli invocation (default "redis-cli"; override to add -h/-p)
#   DRY_RUN      if non-empty, print the payload to stdout instead of pushing

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENV_FILE="/home/pi/.firewalla/config/device_inventory.env"
if [ -f "${ENV_FILE}" ]; then
  # shellcheck source=/dev/null
  . "${ENV_FILE}"
fi

ALLOY_HOST="${ALLOY_HOST:-192.168.139.20}"
ALLOY_PORT="${ALLOY_PORT:-3100}"
PUSH_URL="http://${ALLOY_HOST}:${ALLOY_PORT}/loki/api/v1/push"
REDIS_CLI="${REDIS_CLI:-redis-cli}"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
for dep in jq curl; do
  command -v "$dep" >/dev/null 2>&1 || { echo "FATAL: '$dep' not found in PATH" >&2; exit 1; }
done
# REDIS_CLI may carry flags (e.g. "redis-cli -p 6379"); check the first word.
command -v "${REDIS_CLI%% *}" >/dev/null 2>&1 || { echo "FATAL: redis-cli not found in PATH" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Collect one JSON stream object per (device, ip) into a temp file.
# ---------------------------------------------------------------------------
STREAMS_FILE="$(mktemp)"
trap 'rm -f "${STREAMS_FILE}"' EXIT

# Single timestamp for the whole batch — one hourly snapshot, one instant.
TS_NS="$(date +%s%N)"

# emit <name> <ip> <mac> <family> — append a Loki stream object.
emit() {
  name="$1"; ip="$2"; mac="$3"; family="$4"
  # Strip '|' from the display name so the dev="<name>|<ip>" split stays clean.
  name="${name//|/}"
  jq -n \
    --arg name "$name" --arg ip "$ip" --arg mac "$mac" \
    --arg family "$family" --arg ts "$TS_NS" '
    {
      stream: { log_source: "device_inventory", dev: ($name + "|" + $ip) },
      values: [ [ $ts, ({ name: $name, ip: $ip, mac: $mac, family: $family, source: "firewalla-redis" } | tojson) ] ]
    }' >> "${STREAMS_FILE}"
}

device_count=0
record_count=0

# --scan is cursor-based and non-blocking (unlike KEYS) — safe on a live box.
while IFS= read -r key; do
  [ -n "$key" ] || continue
  device_count=$((device_count + 1))

  mac="$($REDIS_CLI hget "$key" mac 2>/dev/null || true)"
  [ -n "$mac" ] || mac="${key#host:mac:}"

  name="$($REDIS_CLI hget "$key" name 2>/dev/null || true)"
  [ -n "$name" ] || name="$($REDIS_CLI hget "$key" bname 2>/dev/null || true)"
  [ -n "$name" ] || name="$mac"

  ipv4="$($REDIS_CLI hget "$key" ipv4Addr 2>/dev/null || true)"
  ipv6json="$($REDIS_CLI hget "$key" ipv6Addr 2>/dev/null || true)"

  if [ -n "$ipv4" ] && [ "$ipv4" != "null" ]; then
    emit "$name" "$ipv4" "$mac" "4"
    record_count=$((record_count + 1))
  fi

  # ipv6Addr is a JSON-encoded array; tolerate it being empty/missing/invalid.
  if [ -n "$ipv6json" ] && [ "$ipv6json" != "null" ]; then
    while IFS= read -r ip6; do
      [ -n "$ip6" ] || continue
      emit "$name" "$ip6" "$mac" "6"
      record_count=$((record_count + 1))
    done < <(printf '%s' "$ipv6json" | jq -r 'if type == "array" then .[] else empty end' 2>/dev/null || true)
  fi
done < <($REDIS_CLI --scan --pattern 'host:mac:*' 2>/dev/null || true)

if [ "$record_count" -eq 0 ]; then
  echo "No device records with an IP found across ${device_count} host:mac:* key(s) — nothing to push." >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Assemble the batched payload ({streams:[…]}) and push once.
# ---------------------------------------------------------------------------
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "${STREAMS_FILE}" "${PAYLOAD_FILE}"' EXIT
jq -s '{ streams: . }' "${STREAMS_FILE}" > "${PAYLOAD_FILE}"

if [ -n "${DRY_RUN:-}" ]; then
  echo "DRY_RUN: ${record_count} record(s) from ${device_count} device(s); payload for ${PUSH_URL}:" >&2
  cat "${PAYLOAD_FILE}"
  exit 0
fi

http_code="$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST "${PUSH_URL}" \
  -H 'Content-Type: application/json' \
  --data-binary "@${PAYLOAD_FILE}")" \
  || { echo "FATAL: push to ${PUSH_URL} failed (curl error)" >&2; exit 1; }

# Loki/Alloy returns 204 No Content on a successful push.
case "$http_code" in
  204|200)
    echo "Pushed ${record_count} record(s) from ${device_count} device(s) to ${PUSH_URL} (HTTP ${http_code})."
    ;;
  *)
    echo "FATAL: push to ${PUSH_URL} returned HTTP ${http_code}" >&2
    exit 1
    ;;
esac
