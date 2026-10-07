"""Small stdlib Loki pusher: many events, per-event timestamps, one request.

drosera's clients/loki_push.py stamps ``time.time_ns()`` and sends one event
per call; this collector needs neither, so it carries its own rather than
change that shared helper (#113).
"""

import base64
import json
import urllib.error
import urllib.request

PUSH_PATH = "/loki/api/v1/push"

# Loki answers 400 for entries outside its accepted time window. These are
# permanent for that entry, so they're logged and dropped, not retried. In a
# batch Loki still ingests the valid entries alongside the rejected ones.
_DROPPABLE_400_MARKERS = (
    "out of order",
    "too far behind",
    "timestamp too old",
    "greater_than_max_sample_age",
    "too far in the future",
)


class LokiError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"Loki push failed: HTTP {status}: {str(body)[:300]}")
        self.status = status
        self.body = body


def push_url(url):
    url = url.rstrip("/")
    if not url.startswith("https://"):
        url = "https://" + url.split("://", 1)[-1]
    return url if url.endswith(PUSH_PATH) else url + PUSH_PATH


def build_body(events):
    """Group ``(labels, ts_ns, line)`` events into Loki streams.

    Entries are sorted by timestamp within each stream; streams are ordered by
    their labels so the request is deterministic.
    """
    streams = {}
    for labels, ts_ns, line in events:
        streams.setdefault(tuple(sorted(labels.items())), []).append((ts_ns, line))
    out = []
    for key in sorted(streams):
        values = sorted(streams[key], key=lambda v: v[0])
        out.append({"stream": dict(key), "values": [[str(ts), line] for ts, line in values]})
    return {"streams": out}


def is_droppable_400(body):
    text = str(body).lower()
    return any(marker in text for marker in _DROPPABLE_400_MARKERS)


def _default_transport(url, headers, body, timeout=30):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


class LokiPusher:
    def __init__(self, url, user, token, transport=_default_transport):
        if not (url and user and token):
            raise ValueError("Loki url, user and token are all required")
        self.url = push_url(url)
        self._auth = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
        self._transport = transport

    def push(self, events):
        """Send every event in one request.

        Returns ``("ok", None)`` on 2xx, or ``("dropped", <Loki's message>)``
        when Loki rejected entries as out-of-order / too old (logged by the
        caller, never retried). Raises
        LokiError for anything else so the tick is skipped.
        """
        body = json.dumps(build_body(events), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "Authorization": self._auth}
        try:
            status, text = self._transport(self.url, headers, body)
        except (urllib.error.URLError, OSError) as exc:
            raise LokiError(None, exc) from exc
        if 200 <= status < 300:
            return "ok", None
        if status == 400 and is_droppable_400(text):
            return "dropped", text
        raise LokiError(status, text)
