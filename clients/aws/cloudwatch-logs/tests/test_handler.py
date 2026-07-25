"""Unit tests for the Lambda handler — decode-and-ship end to end with a fake.

No AWS creds, no live Axiom: the Axiom client is injected. The subscription
filter delivers log data inline, so there is no object reader to fake.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudwatch_shipper.handler import process_event  # noqa: E402
from tests import sample_events  # noqa: E402


class CapturingAxiom:
    """Stands in for AxiomIngestClient.ship — records the events it is given."""

    def __init__(self):
        self.shipped = []

    def ship(self, events):
        batch = list(events)
        self.shipped.extend(batch)
        return len(batch)


class ProcessEventTest(unittest.TestCase):
    def test_data_message_decoded_parsed_and_shipped(self):
        event = sample_events.awslogs_event(sample_events.DATA_PAYLOAD)
        axiom = CapturingAxiom()
        total = process_event(event, axiom)

        self.assertEqual(total, len(sample_events.DATA_PAYLOAD["logEvents"]))
        self.assertEqual(len(axiom.shipped), total)
        # The structured ask_query line's fields survived the whole path.
        events = {r.get("event") for r in axiom.shipped}
        self.assertIn("ask_query", events)
        questions = {r.get("question") for r in axiom.shipped}
        self.assertIn("When is the next HOA board meeting?", questions)
        # Source fields present on every record for cross-source disambiguation.
        self.assertTrue(all(r.get("logGroup") == sample_events.LOG_GROUP for r in axiom.shipped))

    def test_control_message_ships_nothing(self):
        event = sample_events.awslogs_event(sample_events.CONTROL_PAYLOAD)
        axiom = CapturingAxiom()
        self.assertEqual(process_event(event, axiom), 0)
        self.assertEqual(axiom.shipped, [])

    def test_missing_awslogs_data_ships_nothing(self):
        axiom = CapturingAxiom()
        self.assertEqual(process_event({}, axiom), 0)
        self.assertEqual(axiom.shipped, [])

    def test_malformed_payload_raises(self):
        # A malformed blob must propagate so Lambda retries, not silently drop.
        event = {"awslogs": {"data": "not-valid-base64-gzip!!!"}}
        axiom = CapturingAxiom()
        with self.assertRaises(ValueError):
            process_event(event, axiom)
        self.assertEqual(axiom.shipped, [])

    def test_platform_lines_do_not_break_the_batch(self):
        # START/END/REPORT lines ride alongside the structured line and ship as
        # raw messages rather than failing the batch.
        event = sample_events.awslogs_event(sample_events.DATA_PAYLOAD)
        axiom = CapturingAxiom()
        process_event(event, axiom)
        raw = [r.get("message") for r in axiom.shipped if "message" in r]
        self.assertIn(sample_events.START_LINE, raw)
        self.assertIn(sample_events.REPORT_LINE, raw)


if __name__ == "__main__":
    unittest.main()
