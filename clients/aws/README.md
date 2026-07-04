# betula client: aws (solidago platform)

The second betula collector client, per the core/client roadmap (#74): the
**solidago** AWS platform ships ECS container logs to the Axiom archive.
Unlike the Firewalla client (a Fluent Bit container this repo deploys), the
AWS emitter is **FireLens** — Fluent Bit sidecars declared in solidago's own
task definitions — so the moving parts live in `lentago/solidago` and this
directory holds the contract.

## Contract

| Item | Value | Owner |
|---|---|---|
| Dataset | `cjp-solidago-ecs` | betula (this file names it; created in Axiom) |
| Ingest token | Axiom token scoped to that dataset, ingest-only | betula |
| Token delivery | AWS Secrets Manager `foundry-dev-axiom-ingest-header`, value `Authorization Bearer <token>` (Fluent Bit header syntax — no colon) | solidago |
| Emitter | FireLens sidecars in `modules/ecs` + `modules/site` (HTTP output: json_lines, gzip, TLS, `enable-ecs-log-metadata`) | solidago |
| Event metadata | `ecs_cluster`, `ecs_task_arn`, `ecs_task_definition`, `container_name` stamped per event | FireLens |

**Coordination rule:** renaming the dataset, rotating to a differently-scoped
token, or changing the header format is a cross-repo change with solidago —
the same discipline as the `log_source` label contract with drosera.

**Boundary:** betula owns capture + archive (this dataset); drosera owns the
live pane. Solidago platform *metrics* reach Grafana via the Phase 1
CloudWatch datasource — logs never go to Grafana, metrics never come here
(solidago ADR-0001).

**Overnight gaps are the DR drill:** solidago tears down nightly; ingest
stops while the platform is down. That is expected, not a betula fault.

## Not yet done (tracked in #74)

- Terraform for the Axiom side (dataset + token) — today they are created by
  hand in the Axiom UI; `core/axiom` will absorb this along with #12.
- The full core/client tree split (firewalla move + poller classification).
