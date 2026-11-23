"""The Claude Code hook entry point: record, detach, exit 0.

The hook never ships anything itself. It reads the hook payload, writes a
small pending record, starts a detached ``sync`` for that session and returns
at once, so a slow NAS or an unreachable Loki can never hold a session up.
Every failure exits 0: shipping transcripts must never break a session.

Coalescing: a per-session lock means one sync runs at a time. A hook that fires
while one is running leaves a ``dirty`` mark, and the running sync goes round
again before it exits, so the last turn is never left behind.
"""

import json
import os
import subprocess
import sys

from . import config as config_mod
from .sync import surface_from_env

ENV_KEYS = (
    "CLAUDE_CODE_REMOTE",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
)


def pending_record(payload, env):
    return {
        "session_id": payload.get("session_id", ""),
        "transcript_path": payload.get("transcript_path", ""),
        "cwd": payload.get("cwd", ""),
        "event": payload.get("hook_event_name", ""),
        "surface": surface_from_env(env),
        "entrypoint": env.get("CLAUDE_CODE_ENTRYPOINT", ""),
        "remote_session_id": env.get("CLAUDE_CODE_REMOTE_SESSION_ID", ""),
        "bridge_session_id": env.get("CLAUDE_CODE_BRIDGE_SESSION_ID", ""),
    }


def pending_path(cfg, session_id):
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return os.path.join(cfg.state_dir, "pending", safe + ".json")


def merge_pending(path, rec):
    """Keep ids seen on earlier events (a resume may run under another surface)."""
    try:
        with open(path, encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = {}
    out = dict(prev)
    for k, v in rec.items():
        if v:
            out[k] = v
    return out


def run(stdin=sys.stdin, environ=os.environ, spawn=True):
    try:
        payload = json.loads(stdin.read() or "{}")
        sid = payload.get("session_id")
        if not sid:
            return 0
        cfg = config_mod.load(environ)
        if not (cfg.archive_on or cfg.loki_on):
            return 0
        rec = pending_record(payload, environ)
        path = pending_path(cfg, sid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = merge_pending(path, rec)
        tmp = f"{path}.{os.getpid()}.partial"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)
        if spawn:
            pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = dict(environ)
            env["PYTHONPATH"] = pkg_parent
            subprocess.Popen(
                [sys.executable, "-m", "cc_transcripts", "sync", "--session", sid],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True, env=env, cwd="/",
            )
    except Exception:  # noqa: BLE001 - fail open, always
        return 0
    return 0
