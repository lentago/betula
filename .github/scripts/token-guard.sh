#!/usr/bin/env bash
# =============================================================================
# Token rotation guard for terraform/ (#119)
#
# An update to an axiom_token regenerates it. The old value lapses after
# rotation_grace_period (48h), and every consumer still holding it breaks.
# A delete revokes it at once. A plan that does either still exits 0, so
# `gate` alone would pass it. The terraform workflow runs this over the saved
# plan in both the PR plan job and the apply job on main.
#
#   - No token update or delete     → pass.
#   - Any token delete (or replace) → fail, always. No label covers it.
#   - Token updates only            → pass only if the PR carries the
#     token-rotation label and its most recent `labeled` event came from a
#     User account, not a Bot. The label records that a human account declared
#     a planned rotation. It is not an approval: the authorization is the
#     release of that rotation on its issue (terraform/README.md § Tokens).
#
# Usage: token-guard.sh <plan.json> <pr-number|"">
#   An empty PR number (no PR found for the commit) fails any token update.
#   Needs jq, plus gh with GH_TOKEN and GITHUB_REPOSITORY for label lookups.
# =============================================================================

set -euo pipefail

readonly OVERRIDE_LABEL="token-rotation"

if [ "$#" -ne 2 ]; then
    echo "[token-guard] usage: $0 <plan.json> <pr-number|\"\">" >&2
    exit 1
fi
readonly PLAN_JSON="$1"
readonly PR_NUMBER="$2"

# ── Token changes in the plan ──────────────────────────────────────────────
token_changes() {  # token_changes <action> — addresses whose actions include it
    jq -r --arg a "$1" '
      .resource_changes[]? | select(.type == "axiom_token")
      | select(any(.change.actions[]; . == $a)) | .address' "$PLAN_JSON"
}

deletes=$(token_changes delete)
updates=$(token_changes update)

if [ -z "$deletes" ] && [ -z "$updates" ]; then
    echo "[token-guard] No token update or delete in this plan."
    exit 0
fi

if [ -n "$deletes" ]; then
    echo "::error::[token-guard] This plan deletes or replaces an Axiom token, which revokes it at once with no grace period. No label covers that (terraform/README.md § Tokens):"
    echo "$deletes"
    exit 1
fi

echo "[token-guard] This plan regenerates these Axiom tokens:"
echo "$updates"

# ── Override: the token-rotation label, applied by a human account ─────────
fail_update() {
    echo "::error::[token-guard] $1 A token update is allowed only as a planned rotation: label the PR ${OVERRIDE_LABEL} from a human account, with the consumer handoff ready (terraform/README.md § Tokens)."
    exit 1
}

[ -n "$PR_NUMBER" ] || fail_update "No pull request found for this commit."

labels=$(gh api "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/labels" --jq '.[].name')
grep -qxF "$OVERRIDE_LABEL" <<< "$labels" \
    || fail_update "PR #${PR_NUMBER} does not carry the ${OVERRIDE_LABEL} label."

labeler=$(gh api --paginate "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/events" \
    --jq ".[] | select(.event == \"labeled\" and .label.name == \"${OVERRIDE_LABEL}\") | \"\(.actor.type) \(.actor.login)\"" \
    | tail -n 1)
[ "${labeler%% *}" = "User" ] \
    || fail_update "The ${OVERRIDE_LABEL} label on PR #${PR_NUMBER} was last applied by '${labeler:-unknown}', not a User account."

echo "::warning::[token-guard] Planned rotation (PR #${PR_NUMBER}, ${OVERRIDE_LABEL} applied by ${labeler#* }): the tokens above are regenerated on apply. Each consumer must apply within rotation_grace_period (48h)."
