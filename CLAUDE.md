# CLAUDE.md — AI Assistant Guide for betula

## Persona — introduce yourself

When Claude initializes in this directory, open the first response with a
brief self-introduction as **Betula Claude** — keeper of the Lentago Labs log
capture layer (per-source collectors → Grafana Cloud Loki; Fluent Bit + gitops
sync on the Firewalla today). The appliance itself and on-device operations
are Home Claude's turf — see `~/CLAUDE.md`. One sentence is plenty; don't make
a meal of it.

## What This Project Does

**Betula** is the Lentago Labs log capture layer — renamed from
`firewalla-axiom-pipeline` on 2026-07-04. Direction of travel: a core/client
split where the Firewalla is one collector client and the solidago AWS
platform is the next. **Keep the old name on-device**: the clone at
`/home/pi/.firewalla/firewalla-axiom-pipeline/` and `bootstrap.sh`'s
`CLONE_PATH` stay as-is (live gitops path; the device remote already points at
`lentago/betula`).

A log-shipping pipeline that captures DNS queries, connection flows, and ACL block events from a **Firewalla Gold SE** appliance and sends them to **Grafana Cloud Loki** for search, dashboards, and alerting via [lentago/drosera](https://github.com/lentago/drosera).

> **Axiom removed (2026-07-09):** betula previously shipped to Axiom as its
> primary long-retention archive. The Axiom HTTP output, the device-inventory
> export (`device_lookup_export.sh` → `firewalla-devices`), and the host/Zeek
> system-metrics export (`system_metrics_export.sh` → `firewalla`) were all
> removed to reclaim those two Axiom datasets. The Firewalla now ships **only**
> to Grafana Cloud Loki. The git history before this date holds the Axiom
> archive path if it's revived as a client later.

### Data Flow

```
Firewalla Zeek logs (dns/conn/ssl) + acl-audit ──► Fluent Bit (Docker) ──► Grafana Cloud Loki (direct HTTPS push, no LAN relay)
```

The Loki output is the pipeline's sole output. Any Loki-compatible consumer (Grafana Cloud, a self-hosted Loki, Promtail, Vector) works by swapping the endpoint/creds.

## Tech Stack

- **Fluent Bit** — Log collection agent (runs as Docker container on Firewalla)
- **Bash** — All scripts use `set -euo pipefail`
- **Zeek** — Log format (JSON on recent Firewalla firmware)
- **Grafana Cloud Loki** — Log destination (LogQL; queried in drosera's Grafana)
- **Docker** — Container runtime on Firewalla

## Project Structure

```
betula/
├── CLAUDE.md                       # This file
├── README.md                       # User-facing docs, setup, troubleshooting
├── LICENSE                         # MIT
├── deploy.sh                       # Break-glass workstation deploy via SSH
├── env.example                     # Template for .env (GRAFANA_CLOUD_LOGS_*)
├── .gitignore                      # Excludes .env, *.log, /tmp/
├── fluent-bit/
│   ├── fluent-bit.conf             # Inputs + Grafana Cloud Loki output
│   └── parsers.conf                # Zeek timestamp parser
├── scripts/
│   ├── bootstrap.sh                # One-time on-device clone + cron install
│   ├── gitops-sync.sh              # 5-min poll → fetch → validate → reload
│   ├── start_log_shipping.sh       # Docker bootstrap; lives in post_main.d/ on device
│   ├── fluent_bit_healthcheck.sh   # Cron-driven wedged-container restarter
│   └── rotate_logs.sh              # Daily pipeline-log rotation
├── cron/
│   └── user_crontab                # Log cleanup, healthcheck, log rotation, gitops poll
└── docs/
    ├── architecture.svg            # Pipeline diagram (still shows retired Axiom path)
    └── zeek-field-reference.md     # Zeek JSON field reference
```

## Key Entry Points

| File | Purpose | Runs Where |
|------|---------|------------|
| `scripts/bootstrap.sh` | One-time on-device setup (clone + cron + container start) | Firewalla (manual, once) |
| `scripts/gitops-sync.sh` | Poll origin/main, validate, swap live files, restart container | Firewalla (cron, every 5 min) |
| `scripts/start_log_shipping.sh` | Starts Fluent Bit container; auto-runs after firmware updates | Firewalla (`post_main.d/`) |
| `scripts/fluent_bit_healthcheck.sh` | Restart wedged container based on log error rate | Firewalla (cron, every 5 min) |
| `fluent-bit/fluent-bit.conf` | Defines log inputs + Grafana Cloud Loki output | Inside Fluent Bit container |
| `deploy.sh` | Break-glass workstation push (when GitOps is unusable) | Developer machine |

## Coding Conventions

### Bash Scripts
- Always start with `#!/usr/bin/env bash` and `set -euo pipefail`
- Log messages use bracketed prefixes: `[log-shipping]`, `[healthcheck]`
- Section headers use comment dividers: `# ── Section Name ──`
- Environment variables: `UPPER_SNAKE_CASE`
- Exit code `1` for all errors, with descriptive messages
- Validate preconditions early (e.g., check `.env` exists, Docker available)

### Fluent Bit Config
- INI-style format with `[INPUT]`, `[FILTER]`, `[OUTPUT]` sections
- Sensitive values via `${ENV_VAR}` substitution (never hardcoded)
- Each input gets a unique `Tag` for routing: `zeek.dns`, `zeek.conn`, `firewalla.acl`
- Metadata added via `record_modifier` / `modify` filters
- The `loki` output's stream labels (`job`, `cluster`, `log_source`) are a
  contract with the drosera Grafana queries — changing one silently empties
  panels. See README § Loki output contract.

## Important Paths (on Firewalla)

| Path | Description |
|------|-------------|
| `/home/pi/.firewalla/config/` | Persistent config dir (survives firmware updates) |
| `/home/pi/.firewalla/config/post_main.d/` | Auto-run scripts after boot/firmware update |
| `/home/pi/.firewalla/firewalla-axiom-pipeline/` | On-device git clone managed by `gitops-sync.sh` |
| `/home/pi/.firewalla/config/gitops-sync.log` | GitOps poller log (1 MB rotation → `.log.1`) |
| `/home/pi/.firewalla/config/.gitops-sync.lock` | flock file preventing concurrent poller runs |
| `/bspool/manager/dns.log` | Zeek DNS log (tmpfs, 30 MB limit) |
| `/bspool/manager/conn.log` | Zeek connection log |
| `/alog/acl-audit.log` | Firewalla ACL block log (kernel iptables FW_ADT lines) |
| `/home/pi/.firewalla/config/log_shipping.env` | Deployed .env file on device (NEVER in git) |

## Environment Variables

Defined in `.env` (copied from `env.example`), never committed to git:

| Variable | Example | Used By |
|----------|---------|---------|
| `GRAFANA_CLOUD_LOGS_HOST` | `logs-prod-042.grafana.net` | fluent-bit.conf, start_log_shipping.sh |
| `GRAFANA_CLOUD_LOGS_USER` | `000000` | fluent-bit.conf, start_log_shipping.sh |
| `GRAFANA_CLOUD_LOGS_TOKEN` | `glc_...` | fluent-bit.conf, start_log_shipping.sh |

## Development Workflow

### Making Changes
1. Edit config/scripts on a branch, open a PR, merge to `main`.
2. The Firewalla's GitOps poller picks up the change within 5 min — no SSH
   required for routine config changes.
3. Check `/home/pi/.firewalla/config/gitops-sync.log` to confirm the apply,
   and Grafana (Explore → `{job="firewalla"}`) for the data result.

### GitOps auto-deploy (the normal path)
`scripts/gitops-sync.sh` runs every 5 min from `cron/user_crontab`. It
fetches `origin/main`, validates any new `fluent-bit/*.conf` via
`fluent-bit --dry-run` in a throwaway container, then swaps live files in
`/home/pi/.firewalla/config/` and restarts `fluent-bit-axiom` only if the
relevant files changed. Validation failure → `git reset --hard` to the
rollback SHA; the live container is never disturbed by a bad commit.

`.env` / `log_shipping.env` is device-local and never touched by sync.

### Deployment (`deploy.sh`) — break-glass only
Workstation-driven push for the rare cases GitOps can't help (Firewalla
offline from GitHub, bad commit blocking the poller, first-time bootstrap
without `bootstrap.sh`). Steps:
1. Validates `.env` exists locally
2. Creates persistent directories on Firewalla via SSH
3. Copies all config files and scripts via SCP
4. Copies `.env` as `log_shipping.env`
5. Sets executable permissions on scripts
6. Runs `start_log_shipping.sh` to (re)start the container
7. Merges cron jobs from `cron/user_crontab` via Firewalla's `update_crontab.sh` (never a raw `crontab` install — that wipes system jobs, #67)

### Common Troubleshooting
- **No data in Loki**: Check `docker logs fluent-bit-axiom` for auth errors (401/403 = bad `GRAFANA_CLOUD_LOGS_*` creds; 5xx = Grafana Cloud outage, retries automatically). The container name is still `fluent-bit-axiom` (unchanged to avoid disturbing the running instance).
- **A merged PR didn't deploy**: Tail `/home/pi/.firewalla/config/gitops-sync.log`. Look for `Dry-run FAILED` (bad config — rollback already happened, fix the PR), `git fetch failed` (WAN/GitHub), or nothing at all (poller cron not installed — `crontab -l | grep gitops-sync`).
- **bspool full**: The 5-min cron cleanup job handles rotated logs; verify it's running

## Guidelines for AI Assistants

- **Never hardcode secrets** — use environment variables via `.env`
- **Preserve firmware-update resilience** — all persistent files go under `/home/pi/.firewalla/config/`
- **Keep resource usage low** — Firewalla is an appliance with limited RAM (~50 MB budget for this pipeline)
- **Maintain the strict bash style** — `set -euo pipefail`, prefixed log messages, early validation
- **Don't add dependencies** — the Firewalla has limited packages; only `bash`, `docker`, `curl`, `redis-cli`, `ssh`, `git`, `flock` are available
- **Test deploy.sh changes carefully** — it runs over SSH on a production network appliance
- **Keep .env out of git** — it's in `.gitignore`; use `env.example` as the template
- **Grafana Cloud Loki label discipline** — Loki bills and indexes on stream labels; keep them low-cardinality (`job`, `cluster`, `log_source` only). Never promote a high-cardinality Zeek field (IP, domain) to a Loki label — it explodes the stream count. High-cardinality data belongs in the log line, queried with LogQL filters.
- **Never install the crontab with raw `crontab user_crontab`** — that replaces pi's entire crontab and wipes Firewalla's ~60 system jobs (clean_log, zeekctl crash-recovery, watchdogs, scheduled reboot). This caused a ~15h outage cascade (#67). Always merge via `/home/pi/firewalla/scripts/update_crontab.sh`, run **as pi, never sudo** (as root it fails on a tempfile permission error and empties the crontab). If that script is missing, fail loudly — do not fall back to raw `crontab`.
- **Cron + docker on Firewalla needs `sudo`** — the `pi` user is in the docker group via PAM at login, but cron sessions don't inherit it (this bit us in #48). Any new script invoked from `user_crontab` that touches docker must use `sudo docker`, not bare `docker`.
- **Retry_Limit on every fluent-bit OUTPUT is `False`** — finite retries with a long peer outage silently stop the output forever (#43). On-disk buffering bounds the backlog; leave the limit unbounded.
- **Changes to `scripts/gitops-sync.sh` itself need extra care** — a bug in the poller can either stop the loop or break future deploys. The `scripts/gitops-sync.sh|scripts/bootstrap.sh` case branch in the file classifier exists specifically so a script-only PR doesn't trigger a fluent-bit dry-run + restart. When changing the script, the safe rollout is: scp the new version to the on-device clone out-of-band, then merge the PR — that way the running poller picks up the fix immediately instead of needing one more cycle.

PR workflow + auto-merge arming protocol is fleet-wide; see `~/repos/CLAUDE.md`.

## CI/CD

- **ShellCheck** (`.github/workflows/shellcheck.yml`): Static analysis on every
  non-draft PR. Severity: warning+. Required status check.
- **Claude Code Review** (`.github/workflows/claude-code-review.yml`): Automated
  review focused on appliance safety, secret handling, dependency creep, and
  bash correctness. Required status check.
- **Claude Code** (`.github/workflows/claude.yml`): Triggered by `@claude` in
  issues/PR comments. Implements changes, creates PRs with auto-merge.
- **Auto-merge**: PRs merge automatically when ShellCheck and review pass.
