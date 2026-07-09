"""betula aws client — ALB access-log → Axiom shipper.

Pure, dependency-light building blocks so the parser and Axiom shipper are
unit-testable without AWS creds or a live Axiom endpoint. The S3/Lambda glue
lives in :mod:`alb_shipper.handler` and is deliberately thin and injectable;
solidago#108 wires it into a real Lambda.
"""

from .parser import ALB_FIELDS, parse_line, parse_lines
from .axiom import AxiomIngestClient, AxiomIngestError

__all__ = [
    "ALB_FIELDS",
    "parse_line",
    "parse_lines",
    "AxiomIngestClient",
    "AxiomIngestError",
]
