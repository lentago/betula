"""Real-shape CloudWatch Logs subscription-filter payloads for the unit tests.

Covers the cases the collector must handle: a structured ``ask_query`` line
(the whole reason this collector exists), the unstructured Lambda platform lines
(``START`` / ``END`` / ``REPORT``), a JSON message that is not an object (must
fall back to raw), the CONTROL_MESSAGE heartbeat CloudWatch sends to validate a
subscription, and a helper to base64+gzip a payload the way AWS delivers it.
"""

import base64
import gzip
import json

# The structured event the Ask handler emits (site-pondviewlane-com#23). Its
# fields must be merged into the Axiom record, not buried in a raw string.
ASK_QUERY_MESSAGE = json.dumps(
    {
        "event": "ask_query",
        "question": "When is the next HOA board meeting?",
        "matched": True,
        "latency_ms": 812,
        "cost_usd": 0.0021,
    }
)

# Lambda's unstructured platform lines — valid log events, NOT JSON objects.
START_LINE = "START RequestId: 5f9c0e2a-1b3d-4e5f-8a90-abcdef012345 Version: $LATEST"
END_LINE = "END RequestId: 5f9c0e2a-1b3d-4e5f-8a90-abcdef012345"
REPORT_LINE = (
    "REPORT RequestId: 5f9c0e2a-1b3d-4e5f-8a90-abcdef012345\t"
    "Duration: 812.44 ms\tBilled Duration: 813 ms\t"
    "Memory Size: 256 MB\tMax Memory Used: 118 MB"
)

# A JSON message that is NOT an object — must fall back to a raw message, not be
# spread into the record.
JSON_SCALAR_MESSAGE = json.dumps("plain string that happens to be json-quoted")

LOG_GROUP = "/aws/lambda/ask-pondview"
LOG_STREAM = "2026/07/25/[$LATEST]abcdef0123456789abcdef0123456789"

# timestamp is epoch milliseconds. 1784985600100 ms → 2026-07-25T13:20:00.100Z.
DATA_PAYLOAD = {
    "messageType": "DATA_MESSAGE",
    "owner": "365184644049",
    "logGroup": LOG_GROUP,
    "logStream": LOG_STREAM,
    "subscriptionFilters": ["ask-pondview-to-axiom"],
    "logEvents": [
        {"id": "38900000000000000000000000001", "timestamp": 1784985600000, "message": START_LINE},
        {"id": "38900000000000000000000000002", "timestamp": 1784985600100, "message": ASK_QUERY_MESSAGE},
        {"id": "38900000000000000000000000003", "timestamp": 1784985600200, "message": JSON_SCALAR_MESSAGE},
        {"id": "38900000000000000000000000004", "timestamp": 1784985600900, "message": REPORT_LINE},
    ],
}

# CloudWatch sends this when it validates the subscription's destination. It
# carries no real logs and must be skipped.
CONTROL_PAYLOAD = {
    "messageType": "CONTROL_MESSAGE",
    "owner": "CloudwatchLogs",
    "logGroup": "",
    "logStream": "",
    "subscriptionFilters": [],
    "logEvents": [
        {
            "id": "",
            "timestamp": 1784985600000,
            "message": "CWL CONTROL MESSAGE: Checking health of destination Lambda.",
        }
    ],
}


def encode_awslogs(payload):
    """Base64-encode gzip-compressed JSON exactly as a subscription filter does."""
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def awslogs_event(payload):
    """Wrap a payload in the ``{"awslogs": {"data": ...}}`` Lambda envelope."""
    return {"awslogs": {"data": encode_awslogs(payload)}}
