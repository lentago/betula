#!/usr/bin/env bash
# =============================================================================
# Regression tests for token-guard.sh (#119)
#
# Runs the guard against synthetic plans, with a stub `gh` that serves the PR's
# labels and issue events from fixture files. Each case asserts the exit code.
# CI runs this in the terraform workflow's validate job.
#
# Usage: token-guard_test.sh   (needs bash and jq)
# =============================================================================

set -euo pipefail

GUARD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/token-guard.sh"
readonly GUARD
WORK=$(mktemp -d)
readonly WORK
trap 'rm -rf "$WORK"' EXIT

# ── Stub gh: `gh api [--paginate] <path> --jq <expr>` over fixture files ──────
mkdir -p "$WORK/bin"
cat > "$WORK/bin/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
shift  # "api"
path=""; jqexpr=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --jq) jqexpr="$2"; shift 2 ;;
        --paginate) shift ;;
        *) path="$1"; shift ;;
    esac
done
case "$path" in
    */labels) jq -r "$jqexpr" "$STUB_DIR/labels.json" ;;
    */events) jq -r "$jqexpr" "$STUB_DIR/events.json" ;;
    *) echo "stub gh: unexpected path $path" >&2; exit 1 ;;
esac
STUB
chmod +x "$WORK/bin/gh"

# ── Fixtures ───────────────────────────────────────────────────────────────
plan() {  # plan <token actions JSON>… — a dataset update plus one token per arg
    local changes='{"address":"axiom_dataset.this[\"d\"]","type":"axiom_dataset","change":{"actions":["update"]}}'
    local i=0 a
    for a in "$@"; do
        changes+=",{\"address\":\"axiom_token.t${i}\",\"type\":\"axiom_token\",\"change\":{\"actions\":${a}}}"
        i=$((i + 1))
    done
    printf '{"resource_changes":[%s]}\n' "$changes"
}
labeled() {  # labeled <actor type> <login> — one `labeled` event for the override
    printf '{"event":"labeled","label":{"name":"token-rotation"},"actor":{"type":"%s","login":"%s"}}' "$1" "$2"
}

readonly WITH_LABEL='[{"name":"token-rotation"},{"name":"re-review"}]'
readonly NO_LABEL='[{"name":"re-review"}]'
BY_USER="[$(labeled User cpitzi)]"
BY_BOT="[$(labeled Bot 'lentago-claude-runner[bot]')]"
BOT_THEN_USER="[$(labeled Bot 'x[bot]'),$(labeled User cpitzi)]"
USER_THEN_BOT="[$(labeled User cpitzi),$(labeled Bot 'x[bot]')]"
readonly BY_USER BY_BOT BOT_THEN_USER USER_THEN_BOT

# ── Cases ──────────────────────────────────────────────────────────────────
failures=0
case_n=0
check() {  # check <name> <expected rc> <plan JSON> <pr> <labels> <events>
    local name="$1" want="$2" dir
    case_n=$((case_n + 1))
    dir="$WORK/case$case_n"
    mkdir -p "$dir"
    printf '%s\n' "$3" > "$dir/plan.json"
    printf '%s\n' "$5" > "$dir/labels.json"
    printf '%s\n' "$6" > "$dir/events.json"
    local got=0
    STUB_DIR="$dir" PATH="$WORK/bin:$PATH" GITHUB_REPOSITORY=lentago/betula \
        "$GUARD" "$dir/plan.json" "$4" > "$dir/out.txt" 2>&1 || got=$?
    if [ "$got" -eq "$want" ]; then
        echo "ok   ${name}"
    else
        echo "FAIL ${name}: exit ${got}, want ${want}"
        sed 's/^/     | /' "$dir/out.txt"
        failures=$((failures + 1))
    fi
}

check "no token change"                0 "$(plan '["no-op"]' '["create"]')" ""  "$NO_LABEL"   '[]'
check "token delete, even labeled"     1 "$(plan '["delete"]')"             5   "$WITH_LABEL" "$BY_USER"
check "token replace, even labeled"    1 "$(plan '["delete","create"]')"    5   "$WITH_LABEL" "$BY_USER"
check "update, replace in same plan"   1 "$(plan '["update"]' '["create","delete"]')" 5 "$WITH_LABEL" "$BY_USER"
check "update, no PR for commit"       1 "$(plan '["update"]')"             ""  "$WITH_LABEL" "$BY_USER"
check "update, no label"               1 "$(plan '["update"]')"             5   "$NO_LABEL"   '[]'
check "update, label by Bot"           1 "$(plan '["update"]')"             5   "$WITH_LABEL" "$BY_BOT"
check "update, label by User"          0 "$(plan '["update"]')"             5   "$WITH_LABEL" "$BY_USER"
check "update, Bot then User"          0 "$(plan '["update"]')"             5   "$WITH_LABEL" "$BOT_THEN_USER"
check "update, User then Bot"          1 "$(plan '["update"]')"             5   "$WITH_LABEL" "$USER_THEN_BOT"
check "update, label but no event"     1 "$(plan '["update"]')"             5   "$WITH_LABEL" '[]'

echo "${case_n} cases, ${failures} failed"
[ "$failures" -eq 0 ]
