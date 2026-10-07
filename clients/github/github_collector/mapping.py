"""Map GitHub run/job objects to the feed contract (labels + JSON line).

The contract is consumed by drosera (#234, #204, #251); changing a label or a
payload key is breaking per ADR-0006. Exactly six labels, all low-cardinality;
everything else goes in the line.
"""

import json
from datetime import datetime, timezone

SOURCE = "github_actions"
PIPELINE = "ci"


def parse_ts(value):
    """GitHub ISO-8601 (``2026-10-04T12:34:56Z``) → aware datetime, or None."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def to_ns(dt):
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1_000


def _seconds_between(start, end):
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds())


def labels(stage, cluster, repo):
    return {
        "log_source": f"{SOURCE}_{stage}",
        "cluster": cluster,
        "source": SOURCE,
        "pipeline": PIPELINE,
        "stage": stage,
        "repo": repo,
    }


def _line(payload):
    return json.dumps(payload, separators=(",", ":"))


def run_event(run, repo, cluster):
    """One run attempt → ``(labels, ts_ns, line)``, stamped at ``updated_at``.

    The run object has no ``completed_at``; for a completed run ``updated_at``
    is when it finished, so ``duration_s`` is approximate.
    """
    payload = {
        "repo": repo,
        "workflow": run.get("name"),
        "workflow_path": run.get("path"),
        "run_id": run["id"],
        "run_attempt": run.get("run_attempt", 1),
        "run_number": run.get("run_number"),
        "event": run.get("event"),
        "branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "actor": (run.get("actor") or {}).get("login"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "run_started_at": run.get("run_started_at"),
        "updated_at": run.get("updated_at"),
        "duration_s": _seconds_between(run.get("run_started_at"), run.get("updated_at")),
        "html_url": run.get("html_url"),
    }
    ts = parse_ts(run.get("updated_at")) or parse_ts(run.get("created_at"))
    return labels("run", cluster, repo), to_ns(ts), _line(payload)


def job_event(job, run, repo, cluster):
    """One job → ``(labels, ts_ns, line)``, stamped at ``completed_at``.

    A job that never started can lack ``completed_at``; it falls back to the
    run's ``updated_at`` (still event time, never poll time). Skipped jobs are
    emitted like any other — consumers exclude them from timing stats.
    """
    runner_labels = list(job.get("labels") or [])
    payload = {
        "repo": repo,
        "workflow": job.get("workflow_name") or run.get("name"),
        "run_id": run["id"],
        "run_attempt": job.get("run_attempt") or run.get("run_attempt", 1),
        "job_id": job["id"],
        "job_name": job.get("name"),
        "conclusion": job.get("conclusion"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "queue_s": _seconds_between(job.get("created_at"), job.get("started_at")),
        "duration_s": _seconds_between(job.get("started_at"), job.get("completed_at")),
        "runner_name": job.get("runner_name"),
        "runner_group": job.get("runner_group_name"),
        "runner_labels": runner_labels,
        "self_hosted": "self-hosted" in runner_labels,
        "html_url": job.get("html_url"),
    }
    ts = (
        parse_ts(job.get("completed_at"))
        or parse_ts(run.get("updated_at"))
        or parse_ts(run.get("created_at"))
    )
    return labels("job", cluster, repo), to_ns(ts), _line(payload)


BRANCH_HEAD = "github_branch_head"


def branch_head_event(branch_obj, branch, repo, cluster, now):
    """One repo's default-branch head → ``(labels, ts_ns, line)``, stamped at poll time.

    Unlike run/job events this is a state sample ("the newest head as of
    now"), so the poll time is the right timestamp. Only three labels
    (``log_source``, ``cluster``, ``repo``); the rest is in the line.
    """
    commit = branch_obj.get("commit") or {}
    committer = (commit.get("commit") or {}).get("committer") or {}
    observed = datetime.fromtimestamp(now, tz=timezone.utc)
    payload = {
        "branch": branch,
        "sha": commit.get("sha"),
        "committed_at": committer.get("date"),
        "url": commit.get("html_url"),
        "observed_at": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    lbls = {"log_source": BRANCH_HEAD, "cluster": cluster, "repo": repo}
    return lbls, to_ns(observed), _line(payload)
