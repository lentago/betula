"""Parse AWS Application Load Balancer (ALB) access-log lines into events.

ALB access logs are space-delimited with a fixed field order, where several
fields are double-quoted (and may therefore contain spaces) and every empty
field is written as a bare ``-``. See:
https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-access-logs.html

The parser is pure — no I/O, no AWS, no Axiom — so it is trivially unit
testable over real sample lines. It emphasises the visitor-*source* signal
fields that CloudWatch metrics cannot carry: the client IP (split from
``client:port``), ``user_agent``, the ``request`` line (split into method /
url / protocol), and ``domain_name`` (the Host header, which gives the
per-site breakdown on the drosera pane).
"""

import re

# The documented ALB access-log field order. Quoted fields are noted in the
# comments; the tokenizer strips the quotes so the names below are positional.
# The final `conn_trace_id` field is only present on newer ALB log versions,
# so lines with one fewer token are still accepted (see parse_line).
ALB_FIELDS = (
    "type",                      # http | https | h2 | grpcs | ws | wss
    "time",                      # ISO 8601 — used as the Axiom event _time
    "elb",                       # load balancer resource id
    "client_port",               # "client:port" — split below
    "target_port",               # "target:port" — split below (may be -)
    "request_processing_time",
    "target_processing_time",
    "response_processing_time",
    "elb_status_code",
    "target_status_code",
    "received_bytes",
    "sent_bytes",
    "request",                   # quoted: "METHOD URL PROTOCOL"
    "user_agent",                # quoted
    "ssl_cipher",
    "ssl_protocol",
    "target_group_arn",
    "trace_id",                  # quoted
    "domain_name",               # quoted — the TLS SNI / Host header
    "chosen_cert_arn",           # quoted
    "matched_rule_priority",
    "request_creation_time",
    "actions_executed",          # quoted
    "redirect_url",              # quoted
    "error_reason",              # quoted
    "target_port_list",          # quoted: "target:port ..."
    "target_status_code_list",   # quoted
    "classification",            # quoted
    "classification_reason",     # quoted
    "conn_trace_id",             # newer field; may be absent
)

# Minimum tokens for a well-formed line: the documented fields without the
# optional trailing conn_trace_id. AWS *appends* new trailing fields to the ALB
# log format over time (a real 2026 line already carries 34 tokens, past the 30
# named above), so there is deliberately no upper bound -- extra trailing tokens
# are tolerated and dropped (see parse_line). A line with FEWER than this is
# malformed.
_MIN_FIELDS = len(ALB_FIELDS) - 1  # without conn_trace_id

# Match either a double-quoted string (allowing backslash escapes such as the
# \" that ALB writes inside user_agent) or a run of non-space characters.
_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|(\S+)')

# Fields that ALB serialises as integers; kept as ints when present.
_INT_FIELDS = (
    "elb_status_code",
    "target_status_code",
    "received_bytes",
    "sent_bytes",
    "matched_rule_priority",
)

# Fields that ALB serialises as floating-point seconds. A value of -1 means the
# request did not reach that processing stage; it is preserved as-is.
_FLOAT_FIELDS = (
    "request_processing_time",
    "target_processing_time",
    "response_processing_time",
)


def _tokenize(line):
    """Split one ALB log line into raw string tokens, honouring quoting."""
    tokens = []
    for match in _TOKEN_RE.finditer(line):
        quoted, bare = match.group(1), match.group(2)
        if quoted is not None:
            # Unescape the \" and \\ sequences ALB uses inside quoted fields.
            tokens.append(quoted.replace('\\"', '"').replace("\\\\", "\\"))
        else:
            tokens.append(bare)
    return tokens


def _split_host_port(value):
    """Split a ``host:port`` token into (host, port) — either side may be None.

    Returns (None, None) for the ``-`` empty marker. Handles the ``-1`` port
    ALB writes when no target was chosen, and leaves IPv6 hosts intact by
    splitting only on the final colon.
    """
    if value is None:
        return None, None
    host, sep, port = value.rpartition(":")
    if not sep:
        return value or None, None
    return (host or None), (port or None)


def _split_request(value):
    """Split the ``"METHOD URL PROTOCOL"`` request line into its three parts.

    Malformed or truncated request lines (ALB writes them verbatim, including
    ``- - -`` for a dropped request) degrade gracefully to whatever parts are
    present rather than raising.
    """
    if value is None:
        return None, None, None
    parts = value.split(" ")
    method = parts[0] if len(parts) > 0 else None
    url = parts[1] if len(parts) > 1 else None
    protocol = parts[2] if len(parts) > 2 else None
    return (
        None if method in (None, "-") else method,
        None if url in (None, "-") else url,
        None if protocol in (None, "-") else protocol,
    )


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def parse_line(line):
    """Parse a single ALB access-log line into a structured event dict.

    Empty fields (``-``) are dropped rather than emitted as the literal
    ``"-"``, so downstream Axiom queries never have to special-case them. The
    ALB ``time`` field is mapped to ``_time`` so Axiom uses the request
    timestamp (not ingest time) as the event time.

    Returns ``None`` for blank lines. Raises ``ValueError`` only if the line
    has fewer fields than the known ALB schema; extra trailing fields (which
    AWS adds to the format over time) are tolerated and ignored.
    """
    line = line.strip()
    if not line:
        return None

    tokens = _tokenize(line)
    # No upper bound: AWS appends new trailing fields to the ALB log format, so
    # real lines can carry more tokens than ALB_FIELDS names. The visitor-source
    # fields all live in the stable leading positions, and dict(zip(...)) below
    # keeps only the named ones -- surplus trailing tokens fall away. Only a line
    # too SHORT to cover the known schema is malformed.
    if len(tokens) < _MIN_FIELDS:
        raise ValueError(
            f"expected at least {_MIN_FIELDS} ALB fields, got {len(tokens)}"
        )

    raw = dict(zip(ALB_FIELDS, tokens))

    event = {}
    for name, value in raw.items():
        # Normalise the bare "-" empty marker to a dropped field.
        if value == "-":
            continue
        if name in _INT_FIELDS:
            event[name] = _coerce_int(value)
        elif name in _FLOAT_FIELDS:
            event[name] = _coerce_float(value)
        else:
            event[name] = value

    # Axiom uses _time as the canonical event timestamp.
    if "time" in event:
        event["_time"] = event.pop("time")

    # Split client:port and target:port into their source-signal components.
    client_ip, client_port = _split_host_port(raw.get("client_port"))
    if client_ip is not None:
        event["client_ip"] = client_ip
    if client_port is not None:
        event["client_port"] = _coerce_int(client_port)
    else:
        event.pop("client_port", None)

    target_ip, target_port = _split_host_port(
        None if raw.get("target_port") == "-" else raw.get("target_port")
    )
    if target_ip is not None:
        event["target_ip"] = target_ip
    if target_port is not None:
        event["target_port"] = _coerce_int(target_port)
    else:
        event.pop("target_port", None)

    # Split the request line into method / url / protocol.
    method, url, protocol = _split_request(raw.get("request"))
    if method is not None:
        event["request_method"] = method
    if url is not None:
        event["request_url"] = url
    if protocol is not None:
        event["request_protocol"] = protocol

    return event


def parse_lines(lines):
    """Parse an iterable of ALB log lines, yielding one event dict per record.

    Blank lines are skipped. This is a generator so a large gunzipped object
    can be streamed line-by-line into the shipper without materialising the
    whole file.
    """
    for line in lines:
        event = parse_line(line)
        if event is not None:
            yield event
