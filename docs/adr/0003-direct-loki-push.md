# ADR-0003: Direct HTTPS push to Grafana Cloud Loki; LAN relay dropped

**Status:** Accepted (2026-06-10; reconstructed 2026-08-13)

## Context

Prior to PR #51, the Fluent Bit `loki` output shipped to a LAN Alloy relay,
which forwarded events to Grafana Cloud Loki. The relay was a separate
always-on host on the home network.

PR #51 (2026-06-10) found the relay had silently stopped delivering for 36h or
more — all Grafana Cloud Loki panels fed by the Firewalla were empty. The Axiom
output was unaffected throughout. No error was surfaced to the operator; the
container appeared healthy and Axiom kept flowing.

The relay was a central point of failure invisible from the appliance side: if
the relay host was down, the Firewalla's Loki output failed silently, with no
retry path back to the cloud endpoint.

## Decision

Repoint the `loki` output directly to Grafana Cloud Loki over HTTPS (port 443,
TLS, HTTP Basic Auth with `GRAFANA_CLOUD_LOGS_{USER,TOKEN}`). The Firewalla
owns its own log shipping end-to-end, mirroring the per-host Alloy push model
used by other hosts in the fleet. No relay in the data path.

The `cluster` label (previously injected by the relay's `external_labels`)
was added to the `Labels` directive in `fluent-bit.conf` so existing Grafana
queries kept working without changes to the consumer side.

## Alternatives

**LAN relay path** (prior state): adds a central point of failure; relay
outage silently empties panels; the Firewalla already has a WAN path, so the
relay adds no throughput or latency benefit that justifies the fragility.

**Self-hosted Loki instance on the home network** *(retrospective — not
considered at the time)*: lateral. A local Loki avoids cloud dependency and
keeps log data on-premise, which may be preferable for some use cases. However,
it replaces one in-LAN hop (relay) with another (self-hosted Loki), preserves
the home-network SPOF, and adds self-hosting maintenance burden. For a lab
already running Grafana Cloud for other fleet hosts, a local Loki is additional
complexity without a clear benefit over direct cloud push.

## Consequences

- Loki shipping now depends on WAN connectivity; the Firewalla already needs
  WAN for its primary function, so this is not a regression.
- Three `GRAFANA_CLOUD_LOGS_*` variables are required in `log_shipping.env`
  (subsequently hardened to fail-fast rather than warn-and-continue in PR #82).
- One manual container recreate was required at activation time (`start_log_shipping.sh`
  recreates the container with the new `-e` env vars; the GitOps poller's
  `docker restart` would not have picked up the new env).
- The relay host (Alloy LXC) is still used by other fleet hosts in the
  `lentago/drosera` stack; this change only removes it from the Firewalla's
  data path.
