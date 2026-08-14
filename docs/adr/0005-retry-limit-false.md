# ADR-0005: `Retry_Limit False` on every Fluent Bit output

**Status:** Accepted (2026-05-28; reconstructed 2026-08-13)

## Context

A power outage on 2026-05-28 took down both the Firewalla and the Alloy LAN
relay simultaneously (issue #43). When power returned:

- Alloy came back at approximately 09:24 UTC; the Firewalla shortly after.
- The **Axiom output** (`Retry_Limit False`) kept catching up automatically
  once the Firewalla was back online.
- The **Loki output** (`Retry_Limit 3`) had exhausted its three retry attempts
  on in-flight chunks during the outage. It went permanently silent — no
  further retries, no error surfaced. The container kept running and Axiom kept
  flowing, so nothing was immediately visible; the failure mode was silent until
  empty Grafana panels were noticed ~9.5 hours later.

The asymmetry between the two outputs was the root cause: they had been
configured inconsistently (`Retry_Limit False` on Axiom, `Retry_Limit 3` on
Loki), apparently without recognising that a long peer outage would exhaust
even a small finite limit.

## Decision

Set `Retry_Limit False` on every Fluent Bit output (PR #44). The on-disk buffer
(`storage.total_limit_size 50M` in `[SERVICE]`) bounds the backlog; oldest
events are dropped on disk pressure rather than the output going silent. A peer
outage of any duration now self-heals once connectivity returns, without
operator intervention.

This is recorded as a standing requirement in CLAUDE.md: `Retry_Limit False`
on every output is non-negotiable; any new output added must carry it.

## Alternatives

**A large finite limit** (e.g., `Retry_Limit 10`), floated in issue #43:
rejected. A larger number extends the window before exhaustion, but the failure
mode is identical — a long enough outage (several hours of retries, each with
back-off) will still exhaust even a large finite limit. "Silent stop after N
retries" and "silent stop after 3 retries" are the same problem at different
timescales. `Retry_Limit False` eliminates the failure mode rather than
extending it.

**Container-restart watchdog on Loki silence** *(retrospective — not
considered at the time)*: worse. The container was healthy and running; a
watchdog keyed on container health would not have triggered. A watchdog keyed
on Loki output silence would be complex to instrument and treats the symptom
(missing data) rather than the cause (exhausted retry loop). `Retry_Limit False`
fixes the cause directly.

## Consequences

- During a prolonged peer outage, events accumulate on disk up to 50 MB.
  Events older than the buffer limit are dropped when the limit is hit.
- For the Loki output (live-pane destination), data loss during a long outage
  is acceptable: the backlog flushes on reconnect, and a gap in panels is
  preferable to a permanently silent output.
- Every future output added to `fluent-bit.conf` must carry `Retry_Limit False`
  explicitly; the default (`Retry_Limit 1`) is unsafe for this workload.
