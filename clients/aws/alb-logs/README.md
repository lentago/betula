# aws client — ALB access logs (S3 → Axiom)

The second emitter of the betula `aws` client (see `../README.md`). Where the
ECS emitter is a FireLens *stream*, ALB access logs have **no streaming path**:
AWS only writes them to S3 as gzipped batch files. So there is a genuine
standalone capture component to own here — an **S3 → Axiom shipper** — which is
exactly the kind of thing betula exists for (roadmap #74, issue #80).

```
solidago ALB ──(access logs)──► S3 bucket ──ObjectCreated──► Lambda (solidago#108)
                                                                │
                                          gunzip → parse lines → gzip json_lines POST
                                                                │
                                                                ▼
                                                   Axiom dataset  cjp-solidago-alb
```

## Contract

| Item | Value | Owner |
|---|---|---|
| Dataset | `cjp-solidago-alb` (parallel to `cjp-solidago-ecs`) | betula (this file names it; created in Axiom) |
| Ingest token | Axiom token scoped to that dataset, ingest-only | betula |
| Token delivery | AWS Secrets Manager `solidago-dev-axiom-alb-ingest-header`, value `Authorization Bearer <token>` — injected into the Lambda env (`AXIOM_API_TOKEN`) at deploy time | solidago |
| Reusable logic | the parser + Axiom shipper + handler in `alb_shipper/` (pure, unit-tested, no AWS creds to develop) | betula (this dir) |
| Deployment | S3 notification, IAM role, packaging, reading the `alb_access_logs_bucket` from solidago#107 | solidago (follow-up #108) |

**Coordination rule (same as ECS):** renaming the dataset, rotating to a
differently-scoped token, or changing the header format is a cross-repo change
with solidago.

**Boundary:** betula owns capture + archive (this dataset). The per-request
*source* signal — client IP, user-agent, request line, and `domain_name` (the
Host header, giving a per-site breakdown across lentago.dev /
icecreamtofightwith.com / the preview host) — is precisely what CloudWatch
metrics (and therefore the drosera Grafana pane) cannot carry.

## What lives here

```
alb-logs/
├── README.md                  # this file — the contract + field mapping
├── alb_shipper/
│   ├── __init__.py
│   ├── parser.py              # ALB access-log line → structured event (pure)
│   ├── axiom.py               # gzip + json_lines HTTP POST to Axiom ingest
│   └── handler.py             # S3 ObjectCreated entrypoint (thin, injectable)
└── tests/
    ├── sample_lines.py        # real-shape ALB lines (HTTP/HTTPS/edge cases)
    ├── test_parser.py
    ├── test_axiom.py
    └── test_handler.py
```

Standard-library only — no third-party dependencies in the shipper, so the
Lambda package stays small. `boto3` (present in the Lambda runtime) is imported
lazily inside the default S3 object reader and is **not** needed to run the
tests. The parser and shipper are pure and injectable, so the whole
parse-and-ship path is exercised in tests with fakes — no AWS creds, no live
Axiom endpoint.

## Ingest shape

Mirrors the Firewalla client's Fluent Bit HTTP output
(`../../../fluent-bit/fluent-bit.conf`): TLS POST to
`https://api.axiom.co/v1/datasets/<dataset>/ingest` with an
`Authorization: Bearer <token>` header and gzip compression. The one difference
is framing — this client sends newline-delimited JSON
(`Content-Type: application/x-ndjson`, `Content-Encoding: gzip`) so a large
gunzipped object streams batch-by-batch rather than buffering a JSON array.

## ALB → Axiom field mapping

Empty ALB fields (`-`) are dropped rather than emitted as the literal `"-"`, so
Axiom queries never special-case them. Emphasis is on the visitor-source
fields.

| ALB field | Axiom field(s) | Notes |
|---|---|---|
| `time` | `_time` | mapped to Axiom's canonical event timestamp (request time, not ingest time) |
| `client:port` | `client_ip`, `client_port` | **source signal** — split on the final `:` |
| `request` (quoted) | `request_method`, `request_url`, `request_protocol` | **source signal** — split `"METHOD URL PROTOCOL"`; raw kept as `request` |
| `user_agent` (quoted) | `user_agent` | **source signal** — escaped `\"` unescaped |
| `domain_name` (quoted) | `domain_name` | **source signal** — Host header → per-site breakdown |
| `ssl_protocol` | `ssl_protocol` | present on HTTPS lines |
| `ssl_cipher` | `ssl_cipher` | |
| `elb_status_code` | `elb_status_code` | int |
| `target_status_code` | `target_status_code` | int |
| `target:port` | `target_ip`, `target_port` | int port; absent when no target chosen |
| `type`, `elb`, `*_processing_time`, `received_bytes`, `sent_bytes`, `target_group_arn`, `trace_id`, `chosen_cert_arn`, `matched_rule_priority`, `request_creation_time`, `actions_executed`, `redirect_url`, `error_reason`, `target:port_list`, `target_status_code_list`, `classification`, `classification_reason`, `conn_trace_id` | same names | full documented field order preserved; numeric fields coerced; `*_list` and quoted fields handled; `conn_trace_id` present only on newer log lines |

Field order follows the AWS reference:
<https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-access-logs.html>

## Running the tests

```
cd clients/aws/alb-logs
python3 -m unittest discover -s tests
```

No network, no AWS, no Axiom token required.

## Handoff to solidago#108

betula stops at the reusable shipper + contract. solidago#108 packages
`alb_shipper/` as a Lambda, wires the S3 `ObjectCreated` notification on the
`alb_access_logs_bucket` (solidago#107), grants the IAM read role, and injects
the Axiom dataset/token via the Lambda environment from the Secrets Manager
secret named above. `lambda_handler` in `handler.py` is the entrypoint.
