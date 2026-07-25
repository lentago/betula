"""Lambda entrypoint for the CloudWatch Logs → Axiom forwarder.

The AWS-facing surface is deliberately thin. A CloudWatch Logs subscription
filter delivers a single ``{"awslogs": {"data": "<base64 gzip json>"}}`` event;
this handler decodes it with the pure :mod:`cloudwatch_shipper.parser` and ships
the resulting records with :class:`cloudwatch_shipper.axiom.AxiomIngestClient`.

Unlike the ALB shipper there is no boto3 dependency at all — a subscription
filter hands the log data to the Lambda inline, so the whole decode-parse-ship
path is pure and runs end-to-end in tests with a fake Axiom client, no AWS
creds, and no live Axiom endpoint. solidago wires the subscription filter, packs
this collector as the forwarder Lambda, and injects the Axiom dataset/token from
Secrets Manager at deploy time (see this client's README for the handoff).
"""

from .axiom import AxiomIngestClient
from .parser import decode_awslogs_data, iter_records


def process_event(event, axiom_client):
    """Decode a subscription-filter event and ship its records.

    Returns the number of records shipped. A payload with no ``awslogs.data``
    (nothing to forward) ships nothing and returns ``0``. Any decode or ingest
    failure propagates so Lambda retries the delivery rather than dropping
    records.
    """
    data = event.get("awslogs", {}).get("data")
    if not data:
        return 0
    payload = decode_awslogs_data(data)
    return axiom_client.ship(iter_records(payload))


def lambda_handler(event, context=None):
    """AWS Lambda entrypoint.

    Builds the Axiom client from the environment (dataset + bare token, the
    latter injected from Secrets Manager by the solidago wiring) and ships the
    records in the triggering subscription-filter event. Any failure propagates
    so Lambda retries rather than dropping records.
    """
    axiom_client = AxiomIngestClient.from_env()
    shipped = process_event(event, axiom_client)
    return {"shipped": shipped}
