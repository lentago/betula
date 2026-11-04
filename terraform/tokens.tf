# Axiom API tokens that betula's archive plane issues to consumers in other
# repos (#119). Adopted from live state by an exact import (#153). The import
# blocks were removed once applied (#119 step 3): a rotation changes a
# token's ID, so a kept block would name a dead ID. Consumers read the
# values from outputs.tf.
#
# ROTATION GUARD: in provider 1.6.x, ANY difference in name, description,
# expires_at, dataset_capabilities or org_capabilities makes the apply
# REGENERATE the token. The previous value stays valid only for
# rotation_grace_period (48h by default), and every consumer still holding it
# breaks after that. So a plan that shows `update in-place` on a token is a
# rotation, never a tidy-up: merge one only as a planned rotation with its
# consumer handoff ready (terraform/README.md § Tokens). Adoption must plan as
# `import` with `0 to change`. CI enforces this: the token rotation guard
# (.github/scripts/token-guard.sh) fails plan and apply on any token delete,
# and on any token update unless the PR carries the token-rotation label from
# a human account.
#
# prevent_destroy: removing a token revokes it at once, with no grace period.
#
# Not managed: the bootstrap token this provider authenticates with
# (providers.tf), so the config can never revoke its own credential.

# --- Ingest: solidago ECS container logs via FireLens ------------------------
# Consumer: solidago Secrets Manager `…-axiom-ingest-header`.
resource "axiom_token" "solidago_ecs_ingest" {
  name = "solidago-ecs-ingest"

  dataset_capabilities = {
    "cjp-solidago-ecs" = { ingest = ["create"] }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# --- Ingest: solidago ALB access logs via the alb-logs shipper ---------------
# Consumer: the clients/aws/alb-logs shipper deployed from lentago/solidago.
resource "axiom_token" "solidago_alb_ingest" {
  name = "cjp-solidago-alb"

  dataset_capabilities = {
    "cjp-solidago-alb" = { ingest = ["create"] }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# --- Query: drosera's "Solidago Axiom" Grafana datasource --------------------
# Consumer: lentago/drosera, reading axiom_tokens["cjp-solidago-alb-query"]
# from this state (drosera#313). Query on the ALB dataset only: every drosera
# panel through this datasource queries cjp-solidago-alb. Narrowed from "*"
# in #119 step 3, a planned rotation.
resource "axiom_token" "solidago_alb_query" {
  name = "cjp-solidago-alb-query"

  dataset_capabilities = {
    "cjp-solidago-alb" = { query = ["read"] }
  }

  lifecycle {
    prevent_destroy = true
  }
}
