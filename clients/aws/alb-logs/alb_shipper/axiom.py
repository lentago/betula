"""Axiom ingest client — gzip + json_lines HTTP POST, dependency-light.

Mirrors the destination shape of the Firewalla client's Fluent Bit HTTP output
(``fluent-bit/fluent-bit.conf``): TLS POST to the Axiom ingest endpoint, a
``Authorization: Bearer <token>`` header, and gzip compression. Where Fluent
Bit sends a JSON array, this client sends newline-delimited JSON
(``application/x-ndjson``), which Axiom accepts and which streams batch-by-batch
without buffering an array in memory.

Uses only the Python standard library (``urllib``) so it adds no dependencies
to the Lambda package. The token is read from an env var or injected config and
is never hard-coded. The HTTP transport is injectable so unit tests exercise
batching and framing without a live Axiom endpoint.
"""

import gzip
import json
import os
import urllib.error
import urllib.request

DEFAULT_HOST = "api.axiom.co"
DEFAULT_BATCH_SIZE = 1000
# Axiom's ingest token env var; falls back to the generic name used by the
# Firewalla client's fluent-bit.conf for consistency across the two emitters.
TOKEN_ENV_VARS = ("AXIOM_API_TOKEN", "AXIOM_INGEST_TOKEN")
DATASET_ENV_VAR = "AXIOM_DATASET"


class AxiomIngestError(RuntimeError):
    """Raised when the Axiom ingest endpoint returns a non-2xx response."""

    def __init__(self, status, body):
        super().__init__(f"Axiom ingest failed: HTTP {status}: {body}")
        self.status = status
        self.body = body


def _default_transport(url, headers, body):
    """POST ``body`` to ``url`` with ``headers`` via urllib; return (status, text).

    Isolated behind a plain function so tests can inject a fake transport and
    assert on the framing (gzip, ndjson, auth header) with no network.
    """
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        return exc.code, exc.read().decode("utf-8", "replace")


class AxiomIngestClient:
    """Batches events and ships them to an Axiom dataset as gzip'd ndjson."""

    def __init__(
        self,
        dataset,
        token,
        host=DEFAULT_HOST,
        batch_size=DEFAULT_BATCH_SIZE,
        transport=_default_transport,
    ):
        if not dataset:
            raise ValueError("dataset is required")
        if not token:
            raise ValueError("token is required (never hard-code it)")
        self.dataset = dataset
        self.token = token
        self.host = host
        self.batch_size = batch_size
        self._transport = transport

    @classmethod
    def from_env(cls, env=None, **kwargs):
        """Build a client from environment variables.

        Reads the dataset from ``AXIOM_DATASET`` and the token from the first
        set variable in :data:`TOKEN_ENV_VARS`. Keeping the token in the
        environment (populated from Secrets Manager on the solidago side) means
        it never appears in code or logs.
        """
        env = os.environ if env is None else env
        dataset = env.get(DATASET_ENV_VAR)
        token = next((env[name] for name in TOKEN_ENV_VARS if env.get(name)), None)
        if not dataset:
            raise ValueError(f"{DATASET_ENV_VAR} is not set")
        if not token:
            raise ValueError(
                "no Axiom token set (" + " or ".join(TOKEN_ENV_VARS) + ")"
            )
        return cls(dataset=dataset, token=token, **kwargs)

    @property
    def ingest_url(self):
        return f"https://{self.host}/v1/datasets/{self.dataset}/ingest"

    @staticmethod
    def _encode_batch(events):
        """Serialise a batch of events to gzip-compressed ndjson bytes."""
        ndjson = "\n".join(json.dumps(event, separators=(",", ":")) for event in events)
        return gzip.compress(ndjson.encode("utf-8"))

    def _post_batch(self, events):
        body = self._encode_batch(events)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
        }
        status, text = self._transport(self.ingest_url, headers, body)
        if not 200 <= status < 300:
            raise AxiomIngestError(status, text)
        return status

    def ship(self, events):
        """Ship an iterable of event dicts in batches of ``batch_size``.

        Returns the number of events sent. Raises :class:`AxiomIngestError` on
        the first batch the endpoint rejects, so the caller (Lambda) surfaces
        the failure and lets the S3 event be retried rather than silently
        dropping records.
        """
        sent = 0
        batch = []
        for event in events:
            batch.append(event)
            if len(batch) >= self.batch_size:
                self._post_batch(batch)
                sent += len(batch)
                batch = []
        if batch:
            self._post_batch(batch)
            sent += len(batch)
        return sent
