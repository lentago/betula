"""Minimal GitHub REST client: repo enumeration, completed runs, attempt jobs.

The HTTP transport is injectable so tests drive it with a fake GitHub — no
token, no network. Every failure surfaces as GitHubError (or its RateLimited
subclass) so the collector can skip the tick in one place.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.github.com"
PER_PAGE = 100
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(RuntimeError):
    def __init__(self, status, url, body):
        super().__init__(f"GitHub {url} failed: HTTP {status}: {str(body)[:300]}")
        self.status = status
        self.url = url


class RateLimited(GitHubError):
    """Primary or secondary rate limit hit; ``until`` is an epoch to wait for."""

    def __init__(self, status, url, body, until):
        super().__init__(status, url, body)
        self.until = until


def _default_transport(url, headers, timeout=30):
    """GET ``url``; return (status, lower-cased headers dict, body text)."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry headers + body
        hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return exc.code, hdrs, exc.read().decode("utf-8", "replace")


def rate_limit_until(status, headers, body, now):
    """Return the epoch to back off until if this response is a rate limit, else None.

    Covers the primary limit (403/429 with ``x-ratelimit-remaining: 0`` and a
    reset epoch) and secondary limits (``retry-after`` seconds, or a 403 whose
    body says "rate limit"). Waits at least 60s when GitHub gives no hint.
    """
    if status not in (403, 429):
        return None
    retry_after = headers.get("retry-after")
    if retry_after and retry_after.isdigit():
        return now + max(int(retry_after), 1)
    if headers.get("x-ratelimit-remaining") == "0":
        reset = headers.get("x-ratelimit-reset", "")
        return max(int(reset), now + 1) if reset.isdigit() else now + 60
    if status == 429 or "rate limit" in str(body).lower():
        return now + 60
    return None


class GitHubClient:
    def __init__(self, token, transport=_default_transport, clock=time.time, api_root=API_ROOT):
        if not token:
            raise ValueError("a GitHub token is required (never hard-code it)")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "betula-github-collector",
        }
        self._transport = transport
        self._clock = clock
        self._root = api_root.rstrip("/")
        self.requests = 0

    def _get(self, url):
        try:
            status, headers, body = self._transport(url, dict(self._headers))
        except (urllib.error.URLError, OSError) as exc:
            raise GitHubError(None, url, exc) from exc
        self.requests += 1
        if 200 <= status < 300:
            try:
                return json.loads(body) if body else {}, headers
            except ValueError as exc:
                raise GitHubError(status, url, f"invalid JSON: {exc}") from exc
        until = rate_limit_until(status, headers, body, int(self._clock()))
        if until is not None:
            raise RateLimited(status, url, body, until)
        raise GitHubError(status, url, body)

    def _paginate(self, path, params, key=None):
        """Yield items across every page, following the Link rel="next" header."""
        url = f"{self._root}{path}?" + urllib.parse.urlencode({**params, "per_page": PER_PAGE})
        while url:
            data, headers = self._get(url)
            items = data.get(key, []) if key else data
            yield from items
            match = _LINK_NEXT_RE.search(headers.get("link", ""))
            url = match.group(1) if match else None

    def list_repos(self, owner):
        """Non-archived repos for an org (falls back to the user endpoint on 404)."""
        try:
            repos = list(self._paginate(f"/orgs/{owner}/repos", {"type": "all"}))
        except GitHubError as exc:
            if exc.status != 404 or isinstance(exc, RateLimited):
                raise
            repos = list(self._paginate(f"/users/{owner}/repos", {"type": "owner"}))
        return sorted(r["full_name"] for r in repos if not r.get("archived"))

    def list_completed_runs(self, full_name, since_iso):
        """Completed runs created at or after ``since_iso`` (ISO-8601, UTC)."""
        params = {"status": "completed", "created": f">={since_iso}"}
        return list(self._paginate(f"/repos/{full_name}/actions/runs", params, key="workflow_runs"))

    def list_attempt_jobs(self, full_name, run_id, attempt):
        """Jobs of one specific run attempt.

        Deliberately the attempt-scoped endpoint: plain ``/runs/{id}/jobs``
        defaults to ``filter=latest`` and would attribute a re-run's jobs to
        the wrong attempt.
        """
        path = f"/repos/{full_name}/actions/runs/{run_id}/attempts/{attempt}/jobs"
        return list(self._paginate(path, {}, key="jobs"))
