"""Render one transcript line into drosera's `claude_transcript` narration.

This is drosera's worker Alloy pipeline (alloy/runner-transcript.alloy, #71)
re-expressed in Python so a hook can ship the same stream from any host. The
egress posture is drosera ADR-0006 and must not widen here:

* only ``type == "assistant"`` lines; ``user`` lines carry tool RESULTS;
* ``thinking`` blocks are never selected;
* tool calls ship their NAME only, never ``input``;
* the drosera keyword scrub, then betula's token scrub, over what survives.

Returns ``None`` for anything that must not ship.
"""

import json
import re
from datetime import datetime, timezone

from .redact import redact

# Loki rejects lines over its limit (256 KB by default) with a permanent 400;
# a narration line that long is noise anyway, so it is cut well under that.
MAX_LINE = 16 * 1024

# drosera's Alloy stage.replace expression, verbatim (#325): only the value is
# captured and swapped.
_ALLOY_SCRUB = re.compile(
    r"(?i)(?:bearer|authorization|token|password|passwd|secret|api[_-]?key|private[_-]?key)"
    r"[\"'\s:=]+(?:(?:bearer|basic|token)\s+)?(\S+)"
)


def _alloy_scrub(text):
    def _sub(m):
        s, e = m.span(1)
        b = m.start(0)
        w = m.group(0)
        return w[: s - b] + "***REDACTED***" + w[e - b:]

    return _ALLOY_SCRUB.sub(_sub, text)


def parse_ts(value):
    """RFC 3339 -> ns since epoch, or None."""
    if not isinstance(value, str) or not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    # Python < 3.11 can't take more than 6 fractional digits.
    m = re.match(r"^(.*\.\d{6})\d+(.*)$", v)
    if m:
        v = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000) * 1000


def narration(raw_line):
    """``(ts_ns, line)`` for an assistant line with visible content, else None."""
    try:
        evt = json.loads(raw_line)
    except (ValueError, TypeError):
        return None
    if not isinstance(evt, dict) or evt.get("type") != "assistant":
        return None
    ts = parse_ts(evt.get("timestamp"))
    if ts is None:
        return None
    content = (evt.get("message") or {}).get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return None
    texts = " ".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ).strip()
    tools = ", ".join(
        str(b.get("name", "")) for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ).strip(", ")
    out = ""
    if texts:
        out = "[TEXT] " + texts
    if tools:
        out += ("  " if out else "") + "[TOOL] " + tools
    if not out.strip():
        return None
    out = _alloy_scrub(out)
    out, _ = redact(out)
    if len(out) > MAX_LINE:
        out = out[:MAX_LINE] + " [truncated]"
    return ts, out
