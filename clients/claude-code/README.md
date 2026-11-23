# betula client: claude-code (session transcripts → NAS archive + Loki narration)

> **Authorship:** Claude wrote this client's code and docs. Chris directed the
> design and reviewed it.

**What you're about to do:** install a small stdlib-only Python hook on a
machine that runs Claude Code. From then on, every session on that machine
copies its transcript to one central archive on the NAS, with secrets
redacted, and streams a scrubbed narration line per assistant turn to Grafana
Cloud Loki, next to the bullpen's.

**Why bother:** every host keeps its own `~/.claude/projects/<dir>/<session>.jsonl`,
so a conversation that moved between hosts (cloud, a workstation, Remote
Control on another box) is split across several files and there is no one
place to read it. This client gives every host the same two outputs the
claytonia workers already have: a full transcript on the NAS
(`logs/<runid>.transcript.jsonl` there) and a live narration stream in Loki
(drosera's `alloy/runner-transcript.alloy`). The design record is
[ADR-0008](../../docs/adr/0008-claude-code-transcripts.md).

**Time:** five minutes per machine, after the env file exists.

## How it works

```
Claude Code session ──hook (SessionStart, Stop, SubagentStop, PreCompact, SessionEnd)──▶ cc-transcripts hook
        │  writes pending/<session>.json, starts a detached sync, exits 0 in ~0.1 s
        ▼
cc-transcripts sync --session <id>    (one at a time per session; a busy sync is asked to go round again)
        │  reads only complete new lines past each sink's byte cursor
        ├──▶ archive: redact secrets → append to <archive>/<host>/<projects-relative path>   (cursor moves after fsync)
        │             + <archive>/<host>/index/<session>.json   (ids for stitching)
        └──▶ loki:    assistant text + tool NAMES only → job="claude_transcript"            (cursor moves after 2xx)
```

- **Fail open, never block.** The hook is `async`, detaches its work, and
  exits 0 on every error. A slow NAS or a dead Loki can't hold a session.
- **The transcript on disk is the spool.** An unmounted NAS (the archive root
  doesn't exist) or an unreachable Loki leaves that cursor where it was. The
  next hook event in that session catches up. A session that never fires
  another hook (killed, or closed while the NAS was away) waits until
  something runs `cc-transcripts sync --all`; `install.sh` doesn't schedule
  that, so put it on a timer (the kalmia rollout role is the place). The
  shipper never creates the archive root, so it can't write underneath an
  empty mountpoint.
- **Failures are recorded, not printed.** The hook runs detached, so a failing
  sync writes `last_error` into its cursor file and a line to
  `~/.local/state/betula-cc/sync.log`. `cc-transcripts status` lists them.
- **At-least-once** (claytonia ADR-0006): a crash between a write and its
  cursor save repeats lines. `stitch` drops repeats by `uuid`.
- **No backfill.** `install.sh --apply` seeds every existing transcript first,
  so only lines written after install ship.
- **Subagents** ride along: `<session>/subagents/*.jsonl` sync with their parent.

## What ships where

| | NAS archive | Grafana Cloud Loki |
|---|---|---|
| Content | the whole transcript: prompts, thinking, tool inputs and results | assistant text and tool **names** only (drosera ADR-0006) |
| Secret handling | token and `key=value` redaction, JSON kept valid ([`redact.py`](cc_transcripts/redact.py)) | drosera's Alloy scrub, then the same redaction |
| Who can read it | the LAN share's users | anyone with the Grafana stack |
| Session in an excluded cwd | nothing | nothing |

**Redaction is a seatbelt, not a guarantee.** It catches known token shapes
(Grafana, GitHub, Anthropic, AWS, Slack, Google, Bitwarden SM, JWTs, PEM keys,
`user:pass@` URLs) and anything after `token`, `password`, `secret`, `api_key`,
`authorization` and friends. It also redacts some prose after those words
("token handling" → `token ***REDACTED***`), which is accepted. A secret with
no telltale shape or keyword gets through to the NAS. That is why the archive
stays on the LAN and Loki gets the narrow narration only.

### Keep work-confidential sessions off both sinks

`BETULA_CC_EXCLUDE` is a colon-separated list of cwd prefixes or globs. A
session whose cwd matches ships nothing anywhere, the same posture as
claytonia's `offbox_transcript: false`. Put every employer or client checkout
in it **before** installing on a machine that ever touches one. The rule is
about where the session runs, not what it reads.

## Coverage by surface

| Surface | Covered? | How | Confidence |
|---|---|---|---|
| Local CLI, desktop app, IDE | yes | managed-settings drop-in | verified: docs say managed settings apply; hook tested end-to-end in a container |
| Remote Control on a LAN host | yes | same drop-in; the session runs locally | verified on xubuntu-ws 2026-11-22 (Claude Code 2.1.330) |
| `claude -p` / Agent SDK on a host | yes | managed settings load regardless of `settingSources` | verified in docs |
| Bullpen workers (claytonia) | already covered | drosera's Alloy shipper + NAS `logs/` | verified in repos; `install.sh` refuses `claude-runner*` hosts |
| Cloud sessions with one repo | not yet | needs a repo-committed hook, an allowlisted Loki host, and a token in the environment | verified in docs; this container got `403` from the proxy for `*.grafana.net` |
| Cloud sessions from project threads | **no** | they read neither user nor server-managed settings, and often start with no repo | inferred from docs (Claude Tag sessions get no server-managed settings) and this container's env |
| Windows | not yet | the drop-in path differs and there's no `flock` | not built |

The cloud gap is a decision, not a bug: see ADR-0008 § Cloud.

## Install

```bash
# 1. Config (secrets stay here, never in git)
sudo install -d /etc/betula
sudo cp env.example /etc/betula/claude-code.env
sudoedit /etc/betula/claude-code.env          # archive dir, Loki creds, BETULA_CC_EXCLUDE
sudo chmod 0640 /etc/betula/claude-code.env && sudo chgrp "$(id -gn)" /etc/betula/claude-code.env

# 2. Preview: read-only, changes nothing, shows what one recent session would ship
./install.sh

# 3. Install (copies to /opt/betula/claude-code, seeds, writes the drop-in)
./install.sh --apply
```

The archive root (`/mnt/lentago/claude-state/transcripts`) must exist on the
NAS once: `mkdir` it from any host that mounts the share. Never put it under
`web/`: `pub.lan` serves that to the whole LAN (kalmia#286).

Turn it off with `./install.sh --uninstall`, or just delete
`/etc/claude-code/managed-settings.d/60-betula-transcripts.json`.

## Check it's working

```bash
/opt/betula/claude-code/cc-transcripts check                      # resolved config, no secrets
/opt/betula/claude-code/cc-transcripts status                     # any file whose last sync failed
/opt/betula/claude-code/cc-transcripts sync --all --days 1 --dry-run
ls /mnt/lentago/claude-state/transcripts/$(hostname)/index/       # one file per session
```

In Grafana Explore: `{job="claude_transcript", worker="<host>"}`. Workstation
lines carry a `surface` label (`cli`, `desktop`, `ide`, `remote-control`,
`headless`, `cloud`); the bullpen's don't, so `{job="claude_transcript", surface=""}`
is the bullpen alone.

**`surface` names the launcher, not the route you watched it through.** A
session started by `claude remote-control` from a CLI is `remote-control`
(Claude Code sets `CLAUDE_CODE_BRIDGE_SESSION_ID`). One started over Remote
Control from a claude.ai project thread is `desktop`: it runs as a child of
the desktop app with no remote or bridge variable, so nothing inside it can
tell it from a local desktop session (verified on Claude Code 2.1.330). Its
index record keeps the desktop app's `local_…` id as `host_session_id`.
**Not yet confirmed** whether that id is per conversation or per app process,
so `stitch` does not join on it: as a link it could merge every session the
app ran into one thread. Group by it by hand
(`grep -l local_… <archive>/*/index/*.json`) until its scope is known.

## Put a conversation back together

```bash
cc-transcripts stitch <any session id, cse_ id or bridge id>          # list the pieces
cc-transcripts stitch <id> --merge > thread.jsonl                     # one time-ordered file
```

Each host's index names every id it saw: the local session id, the cloud
session id, the Remote Control bridge id, and any id-shaped value in the
transcript's first lines. Records that share one are joined. Merged lines carry
`_betula: {host, surface, file}`.

**Not yet confirmed:** the exact first-line record a moved thread writes (its
"derived-from" link). The transcript format is internal to Claude Code and
changes between versions, so the index collects any id under a key that looks
like a link (`session`, `parent`, `derived`, `fork`, `origin`, `source`) rather
than trusting one name. Check with a real moved thread before relying on it.

## Feed contract (Loki)

Same stream as the bullpen's, so the existing dashboard panels pick it up:

| Label | Value |
|---|---|
| `job` | `claude_transcript` |
| `cluster` | `lentago-lab` (`BETULA_CC_CLUSTER`) |
| `worker` | short hostname |
| `project` | basename of the session's cwd, else `adhoc` |
| `session_id` | the local session id |
| `surface` | **new**, workstation streams only; see above |

Line: `[TEXT] <narration>  [TOOL] <name>, <name>`, stamped with the
transcript line's own time. Lines older than `BETULA_CC_LOKI_MAX_AGE_S`
(default 3600, the Alloy pipeline's `older_than`) go to the archive only.
Lines are capped at 16 KB so one huge turn can't earn a permanent 400. Subagent
lines share their parent's stream and can be older than lines already sent;
Grafana Cloud Loki accepts out-of-order writes within its window, and anything
it still refuses as out of order is dropped from Loki only (it's in the archive).

`session_id` is a label because the bullpen's stream already carries it
(drosera's `runner-transcript.alloy`); this client keeps that contract rather
than forking it. It is one stream per session, which is the same cardinality
the bullpen already pays.
`surface` is an addition under ADR-0006 (label contract): existing selectors
still match; drosera needs no change to keep working.

## Tests

```bash
python3 -m unittest discover -s tests
```

The suite plants `LEAK-` markers in prompts, thinking, tool inputs and tool
results and fails if one reaches the Loki body; it also checks the archive
stays valid JSON after redaction, cursors hold on failure, an unmounted
archive is never written under, excluded cwds ship nothing, and stitching
joins two hosts and drops a repeated line.
