"""Unit tests for the pure CloudWatch Logs payload parser — no AWS, no network."""

import base64
import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudwatch_shipper.parser import (  # noqa: E402
    decode_awslogs_data,
    epoch_ms_to_iso,
    event_to_record,
    iter_records,
    parse_awslogs_data,
)
from tests import sample_events  # noqa: E402


class DecodeAwslogsDataTest(unittest.TestCase):
    def test_round_trips_base64_gzip_json(self):
        data = sample_events.encode_awslogs(sample_events.DATA_PAYLOAD)
        self.assertEqual(decode_awslogs_data(data), sample_events.DATA_PAYLOAD)

    def test_malformed_base64_raises_value_error(self):
        with self.assertRaises(ValueError):
            decode_awslogs_data("not-valid-base64-gzip!!!")

    def test_valid_base64_but_not_gzip_raises_value_error(self):
        blob = base64.b64encode(b"plain bytes, no gzip magic").decode("ascii")
        with self.assertRaises(ValueError):
            decode_awslogs_data(blob)

    def test_gzip_of_non_json_raises_value_error(self):
        blob = base64.b64encode(gzip.compress(b"not json at all")).decode("ascii")
        with self.assertRaises(ValueError):
            decode_awslogs_data(blob)


class EpochMsToIsoTest(unittest.TestCase):
    def test_millisecond_precision_preserved(self):
        self.assertEqual(epoch_ms_to_iso(1784985600100), "2026-07-25T13:20:00.100Z")

    def test_whole_second(self):
        self.assertEqual(epoch_ms_to_iso(0), "1970-01-01T00:00:00.000Z")


class EventToRecordTest(unittest.TestCase):
    def test_structured_message_fields_are_merged(self):
        record = event_to_record(
            {"id": "e1", "timestamp": 1784985600100, "message": sample_events.ASK_QUERY_MESSAGE},
            log_group=sample_events.LOG_GROUP,
            log_stream=sample_events.LOG_STREAM,
        )
        self.assertEqual(record["event"], "ask_query")
        self.assertEqual(record["question"], "When is the next HOA board meeting?")
        self.assertEqual(record["matched"], True)
        self.assertEqual(record["latency_ms"], 812)
        # No raw "message" key when the line was a structured object.
        self.assertNotIn("message", record)

    def test_source_fields_preserved(self):
        record = event_to_record(
            {"id": "e1", "timestamp": 1784985600100, "message": sample_events.ASK_QUERY_MESSAGE},
            log_group=sample_events.LOG_GROUP,
            log_stream=sample_events.LOG_STREAM,
        )
        self.assertEqual(record["_time"], "2026-07-25T13:20:00.100Z")
        self.assertEqual(record["logGroup"], sample_events.LOG_GROUP)
        self.assertEqual(record["logStream"], sample_events.LOG_STREAM)
        self.assertEqual(record["cw_id"], "e1")

    def test_platform_line_kept_as_raw_message(self):
        record = event_to_record(
            {"id": "e2", "timestamp": 1753440000000, "message": sample_events.START_LINE},
            log_group=sample_events.LOG_GROUP,
            log_stream=sample_events.LOG_STREAM,
        )
        self.assertEqual(record["message"], sample_events.START_LINE)
        self.assertEqual(record["logGroup"], sample_events.LOG_GROUP)

    def test_json_scalar_message_falls_back_to_raw(self):
        # Valid JSON, but a string not an object — must not be spread.
        record = event_to_record(
            {"id": "e3", "timestamp": 1, "message": sample_events.JSON_SCALAR_MESSAGE},
        )
        self.assertEqual(record["message"], sample_events.JSON_SCALAR_MESSAGE)

    def test_source_fields_win_over_structured_line(self):
        # A structured line carrying its own _time / logGroup must NOT shadow the
        # authoritative CloudWatch delivery values.
        message = json.dumps({"_time": "1999-01-01T00:00:00Z", "logGroup": "spoofed", "q": "x"})
        record = event_to_record(
            {"id": "e4", "timestamp": 1784985600100, "message": message},
            log_group=sample_events.LOG_GROUP,
            log_stream=sample_events.LOG_STREAM,
        )
        self.assertEqual(record["_time"], "2026-07-25T13:20:00.100Z")
        self.assertEqual(record["logGroup"], sample_events.LOG_GROUP)
        self.assertEqual(record["q"], "x")


class IterRecordsTest(unittest.TestCase):
    def test_one_record_per_log_event(self):
        records = list(iter_records(sample_events.DATA_PAYLOAD))
        self.assertEqual(len(records), len(sample_events.DATA_PAYLOAD["logEvents"]))

    def test_mixed_structured_and_platform_lines(self):
        records = list(iter_records(sample_events.DATA_PAYLOAD))
        events = [r.get("event") for r in records]
        self.assertIn("ask_query", events)
        # The START and REPORT lines survive as raw messages.
        raw = [r.get("message") for r in records if "message" in r]
        self.assertIn(sample_events.START_LINE, raw)
        self.assertIn(sample_events.REPORT_LINE, raw)

    def test_control_message_yields_nothing(self):
        self.assertEqual(list(iter_records(sample_events.CONTROL_PAYLOAD)), [])

    def test_iter_records_is_a_generator(self):
        import types

        self.assertIsInstance(iter_records({"logEvents": []}), types.GeneratorType)

    def test_parse_awslogs_data_end_to_end(self):
        data = sample_events.encode_awslogs(sample_events.DATA_PAYLOAD)
        records = list(parse_awslogs_data(data))
        self.assertEqual(len(records), len(sample_events.DATA_PAYLOAD["logEvents"]))
        self.assertTrue(any(r.get("event") == "ask_query" for r in records))


if __name__ == "__main__":
    unittest.main()
