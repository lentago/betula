"""Small stdlib Loki pusher, the same shape as clients/github's (#113).

Each client carries its own copy so no client edits another.
"""

import base64
import json
import urllib.error
import urllib.request

PUSH_PATH = "/loki/api/v1/push"

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
    streams = {}
    for labels, ts_ns, line in events:
        streams.setdefault(tuple(sorted(labels.items())), []).append((ts_ns, line))
    out = []
    for key in sorted(streams):
        values = sorted(streams[key], key=lambda v: v[0])
        out.append({"stream": dict(key), "values": [[str(ts), line] for ts, line in values]})
    return {"streams": out}


def _default_transport(url, headers, body, timeout=10):
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
        """``"ok"`` on 2xx, ``"dropped"`` for out-of-window entries; raises otherwise."""
        if not events:
            return "ok"
        body = json.dumps(build_body(events), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "Authorization": self._auth}
        try:
            status, text = self._transport(self.url, headers, body)
        except (urllib.error.URLError, OSError) as exc:
            raise LokiError(None, exc) from exc
        if 200 <= status < 300:
            return "ok"
        if status == 400 and any(m in str(text).lower() for m in _DROPPABLE_400_MARKERS):
            return "dropped"
        raise LokiError(status, text)
