"""Collector tick: window, dedupe, persistence, re-runs, and failure behaviour."""

import io
import logging
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from github_collector import collector, mapping  # noqa: E402
from github_collector.config import Config  # noqa: E402
from github_collector.github import GitHubClient  # noqa: E402
from github_collector.loki import LokiPusher  # noqa: E402
from github_collector.state import State  # noqa: E402
from tests.fakes import FakeGitHub, FakeLoki, job, run  # noqa: E402

NOW = int(mapping.parse_ts("2026-10-07T00:00:00Z").timestamp())
REPO = "lentago/kalmia"


class TickTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.cfg = Config.from_dict(
            {"owners": [{"owner": "lentago", "cluster": "lentago"}], "interval": "5m", "lookback": "24h"},
            env={"STATE_DIRECTORY": self.dir},
        )
        self.gh = FakeGitHub()
        self.gh.set_repos("lentago", "kalmia", archived=("fossil",))
        self.gh.set_runs(REPO, run(1), run(2, conclusion="failure"))
        self.gh.set_jobs(REPO, 1, 1, job(10, 1), job(11, 1, name="lint"))
        self.gh.set_jobs(REPO, 2, 1, job(20, 2, conclusion="failure"))
        self.loki = FakeLoki()

    def tick(self, now=NOW, state=None, **kwargs):
        state = state or State(self.dir)
        gh = GitHubClient("t", transport=self.gh, clock=lambda: now)
        loki = LokiPusher("https://loki", "1", "t", transport=self.loki)
        return collector.tick(self.cfg, gh, loki, state, now, **kwargs), state

    def pushed_lines(self, push_index=-1):
        body = self.loki.pushes[push_index][2]
        return [(s["stream"]["log_source"], v[1]) for s in body["streams"] for v in s["values"]]

    # ── happy path ──

    def test_pushes_runs_and_jobs_in_one_request(self):
        outcome, state = self.tick()
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(len(self.loki.pushes), 1)
        kinds = [k for k, _ in self.pushed_lines()]
        self.assertEqual(kinds.count("github_actions_run"), 2)
        self.assertEqual(kinds.count("github_actions_job"), 3)
        self.assertEqual(set(state.seen), {"1:1", "2:1"})

    def test_window_is_created_since_now_minus_lookback(self):
        self.tick()
        runs_url = next(u for u, _ in self.gh.calls if u.split("?")[0].endswith("/actions/runs"))
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(runs_url).query))
        self.assertEqual(query["created"], ">=2026-10-06T00:00:00Z")
        self.assertEqual(query["status"], "completed")

    def test_archived_repos_are_not_polled(self):
        self.tick()
        self.assertNotIn("/repos/lentago/fossil/actions/runs", self.gh.paths())

    def test_jobs_fetched_per_attempt(self):
        self.tick()
        self.assertIn("/repos/lentago/kalmia/actions/runs/1/attempts/1/jobs", self.gh.paths())
        self.assertNotIn("/repos/lentago/kalmia/actions/runs/1/jobs", self.gh.paths())

    def test_dry_run_prints_and_does_not_push_or_advance(self):
        out = io.StringIO()
        outcome, state = self.tick(dry_run=True, out=out)
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(self.loki.pushes, [])
        self.assertEqual(State(self.dir).seen, {})
        self.assertEqual(len(out.getvalue().splitlines()), 5)

    # ── dedupe ──

    def test_consecutive_ticks_do_not_duplicate(self):
        self.tick()
        self.gh.calls.clear()
        self.tick(now=NOW + 300)
        self.assertEqual(len(self.loki.pushes), 1)  # second tick had nothing new
        self.assertFalse(any("/attempts/" in p for p in self.gh.paths()))

    def test_seen_set_survives_a_restart(self):
        self.tick()
        fresh = State(self.dir)  # a new process reading the persisted state
        self.assertEqual(set(fresh.seen), {"1:1", "2:1"})
        self.tick(now=NOW + 300, state=fresh)
        self.assertEqual(len(self.loki.pushes), 1)

    def test_rerun_emits_new_attempt_with_its_own_jobs(self):
        self.tick()
        self.gh.set_runs(REPO, run(1, attempt=2, updated="2026-10-06T23:59:00Z"), run(2, conclusion="failure"))
        self.gh.set_jobs(REPO, 1, 2, job(12, 1, attempt=2, completed="2026-10-06T23:58:00Z"))
        outcome, state = self.tick(now=NOW + 300)
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(len(self.loki.pushes), 2)
        lines = self.pushed_lines()
        self.assertEqual(len(lines), 2)  # the new run attempt + its one job; attempt 1 untouched
        self.assertTrue(all('"run_attempt":2' in line for _, line in lines))
        self.assertIn("/repos/lentago/kalmia/actions/runs/1/attempts/2/jobs", self.gh.paths())
        self.assertEqual(set(state.seen), {"1:1", "1:2", "2:1"})

    def test_seen_keys_older_than_lookback_are_pruned(self):
        self.tick()
        self.gh.set_runs(REPO)
        _, state = self.tick(now=NOW + 2 * 86400)
        self.assertEqual(state.seen, {})

    # ── repo enumeration cache ──

    def test_repo_list_refreshed_at_most_hourly(self):
        self.tick()
        self.tick(now=NOW + 300)
        self.assertEqual(self.gh.paths().count("/orgs/lentago/repos"), 1)
        self.tick(now=NOW + 3600)
        self.assertEqual(self.gh.paths().count("/orgs/lentago/repos"), 2)

    def test_second_owner_is_config_only(self):
        self.cfg = Config.from_dict(
            {"owners": [{"owner": "lentago", "cluster": "lentago"}, {"owner": "pitzilabs", "cluster": "pitzilabs"}]},
            env={"STATE_DIRECTORY": self.dir},
        )
        self.gh.set_repos("pitzilabs", "site")
        self.gh.set_runs("pitzilabs/site", run(99, repo="pitzilabs/site"))
        self.gh.set_jobs("pitzilabs/site", 99, 1)
        self.tick()
        clusters = {s["stream"]["cluster"] for s in self.loki.pushes[0][2]["streams"]}
        self.assertEqual(clusters, {"lentago", "pitzilabs"})

    # ── failure behaviour ──

    def test_github_failure_skips_tick_without_advancing(self):
        self.gh.routes["/repos/lentago/kalmia/actions/runs/2/attempts/1/jobs"] = (500, {}, "boom")
        outcome, state = self.tick()
        self.assertEqual(outcome, collector.FAILED)
        self.assertEqual(self.loki.pushes, [])
        self.assertEqual(State(self.dir).seen, {})

    def test_missing_repo_404_invalidates_repo_cache(self):
        self.gh.routes["/repos/lentago/kalmia/actions/runs"] = (404, {}, "Not Found")
        outcome, _ = self.tick()
        self.assertEqual(outcome, collector.FAILED)
        self.assertNotIn("lentago", State(self.dir).repos)

    def test_loki_failure_skips_tick_without_advancing(self):
        self.loki = FakeLoki((503, "unavailable"))
        outcome, _ = self.tick()
        self.assertEqual(outcome, collector.FAILED)
        self.assertEqual(State(self.dir).seen, {})
        outcome, state = self.tick(now=NOW + 300)  # next tick retries the same attempts
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(len(self.pushed_lines()), 5)
        self.assertEqual(set(state.seen), {"1:1", "2:1"})

    def test_loki_out_of_order_400_is_dropped_and_not_retried(self):
        self.loki = FakeLoki((400, "entry too far behind, entry timestamp is: ..."))
        outcome, state = self.tick()
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(set(State(self.dir).seen), {"1:1", "2:1"})
        self.tick(now=NOW + 300)
        self.assertEqual(len(self.loki.pushes), 1)

    def test_rate_limit_backs_off_until_reset(self):
        self.gh.routes["/repos/lentago/kalmia/actions/runs"] = (
            403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(NOW + 1200)}, "rate limit exceeded",
        )
        outcome, _ = self.tick()
        self.assertEqual(outcome, collector.BACKOFF)
        self.assertEqual(State(self.dir).backoff_until, NOW + 1200)
        self.assertEqual(State(self.dir).seen, {})

        self.gh.set_runs(REPO, run(1))
        self.gh.calls.clear()
        outcome, _ = self.tick(now=NOW + 600)  # still inside the backoff
        self.assertEqual(outcome, collector.BACKOFF)
        self.assertEqual(self.gh.calls, [])

        outcome, state = self.tick(now=NOW + 1200)
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(set(state.seen), {"1:1"})


if __name__ == "__main__":
    unittest.main()
