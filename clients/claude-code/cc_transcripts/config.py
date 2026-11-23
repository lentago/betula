"""Configuration: an env file plus the process environment (environment wins).

Secrets (``GRAFANA_CLOUD_LOGS_*``) live only in the env file, mode 0600, never
in git. Every key is optional: a sink with no config is simply off.
"""

import fnmatch
import os
import re
import socket
from dataclasses import dataclass, field

ENV_FILES = (
    "/etc/betula/claude-code.env",
    os.path.expanduser("~/.config/betula/claude-code.env"),
)


def read_env_file(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                out[key] = value
    except OSError:
        pass
    return out


@dataclass
class Config:
    host: str
    state_dir: str
    projects_dir: str
    archive_dir: str = ""
    loki_url: str = ""
    loki_user: str = ""
    loki_token: str = ""
    cluster: str = "lentago-lab"
    exclude: list = field(default_factory=list)
    loki_max_age_s: int = 3600
    sinks: tuple = ("archive", "loki")

    @property
    def archive_on(self):
        return "archive" in self.sinks and bool(self.archive_dir)

    @property
    def loki_on(self):
        return "loki" in self.sinks and bool(self.loki_url and self.loki_user and self.loki_token)

    def excluded(self, cwd):
        """True when ``cwd`` (or any parent) matches an exclude glob."""
        if not cwd:
            return False
        cwd = os.path.normpath(cwd)
        for pattern in self.exclude:
            pattern = os.path.normpath(os.path.expanduser(pattern))
            if fnmatch.fnmatch(cwd, pattern) or cwd == pattern or cwd.startswith(pattern + os.sep):
                return True
        return False

    def describe(self):
        """A secret-free summary for ``check`` and dry runs."""
        return {
            "host": self.host,
            "state_dir": self.state_dir,
            "projects_dir": self.projects_dir,
            "archive": self.archive_dir if self.archive_on else "off",
            "loki": self.loki_url if self.loki_on else "off",
            "cluster": self.cluster,
            "exclude": self.exclude,
            "loki_max_age_s": self.loki_max_age_s,
        }


def _host():
    name = socket.gethostname().split(".")[0]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "unknown"


def load(environ=None, env_files=ENV_FILES):
    environ = dict(os.environ if environ is None else environ)
    merged = {}
    extra = environ.get("BETULA_CC_ENV")
    for path in list(env_files) + ([extra] if extra else []):
        merged.update(read_env_file(path))
    merged.update({k: v for k, v in environ.items() if k.startswith(("BETULA_CC_", "GRAFANA_CLOUD_LOGS_"))})

    config_dir = environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    sinks = tuple(s.strip() for s in merged.get("BETULA_CC_SINKS", "archive,loki").split(",") if s.strip())
    exclude = [p for p in merged.get("BETULA_CC_EXCLUDE", "").split(":") if p.strip()]
    try:
        max_age = int(merged.get("BETULA_CC_LOKI_MAX_AGE_S", "3600"))
    except ValueError:
        max_age = 3600
    return Config(
        host=merged.get("BETULA_CC_HOST") or _host(),
        state_dir=os.path.expanduser(merged.get("BETULA_CC_STATE_DIR", "~/.local/state/betula-cc")),
        projects_dir=os.path.join(config_dir, "projects"),
        archive_dir=os.path.expanduser(merged.get("BETULA_CC_ARCHIVE_DIR", "")),
        loki_url=merged.get("GRAFANA_CLOUD_LOGS_URL", ""),
        loki_user=merged.get("GRAFANA_CLOUD_LOGS_USER", ""),
        loki_token=merged.get("GRAFANA_CLOUD_LOGS_TOKEN", ""),
        cluster=merged.get("BETULA_CC_CLUSTER", "lentago-lab"),
        exclude=exclude,
        loki_max_age_s=max_age,
        sinks=sinks,
    )
