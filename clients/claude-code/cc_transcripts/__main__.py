"""CLI: ``python3 -m cc_transcripts <command>``.

  hook                      Claude Code hook entry (reads the payload on stdin). Always exits 0.
  sync --session SID        Sync one session now (what the hook starts, detached).
  sync --all [--days N]     Sync every transcript touched in the last N days (timer backstop).
  seed                      Claim every existing transcript without shipping it (no backfill).
  check                     Print the resolved config, secret-free, and exit.
  status                    List transcript files whose last sync failed (exit 1 if any).
  stitch ID [--merge]       List (or merge into one JSONL) every archived piece of a conversation.

``--dry-run`` on sync and seed prints what would happen and changes nothing.
"""

import argparse
import glob
import json
import os
import sys
import time

from . import config as config_mod
from . import hook as hook_mod
from . import stitch as stitch_mod
from .loki import LokiPusher
from .sync import Syncer, find_transcript, safe_id

try:
    import fcntl
except ImportError:  # Windows: no flock; sync still works, just uncoalesced
    fcntl = None


def _lock(cfg, name):
    if fcntl is None:
        return object()
    os.makedirs(os.path.join(cfg.state_dir, "locks"), exist_ok=True)
    fh = open(os.path.join(cfg.state_dir, "locks", name + ".lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _syncer(cfg, dry_run):
    pusher = None
    if cfg.loki_on and not dry_run:
        pusher = LokiPusher(cfg.loki_url, cfg.loki_user, cfg.loki_token)
    return Syncer(cfg, pusher=pusher, dry_run=dry_run, out=sys.stdout if dry_run else None)


def _meta_for(cfg, sid):
    try:
        with open(hook_mod.pending_path(cfg, sid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"session_id": sid}


def cmd_sync_session(cfg, sid, dry_run):
    sid = safe_id(sid)
    dirty = os.path.join(cfg.state_dir, "pending", sid + ".dirty")
    lock = None if dry_run else _lock(cfg, sid)
    if not dry_run and lock is None:
        # Another sync holds this session: ask it to go round once more.
        os.makedirs(os.path.dirname(dirty), exist_ok=True)
        open(dirty, "w").close()
        return 0
    syncer = _syncer(cfg, dry_run)
    for _ in range(5):  # bounded: a runaway hook can't keep one sync alive forever
        meta = _meta_for(cfg, sid)
        path = meta.get("transcript_path") or find_transcript(cfg.projects_dir, sid)
        if not path:
            return 0
        res = syncer.sync_session(path, dict(meta, session_id=sid))
        if dry_run:
            print(json.dumps(res, indent=1))
            return 0
        if not os.path.exists(dirty):
            break
        os.remove(dirty)
    return 0


def cmd_sync_all(cfg, days, dry_run):
    lock = None if dry_run else _lock(cfg, "_all")
    if not dry_run and lock is None:
        return 0
    syncer = _syncer(cfg, dry_run)
    cutoff = time.time() - days * 86400
    for path in sorted(glob.glob(os.path.join(cfg.projects_dir, "*", "*.jsonl"))):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        sid = safe_id(os.path.basename(path)[:-6])
        slock = None if dry_run else _lock(cfg, sid)
        if not dry_run and slock is None:
            continue  # a hook-started sync has it
        meta = _meta_for(cfg, sid)
        meta.setdefault("transcript_path", path)
        if dry_run:
            print(f"== {sid}  ({path})")
        syncer.sync_session(meta["transcript_path"], dict(meta, session_id=sid))
    return 0


def cmd_seed(cfg, dry_run):
    syncer = Syncer(cfg, dry_run=dry_run)
    n = 0
    for path in glob.glob(os.path.join(cfg.projects_dir, "*", "*.jsonl")) + \
            glob.glob(os.path.join(cfg.projects_dir, "*", "*", "subagents", "*.jsonl")):
        syncer.seed(path)
        n += 1
    print(f"{'would seed' if dry_run else 'seeded'} {n} transcript files (nothing shipped)")
    return 0


def cmd_status(cfg):
    """Surface failures the detached hook can't print (see <state>/sync.log)."""
    bad = 0
    for path in sorted(glob.glob(os.path.join(cfg.state_dir, "cursors", "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            continue
        if st.get("last_error"):
            bad += 1
            when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(st.get("last_error_at", 0)))
            print(f"{when}Z  {st.get('path')}\n    {st['last_error']}")
    print(f"{bad} file(s) failing; log: {os.path.join(cfg.state_dir, 'sync.log')}")
    return 1 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="cc_transcripts", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook")
    s = sub.add_parser("sync")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--session")
    g.add_argument("--all", action="store_true")
    s.add_argument("--days", type=float, default=2)
    s.add_argument("--dry-run", action="store_true")
    sd = sub.add_parser("seed")
    sd.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    sub.add_parser("status")
    st = sub.add_parser("stitch")
    st.add_argument("id")
    st.add_argument("--merge", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "hook":
        return hook_mod.run()
    cfg = config_mod.load()
    if args.cmd == "check":
        print(json.dumps(cfg.describe(), indent=1))
        return 0
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "sync":
        if args.session:
            return cmd_sync_session(cfg, args.session, args.dry_run)
        return cmd_sync_all(cfg, args.days, args.dry_run)
    if args.cmd == "seed":
        return cmd_seed(cfg, args.dry_run)
    if args.cmd == "stitch":
        if not cfg.archive_dir or not os.path.isdir(cfg.archive_dir):
            print("no archive (BETULA_CC_ARCHIVE_DIR unset or unmounted)", file=sys.stderr)
            return 1
        members = stitch_mod.group(stitch_mod.load_index(cfg.archive_dir), args.id)
        if args.merge:
            for obj in stitch_mod.merge(members):
                print(json.dumps(obj, separators=(",", ":")))
        else:
            for r in members:
                print(f"{r.get('host')}\t{r.get('surface')}\t{r.get('session_id')}\t{r['_file']}")
        return 0 if members else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
