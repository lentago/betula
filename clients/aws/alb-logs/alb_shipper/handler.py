"""Lambda entrypoint for the S3 → Axiom ALB-log shipper.

The AWS-facing surface is deliberately thin: an S3 ``ObjectCreated`` event is
decoded, each referenced object is gunzipped, its lines are parsed by the pure
:mod:`alb_shipper.parser`, and the events are shipped by
:class:`alb_shipper.axiom.AxiomIngestClient`. The boto3 dependency and the
Axiom client are both *injectable*, so the parse-and-ship path is unit-testable
with no AWS creds and no live Axiom endpoint. solidago#108 supplies the real
S3 object reader (boto3) and the token (from Secrets Manager) at deploy time.
"""

import gzip

from .axiom import AxiomIngestClient
from .parser import parse_lines


def iter_s3_records(event):
    """Yield ``(bucket, key)`` pairs from an S3 ObjectCreated notification.

    Accepts the standard ``{"Records": [{"s3": {...}}]}`` envelope. Keys are
    left URL-encoded exactly as S3 delivers them; the object reader is
    responsible for any decoding its SDK requires.
    """
    for record in event.get("Records", []):
        s3 = record.get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        key = s3.get("object", {}).get("key")
        if bucket and key:
            yield bucket, key


def _boto3_object_reader(bucket, key):
    """Default object reader: fetch the gzipped object body via boto3.

    boto3 is imported lazily so importing this module (and running the unit
    tests) never requires the AWS SDK or credentials. It is present in the
    Lambda runtime that solidago#108 deploys.
    """
    import boto3  # noqa: PLC0415 — lazy so tests stay AWS-free

    client = boto3.client("s3")
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def _iter_object_lines(raw_bytes):
    """Decode a (gzipped or plain) ALB log object into text lines.

    ALB writes gzipped objects; a plain-text body is still accepted so a test
    or a manual replay can feed uncompressed samples through the same path.
    """
    if raw_bytes[:2] == b"\x1f\x8b":  # gzip magic
        raw_bytes = gzip.decompress(raw_bytes)
    text = raw_bytes.decode("utf-8", "replace")
    return text.splitlines()


def process_event(event, axiom_client, object_reader=_boto3_object_reader):
    """Parse and ship every ALB object referenced by an S3 event.

    Pure orchestration over injected collaborators — ``axiom_client`` and
    ``object_reader`` — so it runs end-to-end in tests with fakes. Returns the
    total number of events shipped across all objects in the event.
    """
    total = 0
    for bucket, key in iter_s3_records(event):
        raw = object_reader(bucket, key)
        lines = _iter_object_lines(raw)
        total += axiom_client.ship(parse_lines(lines))
    return total


def lambda_handler(event, context=None):
    """AWS Lambda entrypoint.

    Builds the Axiom client from the environment (dataset + token, the latter
    injected from Secrets Manager by solidago#108) and ships every object in
    the triggering S3 event. Any failure propagates so Lambda retries the S3
    notification rather than dropping records.
    """
    axiom_client = AxiomIngestClient.from_env()
    shipped = process_event(event, axiom_client)
    return {"shipped": shipped}
