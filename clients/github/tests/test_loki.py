"""Loki pusher: batching, per-stream ordering, auth, and 400 handling."""

import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from github_collector.loki import LokiError, LokiPusher, build_body, push_url  # noqa: E402
from tests.fakes import FakeLoki  # noqa: E402

A = {"log_source": "github_actions_run", "repo": "lentago/a"}
B = {"log_source": "github_actions_job", "repo": "lentago/a"}


class BuildBodyTest(unittest.TestCase):
    def test_groups_by_labels_and_sorts_within_stream(self):
        body = build_body([(A, 30, "a3"), (B, 20, "b2"), (A, 10, "a1"), (B, 5, "b1")])
        streams = {s["stream"]["log_source"]: s["values"] for s in body["streams"]}
        self.assertEqual(streams["github_actions_run"], [["10", "a1"], ["30", "a3"]])
        self.assertEqual(streams["github_actions_job"], [["5", "b1"], ["20", "b2"]])

    def test_timestamps_are_string_nanoseconds(self):
        body = build_body([(A, 1_791_000_000_000_000_000, "x")])
        self.assertEqual(body["streams"][0]["values"][0][0], "1791000000000000000")


class PushTest(unittest.TestCase):
    def test_single_request_for_whole_batch_with_basic_auth(self):
        fake = FakeLoki()
        pusher = LokiPusher("https://logs-prod-042.grafana.net", "123456", "glc_x", transport=fake)
        self.assertEqual(pusher.push([(A, 1, "a"), (B, 2, "b"), (A, 3, "c")]), ("ok", None))
        self.assertEqual(len(fake.pushes), 1)
        url, headers, body = fake.pushes[0]
        self.assertEqual(url, "https://logs-prod-042.grafana.net/loki/api/v1/push")
        self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(b"123456:glc_x").decode())
        self.assertEqual(len(body["streams"]), 2)

    def test_push_url_normalisation(self):
        self.assertEqual(push_url("https://h/loki/api/v1/push"), "https://h/loki/api/v1/push")
        self.assertEqual(push_url("https://h/"), "https://h/loki/api/v1/push")
        self.assertEqual(push_url("h"), "https://h/loki/api/v1/push")

    def test_out_of_order_400_is_dropped_not_raised(self):
        msg = "entry for stream '{...}' has timestamp too old: 2026-09-01, oldest acceptable timestamp is: 2026-09-30"
        pusher = LokiPusher("https://h", "1", "t", transport=FakeLoki((400, msg)))
        result, detail = pusher.push([(A, 1, "a")])
        self.assertEqual(result, "dropped")
        self.assertIn("too old", detail)

    def test_other_400_raises(self):
        pusher = LokiPusher("https://h", "1", "t", transport=FakeLoki((400, "error parsing labels")))
        with self.assertRaises(LokiError):
            pusher.push([(A, 1, "a")])

    def test_5xx_and_401_raise(self):
        for status in (401, 429, 500, 503):
            pusher = LokiPusher("https://h", "1", "t", transport=FakeLoki((status, "nope")))
            with self.assertRaises(LokiError) as ctx:
                pusher.push([(A, 1, "a")])
            self.assertEqual(ctx.exception.status, status)

    def test_network_error_raises(self):
        def broken(url, headers, body):
            raise OSError("timed out")

        with self.assertRaises(LokiError):
            LokiPusher("https://h", "1", "t", transport=broken).push([(A, 1, "a")])


if __name__ == "__main__":
    unittest.main()
