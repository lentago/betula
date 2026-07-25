# aws client — CloudWatch Logs (subscription filter → Axiom)

The third emitter of the betula `aws` client (see `../README.md`). Where the ECS
emitter is a FireLens *stream* and the ALB emitter is an *S3 batch* shipper,
this one closes the gap for **AWS Lambda application logs** (lentago/solidago#144):
a Lambda has no sidecar, so its `console.log` output lands only in CloudWatch
Logs (`/aws/lambda/<name>`) and stops there. The highest-value case is the
structured `ask_query` events the Pond View Lane Ask handler emits
(lentago/site-pondviewlane-com#23) — stranded in CloudWatch they are unjoinable
with the ALB request data already in Axiom and invisible to the drosera panels
(lentago/drosera#161).

```
Ask Lambda  ──(console.log)──►  CloudWatch Logs  ──subscription filter──►  forwarder Lambda
  /aws/lambda/ask-pondview                                                       │
                                        base64+gzip decode → parse → gzip ndjson POST
                                                                                 │
                                                                                 ▼
                                                              Axiom dataset  cjp-solidago-ask
```

## Why a subscription filter → forwarder Lambda (not shipping from the handler)

The design decision is **CloudWatch Logs subscription filter → a forwarder
Lambda → Axiom** — issue #144's option 1. betula owns this collector; solidago
wires the subscription filter and deploys the function. It:

- **preserves the fleet boundary** — betula owns capture and archive, solidago
  deploys; a subscription filter is a capture concern;
- **mirrors the ALB collector exactly** — solidago already deploys that as a
  Lambda vendoring betula code via `betula_ref`, so there is a proven pattern;
- **keeps the ingest token out of the user-facing request path** — the token
  lives only in the forwarder Lambda's environment, never in the Ask handler.

Option 2 (shipping straight from the Ask handler) was rejected: it would put an
Axiom ingest token in the site's Lambda environment, add latency or
fire-and-forget complexity to a request a human is waiting on, and couple the
site's code directly to the archive plane — against the betula/drosera boundary.

## Contract

| Item | Value | Owner |
|---|---|---|
| Dataset | `cjp-solidago-ask` (dedicated — see *Retention* below) | betula (this file names it; created in Axiom) |
| Ingest token | Axiom token scoped to that dataset, ingest-only | betula |
| Token delivery | AWS Secrets Manager secret, value a **bare** token — injected into the forwarder Lambda env (`AXIOM_API_TOKEN`) at deploy time | solidago |
| Reusable logic | the payload decoder + Axiom shipper + handler in `cloudwatch_shipper/` (pure, unit-tested, no AWS creds to develop) | betula (this dir) |
| Deployment | subscription filter on the Ask Lambda's log group, forwarder Lambda packaging, IAM | solidago (follow-up — see below) |

> **Bare token, not a header string.** `cloudwatch_shipper/axiom.py` reads a
> **bare** token from the env and builds the `Authorization: Bearer <token>`
> header itself — same as the ALB shipper. Do **not** deliver an
> `Authorization Bearer <token>` header-string here: that is the FireLens/ECS
> secret convention (used for the `cjp-solidago-ecs` FireLens output), and
> feeding it to this client would produce a broken double-`Bearer` header. This
> exact distinction is documented in solidago's `modules/secrets/main.tf`.

**Coordination rule (same as ECS/ALB):** renaming the dataset, rotating to a
differently-scoped token, or changing the header format is a cross-repo change
with solidago.

**Boundary:** betula owns capture + archive (this dataset). drosera owns the
live pane (lentago/drosera#161).

## Retention — must be chosen deliberately

The `ask_query` events carry **resident-authored question text**, so this
dataset inherits the same privacy concern as lentago/betula#90. The 14-day
CloudWatch default was set for a Lambda's operational logs, not for an analytics
event stream of resident questions. site-pondviewlane-com#23's privacy analysis
recommends a **deliberate 30–90 day** retention.

**Recommendation: a dedicated dataset, `cjp-solidago-ask`** — not reuse of
`cjp-solidago-ecs` or `cjp-solidago-alb`. A dedicated dataset makes an
independent retention policy possible: the Ask question stream can be held to
its own 30–90 day window without dragging the ALB/ECS operational data to the
same (shorter or longer) policy, and a future purge or export scoped to resident
question text touches exactly one dataset. Retention itself is set on the Axiom
side (betula, per #74's `core/axiom` work) and must be an explicit choice, not
the inherited CloudWatch default.

## What lives here

```
cloudwatch-logs/
├── README.md                     # this file — the contract + record mapping
├── cloudwatch_shipper/
│   ├── __init__.py
│   ├── parser.py                 # awslogs.data (base64+gzip json) → records (pure)
│   ├── axiom.py                  # gzip + ndjson HTTP POST to Axiom ingest
│   └── handler.py                # subscription-filter Lambda entrypoint (thin, pure)
└── tests/
    ├── sample_events.py          # real-shape payloads (ask_query / platform / control)
    ├── test_parser.py
    ├── test_axiom.py
    └── test_handler.py
```

Standard-library only — no third-party dependencies, so the Lambda package stays
small. Unlike the ALB shipper there is **no boto3 at all**: a subscription filter
delivers the log data *inline* in the invocation event, so the whole
decode-parse-ship path is pure and the tests exercise it end-to-end with a fake
Axiom client — no AWS creds, no live Axiom endpoint.

## CloudWatch Logs payload → Axiom record mapping

A subscription filter invokes the Lambda with a single `awslogs.data` field:
**base64-encoded, gzip-compressed JSON**. Decoded it is an object with
`messageType`, `logGroup`, `logStream`, and a `logEvents` array of
`{id, timestamp, message}`. See
<https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/SubscriptionFilters.html>.

One Axiom record is produced per log event:

| CloudWatch field | Axiom field(s) | Notes |
|---|---|---|
| `timestamp` (epoch ms) | `_time` | RFC3339 UTC — the event's own time, not ingest time |
| `logGroup` | `logGroup` | preserved so several source log groups share one dataset |
| `logStream` | `logStream` | preserved for the same reason |
| `id` | `cw_id` | the CloudWatch log-event id |
| `message` **(JSON object)** | *its fields, merged* | the structured `ask_query` line — parsed and spread into the record |
| `message` **(anything else)** | `message` | a plain string, a JSON scalar/array, or Lambda's `START`/`END`/`REPORT` platform lines — kept verbatim |

**Precedence:** the CloudWatch source fields (`_time`, `logGroup`, `logStream`,
`cw_id`) are applied *over* a parsed message, so a structured line carrying its
own `_time` or `logGroup` cannot shadow (or spoof) the delivery's real timestamp
or source labels.

**Robustness:** an unparseable message line never fails the batch — it falls back
to a raw `message`. A `CONTROL_MESSAGE` payload (CloudWatch's subscription
health-check heartbeat) yields no records. A malformed `awslogs.data` blob raises,
so the Lambda surfaces the failure and CloudWatch retries the delivery rather than
shipping a half-decoded batch.

## Running the tests

```
cd clients/aws/cloudwatch-logs
python3 -m unittest discover -s tests
```

No network, no AWS, no Axiom token required.

## Handoff to solidago (follow-up, not filed here)

betula stops at the reusable collector + contract. The solidago side needs:

1. **Subscription filter** on the Ask Lambda's log group
   (`aws_cloudwatch_log_group` in `modules/ask-lambda`, `/aws/lambda/<name>`):
   an `aws_cloudwatch_log_subscription_filter` with an empty `filter_pattern`
   (forward every line — platform lines included; the collector sorts them out),
   `destination_arn` = the forwarder Lambda.
2. **Forwarder Lambda** vendoring this collector (`cloudwatch_shipper/`) exactly
   as `modules/alb-logs` vendors `alb_shipper/` via `betula_ref`. Entrypoint:
   `cloudwatch_shipper.handler.lambda_handler`. Env: `AXIOM_DATASET=cjp-solidago-ask`
   and `AXIOM_API_TOKEN` = the **bare** token from Secrets Manager.
3. **IAM**: the subscription filter needs `lambda:InvokeFunction` on the
   forwarder (via a `logs.<region>.amazonaws.com`-principal `aws_lambda_permission`),
   and the forwarder's execution role needs the basic CloudWatch Logs write
   policy for its own log group. No S3 or extra data-plane access — the log data
   arrives inline in the event.
4. **Retention** chosen deliberately (30–90 days) on the Axiom dataset, per the
   *Retention* section above — not the inherited 14-day CloudWatch default.

Because `modules/ask-lambda` is reusable, the subscription filter should be wired
in that module (or a thin wrapper) so a **second** consumer of the Ask module
gets the same forwarding without per-site special-casing — issue #144's
acceptance criterion.
