# ADR-0002: Pull-based GitOps to a production appliance

**Status:** Accepted (2026-05-28; reconstructed 2026-08-13)

## Context

Before PR #46, all config changes required running `deploy.sh` from a
workstation — SSH and SCP to the Firewalla every time a config line changed.
This had two failure modes:

1. **Operator dependency**: a change could only land when someone was at a
   workstation with network access to the device.
2. **Drift**: nothing enforced that the on-device files matched `main`. PR #35
   (2026-05-23) made this concrete: a Loki output block had been hand-appended
   directly on the Firewalla via a heredoc and never committed to git. It was
   only discovered when deploying PR #34 (ssl.log) and noticing the on-box
   `fluent-bit.conf` was ~1.7 KB larger than git's version. Without recovery,
   the next clean deploy would have silently dropped the Loki output.

Issue #10 had proposed pull-based GitOps. Issue #45 specced the detailed
design: `flock`-guarded poll, `git fetch` + `reset --hard`, file classifier,
dry-run validation, rollback SHA. PR #46 implemented it (2026-05-28).

## Decision

`scripts/gitops-sync.sh` runs every 5 minutes from `cron/user_crontab`. On
each run it:

1. Acquires `flock` on a lock file to prevent concurrent runs.
2. `git fetch origin`; if already up to date, exits silently.
3. Captures a rollback SHA, then `git reset --hard origin/main`.
4. Classifies the diff — fluent-bit config, crontab, scripts, or docs-only.
5. For any new `fluent-bit/*.conf`, validates via `fluent-bit --dry-run` in a
   throwaway container before touching live files.
6. On pass: copies relevant files into `/home/pi/.firewalla/config/`, restarts
   the container, reinstalls the crontab (if changed).
7. On failure: `git reset --hard <rollback-sha>`; live container untouched.

`deploy.sh` is retained as break-glass for the rare cases where GitOps cannot
help (WAN unreachable, poller bug, first-time bootstrap).

Changes to `gitops-sync.sh` itself need extra care: a bug in the poller can
break the loop. The safe rollout is to `scp` the new version to the on-device
clone out-of-band, then merge the PR — so the running poller picks up the fix
immediately rather than needing one more cycle.

## Alternatives

**Manual `deploy.sh` from workstation** (prior state): requires operator
presence, permits drift (proven by PR #35), and involves SSH into a production
security appliance for every routine config change. Retained only as
break-glass.

**Webhook / push-based CD** *(retrospective — not considered at the time)*:
worse. Inbound webhooks require opening the Firewalla's perimeter to incoming
connections — contrary to the threat model of a security appliance that has no
business accepting inbound push commands. Pull-based polling keeps the device's
network posture unchanged.

## Consequences

- Config changes go live within 5 minutes of merging to `main`; no SSH
  required.
- A bad `fluent-bit.conf` commit never disrupts the running container: the
  dry-run catches it, the rollback SHA is applied, and the poller logs the
  error.
- The `log_shipping.env` (credentials) is device-local and never touched by
  the poller; rotating credentials requires a manual `scp`.
- A bug in the poller script itself is the one failure mode that cannot
  self-heal via the normal GitOps path.
