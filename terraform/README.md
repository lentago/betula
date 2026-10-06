# terraform/ — the Axiom side of betula's archive

Declares the Axiom datasets betula's AWS (solidago) client ships to, with
their retention. Plan runs on every PR (posted as a comment); apply runs on
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

## Not yet managed

Ingest and query **tokens** are a separate follow-up (#119). Any field difference on
an imported token makes the provider regenerate it, and the old value lapses
after 48h, so tokens need an exact import plus a consumer-handoff design
(solidago's Secrets Manager entries, drosera's `TF_VAR_axiom_api_token`).
