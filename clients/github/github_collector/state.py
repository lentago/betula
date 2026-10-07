"""Persisted collector state under the state dir (/var/lib/betula-github/).

Two files, written atomically (temp file + rename):

- ``seen.json``  — ``{"<run_id>:<run_attempt>": <run created_at epoch>}``.
  Only ever advanced after Loki accepted the push.
- ``cache.json`` — the per-owner repo list (name + default branch) with its fetch time, and the
  rate-limit ``backoff_until`` epoch. Safe to delete; it's rebuilt.
"""

import json
import os
import tempfile


def seen_key(run_id, attempt):
    return f"{run_id}:{attempt}"


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return default
    return data if isinstance(data, dict) else default


def _write(path, data):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class State:
    def __init__(self, state_dir):
        os.makedirs(state_dir, exist_ok=True)
        self._seen_path = os.path.join(state_dir, "seen.json")
        self._cache_path = os.path.join(state_dir, "cache.json")
        self.seen = _read(self._seen_path, {})
        cache = _read(self._cache_path, {})
        self.repos = cache.get("repos", {})  # owner -> {"fetched_at": epoch, "repos": [...]}
        self.backoff_until = cache.get("backoff_until", 0)

    def save_seen(self):
        _write(self._seen_path, self.seen)

    def save_cache(self):
        _write(self._cache_path, {"repos": self.repos, "backoff_until": self.backoff_until})

    def prune_seen(self, cutoff):
        """Forget keys for runs created before ``cutoff`` — the window no longer lists them."""
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
