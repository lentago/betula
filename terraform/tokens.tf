# Axiom API tokens that betula's archive plane issues to consumers in other
# repos (#119). Adopted from live state with `import` blocks at their exact
# attributes: the first apply is a pure import with no changes.
#
# ROTATION GUARD: in provider 1.6.x, ANY difference in name, description,
# expires_at, dataset_capabilities or org_capabilities makes the apply
# REGENERATE the token. The previous value stays valid only for
# rotation_grace_period (48h by default), and every consumer still holding it
# breaks after that. So a plan that shows `update in-place` on a token is a
# rotation, never a tidy-up: merge one only as a planned rotation with its
# consumer handoff ready (terraform/README.md § Tokens). Adoption must plan as
# `import` with `0 to change`. CI enforces this: the token rotation guard in
# .github/workflows/terraform.yml fails plan and apply on any token update or
# delete.
#
# prevent_destroy: removing a token revokes it at once, with no grace period.
#
# Not managed: the bootstrap token this provider authenticates with
# (providers.tf), so the config can never revoke its own credential.

# --- Ingest: solidago ECS container logs via FireLens ------------------------
# Consumer: solidago Secrets Manager `…-axiom-ingest-header`.
import {
  to = axiom_token.solidago_ecs_ingest
  id = "ef289421464f0254c71a9941a8e5b2f2c2721fc29b9ab2f7a8cb5b481a7a8719"
}

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
import {
  to = axiom_token.solidago_alb_ingest
  id = "a887a0dbb04f8001373ee7c14ef7a9952c13038a44cec357b0b22eaf7341a1d9"
}

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
# Consumer: lentago/drosera TF_VAR_axiom_api_token.
# Imported as-is with query on every dataset ("*"). Narrowing it to the ALB
# dataset is a capability change, so it regenerates the token: that happens as
# a planned rotation (#119 step 3), never as part of this import.
import {
  to = axiom_token.solidago_alb_query
  id = "ab16a1d630d9edd926fccad2bddea5288fb9a60b26ead8a057d88da017e723bd"
}

resource "axiom_token" "solidago_alb_query" {
  name = "cjp-solidago-alb-query"

  dataset_capabilities = {
    "*" = { query = ["read"] }
  }

  lifecycle {
    prevent_destroy = true
  }
}
