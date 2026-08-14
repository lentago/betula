# Architecture Decision Records

> **Provenance note.** These records were reconstructed on 2026-08-13 from the
> repository's commit history, GitHub issues and pull requests, `CLAUDE.md`, and
> fleet session archives. They were not written at decision time. Dates shown in
> each **Status** line are the original decision dates (the date the relevant
> commit or PR merged, or the date the incident that forced the decision
> occurred); "reconstructed 2026-08-13" is the date the record was written.
> Where the evidence is ambiguous or cross-references a repo that cannot be
> verified from here, this is noted inline.

## Index

| # | Title | Status | Date |
|---|-------|--------|------|
| [0001](0001-on-appliance-capture.md) | On-appliance capture via Dockerized Fluent Bit | Accepted | circa 2026-03-13 |
| [0002](0002-pull-based-gitops.md) | Pull-based GitOps to a production appliance | Accepted | 2026-05-28 |
| [0003](0003-direct-loki-push.md) | Direct HTTPS push to Grafana Cloud Loki; LAN relay dropped | Accepted | 2026-06-10 |
| [0004](0004-loki-sole-destination.md) | Grafana Cloud Loki as sole destination; Axiom path deleted | Accepted | 2026-07-09 |
| [0005](0005-retry-limit-false.md) | `Retry_Limit False` on every Fluent Bit output | Accepted | 2026-05-28 |
| [0006](0006-loki-label-contract.md) | Low-cardinality Loki stream-label contract | Accepted | circa 2026-06-10 |
| [0007](0007-visitor-ip-truncation.md) | Visitor-IP truncation at parse time; hashing rejected | Accepted | 2026-07-25 |
