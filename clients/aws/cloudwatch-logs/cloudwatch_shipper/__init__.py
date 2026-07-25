"""betula aws client — CloudWatch Logs → Axiom forwarder.

Pure, dependency-light building blocks so the payload decoder and Axiom shipper
are unit-testable without AWS creds or a live Axiom endpoint. A CloudWatch Logs
subscription filter delivers log data inline (base64 + gzip JSON), so — unlike
the sibling ALB shipper — there is no boto3 glue at all: the decode-parse-ship
path in :mod:`cloudwatch_shipper.handler` is entirely pure. solidago wires the
subscription filter and deploys this as a forwarder Lambda (see README).
"""

from .parser import (
    decode_awslogs_data,
    epoch_ms_to_iso,
    event_to_record,
    iter_records,
    parse_awslogs_data,
)
from .axiom import AxiomIngestClient, AxiomIngestError

__all__ = [
    "decode_awslogs_data",
    "epoch_ms_to_iso",
    "event_to_record",
    "iter_records",
    "parse_awslogs_data",
    "AxiomIngestClient",
    "AxiomIngestError",
]
