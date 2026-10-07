"""GitHub client: repo enumeration, run listing, attempt-scoped jobs, rate limits."""

import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from github_collector.github import (  # noqa: E402
    GitHubClient,
    GitHubError,
    RateLimited,
    rate_limit_until,
)
from tests.fakes import FakeGitHub, job, run  # noqa: E402

NOW = 1_791_000_000


def client(fake):
    return GitHubClient("github_pat_test", transport=fake, clock=lambda: NOW)


class RepoEnumerationTest(unittest.TestCase):
    def test_lists_non_archived_repos_sorted(self):
        fake = FakeGitHub()
        fake.set_repos("lentago", "kalmia", "drosera", archived=("old-thing",))
        self.assertEqual(client(fake).list_repos("lentago"), ["lentago/drosera", "lentago/kalmia"])

    def test_follows_pagination(self):
        fake = FakeGitHub()
        fake.routes["/orgs/lentago/repos"] = [
            [{"full_name": "lentago/a", "archived": False}],
            [{"full_name": "lentago/b", "archived": False}],
        ]
        self.assertEqual(client(fake).list_repos("lentago"), ["lentago/a", "lentago/b"])
        self.assertEqual(len(fake.calls), 2)

    def test_falls_back_to_user_endpoint_when_not_an_org(self):
        fake = FakeGitHub()
        fake.routes["/users/cpitzi/repos"] = [[{"full_name": "cpitzi/dotfiles", "archived": False}]]
        self.assertEqual(client(fake).list_repos("cpitzi"), ["cpitzi/dotfiles"])
        self.assertEqual(fake.paths(), ["/orgs/cpitzi/repos", "/users/cpitzi/repos"])

    def test_sends_auth_and_api_version_headers(self):
        fake = FakeGitHub()
        fake.set_repos("lentago", "kalmia")
        client(fake).list_repos("lentago")
        headers = fake.calls[0][1]
        self.assertEqual(headers["Authorization"], "Bearer github_pat_test")
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")


class RunsAndJobsTest(unittest.TestCase):
    def test_runs_query_is_completed_within_window(self):
        fake = FakeGitHub()
        fake.set_runs("lentago/kalmia", run(1))
        runs = client(fake).list_completed_runs("lentago/kalmia", "2026-10-05T12:00:00Z")
        self.assertEqual([r["id"] for r in runs], [1])
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(fake.calls[0][0]).query))
        self.assertEqual(query["status"], "completed")
        self.assertEqual(query["created"], ">=2026-10-05T12:00:00Z")
        self.assertEqual(query["per_page"], "100")

    def test_jobs_use_the_attempt_scoped_endpoint(self):
        fake = FakeGitHub()
        fake.set_jobs("lentago/kalmia", 7, 2, job(70, 7, attempt=2))
        jobs = client(fake).list_attempt_jobs("lentago/kalmia", 7, 2)
        self.assertEqual([j["id"] for j in jobs], [70])
        self.assertEqual(fake.paths(), ["/repos/lentago/kalmia/actions/runs/7/attempts/2/jobs"])


class FailureTest(unittest.TestCase):
    def test_server_error_raises_github_error(self):
        fake = FakeGitHub()
        fake.routes["/repos/lentago/kalmia/actions/runs"] = (502, {}, "bad gateway")
        with self.assertRaises(GitHubError) as ctx:
            client(fake).list_completed_runs("lentago/kalmia", "2026-10-05T12:00:00Z")
        self.assertEqual(ctx.exception.status, 502)
        self.assertNotIsInstance(ctx.exception, RateLimited)

    def test_network_error_raises_github_error(self):
        def broken(url, headers):
            raise OSError("connection reset")

        with self.assertRaises(GitHubError):
            GitHubClient("t", transport=broken).list_repos("lentago")

    def test_primary_rate_limit_backs_off_to_reset(self):
        fake = FakeGitHub()
        fake.routes["/orgs/lentago/repos"] = (
            403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(NOW + 900)}, "API rate limit exceeded",
        )
        with self.assertRaises(RateLimited) as ctx:
            client(fake).list_repos("lentago")
        self.assertEqual(ctx.exception.until, NOW + 900)

    def test_rate_limited_org_lookup_does_not_fall_back(self):
        fake = FakeGitHub()
        fake.routes["/orgs/lentago/repos"] = (429, {"retry-after": "30"}, "")
        with self.assertRaises(RateLimited):
            client(fake).list_repos("lentago")
        self.assertEqual(len(fake.calls), 1)

    def test_rate_limit_classification(self):
        self.assertEqual(rate_limit_until(429, {"retry-after": "120"}, "", NOW), NOW + 120)
        self.assertEqual(rate_limit_until(403, {}, "You have exceeded a secondary rate limit", NOW), NOW + 60)
        self.assertEqual(rate_limit_until(429, {}, "", NOW), NOW + 60)
        self.assertIsNone(rate_limit_until(403, {}, "Resource not accessible by personal access token", NOW))
        self.assertIsNone(rate_limit_until(500, {"retry-after": "5"}, "", NOW))


if __name__ == "__main__":
    unittest.main()
