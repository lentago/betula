"""Put one conversation back together across hosts.

A thread that moves between hosts (cloud, a workstation, Remote Control on
another box) leaves one transcript per host. Each host's sync writes an index
record, ``<archive>/<host>/index/<session_id>.json``, naming the session id and
every other id it saw: Remote Control's bridge id, the cloud session id, and any
id-shaped value in the transcript's first lines. Records that share an id are
the same conversation. ``stitch`` finds that group and, with ``merge``, emits
its lines in time order, each tagged with the host it came from, with repeats
(at-least-once delivery) dropped by ``uuid``.
"""

import glob
import json
import os


def load_index(archive_dir):
    recs = []
    for path in glob.glob(os.path.join(archive_dir, "*", "index", "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        rec["_file"] = os.path.join(archive_dir, rec.get("host", ""), rec.get("transcript", ""))
        recs.append(rec)
    return recs


def group(recs, start_id):
    """Every record connected to ``start_id`` through a shared id."""
    def ids(r):
        return {r.get("session_id")} | set(r.get("links", []))

    wanted, members, changed = {start_id}, [], True
    while changed:
        changed = False
        for r in recs:
            if r in members:
                continue
            if ids(r) & wanted:
                members.append(r)
                wanted |= ids(r)
                changed = True
    return sorted(members, key=lambda r: r.get("first_seen", 0))


def merge(members):
    seen, rows = set(), []
    for r in members:
        files = [r["_file"]]
        stem = r["_file"][:-6] if r["_file"].endswith(".jsonl") else r["_file"]
        files += sorted(glob.glob(os.path.join(stem, "subagents", "*.jsonl")))
        for path in files:
            try:
                fh = open(path, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    key = obj.get("uuid") or line
                    if key in seen:
                        continue
                    seen.add(key)
                    obj["_betula"] = {"host": r.get("host"), "surface": r.get("surface"),
                                      "file": os.path.basename(path)}
                    rows.append((obj.get("timestamp") or "", obj))
    rows.sort(key=lambda t: t[0])
    return [o for _, o in rows]
