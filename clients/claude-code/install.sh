#!/usr/bin/env bash
# install.sh — install the betula claude-code transcript client on THIS machine.
#
# Read-only by default: it prints what it would change and a dry run of what
# would ship, and changes nothing. Pass --apply to make the changes (uses sudo
# for /opt and the managed-settings drop-in), or --uninstall to remove them.
#
#   ./install.sh              # preview (safe on any host)
#   ./install.sh --apply      # install + seed (existing transcripts are NOT backfilled)
#   ./install.sh --uninstall  # remove the drop-in and /opt/betula/claude-code
#
# Machine-wide by design: a managed-settings drop-in is read by every user's
# interactive sessions, Remote Control sessions, `claude -p` and the Agent SDK
# (managed settings load regardless of settingSources). Removing the one file
# turns it off. Do not install on claytonia workers: drosera's Alloy shipper
# already covers them, and a second copy would double-ship.
set -euo pipefail

MODE=preview
case "${1:-}" in
  "") ;;
  --apply) MODE=apply ;;
  --uninstall) MODE=uninstall ;;
  -h|--help) sed -n 2,17p "$0"; exit 0 ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/betula/claude-code
case "$(uname -s)" in
  Linux)  MANAGED_DIR=/etc/claude-code/managed-settings.d ;;
  Darwin) MANAGED_DIR="/Library/Application Support/ClaudeCode/managed-settings.d" ;;
  *) echo "unsupported OS $(uname -s): Linux and macOS only (Windows: not yet)" >&2; exit 1 ;;
esac
DROPIN="$MANAGED_DIR/60-betula-transcripts.json"
MANAGED_MAIN="$(dirname "$MANAGED_DIR")/managed-settings.json"

say() { printf '%s\n' "$*"; }
hr()  { printf '\n== %s\n' "$*"; }

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || { echo "python3 >= 3.8 is required" >&2; exit 1; }

case "$(hostname)" in
  claude-runner*) echo "refusing: $(hostname) is a claytonia worker (covered by drosera's Alloy shipper)" >&2; exit 1 ;;
esac

if [ "$MODE" = uninstall ]; then
  hr "uninstall"
  say "sudo rm -f '$DROPIN'"; sudo rm -f "$DROPIN"
  say "sudo rm -rf '$DEST'";  sudo rm -rf "$DEST"
  say "Left in place: ~/.local/state/betula-cc (cursors) and any env file. Archived copies on the NAS are untouched."
  exit 0
fi

hr "1. files -> $DEST"
for f in cc-transcripts cc_transcripts/*.py; do say "  $f"; done
[ -d "$DEST" ] && say "  (exists: would be replaced)"

hr "2. managed-settings drop-in -> $DROPIN"
cat "$HERE/hooks/managed-settings.d/60-betula-transcripts.json" | sed 's/^/  /'
if [ -f "$MANAGED_MAIN" ] && grep -q '"disableAllHooks"[[:space:]]*:[[:space:]]*true' "$MANAGED_MAIN"; then
  say "  WARNING: $MANAGED_MAIN sets disableAllHooks; these hooks will not run."
fi
[ -f "$DROPIN" ] && { if cmp -s "$DROPIN" "$HERE/hooks/managed-settings.d/60-betula-transcripts.json"; then say "  (already installed, identical)"; else say "  (exists: would be replaced; diff below)"; diff -u "$DROPIN" "$HERE/hooks/managed-settings.d/60-betula-transcripts.json" | sed 's/^/  /' || true; fi; }

hr "3. config (secret-free view)"
PYTHONPATH="$HERE" python3 -m cc_transcripts check | sed 's/^/  /'
for f in /etc/betula/claude-code.env "$HOME/.config/betula/claude-code.env"; do
  if [ -f "$f" ]; then say "  env file: $f ($(stat -c '%a %U' "$f" 2>/dev/null || stat -f '%Lp %Su' "$f"))"; fi
done
say "  No env file means both sinks are off and the hook is a no-op. Template: env.example"

hr "4. seed (claims existing transcripts so history is not backfilled)"
PYTHONPATH="$HERE" python3 -m cc_transcripts seed --dry-run | sed 's/^/  /'

hr "5. what one recent session would ship today, if it were not seeded (read-only)"
latest="$(python3 - "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" <<'PY'
import glob, os, sys
files = glob.glob(os.path.join(sys.argv[1], "*", "*.jsonl"))
print(max(files, key=os.path.getmtime) if files else "")
PY
)"
if [ -n "$latest" ]; then
  sid="$(basename "$latest" .jsonl)"
  # Both sinks are forced on for this preview only, against a throwaway archive
  # root and a placeholder Loki: --dry-run never writes or pushes.
  preview_root="$(mktemp -d)"
  BETULA_CC_ARCHIVE_DIR="$preview_root" BETULA_CC_STATE_DIR="$preview_root/state" \
    GRAFANA_CLOUD_LOGS_URL=https://preview.invalid GRAFANA_CLOUD_LOGS_USER=0 GRAFANA_CLOUD_LOGS_TOKEN=preview \
    PYTHONPATH="$HERE" python3 -m cc_transcripts sync --session "$sid" --dry-run | sed 's/^/  /'
  rm -rf "$preview_root"
else
  say "  no transcripts on this host yet"
fi

if [ "$MODE" = preview ]; then
  hr "preview only: nothing changed. Re-run with --apply to install."
  exit 0
fi

hr "applying"
sudo install -d -m 0755 "$DEST/cc_transcripts"
sudo install -m 0755 "$HERE/cc-transcripts" "$DEST/cc-transcripts"
sudo install -m 0644 "$HERE"/cc_transcripts/*.py "$DEST/cc_transcripts/"
PYTHONPATH="$DEST" python3 -m cc_transcripts seed
sudo install -d -m 0755 "$MANAGED_DIR"
sudo install -m 0644 "$HERE/hooks/managed-settings.d/60-betula-transcripts.json" "$DROPIN"
say "installed. New and resumed sessions ship from their next turn; run /hooks in a session to confirm."
