# betula collector clients

Each directory here is one **collector client**, a source that betula
captures from, per the core/client roadmap ([#74](https://github.com/lentago/betula/issues/74)).
Clients are added alongside each other and never edit one another, so adding
one doesn't touch the Firewalla pipeline at the repo root or any other client.

| Client | Captures | Ships to | Runs where |
|---|---|---|---|
| Firewalla (repo root: `fluent-bit/`, `scripts/`) | Zeek DNS/conn/SSL, ACL blocks, device inventory | Grafana Cloud Loki | Fluent Bit container + cron on the Firewalla |
| [`aws/`](aws/README.md) | solidago ECS, ALB access logs, Lambda app logs | Axiom | inside solidago's AWS infrastructure |
| [`github/`](github/README.md) | GitHub Actions run and job history for each configured owner | Grafana Cloud Loki | systemd timer on LXC 105 (`grafana-stack`) |

Each client's README gives its feed contract, credentials, deployment, and how
to check that it's working. Python clients are stdlib-only, and each one's
`tests/` directory runs with `python3 -m unittest discover -s tests` from its parent
directory.
