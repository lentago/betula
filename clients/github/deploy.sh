#!/usr/bin/env bash
set -euo pipefail

# Deploy the betula GitHub Actions collector (#113) to LXC 105 (grafana-stack,
# pve5) as a systemd timer. Idempotent — safe to re-run; re-running is also how
# you ship a code or config change.
#
# Everything installed comes from this checkout — there is no second copy to
# drift:
#   clients/github/github_collector/   → /opt/betula-github/github_collector/
#   clients/github/config.json         → /etc/betula-github/config.json
#   clients/github/systemd/*.service   → /etc/systemd/system/ (timer gets
#                                        @INTERVAL@ from config.json)
# Secrets go to /etc/default/betula-github (0600 root:root). State (seen-set,
# repo cache) lives in /var/lib/betula-github, created by StateDirectory=.
#
# Required environment:
#   BETULA_GITHUB_TOKEN       fine-grained PAT, owner lentago, public repos, read-only
#   GRAFANA_CLOUD_LOGS_URL    Loki push endpoint (https://logs-prod-NNN.grafana.net)
#   GRAFANA_CLOUD_LOGS_USER   Loki username (numeric instance ID)
#   GRAFANA_CLOUD_LOGS_TOKEN  Grafana Cloud access-policy token, logs:write only
#
# Usage — run from a checkout ON the host, as a sudo-capable user:
#   export BETULA_GITHUB_TOKEN=… GRAFANA_CLOUD_LOGS_URL=… \
#          GRAFANA_CLOUD_LOGS_USER=… GRAFANA_CLOUD_LOGS_TOKEN=…
#   ./clients/github/deploy.sh
#
# One-liner from a control host (public repo, no git auth needed):
#   ssh grafana-stack 'git clone --depth 1 https://github.com/lentago/betula \
#     /tmp/betula && cd /tmp/betula && sudo env BETULA_GITHUB_TOKEN=… \
#     GRAFANA_CLOUD_LOGS_URL=… GRAFANA_CLOUD_LOGS_USER=… \
#     GRAFANA_CLOUD_LOGS_TOKEN=… ./clients/github/deploy.sh; rm -rf /tmp/betula'

# ── Helpers ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${YELLOW}[betula-github]${NC} $*"; }
success() { echo -e "${GREEN}[betula-github]${NC} OK: $*"; }
fail()    { echo -e "${RED}[betula-github]${NC} FAIL: $*" >&2; exit 1; }

SERVICE_USER="betula-github"
APP_DIR="/opt/betula-github"
CONF_DIR="/etc/betula-github"
ENV_FILE="/etc/default/betula-github"
UNIT_DIR="/etc/systemd/system"

# ── Preconditions ──
if [[ ${EUID} -eq 0 ]]; then SUDO=""; else command -v sudo >/dev/null || fail "Run as root, or install sudo."; SUDO="sudo"; fi
command -v systemctl >/dev/null || fail "systemd is required."

# python3 ≥ 3.9 is the only runtime dependency (stdlib only). If the guest
# lacks it, codify the package in kalmia — don't apt-get it by hand here.
[[ -x /usr/bin/python3 ]] || fail "/usr/bin/python3 not found — add python3 to LXC 105's kalmia definition."
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
  || fail "python3 is older than 3.9 ($(/usr/bin/python3 --version 2>&1))."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_SRC="${SCRIPT_DIR}/github_collector"
CFG_SRC="${SCRIPT_DIR}/config.json"
[[ -f "${PKG_SRC}/__main__.py" && -f "${CFG_SRC}" ]] || fail "Run from a betula checkout (missing ${PKG_SRC} or ${CFG_SRC})."

for v in BETULA_GITHUB_TOKEN GRAFANA_CLOUD_LOGS_URL GRAFANA_CLOUD_LOGS_USER GRAFANA_CLOUD_LOGS_TOKEN; do
  [[ -n "${!v:-}" ]] || fail "Required env var ${v} is not set."
done
case "${GRAFANA_CLOUD_LOGS_URL}" in
  https://*) ;;
  *) fail "GRAFANA_CLOUD_LOGS_URL is '${GRAFANA_CLOUD_LOGS_URL}', not an https URL — the var didn't expand." ;;
esac
case "${BETULA_GITHUB_TOKEN}${GRAFANA_CLOUD_LOGS_USER}${GRAFANA_CLOUD_LOGS_TOKEN}" in
  *'$'*|*'"'*) fail "A credential contains a literal '\$' or '\"' — env vars weren't expanded." ;;
esac

# Validate the config before touching the host (no secrets, no network).
PYTHONPATH="${SCRIPT_DIR}" /usr/bin/python3 -B -m github_collector --config "${CFG_SRC}" --check-config \
  || fail "config.json failed validation."
INTERVAL="$(/usr/bin/python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("interval", "5m"))' "${CFG_SRC}")"

# ── Service user ──
if id "${SERVICE_USER}" >/dev/null 2>&1; then
  success "System user ${SERVICE_USER} already exists."
else
  info "Creating system user ${SERVICE_USER}..."
  ${SUDO} useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# ── Code + config ──
info "Installing ${APP_DIR}/github_collector and ${CONF_DIR}/config.json..."
${SUDO} rm -rf "${APP_DIR}/github_collector"   # drop modules removed upstream
${SUDO} install -d -m 0755 "${APP_DIR}/github_collector" "${CONF_DIR}"
${SUDO} install -m 0644 "${PKG_SRC}"/*.py "${APP_DIR}/github_collector/"
${SUDO} install -m 0644 "${CFG_SRC}" "${CONF_DIR}/config.json"

# ── Secrets (0600 root:root; systemd reads it as root) ──
info "Writing ${ENV_FILE} (0600, holds both tokens)..."
${SUDO} install -m 0600 -o root -g root /dev/null "${ENV_FILE}"
${SUDO} tee "${ENV_FILE}" >/dev/null <<ENV_EOF
# Managed by clients/github/deploy.sh — do not edit by hand.
BETULA_GITHUB_TOKEN="${BETULA_GITHUB_TOKEN}"
GRAFANA_CLOUD_LOGS_URL="${GRAFANA_CLOUD_LOGS_URL}"
GRAFANA_CLOUD_LOGS_USER="${GRAFANA_CLOUD_LOGS_USER}"
GRAFANA_CLOUD_LOGS_TOKEN="${GRAFANA_CLOUD_LOGS_TOKEN}"
ENV_EOF

# ── systemd units ──
info "Installing betula-github.service and betula-github.timer (every ${INTERVAL})..."
${SUDO} install -m 0644 "${SCRIPT_DIR}/systemd/betula-github.service" "${UNIT_DIR}/betula-github.service"
sed "s/@INTERVAL@/${INTERVAL}/g" "${SCRIPT_DIR}/systemd/betula-github.timer" \
  | ${SUDO} tee "${UNIT_DIR}/betula-github.timer" >/dev/null
${SUDO} chmod 0644 "${UNIT_DIR}/betula-github.timer"
if command -v systemd-analyze >/dev/null; then
  ${SUDO} systemd-analyze verify "${UNIT_DIR}/betula-github.service" "${UNIT_DIR}/betula-github.timer" \
    || info "WARN: systemd-analyze reported problems (above) — continuing; the first tick below is the real test."
fi
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable --now betula-github.timer >/dev/null 2>&1 \
  || fail "Could not enable betula-github.timer."
${SUDO} systemctl restart betula-github.timer   # pick up a changed interval

# ── First tick, synchronously (oneshot: start returns when the tick ends) ──
info "Running one tick now (the first one backfills the lookback window)..."
${SUDO} systemctl start betula-github.service \
  || fail "First tick failed — check 'journalctl -u betula-github -n 50'."
success "betula-github.timer is active; last tick: $(${SUDO} journalctl -u betula-github -n 1 -o cat --no-pager 2>/dev/null || echo '?')"
info "Watch it: journalctl -u betula-github -f   ·   next run: systemctl list-timers betula-github.timer"
