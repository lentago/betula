# terraform/ — the Axiom side of betula's archive

Declares the Axiom datasets betula's AWS (solidago) client ships to, with
their retention, and the API tokens issued to that data's consumers. Plan runs on every PR (posted as a comment); apply runs on
merge to `main` (`.github/workflows/terraform.yml`). Whatever is on `main` is
the live Axiom state: **never change these datasets in the Axiom UI** without
the same change here, or the next apply reverts it.

| Dataset | Source | Retention |
|---|---|---|
| `cjp-solidago-ecs` | ECS container logs (FireLens) | 30 days |
| `cjp-solidago-alb` | ALB access logs (`clients/aws/alb-logs/`); read by drosera's *Solidago Axiom* datasource | 30 days |

Axiom's built-in sample datasets (`otel-demo-*`, `sample-http-logs`) are not
this org's and are deliberately not managed.

## Guards

- **`prevent_destroy` on every dataset.** The provider *replaces* a dataset
  when `name` or `kind` changes, and replacing deletes its data. With the guard,
  such a plan fails instead (verified: renaming a dataset produces
  `Error: Instance cannot be destroyed`).
- **Retention is a data-deletion knob.** Lowering `retention_days` lets Axiom
  trim older events. Review a decrease as you would a delete.

## Credentials

- **Axiom:** the provider reads `AXIOM_API_TOKEN`. CI passes the
  `AXIOM_TF_API_TOKEN` repo secret. That token is the **bootstrap credential**:
  created by hand in Axiom (Settings → API Tokens) with org capabilities for
  datasets and API tokens, and deliberately **not** managed by this config, so it
  can't lock itself out.
- **State:** S3 backend `solidago-tfstate-365184644049` / `betula/terraform.tfstate`
  with the shared lock table. CI assumes `betula-github-actions-terraform` via
  OIDC. That role is defined in lentago/solidago (`modules/iam`, solidago#207)
  and can touch only this state key.

## Runbook

- **Add a dataset:** add an entry to `local.datasets` in `datasets.tf`. If it
  already exists in Axiom, the `import` block adopts it; otherwise the apply
  creates it. Check the PR's plan comment says `import` (adopt) or `add`
  (create), never `destroy`.
- **Change retention:** edit `retention_days`. Expect `1 to change` in place.
- **Retire a dataset (deletes its data):** in its own PR, remove its entry and
  add a `removed` block or drop the guard. Say in the PR that the data goes.

## Tokens

`tokens.tf` holds the tokens consumers in other repos use (#119). The
bootstrap token the provider authenticates with stays hand-managed (see
*Credentials*).

| Token | Capability | Consumer |
|---|---|---|
| `solidago-ecs-ingest` | ingest `cjp-solidago-ecs` | solidago Secrets Manager `…-axiom-ingest-header` (ECS FireLens) |
| `cjp-solidago-alb` | ingest `cjp-solidago-alb` | the ALB log shipper (`clients/aws/alb-logs/`, deployed from solidago) |
| `cjp-solidago-alb-query` | query `*` (to be narrowed in a planned rotation) | drosera `TF_VAR_axiom_api_token` (*Solidago Axiom* datasource) |

- **Any field change regenerates the token.** In provider 1.6.x a difference
  in `name`, `description`, `expires_at` or either capability block makes the
  apply issue a new value. The old one stays valid only for
  `rotation_grace_period` (48h by default). A plan showing `update in-place`
  on a token is therefore a **rotation**: merge it only with the consumer
  handoff ready. Adopting a live token must plan as `import` with `0 to change`.
- **CI enforces it.** The token rotation guard in
  `.github/workflows/terraform.yml` fails the PR plan, and the apply on
  `main`, when the plan would update or delete any `axiom_token`. That also
  catches drift: a token edited in the Axiom UI blocks the next apply instead
  of being silently regenerated. Imports and new tokens pass. There is no
  override yet; the first planned rotation (#119 step 3) adds one.
- **`prevent_destroy` on every token.** Removing a token revokes it at once,
  with no grace period.
- **Handoff:** consumers will read regenerated values from this state (#119
  steps 2–3, decided 2026-11-04). Until then a rotation is a manual handoff:
  update the consumer's secret within the grace period and verify ingest.
