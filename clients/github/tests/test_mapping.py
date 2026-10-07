"""Payload mapping: the feed contract drosera consumes (#234, #204, #251)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from github_collector import mapping  # noqa: E402
from tests.fakes import job, run  # noqa: E402

RUN_KEYS = [
    "repo", "workflow", "workflow_path", "run_id", "run_attempt", "run_number", "event",
    "branch", "head_sha", "actor", "conclusion", "created_at", "run_started_at",
    "updated_at", "duration_s", "html_url",
]
JOB_KEYS = [
    "repo", "workflow", "run_id", "run_attempt", "job_id", "job_name", "conclusion",
    "created_at", "started_at", "completed_at", "queue_s", "duration_s", "runner_name",
    "runner_group", "runner_labels", "self_hosted", "html_url",
]


def ns(iso):
    return mapping.to_ns(mapping.parse_ts(iso))


class LabelsTest(unittest.TestCase):
    def test_run_labels_are_exactly_the_six_label_contract(self):
        labels, _, _ = mapping.run_event(run(1), "lentago/kalmia", "lentago")
        self.assertEqual(labels, {
            "log_source": "github_actions_run", "cluster": "lentago", "source": "github_actions",
            "pipeline": "ci", "stage": "run", "repo": "lentago/kalmia",
        })

    def test_job_labels_are_exactly_the_six_label_contract(self):
        labels, _, _ = mapping.job_event(job(10, 1), run(1), "lentago/kalmia", "lentago")
        self.assertEqual(labels, {
            "log_source": "github_actions_job", "cluster": "lentago", "source": "github_actions",
            "pipeline": "ci", "stage": "job", "repo": "lentago/kalmia",
        })


class RunPayloadTest(unittest.TestCase):
    def test_run_payload_keys_and_values(self):
        _, ts, line = mapping.run_event(run(5, attempt=2), "lentago/kalmia", "lentago")
        payload = json.loads(line)
        self.assertEqual(list(payload), RUN_KEYS)
        self.assertEqual(payload["run_id"], 5)
        self.assertEqual(payload["run_attempt"], 2)
        self.assertEqual(payload["workflow"], "CI")
        self.assertEqual(payload["workflow_path"], ".github/workflows/ci.yml")
        self.assertEqual(payload["branch"], "main")
        self.assertEqual(payload["actor"], "cpitzi")
        self.assertEqual(payload["duration_s"], 180)  # updated_at − run_started_at

    def test_run_is_stamped_at_updated_at(self):
        _, ts, _ = mapping.run_event(run(5, updated="2026-10-06T11:22:33Z"), "lentago/kalmia", "lentago")
        self.assertEqual(ts, ns("2026-10-06T11:22:33Z"))

    def test_line_is_compact_single_line_json(self):
        _, _, line = mapping.run_event(run(5), "lentago/kalmia", "lentago")
        self.assertNotIn("\n", line)
        self.assertNotIn(", ", line)


class JobPayloadTest(unittest.TestCase):
    def test_job_payload_keys_and_values(self):
        _, _, line = mapping.job_event(job(10, 5, attempt=2), run(5, attempt=2), "lentago/kalmia", "lentago")
        payload = json.loads(line)
        self.assertEqual(list(payload), JOB_KEYS)
        self.assertEqual(payload["job_id"], 10)
        self.assertEqual(payload["run_attempt"], 2)
        self.assertEqual(payload["job_name"], "build")
        self.assertEqual(payload["queue_s"], 10)       # started_at − created_at
        self.assertEqual(payload["duration_s"], 120)   # completed_at − started_at
        self.assertEqual(payload["runner_group"], "GitHub Actions")
        self.assertEqual(payload["runner_labels"], ["ubuntu-latest"])
        self.assertFalse(payload["self_hosted"])

    def test_self_hosted_from_runner_labels(self):
        _, _, line = mapping.job_event(job(10, 5, labels=("self-hosted", "linux")), run(5), "lentago/kalmia", "lentago")
        self.assertTrue(json.loads(line)["self_hosted"])

    def test_job_is_stamped_at_completed_at(self):
        _, ts, _ = mapping.job_event(job(10, 5, completed="2026-10-06T10:02:59Z"), run(5), "lentago/kalmia", "lentago")
        self.assertEqual(ts, ns("2026-10-06T10:02:59Z"))

    def test_skipped_job_is_still_emitted(self):
        skipped = job(11, 5, conclusion="skipped", started="2026-10-06T10:00:06Z", completed="2026-10-06T10:00:06Z")
        _, _, line = mapping.job_event(skipped, run(5), "lentago/kalmia", "lentago")
        self.assertEqual(json.loads(line)["conclusion"], "skipped")

    def test_job_without_completed_at_falls_back_to_run_updated_at(self):
        never_ran = job(12, 5, conclusion="cancelled", started=None, completed=None)
        _, ts, line = mapping.job_event(never_ran, run(5, updated="2026-10-06T10:09:00Z"), "lentago/kalmia", "lentago")
        self.assertEqual(ts, ns("2026-10-06T10:09:00Z"))
        self.assertIsNone(json.loads(line)["duration_s"])


class BranchHeadPayloadTest(unittest.TestCase):
    def test_labels_and_line(self):
        branch = {"commit": {"sha": "f" * 40, "html_url": "https://x/c",
                             "commit": {"committer": {"date": "2026-10-06T22:00:00Z"}}}}
        lbls, ts, line = mapping.branch_head_event(branch, "main", "lentago/.github", "lentago", 1791331200)
        self.assertEqual(lbls, {"log_source": "github_branch_head", "cluster": "lentago", "repo": "lentago/.github"})
        self.assertEqual(ts, 1791331200 * 1_000_000_000)
        self.assertEqual(json.loads(line), {
            "branch": "main", "sha": "f" * 40, "committed_at": "2026-10-06T22:00:00Z",
            "url": "https://x/c", "observed_at": "2026-10-07T00:00:00Z",
        })


if __name__ == "__main__":
    unittest.main()
