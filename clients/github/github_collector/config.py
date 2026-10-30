"""Collector config (JSON, in git) and secrets (env file, never in git).

The config file is a list of ``{owner, cluster}`` entries plus ``interval``
and ``lookback``. Adding a second org is one more entry — no code change.
The optional ``queue_watch`` list of ``{repo, cluster}`` entries names the
repos whose queued jobs are sampled each tick (drosera#292); watching another
self-hosted runner's repo is one more entry. Entries that must stay out of git
(a private repo's name) go in an optional host-local ``queue_watch.local.json``
next to the config file: a list of the same entries, appended to the committed
ones.

Secrets come from the environment, which systemd fills from the 0600
``/etc/default/betula-github`` EnvironmentFile.
"""

import json
import os
import re

DEFAULT_CONFIG_PATH = "/etc/betula-github/config.json"
DEFAULT_STATE_DIR = "/var/lib/betula-github"
# Host-local queue_watch entries, read from the config file's directory.
LOCAL_QUEUE_WATCH = "queue_watch.local.json"

# Same slug rule drosera's clients/loki_push.py applies to `cluster`.
_CLUSTER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
# A GitHub login: alphanumerics and single hyphens.
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
# owner/name, as GitHub spells a repository's full_name.
_REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
# Durations are systemd-compatible ("5m", "24h") so the deploy script can drop
# `interval` straight into the timer unit.
_DURATION_RE = re.compile(r"([1-9][0-9]*)([smhd])")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

SECRET_ENV_VARS = (
    "BETULA_GITHUB_TOKEN",
    "GRAFANA_CLOUD_LOGS_URL",
    "GRAFANA_CLOUD_LOGS_USER",
    "GRAFANA_CLOUD_LOGS_TOKEN",
)


class ConfigError(ValueError):
    """The config file or the environment is unusable."""


def parse_duration(value, name):
    match = _DURATION_RE.fullmatch(str(value))
    if not match:
        raise ConfigError(f"{name} must look like '5m' or '24h', got {value!r}")
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def _parse_queue_watch(entries):
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ConfigError("'queue_watch' must be a list of {repo, cluster}")
    seen = set()
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError(f"queue_watch entry {entry!r} is not an object")
        repo, cluster = entry.get("repo"), entry.get("cluster")
        if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
            raise ConfigError(f"queue_watch repo {repo!r} is not owner/name")
        if not isinstance(cluster, str) or not _CLUSTER_RE.fullmatch(cluster):
            raise ConfigError(f"cluster {cluster!r} must be a lowercase slug")
        if repo.lower() in seen:
            raise ConfigError(f"queue_watch repo {repo!r} is listed twice")
        seen.add(repo.lower())
        parsed.append({"repo": repo, "cluster": cluster})
    return parsed


class Config:
    def __init__(self, owners, interval_s, lookback_s, state_dir, queue_watch=()):
        self.owners = owners  # list of {"owner": ..., "cluster": ...}
        self.interval_s = interval_s
        self.lookback_s = lookback_s
        self.state_dir = state_dir
        self.queue_watch = list(queue_watch)  # list of {"repo": ..., "cluster": ...}

    @classmethod
    def from_dict(cls, raw, env=None):
        env = os.environ if env is None else env
        if not isinstance(raw, dict):
            raise ConfigError("config must be a JSON object")
        owners = raw.get("owners")
        if not isinstance(owners, list) or not owners:
            raise ConfigError("config needs a non-empty 'owners' list of {owner, cluster}")
        seen = set()
        parsed = []
        for entry in owners:
            if not isinstance(entry, dict):
                raise ConfigError(f"owners entry {entry!r} is not an object")
            owner, cluster = entry.get("owner"), entry.get("cluster")
            if not isinstance(owner, str) or not _OWNER_RE.fullmatch(owner):
                raise ConfigError(f"owner {owner!r} is not a GitHub login")
            if not isinstance(cluster, str) or not _CLUSTER_RE.fullmatch(cluster):
                raise ConfigError(f"cluster {cluster!r} must be a lowercase slug")
            if owner.lower() in seen:
                raise ConfigError(f"owner {owner!r} is listed twice")
            seen.add(owner.lower())
            parsed.append({"owner": owner, "cluster": cluster})
        interval_s = parse_duration(raw.get("interval", "5m"), "interval")
        lookback_s = parse_duration(raw.get("lookback", "24h"), "lookback")
        if lookback_s <= interval_s:
            raise ConfigError("lookback must be longer than interval")
        queue_watch = _parse_queue_watch(raw.get("queue_watch"))
        # systemd's StateDirectory= exports STATE_DIRECTORY; honour it first.
        state_dir = env.get("STATE_DIRECTORY") or raw.get("state_dir") or DEFAULT_STATE_DIR
        return cls(parsed, interval_s, lookback_s, state_dir, queue_watch)

    @classmethod
    def load(cls, path, env=None):
        raw = _read_json(path)
        local_path = os.path.join(os.path.dirname(path), LOCAL_QUEUE_WATCH)
        if isinstance(raw, dict) and os.path.exists(local_path):
            local = _read_json(local_path)
            if not isinstance(local, list):
                raise ConfigError(f"{local_path} must be a list of {{repo, cluster}}")
            # Validated with the committed entries, so a repo in both is a duplicate.
            raw = {**raw, "queue_watch": list(raw.get("queue_watch") or []) + local}
        return cls.from_dict(raw, env=env)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc


def load_secrets(env=None):
    """Return the four secret values, or raise ConfigError naming what's missing."""
    env = os.environ if env is None else env
    missing = [name for name in SECRET_ENV_VARS if not env.get(name)]
    if missing:
        raise ConfigError("missing env var(s): " + ", ".join(missing))
    return {name: env[name] for name in SECRET_ENV_VARS}
