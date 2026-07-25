# betula client: aws (solidago platform)

The second betula collector client, per the core/client roadmap (#74): the
**solidago** AWS platform ships logs to the Axiom archive. It has **three
emitters**, architecturally distinct because AWS exposes each log stream
differently:

| | ECS container logs | ALB access logs | Lambda application logs |
|---|---|---|---|
| Emitter | **FireLens** sidecars in solidago's task definitions (streaming) | **none** — AWS only writes ALB logs to S3 as gzipped batch files | **none** — a Lambda has no sidecar; `console.log` lands only in CloudWatch Logs |
| Transport | Fluent Bit HTTP → Axiom | an **S3 → Axiom shipper** betula builds (`alb-logs/`) | a **CloudWatch Logs subscription filter → forwarder Lambda** betula builds (`cloudwatch-logs/`) |
| Dataset | `cjp-solidago-ecs` | `cjp-solidago-alb` | `cjp-solidago-ask` |
| Deploys where | solidago task defs | solidago Lambda (follow-up #108) | solidago (follow-up, lentago/solidago#144) |

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

## Emitter 3 — Lambda application logs (CloudWatch Logs subscription filter)

A Lambda has **no sidecar** to hang a FireLens output on, so its `console.log`
output lands only in CloudWatch Logs and stops there — invisible to Axiom and to
the drosera pane. The first case is the Ask endpoint's structured `ask_query`
events (lentago/site-pondviewlane-com#23), the highest-value signal the Pond
View Lane site produces. betula owns a standalone **CloudWatch Logs → Axiom
forwarder** here; see `cloudwatch-logs/` for the payload decoder, ingest client,
handler, and tests.

| Item | Value | Owner |
|---|---|---|
| Dataset | `cjp-solidago-ask` (dedicated, for an independent retention policy) | betula (this file names it; created in Axiom) |
| Ingest token | Axiom token scoped to that dataset, ingest-only | betula |
| Token delivery | AWS Secrets Manager, value a **bare** token — injected into the forwarder Lambda env (`AXIOM_API_TOKEN`) at deploy time | solidago |
| Reusable logic | `cloudwatch-logs/cloudwatch_shipper/` (base64+gzip decoder + gzip/ndjson Axiom client + subscription-filter handler, pure & unit-tested) | betula (this dir) |
| Deployment | subscription filter on the Ask Lambda's log group + forwarder Lambda + IAM | solidago (follow-up, lentago/solidago#144) |
| Retention | **deliberate 30–90 days** — resident-authored question text; never the inherited 14-day CloudWatch default (see `cloudwatch-logs/README.md`) | betula |

Note the token is delivered as a **bare** value, unlike the ECS FireLens
emitter's `Authorization Bearer <token>` header-string — the ALB and CloudWatch
shippers build the `Bearer` prefix themselves.

**Coordination rule (all three emitters):** renaming a dataset, rotating to a
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
- Deploying the CloudWatch Logs forwarder Lambda (subscription filter on the Ask
  Lambda's log group, IAM, packaging) — solidago follow-up (lentago/solidago#144);
  betula owns only the reusable logic in `cloudwatch-logs/`.
- The full core/client tree split (firewalla move + poller classification).
