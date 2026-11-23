# ADR-0008: Claude Code transcripts: full copy on the NAS, narration in Loki, by hook

**Status:** Proposed (2026-11-22)

## Context

Every Claude Code surface writes its session transcript locally, to
`~/.claude/projects/<cwd-slug>/<session-id>.jsonl` on the host that ran it.
Chris runs sessions in cloud sandboxes, on his workstations, under Remote
Control on LAN hosts, and on the claytonia workers. Sessions started from a
claude.ai project don't appear in his session list, and a thread that moves
between hosts leaves one file on each, so no single place holds a whole
conversation.

What already exists:

- **claytonia workers**: the full transcript is copied to the NAS at job end
  (`logs/<runid>.transcript.jsonl`), and drosera's worker Alloy
  (`alloy/runner-transcript.alloy`) streams a scrubbed narration to Grafana
  Cloud Loki as `job="claude_transcript"`.
- **xubuntu-ws**: a nightly timer mirrors `~/.claude/projects` to
  `/mnt/lentago/claude-state/<host>` (kalmia#286).
- **drosera ADR-0006** limits what reasoning may enter Grafana Cloud: assistant
  text and tool names only; never thinking, tool inputs, or user and
  tool-result lines.
- **Claude Code hooks** get `session_id`, `transcript_path` and `cwd` on every
  event, can run `async`, and fail open by default. Managed settings
  (`/etc/claude-code/managed-settings.d/*.json`) apply to every session on a
  machine, including `claude -p` and the Agent SDK. (Docs, fetched 2026-11-22:
  hooks, settings, managed-settings, headless, agent-sdk.)

## Decision

1. **A betula collector client, `clients/claude-code/`, driven by a hook.**
   Capturing logs off a host is betula's job; drosera keeps the live pane.
   A managed-settings drop-in runs `cc-transcripts hook` on SessionStart, Stop,
   SubagentStop, PreCompact and SessionEnd. The hook detaches a sync and exits 0.
2. **Two sinks, matching the bullpen's two outputs.**
   - The **NAS archive** gets the whole transcript, secrets redacted, at
     `<archive>/<host>/<projects-relative path>`, plus an index record per
     session. It stays on the LAN.
   - **Loki** gets the ADR-0006 narration on the existing `claude_transcript`
     stream, with one new label, `surface`.
3. **Incremental, at-least-once.** A byte cursor per file per sink moves only
   after that sink accepted the lines. The transcript on disk is the spool.
   Repeats are dropped by `uuid` when read back (claytonia ADR-0006).
4. **Privacy by path.** `BETULA_CC_EXCLUDE` keeps sessions under listed cwds off
   both sinks, the same posture as claytonia's `offbox_transcript: false`.
   Work-confidential checkouts go there.
5. **Stitch by ids.** Each index record holds every id the session showed: its
   own, the Remote Control bridge id, the cloud session id, and any id-shaped
   link in the transcript's first lines. `cc-transcripts stitch` joins records
   that share one.
6. **Workers are out of scope.** They're covered already; the installer refuses
   `claude-runner*` hosts so nothing ships twice.

### Cloud

Not covered by this ADR, on purpose. Cloud sessions don't read user settings
or a device's managed settings. A repo-committed `.claude/settings.json` runs
only in a single-repo session, and sessions started from a claude.ai project
receive neither user nor server-managed settings. The default network level
blocks custom hosts: this container got a proxy `403` for `*.grafana.net`.
The LAN NAS can't be reached from the cloud at all.

So covering single-repo cloud sessions needs three things Chris must choose:
the Loki host added to the environment's allowlist, a `logs:write` token stored
as an environment secret, and the hook committed to each repo (most simply via
`repo-template`). Even then, the full archive has no reachable sink, so cloud
would get narration only, unless a private off-LAN sink (an S3 bucket in
solidago, which the default network level already reaches) is added. That's a
separate decision.

## Alternatives

- **OpenTelemetry export** (`CLAUDE_CODE_ENABLE_TELEMETRY`, OTLP logs). It's
  documented and stable, unlike the transcript format. But it doesn't carry the
  transcript: prompts and responses are redacted by default, and the only
  full-conversation option (`OTEL_LOG_RAW_API_BODIES`) ships whole API bodies.
  That's the opposite of ADR-0006. It's also a new collector path for Grafana
  Cloud. It remains a good fit for metrics later.
- **Extend the nightly NAS mirror to every host.** It's simple, but it's a day
  behind, doesn't reach hosts without the share, and gives Loki nothing.
- **Tail with a per-host Alloy, as on the workers.** It needs Alloy running as
  each user and gives no archive copy. It can't tell a Remote Control session
  from a local one either: the hook sees the env vars, a file tailer doesn't.
- **Ship the full transcript to Loki.** Rejected by drosera ADR-0006.
- **Bring back the context ledger's "auditor outside the fleet" git repo**
  (claytonia ADR-0008, retired). The idea is sound, but the ledger was retired
  at Chris's request, and a git commit per turn is the wrong shape for log
  volume. Loki, written with a write-only token, is already a copy the hosts
  can't edit.

## Consequences

- The transcript format is internal to Claude Code and "changes between
  versions" (docs: sessions). The archive copies lines verbatim, so it doesn't
  care. The narration renderer and the stitch-link scan do care. The tests pin
  today's shape.
- Redaction is pattern-based. A secret with no known shape or keyword reaches
  the NAS. That is why full content never leaves the LAN.
- `surface` is a new label value set under ADR-0006 (label contract). It's
  additive: existing drosera selectors still match.
- A host off the LAN keeps its archive cursor until it's back, but narration
  older than `BETULA_CC_LOKI_MAX_AGE_S` goes to the archive only.
- Rollout to many machines belongs to kalmia (a role that places the drop-in
  and the env file). This ADR ships the client and a per-host `install.sh`
  whose default is a read-only preview.
