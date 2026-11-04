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
| `cjp-solidago-alb-query` | query `cjp-solidago-alb` (narrowed from `*` in #119 step 3) | drosera *Solidago Axiom* datasource, reading `axiom_tokens` (drosera#313) |

- **Any field change regenerates the token.** In provider 1.6.x a difference
  in `name`, `description`, `expires_at` or either capability block makes the
  apply issue a new value. The old one stays valid only for
  `rotation_grace_period` (48h by default). A plan showing `update in-place`
  on a token is therefore a **rotation**: merge it only with the consumer
  handoff ready. Adopting a live token must plan as `import` with `0 to change`.
- **CI enforces it.** The token rotation guard
  (`.github/scripts/token-guard.sh`) runs over the saved plan in the PR plan
  job and in the apply on `main`:
  - **A token delete or replace always fails.** No label covers it.
  - **A token update fails** unless the PR carries the `token-rotation` label
    and its most recent `labeled` event came from a User account, not a Bot.
    On `main`, the guard reads the label from the PR the push merged.
  - **Anything else passes,** including imports, new tokens, and dataset
    changes.

  That also catches drift: a token edited in the Axiom UI blocks the next
  apply instead of being silently regenerated.
- **The label records intent; it is not an approval.** Sessions run under
  the operator's account, so a User-applied label shows that a human account
  declared the rotation, and it keeps the runner App from declaring one for
  itself. The authorization is the rotation's release on its issue.
- **What the guard defends against.** It catches *accidental* rotations:
  drift, or a capability edit nobody recognised as a rotation. It cannot stop
  a PR that edits the guard or the workflow, because a PR run uses the PR's
  own copy of both. Changes to CI are covered by review before arming, and a
  runner-App PR also needs a human approval (lentago/.github ADR-0014).
  `.github/scripts/token-guard_test.sh` runs the guard's cases in CI.
- **`prevent_destroy` on every token.** Removing a token revokes it at once,
  with no grace period.
- **Handoff:** consumers read token values from this state (#119, option A,
  decided 2026-11-04). See *Reading a token from another repo* below.

### Reading a token from another repo

The interface is a single sensitive output, `axiom_tokens` (`outputs.tf`): a
map from token name to value. A consumer reads it read-only and takes only its
own entry:

```hcl
data "terraform_remote_state" "betula" {
  backend = "s3"
  config = {
    bucket = "solidago-tfstate-365184644049"
    key    = "betula/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  # null until betula first regenerates this token: keep the current value.
  axiom_query_token = data.terraform_remote_state.betula.outputs.axiom_tokens["cjp-solidago-alb-query"]
}
```

- **Access: one state key, read-only.** The consumer's CI role needs
  `s3:GetObject` on `solidago-tfstate-365184644049/betula/terraform.tfstate`
  and `kms:Decrypt` on the tfstate CMK, and nothing else: no `PutObject`, and
  no lock table, since a remote-state read never takes the lock. Roles are
  granted in lentago/solidago `modules/iam`.
- **The output is the contract, not an access boundary.** Read access covers
  the whole state object, including every token attribute in it. Grant it only
  to roles that consume a token here.
- **Accepted risk: every reader sees all three tokens.** This follows from
  serving every consumer from one state key (#119, option A), and it is
  recorded here deliberately:
  - drosera's role, granted for the query token, can also read the two
    ingest tokens. Each can only ingest into its own `cjp-solidago-*` dataset.
  - solidago's roles can read the query token. They can already read every
    state object in the bucket (lentago/solidago#240).
  - The widest token was narrowed from query on `*` to query on
    `cjp-solidago-alb` (#119 step 3), at the same rotation that first put its
    value here.

  Per-consumer isolation would need a separate state key per consumer.
- **Null until the first rotation.** Axiom returns a value only when a token
  is created or regenerated, and the three tokens were imported (#153). So
  every entry is null until that token's first planned rotation. A consumer
  must treat null as "keep the current value" and fail loudly rather than
  deploy an empty credential.
- **Rotation timing.** A planned rotation regenerates the token on betula's
  apply, and the old value lapses after `rotation_grace_period` (48h). Each
  consumer must re-run its apply within that window to pick up the new value.
  Consumer-side work: lentago/solidago and lentago/drosera issues linked from
  #119.

### Planned rotation

1. **Consumer ready.** The consumer reads its entry from `axiom_tokens`, with
   a fallback to its current value while the entry is null, and its owner
   can run an apply within 48h of betula's.
2. **PR.** Change the token in `tokens.tf` and label the PR
   `token-rotation`. Labels are read live, so if the plan job ran before
   the label, re-run it.
3. **Plan.** The plan comment must show `update in-place` only on the tokens
   you mean to rotate, and `0 to destroy`.
4. **Merge.** betula's apply regenerates each token. Its old value lapses 48h
   later.
5. **Consumer apply within 48h.** Verify the consumer works on the new value,
   for example with a datasource health check or a fresh ingest event.
6. **Remove the fallback** from the consumer once it reads the new value.

Rollback is another rotation: revert the PR with the label, then run the
consumer apply. Within 48h the old value also still works.
