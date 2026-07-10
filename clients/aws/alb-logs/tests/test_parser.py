"""Unit tests for the pure ALB log parser — no AWS, no network."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alb_shipper.parser import parse_line, parse_lines  # noqa: E402
from tests import sample_lines  # noqa: E402


class ParseHttpLineTest(unittest.TestCase):
    def setUp(self):
        self.event = parse_line(sample_lines.HTTP_LINE)

    def test_time_becomes_axiom_time_key(self):
        self.assertEqual(self.event["_time"], "2018-07-02T22:23:00.186641Z")
        self.assertNotIn("time", self.event)

    def test_client_ip_and_port_split(self):
        self.assertEqual(self.event["client_ip"], "192.168.131.39")
        self.assertEqual(self.event["client_port"], 2817)

    def test_target_ip_and_port_split(self):
        self.assertEqual(self.event["target_ip"], "10.0.0.1")
        self.assertEqual(self.event["target_port"], 80)

    def test_request_split_into_parts(self):
        self.assertEqual(self.event["request_method"], "GET")
        self.assertEqual(self.event["request_url"], "http://www.example.com:80/")
        self.assertEqual(self.event["request_protocol"], "HTTP/1.1")

    def test_user_agent(self):
        self.assertEqual(self.event["user_agent"], "curl/7.46.0")

    def test_status_codes_are_ints(self):
        self.assertEqual(self.event["elb_status_code"], 200)
        self.assertEqual(self.event["target_status_code"], 200)

    def test_byte_counts_are_ints(self):
        self.assertEqual(self.event["received_bytes"], 34)
        self.assertEqual(self.event["sent_bytes"], 366)

    def test_processing_times_are_floats(self):
        self.assertEqual(self.event["target_processing_time"], 0.001)

    def test_empty_ssl_fields_dropped(self):
        # ssl_cipher / ssl_protocol were "-" and must not be emitted.
        self.assertNotIn("ssl_cipher", self.event)
        self.assertNotIn("ssl_protocol", self.event)

    def test_no_literal_dash_values(self):
        self.assertNotIn("-", self.event.values())


class ParseHttpsLineTest(unittest.TestCase):
    def setUp(self):
        self.event = parse_line(sample_lines.HTTPS_LINE)

    def test_domain_name_is_host_breakdown_signal(self):
        self.assertEqual(self.event["domain_name"], "lentago.dev")

    def test_ssl_fields_present(self):
        self.assertEqual(self.event["ssl_protocol"], "TLSv1.2")
        self.assertEqual(self.event["ssl_cipher"], "ECDHE-RSA-AES128-GCM-SHA256")

    def test_request_url_and_proto(self):
        self.assertEqual(self.event["request_method"], "GET")
        self.assertEqual(self.event["request_url"], "https://lentago.dev:443/pricing")
        self.assertEqual(self.event["request_protocol"], "HTTP/2.0")

    def test_user_agent_with_spaces(self):
        self.assertEqual(self.event["user_agent"], "Mozilla/5.0 (X11; Linux x86_64)")

    def test_chosen_cert_arn_present(self):
        self.assertIn("chosen_cert_arn", self.event)


class ParseEscapedUserAgentTest(unittest.TestCase):
    def test_escaped_quotes_unescaped(self):
        event = parse_line(sample_lines.ESCAPED_UA_LINE)
        self.assertEqual(event["user_agent"], 'Bot/1.0 ("weird" agent)')
        self.assertEqual(event["domain_name"], "icecreamtofightwith.com")
        self.assertEqual(event["ssl_protocol"], "TLSv1.3")


class ParseDroppedRequestTest(unittest.TestCase):
    def setUp(self):
        self.event = parse_line(sample_lines.DROPPED_REQUEST_LINE)

    def test_no_target_chosen(self):
        self.assertNotIn("target_ip", self.event)
        self.assertNotIn("target_port", self.event)

    def test_dropped_request_has_no_method(self):
        self.assertNotIn("request_method", self.event)
        self.assertNotIn("request_url", self.event)

    def test_elb_status_code(self):
        self.assertEqual(self.event["elb_status_code"], 460)

    def test_conn_trace_id_present_on_newer_line(self):
        self.assertEqual(self.event["conn_trace_id"], "TID_1234567890abcdef")

    def test_missing_target_status_code_dropped(self):
        self.assertNotIn("target_status_code", self.event)


class ParseEdgeCaseTest(unittest.TestCase):
    def test_blank_line_returns_none(self):
        self.assertIsNone(parse_line("   "))

    def test_too_few_fields_raises(self):
        with self.assertRaises(ValueError):
            parse_line("http 2018-07-02T22:23:00Z app/x too few fields")

    def test_extra_trailing_fields_tolerated(self):
        # AWS appends new trailing fields over time; a real 34-field line must
        # parse (not raise) and still yield the leading visitor-source fields.
        event = parse_line(sample_lines.EXTENDED_TRAILING_LINE)
        self.assertEqual(event["client_ip"], "203.0.113.55")
        self.assertEqual(event["domain_name"], "lentago.dev")
        self.assertEqual(event["request_url"], "https://lentago.dev:443/")
        self.assertEqual(event["elb_status_code"], 200)
        # The last named field (conn_trace_id) still maps correctly; the four
        # surplus trailing tokens are dropped, not mis-assigned.
        self.assertEqual(event["conn_trace_id"], "TID_bd7b1888dbb28d409aff3ec7256f89f9")

    def test_parse_lines_skips_blanks_and_yields_all(self):
        lines = [""] + sample_lines.ALL_LINES + ["  "]
        events = list(parse_lines(lines))
        self.assertEqual(len(events), len(sample_lines.ALL_LINES))

    def test_parse_lines_is_a_generator(self):
        import types

        self.assertIsInstance(parse_lines(iter([])), types.GeneratorType)


if __name__ == "__main__":
    unittest.main()
