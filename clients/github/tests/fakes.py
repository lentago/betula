"""In-process fakes for GitHub and Loki — no tokens, no network."""

import json
import urllib.parse

API = "https://api.github.com"


def run(run_id, repo="lentago/kalmia", attempt=1, created="2026-10-06T10:00:00Z",
        started="2026-10-06T10:00:05Z", updated="2026-10-06T10:03:05Z", conclusion="success"):
    return {
        "id": run_id,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "run_attempt": attempt,
        "run_number": 42,
        "event": "push",
        "head_branch": "main",
        "head_sha": "abc123",
        "actor": {"login": "cpitzi"},
        "status": "completed",
        "conclusion": conclusion,
        "created_at": created,
        "run_started_at": started,
        "updated_at": updated,
        "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        "repository": {"full_name": repo},
    }


def job(job_id, run_id, attempt=1, name="build", conclusion="success",
        created="2026-10-06T10:00:06Z", started="2026-10-06T10:00:16Z",
        completed="2026-10-06T10:02:16Z", labels=("ubuntu-latest",)):
    return {
        "id": job_id,
        "run_id": run_id,
        "run_attempt": attempt,
        "workflow_name": "CI",
        "name": name,
        "conclusion": conclusion,
        "created_at": created,
        "started_at": started,
        "completed_at": completed,
        "runner_name": "GitHub Actions 7",
        "runner_group_name": "GitHub Actions",
        "labels": list(labels),
        "html_url": f"https://github.com/lentago/kalmia/actions/runs/{run_id}/job/{job_id}",
    }


class FakeGitHub:
    """Routes GET paths to canned responses and records every request.

    ``routes[path]`` is a list of pages (each a JSON-able object), or a
    ``(status, headers, body)`` tuple for an error response. Pages after the
    first are linked with a ``Link: rel="next"`` header like the real API.
    """

    def __init__(self):
        self.routes = {}
        self.calls = []

    def set_repos(self, owner, *names, archived=(), default_branches=None):
        branches = default_branches or {}
        repos = [{"full_name": f"{owner}/{n}", "archived": False,
                  "default_branch": branches.get(n, "main")} for n in names]
        repos += [{"full_name": f"{owner}/{n}", "archived": True, "default_branch": "main"} for n in archived]
        self.routes[f"/orgs/{owner}/repos"] = [repos]

    def set_branch(self, repo, branch="main", sha="deadbeef" * 5, committed="2026-10-06T22:00:00Z"):
        self.routes[f"/repos/{repo}/branches/{branch}"] = [{
            "name": branch,
            "commit": {
                "sha": sha,
                "html_url": f"https://github.com/{repo}/commit/{sha}",
                "commit": {"committer": {"date": committed}},
            },
        }]

    def set_runs(self, repo, *runs):
        self.routes[f"/repos/{repo}/actions/runs"] = [{"total_count": len(runs), "workflow_runs": list(runs)}]

    def set_jobs(self, repo, run_id, attempt, *jobs):
        path = f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs"
        self.routes[path] = [{"total_count": len(jobs), "jobs": list(jobs)}]

    def paths(self):
        return [urllib.parse.urlsplit(u).path for u, _ in self.calls]

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        route = self.routes.get(parts.path)
        if route is None:
            return 404, {}, json.dumps({"message": "Not Found"})
        if isinstance(route, tuple):
            return route
        page = int(query.get("page", "1"))
        headers_out = {}
        if page < len(route):
            nxt = urllib.parse.urlencode({**query, "page": page + 1})
            headers_out["link"] = f'<{API}{parts.path}?{nxt}>; rel="next"'
        return 200, headers_out, json.dumps(route[page - 1])


class FakeLoki:
    """Records each push; answers with the queued (status, body) or 204."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.pushes = []

    def __call__(self, url, headers, body):
        self.pushes.append((url, headers, json.loads(body)))
        return self.responses.pop(0) if self.responses else (204, "")
