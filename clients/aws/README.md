# betula client: aws (solidago platform)

The second betula collector client, per the core/client roadmap (#74): the
**solidago** AWS platform ships logs to the Axiom archive. It has **two
emitters**, architecturally distinct because AWS exposes the two log streams
differently:

| | ECS container logs | ALB access logs |
|---|---|---|
| Emitter | **FireLens** sidecars in solidago's task definitions (streaming) | **none** — AWS only writes ALB logs to S3 as gzipped batch files |
| Transport | Fluent Bit HTTP → Axiom | an **S3 → Axiom shipper** betula builds (`alb-logs/`) |
| Dataset | `cjp-solidago-ecs` | `cjp-solidago-alb` |
| Deploys where | solidago task defs | solidago Lambda (follow-up #108) |

Unlike the Firewalla client (a Fluent Bit container this repo deploys), both
AWS emitters run inside solidago's own infrastructure — so the moving parts
live in `lentago/solidago` and this directory holds the **contract** plus, for
ALB, the **reusable shipper logic** (`alb-logs/`, pure and unit-tested).

## Emitter 1 — ECS container logs (FireLens stream)

| Item | Value | Owner |
|---|---|---|
| Dataset | `cjp-solidago-ecs` | betula (this file names it; created in Axiom) |
| Ingest token | Axiom token scoped to that dataset, ingest-only | betula |
| Token delivery | AWS Secrets Manager `solidago-dev-axiom-ingest-header`, value `Authorization Bearer <token>` (Fluent Bit header syntax — no colon) | solidago |
| Emitter | FireLens sidecars in `modules/ecs` + `modules/site` (HTTP output: json_lines, gzip, TLS, `enable-ecs-log-metadata`) | solidago |
| Event metadata | `ecs_cluster`, `ecs_task_arn`, `ecs_task_definition`, `container_name` stamped per event | FireLens |

## Emitter 2 — ALB access logs (S3 batch shipper)

Per-request records — **client IP, user-agent, request line, and `domain_name`
(the Host header)** — are the visitor-*source* signal that CloudWatch metrics
(and therefore the drosera Grafana pane) cannot carry. `domain_name` gives the
per-site breakdown across the hosted sites. Because ALB logs have no streaming
path, betula owns a standalone S3 → Axiom shipper here; see `alb-logs/` for the
parser, ingest client, handler, and tests.

| Item | Value | Owner |
|---|---|---|
| Dataset | `cjp-solidago-alb` | betula (this file names it; created in Axiom) |
| Ingest token | Axiom token scoped to that dataset, ingest-only | betula |
| Token delivery | AWS Secrets Manager `solidago-dev-axiom-alb-ingest-header`, value `Authorization Bearer <token>` — injected into the Lambda env at deploy time | solidago |
| Reusable logic | `alb-logs/alb_shipper/` (parser + gzip/json_lines Axiom client + S3 handler, pure & unit-tested) | betula (this dir) |
| Deployment | Lambda on the `alb_access_logs_bucket` S3 `ObjectCreated` (solidago#107) — S3 notification, IAM role, packaging | solidago (follow-up #108) |

**Coordination rule (both emitters):** renaming a dataset, rotating to a
differently-scoped token, or changing the header format is a cross-repo change
with solidago — the same discipline as the `log_source` label contract with
drosera.

**Boundary:** betula owns capture + archive (this dataset); drosera owns the
live pane. Solidago platform *metrics* reach Grafana via the Phase 1
CloudWatch datasource — logs never go to Grafana, metrics never come here
(solidago ADR-0001).

**Overnight gaps are the DR drill:** solidago tears down nightly; ingest
stops while the platform is down. That is expected, not a betula fault.

## Not yet done (tracked in #74)

- Terraform for the Axiom side (both datasets + tokens) — today they are
  created by hand in the Axiom UI; `core/axiom` will absorb this along with #12.
- Deploying the ALB shipper as a Lambda (S3 notification, IAM, packaging) —
  solidago follow-up #108; betula owns only the reusable logic in `alb-logs/`.
- The full core/client tree split (firewalla move + poller classification).
