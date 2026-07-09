"""Unit tests for the S3 handler — parse-and-ship end to end with fakes.

No boto3, no AWS creds, no live Axiom: the S3 object reader and the Axiom
client are both injected.
"""

import gzip
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alb_shipper.handler import iter_s3_records, process_event  # noqa: E402
from tests import sample_lines  # noqa: E402


def s3_event(*keys, bucket="solidago-dev-alb-access-logs"):
    return {
        "Records": [
            {"s3": {"bucket": {"name": bucket}, "object": {"key": k}}} for k in keys
        ]
    }


class CapturingAxiom:
    """Stands in for AxiomIngestClient.ship — records the events it is given."""

    def __init__(self):
        self.shipped = []

    def ship(self, events):
        batch = list(events)
        self.shipped.extend(batch)
        return len(batch)


class IterS3RecordsTest(unittest.TestCase):
    def test_extracts_bucket_and_key(self):
        event = s3_event("AWSLogs/1/elasticloadbalancing/a.log.gz")
        self.assertEqual(
            list(iter_s3_records(event)),
            [("solidago-dev-alb-access-logs", "AWSLogs/1/elasticloadbalancing/a.log.gz")],
        )

    def test_skips_incomplete_records(self):
        event = {"Records": [{"s3": {"bucket": {}, "object": {}}}]}
        self.assertEqual(list(iter_s3_records(event)), [])


class ProcessEventTest(unittest.TestCase):
    def test_gzipped_object_parsed_and_shipped(self):
        body = ("\n".join(sample_lines.ALL_LINES) + "\n").encode("utf-8")
        gzipped = gzip.compress(body)

        def reader(bucket, key):
            self.assertEqual(bucket, "solidago-dev-alb-access-logs")
            return gzipped

        axiom = CapturingAxiom()
        total = process_event(s3_event("a.log.gz"), axiom, object_reader=reader)

        self.assertEqual(total, len(sample_lines.ALL_LINES))
        self.assertEqual(len(axiom.shipped), len(sample_lines.ALL_LINES))
        # The HTTPS line's Host header survived the whole path.
        domains = {e.get("domain_name") for e in axiom.shipped}
        self.assertIn("lentago.dev", domains)

    def test_plain_text_object_also_supported(self):
        body = (sample_lines.HTTP_LINE + "\n").encode("utf-8")

        axiom = CapturingAxiom()
        total = process_event(
            s3_event("a.log"), axiom, object_reader=lambda b, k: body
        )
        self.assertEqual(total, 1)
        self.assertEqual(axiom.shipped[0]["client_ip"], "192.168.131.39")

    def test_multiple_objects_summed(self):
        gz = gzip.compress((sample_lines.HTTP_LINE + "\n").encode("utf-8"))
        axiom = CapturingAxiom()
        total = process_event(
            s3_event("a.log.gz", "b.log.gz"), axiom, object_reader=lambda b, k: gz
        )
        self.assertEqual(total, 2)


if __name__ == "__main__":
    unittest.main()
