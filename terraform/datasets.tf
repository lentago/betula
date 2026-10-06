# Axiom datasets for betula's archive plane — the AWS (solidago) client's
# destinations (clients/aws/README.md). Imported from live state on 2026-10-06
# (#118); the first apply is a pure import with no changes.
#
# DESTRUCTION GUARD: `name` and `kind` are RequiresReplace in the provider, and
# replacing a dataset deletes its data. Every dataset carries prevent_destroy,
# so a rename or kind change fails the plan instead of destroying the archive.
# To retire a dataset deliberately, remove the guard in its own reviewed PR.
#
# RETENTION is policy, set here and reviewed here. Lowering retention_days
# lets Axiom trim older data, so treat a decrease as a data-deletion change.
#
# Not managed: otel-demo-metrics, otel-demo-traces, sample-http-logs. Those are
# Axiom's built-in sample datasets, owned by Axiom rather than this org.

locals {
  datasets = {
    # ECS container logs via FireLens (solidago ECS tasks).
    "cjp-solidago-ecs" = { retention_days = 30 }
    # ALB access logs via the alb-logs shipper; queried from drosera's
    # "Solidago Axiom" Grafana datasource.
    "cjp-solidago-alb" = { retention_days = 30 }
  }
}

import {
  for_each = local.datasets
  to       = axiom_dataset.this[each.key]
  id       = each.key
}

resource "axiom_dataset" "this" {
  for_each = local.datasets

  name                 = each.key
  kind                 = "axiom:events:v1"
  retention_days       = each.value.retention_days
  use_retention_period = true

  lifecycle {
    prevent_destroy = true
  }
}
