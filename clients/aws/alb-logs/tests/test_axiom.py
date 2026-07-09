"""Unit tests for the Axiom ingest client — framing and batching, no network."""

import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alb_shipper.axiom import AxiomIngestClient, AxiomIngestError  # noqa: E402


class FakeTransport:
    """Records every POST and returns a scripted (status, body)."""

    def __init__(self, status=200, body="{}"):
        self.status = status
        self.body = body
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.status, self.body


class AxiomFramingTest(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.client = AxiomIngestClient(
            dataset="cjp-solidago-alb",
            token="xaat-secret",
            transport=self.transport,
        )

    def test_ingest_url(self):
        self.assertEqual(
            self.client.ingest_url,
            "https://api.axiom.co/v1/datasets/cjp-solidago-alb/ingest",
        )

    def test_ship_returns_count_and_posts_once(self):
        sent = self.client.ship([{"a": 1}, {"b": 2}])
        self.assertEqual(sent, 2)
        self.assertEqual(len(self.transport.calls), 1)

    def test_headers_carry_bearer_and_gzip_ndjson(self):
        self.client.ship([{"a": 1}])
        headers = self.transport.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer xaat-secret")
        self.assertEqual(headers["Content-Type"], "application/x-ndjson")
        self.assertEqual(headers["Content-Encoding"], "gzip")

    def test_body_is_gzipped_ndjson(self):
        self.client.ship([{"a": 1}, {"b": 2}])
        raw = self.transport.calls[0]["body"]
        # gzip magic bytes
        self.assertEqual(raw[:2], b"\x1f\x8b")
        decoded = gzip.decompress(raw).decode("utf-8")
        lines = decoded.split("\n")
        self.assertEqual([json.loads(x) for x in lines], [{"a": 1}, {"b": 2}])


class AxiomBatchingTest(unittest.TestCase):
    def test_batches_at_batch_size(self):
        transport = FakeTransport()
        client = AxiomIngestClient(
            dataset="d", token="t", batch_size=2, transport=transport
        )
        sent = client.ship([{"n": i} for i in range(5)])
        self.assertEqual(sent, 5)
        # 5 events, batch_size 2 -> batches of 2, 2, 1
        self.assertEqual(len(transport.calls), 3)
        sizes = [
            len(gzip.decompress(c["body"]).decode().split("\n"))
            for c in transport.calls
        ]
        self.assertEqual(sizes, [2, 2, 1])

    def test_empty_input_posts_nothing(self):
        transport = FakeTransport()
        client = AxiomIngestClient(dataset="d", token="t", transport=transport)
        self.assertEqual(client.ship([]), 0)
        self.assertEqual(len(transport.calls), 0)


class AxiomErrorTest(unittest.TestCase):
    def test_non_2xx_raises(self):
        transport = FakeTransport(status=401, body="unauthorized")
        client = AxiomIngestClient(dataset="d", token="bad", transport=transport)
        with self.assertRaises(AxiomIngestError) as ctx:
            client.ship([{"a": 1}])
        self.assertEqual(ctx.exception.status, 401)


class AxiomConfigTest(unittest.TestCase):
    def test_requires_dataset_and_token(self):
        with self.assertRaises(ValueError):
            AxiomIngestClient(dataset="", token="t")
        with self.assertRaises(ValueError):
            AxiomIngestClient(dataset="d", token="")

    def test_from_env_reads_dataset_and_token(self):
        env = {"AXIOM_DATASET": "cjp-solidago-alb", "AXIOM_API_TOKEN": "xaat-x"}
        client = AxiomIngestClient.from_env(env=env, transport=FakeTransport())
        self.assertEqual(client.dataset, "cjp-solidago-alb")
        self.assertEqual(client.token, "xaat-x")

    def test_from_env_falls_back_to_ingest_token_var(self):
        env = {"AXIOM_DATASET": "d", "AXIOM_INGEST_TOKEN": "xaat-y"}
        client = AxiomIngestClient.from_env(env=env, transport=FakeTransport())
        self.assertEqual(client.token, "xaat-y")

    def test_from_env_missing_token_raises(self):
        with self.assertRaises(ValueError):
            AxiomIngestClient.from_env(env={"AXIOM_DATASET": "d"})


if __name__ == "__main__":
    unittest.main()
