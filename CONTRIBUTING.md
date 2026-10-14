# Contributing

This repo ships Firewalla Zeek IDS (intrusion detection system) logs and network telemetry off-device via Fluent Bit (Grafana Cloud Loki for the Firewalla; the AWS client ships to [Axiom](https://axiom.co/)). It runs on our on-prem estate (the machines in our own building) and is published as a working exhibit — so stability and clarity matter.

## Reporting Issues

Issues are welcome! If you've tried running this pipeline and hit a problem, please open an issue with:

- What you expected to happen
- What actually happened
- Your Firewalla model and firmware version
- Any relevant log output (redact MACs/IPs)

## Suggesting Features

Feature ideas are appreciated. Open an issue describing the use case — *why* you want it matters more than *how* you'd build it.

## Pull Requests

This repo reflects my own infrastructure, so I'm selective about PRs. Before investing time in a contribution:

1. **Open an issue first** to discuss the change
2. **Keep scope small** — one fix or feature per PR
3. **Follow existing conventions** — `set -euo pipefail`, inline comments, idempotent operations
4. **Don't add dependencies** beyond bash, Docker, curl, and redis-cli

I'll review PRs personally and may adapt contributions to fit the pipeline's architecture.

## Code of Conduct

Be kind, be constructive, be respectful. Life's too short for anything else.
