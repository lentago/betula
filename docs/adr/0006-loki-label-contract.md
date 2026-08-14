# ADR-0006: Low-cardinality Loki stream-label contract

**Status:** Accepted (circa 2026-06-10; reconstructed 2026-08-13)

## Context

Grafana Cloud Loki indexes and bills on stream labels. High-cardinality labels
(per-IP, per-domain, per-query) would explode the stream count and increase
cost non-linearly. More concretely: the `lentago/drosera` dashboards and alert
rules query stream labels by exact name — a label renamed or added on the
producer side produces empty panels on the consumer side, with no error
surfaced. There is no schema-enforcing layer between `fluent-bit.conf` and
the Grafana queries; mismatches are invisible until someone notices empty panels.

This sensitivity was made concrete by a producer-ahead-of-consumer lag incident
(details in the `lentago/drosera` issue tracker; cannot be verified from this
repo) in which the Firewalla started shipping a new label value before drosera's
dashboards expected it, and panels went empty.

## Decision

The `loki` output carries exactly three stream labels on every event:

| Label | Value(s) | How set |
|-------|----------|---------|
| `job` | `firewalla` | Static in `Labels` directive |
| `cluster` | `lentago-lab` | Static in `Labels` directive |
| `log_source` | `zeek_dns`, `zeek_conn`, `zeek_ssl`, `firewalla_acl`, etc. | Promoted from the record field via `Label_keys $log_source`; set per-stream by `[FILTER] modify` blocks |

No high-cardinality Zeek field (source IP, destination IP, queried domain,
`query`, `id.orig_h`) is ever promoted to a stream label. Those fields stay
in the log body and are queried with LogQL filter expressions.

Adding a new `log_source` value (e.g., `zeek_http`) requires a coordinated
update to drosera's dashboards. This coordination cost is accepted explicitly;
it is documented in CLAUDE.md and the README §  Loki output contract, and is
enforced by convention rather than tooling.

## Alternatives

**Ad-hoc label additions without a stated contract** (the de facto state before
this was written down): the producer-ahead-of-consumer incident demonstrated
that this produces silent dashboard failures. Writing the contract down on both
sides does not prevent the failure mode, but makes it discoverable before a
change lands rather than after panels go empty.

**A cross-repo CI schema check** *(retrospective — not considered at the time)*:
better in principle — a required check that validates the labels declared in
`fluent-bit.conf` match those expected in drosera's dashboard JSON before a PR
merges. Hard to implement as a required check across two repos with independent
merge timelines (a drosera dashboard update and a betula config change cannot
atomically satisfy a cross-repo required check). The issue brief notes this was
partially realized later via a `check-loki-labels.sh` script and ingest-absence
alerts in the drosera repo; those details cannot be verified from this
repository.

## Consequences

- Loki stream counts are bounded and predictable; billing surprises from a
  misconfigured label are avoided.
- Any new log source added to `fluent-bit.conf` that sets a new `log_source`
  value is a breaking change for drosera until drosera is updated to expect it.
- The schema is enforced by documentation and review, not tooling. A PR
  that adds a new label value should include a note that drosera will need a
  corresponding update.
