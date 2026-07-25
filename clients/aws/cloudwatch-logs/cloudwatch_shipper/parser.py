"""Decode CloudWatch Logs subscription-filter payloads into Axiom events.

A CloudWatch Logs subscription filter invokes its destination Lambda with a
single ``awslogs.data`` field: base64-encoded, gzip-compressed JSON. Decoded,
it is an object with ``logGroup``, ``logStream``, a ``messageType`` and a
``logEvents`` array of ``{id, timestamp, message}``. See:
https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/SubscriptionFilters.html

This module is pure — no I/O, no AWS, no Axiom — so it is trivially unit
testable over real-shape payloads. Each log event becomes one Axiom record:

* ``timestamp`` (epoch ms) → ``_time`` (RFC3339), so Axiom uses the event's own
  time rather than ingest time.
* ``logGroup`` / ``logStream`` are preserved as fields so several source log
  groups can share one dataset and stay disambiguable in a query.
* ``message`` is parsed as JSON when it parses to an object — the whole point of
  this collector is the structured ``ask_query`` lines the Ask handler emits —
  and its fields are merged in. A line that is not a JSON object (Lambda's
  unstructured ``START`` / ``END`` / ``REPORT`` platform lines, or any plain
  string) is kept verbatim as ``message``. An unparseable line never fails the
  batch.

The CloudWatch source fields (``_time``, ``logGroup``, ``logStream``, ``cw_id``)
are authoritative: they are applied *over* the parsed message, so a structured
line carrying its own ``_time`` or ``logGroup`` cannot shadow (or spoof) the
delivery's real timestamp or its source-tracking labels.
"""

import base64
import datetime
import gzip
import json

# messageType CloudWatch uses for real log deliveries. It also sends a
# CONTROL_MESSAGE heartbeat when validating a subscription's destination; those
# carry no real logs and are skipped.
DATA_MESSAGE = "DATA_MESSAGE"


def decode_awslogs_data(data):
    """Decode the base64 + gzip ``awslogs.data`` blob into the payload dict.

    Raises :class:`ValueError` (wrapping the underlying decode error) if the
    blob is not valid base64-encoded gzip-compressed JSON, so the Lambda
    surfaces a malformed delivery and lets it be retried rather than shipping a
    half-decoded batch.
    """
    try:
        compressed = base64.b64decode(data)
        raw = gzip.decompress(compressed)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"malformed awslogs.data payload: {exc}") from exc


def epoch_ms_to_iso(timestamp_ms):
    """Convert a CloudWatch epoch-millisecond timestamp to an RFC3339 UTC string.

    Millisecond precision is preserved (``...T00:00:00.100Z``). Pure — depends
    only on the argument, never on the current clock.
    """
    seconds = timestamp_ms / 1000
    dt = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_message(message):
    """Return the JSON object encoded in ``message``, or ``None``.

    ``None`` means "not a structured object" — the message is a plain string, a
    JSON scalar/array, or simply not JSON at all. Callers keep the raw string in
    that case. Never raises: an unparseable line must not fail the batch.
    """
    if not isinstance(message, str):
        return None
    try:
        value = json.loads(message)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def event_to_record(log_event, log_group=None, log_stream=None):
    """Map one ``{id, timestamp, message}`` log event to an Axiom record.

    The structured message's own fields (if any) form the base of the record;
    the CloudWatch source fields are then applied over the top so they are
    always present and win any collision.
    """
    parsed = _parse_message(log_event.get("message"))
    record = dict(parsed) if parsed is not None else {"message": log_event.get("message")}

    # Apply the CloudWatch source fields last, overwriting any same-named field a
    # structured line carried: the delivery's timestamp and source labels are
    # authoritative and cannot be shadowed (or spoofed) by the log line itself.
    timestamp = log_event.get("timestamp")
    if timestamp is not None:
        record["_time"] = epoch_ms_to_iso(timestamp)
    if log_group is not None:
        record["logGroup"] = log_group
    if log_stream is not None:
        record["logStream"] = log_stream
    if log_event.get("id") is not None:
        record["cw_id"] = log_event["id"]
    return record


def iter_records(payload):
    """Yield one Axiom record per log event in a decoded CloudWatch payload.

    A non-``DATA_MESSAGE`` payload (e.g. the CONTROL_MESSAGE heartbeat) yields
    nothing. This is a generator so a large batch streams into the shipper
    without materialising every record at once.
    """
    if payload.get("messageType") != DATA_MESSAGE:
        return
    log_group = payload.get("logGroup")
    log_stream = payload.get("logStream")
    for log_event in payload.get("logEvents", []):
        yield event_to_record(log_event, log_group=log_group, log_stream=log_stream)


def parse_awslogs_data(data):
    """Decode an ``awslogs.data`` blob and yield its Axiom records.

    Convenience wrapper over :func:`decode_awslogs_data` + :func:`iter_records`.
    """
    yield from iter_records(decode_awslogs_data(data))
