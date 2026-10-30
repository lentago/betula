"""Collector tick: window, dedupe, persistence, re-runs, and failure behaviour."""

import io
import json
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
        self.gh.set_branch(REPO, "main", sha="a" * 40, committed="2026-10-06T22:00:00Z")
        self.loki = FakeLoki()

    def tick(self, now=NOW, state=None, **kwargs):
        state = state or State(self.dir)
        gh = GitHubClient("t", transport=self.gh, clock=lambda: now)
        loki = LokiPusher("https://loki", "1", "t", transport=self.loki)
        return collector.tick(self.cfg, gh, loki, state, now, **kwargs), state

    def head_events(self, push_index=-1):
        body = self.loki.pushes[push_index][2]
        return [(s["stream"], json.loads(v[1])) for s in body["streams"]
                if s["stream"]["log_source"] == "github_branch_head" for v in s["values"]]

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
        self.assertEqual(kinds.count("github_branch_head"), 1)
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
        self.assertEqual(len(out.getvalue().splitlines()), 6)

    # ── dedupe ──

    def test_consecutive_ticks_do_not_duplicate(self):
        self.tick()
        self.gh.calls.clear()
        self.tick(now=NOW + 300)
        self.assertEqual(len(self.loki.pushes), 2)  # only the branch head repeats
        self.assertEqual(len(self.pushed_lines()), 1)
        self.assertFalse(any("/attempts/" in p for p in self.gh.paths()))

    def test_seen_set_survives_a_restart(self):
        self.tick()
        fresh = State(self.dir)  # a new process reading the persisted state
        self.assertEqual(set(fresh.seen), {"1:1", "2:1"})
        self.tick(now=NOW + 300, state=fresh)
        self.assertEqual(len(self.pushed_lines()), 1)

    def test_rerun_emits_new_attempt_with_its_own_jobs(self):
        self.tick()
        self.gh.set_runs(REPO, run(1, attempt=2, updated="2026-10-06T23:59:00Z"), run(2, conclusion="failure"))
        self.gh.set_jobs(REPO, 1, 2, job(12, 1, attempt=2, completed="2026-10-06T23:58:00Z"))
        outcome, state = self.tick(now=NOW + 300)
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(len(self.loki.pushes), 2)
        lines = [line for line in self.pushed_lines() if line[0] != "github_branch_head"]
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

    # ── branch heads (#134) ──

    def test_branch_head_event_has_exact_labels_and_fields(self):
        self.tick()
        [(stream, line)] = self.head_events()
        self.assertEqual(stream, {"log_source": "github_branch_head", "cluster": "lentago", "repo": REPO})
        self.assertEqual(line, {
            "branch": "main",
            "sha": "a" * 40,
            "committed_at": "2026-10-06T22:00:00Z",
            "url": f"https://github.com/{REPO}/commit/{'a' * 40}",
            "observed_at": "2026-10-07T00:00:00Z",
        })

    def test_one_branch_head_per_repo_every_tick_even_when_unchanged(self):
        self.gh.set_repos("lentago", "kalmia", "drosera")
        self.gh.set_runs("lentago/drosera")
        self.gh.set_branch("lentago/drosera", "main", sha="b" * 40)
        self.tick()
        self.tick(now=NOW + 300)
        for index in (0, 1):
            repos = sorted(s["repo"] for s, _ in self.head_events(index))
            self.assertEqual(repos, ["lentago/drosera", "lentago/kalmia"])
        self.assertEqual(self.head_events(1)[0][1]["observed_at"], "2026-10-07T00:05:00Z")
        self.assertEqual(self.gh.paths().count("/repos/lentago/kalmia/branches/main"), 2)

    def test_default_branch_comes_from_the_repository_object(self):
        self.gh.set_repos("lentago", "kalmia", default_branches={"kalmia": "trunk"})
        self.gh.set_branch(REPO, "trunk", sha="c" * 40)
        self.tick()
        self.assertIn("/repos/lentago/kalmia/branches/trunk", self.gh.paths())
        self.assertNotIn("/repos/lentago/kalmia/branches/main", self.gh.paths())
        [(_, line)] = self.head_events()
        self.assertEqual((line["branch"], line["sha"]), ("trunk", "c" * 40))

    def test_failed_branch_request_skips_only_that_repo(self):
        self.gh.set_repos("lentago", "kalmia", "drosera")
        self.gh.set_runs("lentago/drosera")
        self.gh.set_branch("lentago/drosera", "main", sha="b" * 40)
        self.gh.routes["/repos/lentago/kalmia/branches/main"] = (502, {}, "bad gateway")
        outcome, state = self.tick()
        self.assertEqual(outcome, collector.OK)
        self.assertEqual([s["repo"] for s, _ in self.head_events()], ["lentago/drosera"])
        self.assertEqual(set(state.seen), {"1:1", "2:1"})  # runs still pushed and recorded

    def test_branch_heads_push_alone_when_there_are_no_new_runs(self):
        self.tick()
        self.tick(now=NOW + 300)
        self.assertEqual(len(self.head_events()), 1)
        self.assertEqual(len(self.pushed_lines()), 1)

    def test_dry_run_prints_branch_heads_without_pushing(self):
        out = io.StringIO()
        self.tick(dry_run=True, out=out)
        self.assertIn("github_branch_head lentago/kalmia", out.getvalue())
        self.assertEqual(self.loki.pushes, [])

    def test_pre_134_cache_of_bare_names_is_refreshed(self):
        state = State(self.dir)
        state.repos["lentago"] = {"fetched_at": NOW, "repos": [REPO]}
        state.save_cache()
        self.tick()
        self.assertEqual(len(self.head_events()), 1)

    # ── failure behaviour ──

    def test_github_failure_skips_tick_without_advancing(self):
        self.gh.routes["/repos/lentago/kalmia/actions/runs/2/attempts/1/jobs"] = (500, {}, "boom")
        outcome, state = self.tick()
        self.assertEqual(outcome, collector.FAILED)
        self.assertEqual(self.loki.pushes, [])
        self.assertEqual(State(self.dir).seen, {})

    def test_branch_rate_limit_backs_off(self):
        self.gh.routes["/repos/lentago/kalmia/branches/main"] = (429, {"retry-after": "30"}, "")
        outcome, _ = self.tick()
        self.assertEqual(outcome, collector.BACKOFF)
        self.assertEqual(self.loki.pushes, [])

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
        self.assertEqual(len(self.pushed_lines()), 6)
        self.assertEqual(set(state.seen), {"1:1", "2:1"})

    def test_loki_out_of_order_400_is_dropped_and_not_retried(self):
        self.loki = FakeLoki((400, "entry too far behind, entry timestamp is: ..."))
        outcome, state = self.tick()
        self.assertEqual(outcome, collector.OK)
        self.assertEqual(set(State(self.dir).seen), {"1:1", "2:1"})
        self.tick(now=NOW + 300)
        self.assertEqual(len(self.pushed_lines()), 1)  # retry tick carries only the head

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


class QueuedJobsTest(unittest.TestCase):
    """github_actions_queued (drosera#292): queued jobs in the queue_watch repos."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.cfg = Config.from_dict(
            {"owners": [{"owner": "lentago", "cluster": "lentago"}],
             "queue_watch": [{"repo": REPO, "cluster": "lentago"}]},
            env={"STATE_DIRECTORY": self.dir},
        )
        self.gh = FakeGitHub()
        self.gh.set_repos("lentago", "kalmia", "drosera")
        for repo in (REPO, "lentago/drosera"):
            self.gh.set_runs(repo)
            self.gh.set_branch(repo)
        # The terraform shape: hosted jobs ran, the self-hosted apply is waiting,
        # so the run itself already reports in_progress.
        self.gh.set_active_runs(REPO, "queued")
        self.gh.set_active_runs(REPO, "in_progress", run(7, status="in_progress", conclusion=None))
        self.gh.set_jobs(REPO, 7, 1,
                         job(70, 7, name="validate"),
                         job(71, 7, name="apply", status="queued", conclusion=None,
                             created="2026-10-06T23:40:00Z", started=None, completed=None,
                             labels=("self-hosted", "lan")))
        self.loki = FakeLoki()

    def tick(self, now=NOW, **kwargs):
        gh = GitHubClient("t", transport=self.gh, clock=lambda: now)
        loki = LokiPusher("https://loki", "1", "t", transport=self.loki)
        return collector.tick(self.cfg, gh, loki, State(self.dir), now, **kwargs)

    def queued_events(self, push_index=-1):
        body = self.loki.pushes[push_index][2]
        return [(s["stream"], int(v[0]), json.loads(v[1])) for s in body["streams"]
                if s["stream"]["log_source"] == "github_actions_queued" for v in s["values"]]

    def run_listings(self):
        """(path, status=) of every runs listing the tick made."""
        parts = [urllib.parse.urlsplit(u) for u, _ in self.gh.calls]
        return [(p.path, dict(urllib.parse.parse_qsl(p.query)).get("status"))
                for p in parts if p.path.endswith("/actions/runs")]

    def test_queued_job_in_an_in_progress_run_has_exact_labels_and_fields(self):
        self.assertEqual(self.tick(), collector.OK)
        [(stream, ts, line)] = self.queued_events()
        self.assertEqual(stream, {"log_source": "github_actions_queued", "cluster": "lentago", "repo": REPO})
        self.assertEqual(ts, NOW * 1_000_000_000)
        self.assertEqual(line, {
            "repo": REPO,
            "workflow": "CI",
            "run_id": 7,
            "run_attempt": 1,
            "job_id": 71,
            "job_name": "apply",
            "status": "queued",
            "runner_labels": ["self-hosted", "lan"],
            "self_hosted": True,
            "queued_at": "2026-10-06T23:40:00Z",
            "observed_at": "2026-10-07T00:00:00Z",
            "age_s": 1200,
            "html_url": "https://github.com/lentago/kalmia/actions/runs/7/job/71",
        })

    def test_lists_queued_and_in_progress_runs_but_not_waiting(self):
        self.tick()
        statuses = [status for path, status in self.run_listings() if path == f"/repos/{REPO}/actions/runs"]
        self.assertEqual(sorted(statuses), ["completed", "in_progress", "queued"])

    def test_queued_run_with_hosted_job_is_emitted_too(self):
        self.gh.set_active_runs(REPO, "queued", run(8, status="queued", conclusion=None))
        self.gh.set_jobs(REPO, 8, 1, job(80, 8, status="queued", conclusion=None, created="2026-10-06T23:59:00Z",
                                         started=None, completed=None))
        self.tick()
        lines = sorted((line["job_id"], line["self_hosted"], line["age_s"]) for _, _, line in self.queued_events())
        self.assertEqual(lines, [(71, True, 1200), (80, False, 60)])

    def test_only_queued_jobs_are_emitted(self):
        self.gh.set_jobs(REPO, 7, 1,
                         job(70, 7, name="validate"),
                         job(71, 7, name="apply", status="in_progress", conclusion=None, completed=None),
                         job(72, 7, name="deploy", status="waiting", conclusion=None, completed=None))
        self.tick()
        self.assertEqual(self.queued_events(), [])

    def test_nothing_queued_emits_nothing(self):
        self.gh.set_active_runs(REPO, "in_progress")
        self.tick()
        self.assertEqual(self.queued_events(), [])
        self.assertNotIn(f"/repos/{REPO}/actions/runs/7/attempts/1/jobs", self.gh.paths())

    def test_repeats_every_tick_with_a_growing_age_and_leaves_the_seen_set_alone(self):
        self.tick()
        self.tick(now=NOW + 300)
        self.assertEqual([line["age_s"] for _, _, line in self.queued_events(0)], [1200])
        self.assertEqual([line["age_s"] for _, _, line in self.queued_events(1)], [1500])
        self.assertEqual(State(self.dir).seen, {})

    def test_repos_outside_queue_watch_are_not_sampled(self):
        self.tick()
        sampled = {path for path, status in self.run_listings() if status != "completed"}
        self.assertEqual(sampled, {f"/repos/{REPO}/actions/runs"})
        self.assertEqual({s["repo"] for s, _, _ in self.queued_events()}, {REPO})

    def test_watched_repo_outside_owners_uses_its_own_cluster(self):
        self.cfg.queue_watch.append({"repo": "pitzilabs/site", "cluster": "pitzilabs"})
        self.gh.set_active_runs("pitzilabs/site", "queued", run(9, repo="pitzilabs/site", status="queued"))
        self.gh.set_active_runs("pitzilabs/site", "in_progress")
        self.gh.set_jobs("pitzilabs/site", 9, 1, job(90, 9, status="queued", created="2026-10-06T23:55:00Z"))
        self.tick()
        clusters = {s["repo"]: s["cluster"] for s, _, _ in self.queued_events()}
        self.assertEqual(clusters, {REPO: "lentago", "pitzilabs/site": "pitzilabs"})
        self.assertNotIn("/orgs/pitzilabs/repos", self.gh.paths())

    def test_failed_request_skips_only_that_repo(self):
        self.cfg.queue_watch.append({"repo": "lentago/drosera", "cluster": "lentago"})
        self.gh.set_active_runs("lentago/drosera", "queued")
        self.gh.set_active_runs("lentago/drosera", "in_progress", run(5, repo="lentago/drosera", status="in_progress"))
        self.gh.routes["/repos/lentago/drosera/actions/runs/5/attempts/1/jobs"] = (502, {}, "bad gateway")
        self.assertEqual(self.tick(), collector.OK)
        self.assertEqual({s["repo"] for s, _, _ in self.queued_events()}, {REPO})
        heads = [s for s in self.loki.pushes[0][2]["streams"] if s["stream"]["log_source"] == "github_branch_head"]
        self.assertEqual(len(heads), 2)  # the other passes are unaffected

    def test_unreadable_repo_404_skips_only_that_repo(self):
        self.cfg.queue_watch.insert(0, {"repo": "lentago/gone", "cluster": "lentago"})
        self.assertEqual(self.tick(), collector.OK)
        self.assertEqual({s["repo"] for s, _, _ in self.queued_events()}, {REPO})

    def test_rate_limit_backs_off(self):
        self.gh.routes[(f"/repos/{REPO}/actions/runs", "in_progress")] = (429, {"retry-after": "30"}, "")
        self.assertEqual(self.tick(), collector.BACKOFF)
        self.assertEqual(self.loki.pushes, [])

    def test_dry_run_prints_queued_jobs_without_pushing(self):
        out = io.StringIO()
        self.tick(dry_run=True, out=out)
        self.assertIn(f"github_actions_queued {REPO}", out.getvalue())
        self.assertEqual(self.loki.pushes, [])


if __name__ == "__main__":
    unittest.main()
