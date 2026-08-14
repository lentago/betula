# ADR-0004: Grafana Cloud Loki as sole destination; Axiom path deleted

**Status:** Accepted (2026-07-09; reconstructed 2026-08-13)

## Context

Axiom was betula's founding log destination (initial commit, 2026-03-13). Loki
was added alongside it later (the dual-pipeline was the stated design). By
mid-2026-07, Grafana Cloud Loki — via the direct-push path established in
ADR-0003 — was live and sole consumer of the Firewalla's logs. The
`lentago/drosera` Grafana stack consumed only Loki; no active consumer
depended on the Axiom `firewalla` dataset.

At the same time, the Axiom side had grown beyond a single output block.
PR #82 catalogued four Axiom writers:

1. The `[OUTPUT] http` block in `fluent-bit.conf` (to `api.axiom.co`).
2. `device_lookup_export.sh` — device inventory export (cron: hourly + `@reboot`).
3. `system_metrics_export.sh` — host + Zeek process metrics (cron: every 5 min).
4. An `emit_restart_metric` call in `fluent_bit_healthcheck.sh` that POSTed a
   restart event to Axiom on container restart.

Axiom auto-creates a dataset on ingest; leaving `emit_restart_metric` in place
after deleting the dataset would silently re-create `firewalla` — so the
healthcheck call had to go too.

PR #82 also noted this as a reversal: the prior stance had been that a dual
pipeline was intentional (Axiom as long-retention archive, Loki as live pane).
The reversal was recorded explicitly in the PR, CLAUDE.md, and README.

## Decision

Delete all Axiom-facing code from the Firewalla collector. Grafana Cloud Loki
is the sole output. Axiom remains the destination for the AWS collector clients
(`clients/aws/`), which were introduced later and remain Axiom-only. The git
history before PR #82 documents the Axiom path for the Firewalla client if it
needs to be revived as a second client later.

The Fluent Bit container name (`fluent-bit-axiom`) was deliberately kept
unchanged to avoid disturbing the running instance during the GitOps deploy.

## Alternatives

**Keep the dual pipeline**: no Axiom dataset slots reclaimed; operational
surface doubles; no active consumer of the Axiom data at the time of the
decision. The benefit of an independent archive must be weighed against the
maintenance cost of four Axiom writers on a resource-constrained appliance.

**Batch Axiom archive at reduced frequency** *(retrospective — not considered
at the time)*: lateral. A lower-frequency Axiom write (e.g., hourly batch
rather than real-time) would preserve an independent archive while reducing
ingest volume. However, it still occupies the Axiom dataset slots and adds a
second output code path to maintain. For a workload where git history is the
revision record and Loki is the live pane, the operational simplicity of a
single output outweighs the redundancy benefit.

## Consequences

- Axiom `firewalla` and `firewalla-devices` datasets can be reclaimed once
  ingest from all four writers is confirmed at zero.
- Any future Axiom revival for the Firewalla client is a PR against git history
  (the path is documented; it is not lost).
- `GRAFANA_CLOUD_LOGS_*` credentials are now hard-required at container start
  (previously optional, to preserve Axiom-only operation); `start_log_shipping.sh`
  fails fast if they are absent.
- The `drosera` Grafana stack is the sole live consumer. A drosera outage or
  misconfiguration is no longer masked by an independent Axiom path.
