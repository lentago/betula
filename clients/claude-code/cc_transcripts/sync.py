"""Incremental, at-least-once sync of one session's transcript files.

Per transcript file there are two byte cursors, one per sink. A cursor moves
only after its sink accepted the lines (claytonia ADR-0006's posture): a crash
between the write and the cursor save repeats lines, never loses them, and
``stitch`` drops repeats by each line's ``uuid``.

Only complete lines (ending in a newline) are read. The transcript on disk is
the spool: when the NAS is unmounted or Loki is unreachable, the cursor stays
put and the next sync picks up where it left off.
"""

import glob
import hashlib
import json
import os
import re
import time

from . import render
from .redact import redact

READ_CHUNK = 8 * 1024 * 1024  # cap one pass so a huge backlog can't stall a host
UUID_ISH = re.compile(r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|cse_[A-Za-z0-9]+|session_[A-Za-z0-9]+)$")
LINK_KEY = re.compile(r"(?i)session|parent|derived|fork|origin|source")


# --------------------------------------------------------------------------- helpers

def _atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.partial"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def session_files(transcript_path):
    """The main transcript plus its subagent transcripts, if any."""
    files = [transcript_path] if os.path.isfile(transcript_path) else []
    stem = transcript_path[:-len(".jsonl")] if transcript_path.endswith(".jsonl") else transcript_path
    files += sorted(glob.glob(os.path.join(stem, "subagents", "*.jsonl")))
    return files


def find_transcript(projects_dir, session_id):
    """Same lookup as claytonia's ``rt-claude transcript``."""
    hits = glob.glob(os.path.join(projects_dir, "*", f"{session_id}.jsonl"))
    return hits[0] if hits else None


def read_complete_lines(path, offset, limit=None):
    """``(lines, new_offset)``; ``lines`` are str without the newline.

    Reads about ``limit`` bytes, but never stops inside a line: a single line
    longer than ``limit`` (an inline image, a huge tool result) is read whole,
    so one big line can't wedge the cursor.
    """
    limit = limit or READ_CHUNK
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if offset > size:  # file was replaced or truncated: start over
            offset = 0
        fh.seek(offset)
        data = fh.read(limit)
        while data and b"\n" not in data:
            more = fh.read(limit)
            if not more:
                break
            data += more
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = data[: end + 1]
    lines = [l.decode("utf-8", "replace") for l in chunk.split(b"\n")[:-1]]
    return lines, offset + len(chunk)


def safe_id(value):
    """A session id as a single path component (no ``/``, no ``..``)."""
    out = "".join(c for c in str(value) if c.isalnum() or c in "-_")
    return out or "unknown"


def log_error(state_dir, msg, max_bytes=1_000_000):
    """Append to ``<state>/sync.log`` (rotated at 1 MB). Best effort."""
    try:
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "sync.log")
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")
    except OSError:
        pass


def project_label(cwd):
    base = os.path.basename(os.path.normpath(cwd)) if cwd else ""
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", base)
    return base or "adhoc"


def surface_from_env(env):
    """Which Claude Code surface ran the session, from documented env vars."""
    if str(env.get("CLAUDE_CODE_REMOTE", "")).lower() == "true":
        return "cloud"
    if env.get("CLAUDE_CODE_BRIDGE_SESSION_ID"):
        return "remote-control"
    ep = env.get("CLAUDE_CODE_ENTRYPOINT", "")
    if ep.startswith("sdk"):
        return "headless"
    if "desktop" in ep:
        return "desktop"
    if "vscode" in ep or "jetbrains" in ep:
        return "ide"
    return "cli"


def first_lines_links(path, n=3):
    """Ids named in a transcript's first lines, for cross-host stitching.

    The first-line record format is internal to Claude Code and changes between
    versions (docs: sessions), so this collects any id-shaped value under a key
    that looks like a link rather than trusting one key name.
    """
    found = set()

    def walk(obj, key=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key)
        elif isinstance(obj, str) and LINK_KEY.search(key or "") and UUID_ISH.match(obj):
            found.add(obj)

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(n):
                line = fh.readline()
                if not line:
                    break
                try:
                    walk(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return sorted(found)


def cwd_from_transcript(path, n=50):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(n):
                line = fh.readline()
                if not line:
                    break
                try:
                    cwd = json.loads(line).get("cwd")
                except (ValueError, AttributeError):
                    continue
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        pass
    return ""


# --------------------------------------------------------------------------- core

class Syncer:
    def __init__(self, cfg, pusher=None, dry_run=False, out=None, now=time.time):
        self.cfg = cfg
        self.pusher = pusher
        self.dry_run = dry_run
        self.out = out
        self.now = now

    # -- state ---------------------------------------------------------------
    def _state_path(self, path):
        key = hashlib.sha1(os.path.realpath(path).encode()).hexdigest()[:20]
        return os.path.join(self.cfg.state_dir, "cursors", key + ".json")

    def _rel(self, path):
        root = os.path.realpath(self.cfg.projects_dir)
        real = os.path.realpath(path)
        if real.startswith(root + os.sep):
            return os.path.relpath(real, root)
        return os.path.join("_outside", os.path.basename(os.path.dirname(real)), os.path.basename(real))

    def _say(self, msg):
        if self.out is not None:
            print(msg, file=self.out)

    # -- public --------------------------------------------------------------
    def seed(self, path):
        """Claim a file without shipping it: both cursors to its last full line."""
        with open(path, "rb") as fh:
            off = fh.read().rfind(b"\n") + 1
        st = {"path": path, "archive": off, "loki": off, "seeded": True}
        if not self.dry_run:
            _atomic_write_json(self._state_path(path), st)
        return st

    def sync_session(self, transcript_path, meta):
        """Sync one session (main + subagents). ``meta`` carries cwd, ids, surface."""
        results = []
        cwd = meta.get("cwd") or cwd_from_transcript(transcript_path)
        if self.cfg.excluded(cwd):
            self._say(f"skip (excluded cwd): {transcript_path}")
            return [{"path": transcript_path, "excluded": True}]
        for path in session_files(transcript_path):
            results.append(self._sync_file(path, transcript_path, dict(meta, cwd=cwd)))
        if self.cfg.archive_on and not self.dry_run and results:
            self._write_index(transcript_path, meta, cwd)
        return results

    # -- internals -----------------------------------------------------------
    def _sync_file(self, path, main_path, meta):
        spath = self._state_path(path)
        st = _load_json(spath, {"path": path, "archive": 0, "loki": 0})
        res = {"path": path, "archived": 0, "redactions": 0, "loki_lines": 0, "errors": []}

        if self.cfg.archive_on:
            try:
                n, red, new_off = self._archive(path, st.get("archive", 0))
                res["archived"], res["redactions"] = n, red
                st["archive"] = new_off
            except OSError as exc:
                res["errors"].append(f"archive: {exc}")

        if self.cfg.loki_on:
            try:
                n, new_off = self._loki(path, st.get("loki", 0), meta)
                res["loki_lines"] = n
                st["loki"] = new_off
            except Exception as exc:  # noqa: BLE001 - LokiError, OSError: fail open
                res["errors"].append(f"loki: {exc}")

        if not self.dry_run:
            st["updated"] = int(self.now())
            if res["errors"]:
                st["last_error"] = "; ".join(res["errors"])[:500]
                st["last_error_at"] = int(self.now())
                log_error(self.cfg.state_dir, f"{path}: {st['last_error']}")
            else:
                st.pop("last_error", None)
                st.pop("last_error_at", None)
            _atomic_write_json(spath, st)
        return res

    def _archive(self, path, offset):
        root = self.cfg.archive_dir
        # Never create the archive root: an unmounted NAS mountpoint must stay
        # empty, so the cursor waits until the share is back.
        if not os.path.isdir(root):
            raise OSError(f"archive root {root} is not present (NAS unmounted?)")
        lines, new_off = read_complete_lines(path, offset)
        if not lines:
            return 0, 0, offset
        dest = os.path.join(root, self.cfg.host, self._rel(path))
        out, total = [], 0
        for line in lines:
            red, n = redact(line)
            total += n
            out.append(red)
        if self.dry_run:
            self._say(f"archive: {len(out)} lines ({total} redactions) -> {dest}")
            return len(out), total, offset
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return len(out), total, new_off

    def _loki(self, path, offset, meta):
        lines, new_off = read_complete_lines(path, offset)
        if not lines:
            return 0, offset
        floor = (self.now() - self.cfg.loki_max_age_s) * 1e9
        labels = {
            "job": "claude_transcript",
            "cluster": self.cfg.cluster,
            "worker": self.cfg.host,
            "project": project_label(meta.get("cwd")),
            "session_id": meta.get("session_id") or os.path.basename(path)[:-6],
            "surface": meta.get("surface") or "cli",
        }
        events = []
        for line in lines:
            r = render.narration(line)
            if r is None or r[0] < floor:
                continue
            events.append((labels, r[0], r[1]))
        if self.dry_run:
            self._say(f"loki: {len(events)} lines {labels}")
            for _, _, l in events[:5]:
                self._say(f"  {l[:160]}")
            return len(events), offset
        if events:
            self.pusher.push(events)
        return len(events), new_off

    def _write_index(self, transcript_path, meta, cwd):
        sid = safe_id(meta.get("session_id") or os.path.basename(transcript_path)[:-6])
        path = os.path.join(self.cfg.archive_dir, self.cfg.host, "index", f"{sid}.json")
        prev = _load_json(path, {})
        links = sorted(set(prev.get("links", [])) | set(first_lines_links(transcript_path)) |
                       {v for v in (meta.get("remote_session_id"), meta.get("bridge_session_id")) if v})
        rec = {
            "schema": 1,
            "host": self.cfg.host,
            "session_id": sid,
            "surface": meta.get("surface") or prev.get("surface") or "cli",
            "entrypoint": meta.get("entrypoint") or prev.get("entrypoint", ""),
            "cwd": cwd,
            "transcript": self._rel(transcript_path),
            "links": [l for l in links if l != sid],
            "first_seen": prev.get("first_seen") or int(self.now()),
            "last_sync": int(self.now()),
        }
        if os.path.isdir(self.cfg.archive_dir):
            _atomic_write_json(path, rec)
