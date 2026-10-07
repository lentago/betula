"""CLI: ``python3 -m github_collector [--config PATH] [--check-config] [--dry-run]``.

The systemd timer runs one tick per activation; exit 1 means the tick was
skipped on a GitHub or Loki failure (a rate-limit backoff exits 0).
"""

import argparse
import logging
import sys
import time

from . import collector
from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_secrets
from .github import GitHubClient
from .loki import LokiPusher
from .state import State


def main(argv=None):
    parser = argparse.ArgumentParser(prog="github_collector", description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--check-config", action="store_true",
                        help="validate the config file and exit (no secrets, no network)")
    parser.add_argument("--dry-run", action="store_true",
                        help="poll GitHub and print events; don't push or advance the seen-set")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[betula-github] %(levelname)s %(message)s")
    log = logging.getLogger("betula-github")

    try:
        cfg = Config.load(args.config)
        if args.check_config:
            owners = ", ".join(f"{o['owner']}→{o['cluster']}" for o in cfg.owners)
            log.info("config OK: owners [%s], interval %ss, lookback %ss",
                     owners, cfg.interval_s, cfg.lookback_s)
            return 0
        secrets = load_secrets()
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    gh = GitHubClient(secrets["BETULA_GITHUB_TOKEN"])
    loki = LokiPusher(secrets["GRAFANA_CLOUD_LOGS_URL"], secrets["GRAFANA_CLOUD_LOGS_USER"],
                      secrets["GRAFANA_CLOUD_LOGS_TOKEN"])
    state = State(cfg.state_dir)
    outcome = collector.tick(cfg, gh, loki, state, int(time.time()), dry_run=args.dry_run)
    return 1 if outcome == collector.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
