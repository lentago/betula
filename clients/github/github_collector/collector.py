"""One collector tick: enumerate → list completed runs → dedupe → jobs → push.

Failure rules (issue #113):

- Any GitHub or Loki failure skips the tick; the seen-set is not advanced, so
  the next tick retries the same run attempts.
- A rate-limit response records ``backoff_until``; ticks before then exit
  without calling GitHub.
- A failed ``branches/{default}`` request skips only that repo's
  ``github_branch_head`` event for the tick (#134).
- A Loki 400 for out-of-order / too-old entries is logged and dropped: the
  keys are marked seen so the batch isn't retried forever.
"""

import logging
from datetime import datetime, timezone

from . import mapping
from .github import GitHubError, RateLimited
from .loki import LokiError
from .state import seen_key

log = logging.getLogger("betula-github")

REPO_REFRESH_S = 3600  # repo list is refreshed at most hourly

# Tick outcomes (also the CLI exit-code source: only FAILED is non-zero).
OK = "ok"
BACKOFF = "backoff"
FAILED = "failed"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repos_for(owner, gh, state, now):
    cached = state.repos.get(owner)
    # Entries cached before #134 were bare names without a default branch; refetch.
    if (cached and now - cached.get("fetched_at", 0) < REPO_REFRESH_S
            and all(isinstance(r, dict) for r in cached["repos"])):
        return cached["repos"]
    repos = gh.list_repos(owner)
    state.repos[owner] = {"fetched_at": now, "repos": repos}
    log.info("refreshed repo list for %s: %d non-archived repos", owner, len(repos))
    return repos


def collect(cfg, gh, state, now):
    """Return ``(events, new_keys)`` for every unseen completed run attempt.

    Raises GitHubError / RateLimited on any GitHub failure.
    """
    since = now - cfg.lookback_s
    since_iso = _iso(since)
    events, new_keys = [], {}
    for entry in cfg.owners:
        owner, cluster = entry["owner"], entry["cluster"]
        for repo_obj in repos_for(owner, gh, state, now):
            repo = repo_obj["full_name"]
            try:
                runs = gh.list_completed_runs(repo, since_iso)
            except GitHubError as exc:
                # A repo deleted/renamed since the last hourly refresh 404s
                # forever; drop the cached list so the next tick re-enumerates.
                if exc.status == 404:
                    state.repos.pop(owner, None)
                raise
            for run in runs:
                attempt = run.get("run_attempt", 1)
                key = seen_key(run["id"], attempt)
                if key in state.seen or key in new_keys:
                    continue
                jobs = gh.list_attempt_jobs(repo, run["id"], attempt)
                events.append(mapping.run_event(run, repo, cluster))
                events.extend(mapping.job_event(job, run, repo, cluster) for job in jobs)
                created = mapping.parse_ts(run.get("created_at"))
                new_keys[key] = int(created.timestamp()) if created else now
    events.extend(collect_branch_heads(cfg, gh, state, now))
    return events, new_keys


def collect_branch_heads(cfg, gh, state, now):
    """One ``github_branch_head`` event per repo, every tick (not deduplicated).

    A failed branch request skips that repo for this tick only; rate limits
    still propagate so the tick backs off.
    """
    events = []
    for entry in cfg.owners:
        owner, cluster = entry["owner"], entry["cluster"]
        for repo_obj in repos_for(owner, gh, state, now):
            repo, branch = repo_obj["full_name"], repo_obj.get("default_branch")
            if not branch:  # empty repo: nothing to report
                continue
            try:
                branch_obj = gh.get_branch(repo, branch)
            except RateLimited:
                raise
            except GitHubError as exc:
                log.warning("branch head for %s skipped this tick: %s", repo, exc)
                continue
            events.append(mapping.branch_head_event(branch_obj, branch, repo, cluster, now))
    return events


def tick(cfg, gh, loki, state, now, dry_run=False, out=None):
    if state.backoff_until > now:
        log.warning("rate-limit backoff until %s; skipping tick", _iso(state.backoff_until))
        return BACKOFF
    try:
        events, new_keys = collect(cfg, gh, state, now)
    except RateLimited as exc:
        state.backoff_until = exc.until
        state.save_cache()
        log.warning("GitHub rate limit (%s); backing off until %s", exc, _iso(exc.until))
        return BACKOFF
    except GitHubError as exc:
        state.save_cache()
        log.error("GitHub failure, skipping tick (seen-set unchanged): %s", exc)
        return FAILED
    state.save_cache()

    runs = sum(1 for e in events if e[0].get("stage") == "run")
    heads = sum(1 for e in events if e[0]["log_source"] == mapping.BRANCH_HEAD)
    jobs = len(events) - runs - heads
    if dry_run:
        for labels, ts, line in events:
            print(f"{ts} {labels['log_source']} {labels['repo']} {line}", file=out)
        log.info("dry run: %d run(s), %d job(s), %d branch head(s); nothing pushed, seen-set unchanged",
                 runs, jobs, heads)
        return OK

    if events:
        try:
            result, detail = loki.push(events)
        except LokiError as exc:
            log.error("Loki failure, skipping tick (seen-set unchanged): %s", exc)
            return FAILED
        if result == "dropped":
            log.warning("Loki rejected out-of-window entries; dropped, not retried: %s",
                        str(detail)[:500])
        state.seen.update(new_keys)
    state.prune_seen(now - cfg.lookback_s)
    state.save_seen()
    log.info("pushed %d run(s), %d job(s), %d branch head(s); %d GitHub request(s); seen-set %d",
             runs, jobs, heads, gh.requests, len(state.seen))
    return OK
