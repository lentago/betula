# betula client: github (GitHub Actions run/job history → Grafana Cloud Loki)

**What you're about to do:** install a small stdlib-only Python collector on
LXC 105 (`grafana-stack`, pve5) as a systemd timer. Every 5 minutes it polls
the GitHub REST API for completed workflow runs across every non-archived repo
of each configured owner, and pushes one event per run attempt and one per job
to Grafana Cloud Loki.

**Why bother:** GitHub has no org-wide runs endpoint (`GET /orgs/{org}/actions/runs`
is a 404) and its Insights pages only give daily rollups. This feed is the
fleet-wide CI record that drosera's CI dashboard
([drosera#234](https://github.com/lentago/drosera/issues/234)), the red-`main`
alert ([drosera#204](https://github.com/lentago/drosera/issues/204)) and the
change pipeline ([drosera#251](https://github.com/lentago/drosera/issues/251))
read from. A poller that ships a third-party API into Loki is a collector, so it
lives here as a peer of the Firewalla and AWS clients ([#74](https://github.com/lentago/betula/issues/74)).

**Time:** about twenty minutes the first time (mostly minting two tokens), then
one command per redeploy.

## How it works

Each timer tick runs `python3 -m github_collector` once:

1. Lists the non-archived repos for each configured owner. The list is cached
   in `/var/lib/betula-github/cache.json` and refreshed at most hourly.
2. For each repo, lists runs with `status=completed` and
   `created>=now − lookback` (default 24h).
3. Skips any `(run_id, run_attempt)` already in the seen-set
   (`/var/lib/betula-github/seen.json`).
4. For each new run attempt, fetches its jobs from
   `/actions/runs/{id}/attempts/{attempt}/jobs`. The plain `/jobs` endpoint
   defaults to `filter=latest` and would credit a re-run's jobs to the wrong
   attempt.
5. Fetches `GET /repos/{repo}/branches/{default_branch}` for every repo (the
   default branch comes from the repository object in step 1) and builds one
   `github_branch_head` event each. See [Branch head](#branch-head).
6. Sends every run, job and branch-head event to Loki in **one** request,
   sorted by timestamp within each stream.
7. Adds the pushed keys to the seen-set only after Loki returns 2xx, then
   prunes keys for runs older than the lookback.

**Why a lookback window instead of a high-water mark:** a `created>=<mark>`
cursor misses runs created before the mark that finish after it (long runs, or
jobs queued behind the self-hosted runner), and it misses re-runs, which keep
the original `created_at` and only bump `run_attempt`. Polling a fixed window
and deduplicating on `(run_id, run_attempt)` catches both.

**Known limitations:**

- Re-runs of runs created more than one lookback ago are missed.
- If a run is re-run before the collector sees the first attempt, only the
  latest attempt is listed, so only that attempt is emitted.
- GitHub sometimes reports a skipped job finishing a second before it started,
  so `duration_s` can be `-1`. Consumers already exclude skipped jobs from
  timing stats.

### Failure behaviour

| Case | What happens |
|---|---|
| GitHub API error (5xx, network, 404) | Tick skipped and the seen-set is unchanged, so the next tick retries. A 404 on a repo's runs also drops the cached repo list. Exit 1, so the unit shows failed. |
| GitHub rate limit (403/429 with `x-ratelimit-remaining: 0`, `retry-after`, or a secondary-limit message) | `backoff_until` is saved to `cache.json`. Ticks before that time exit 0 without calling GitHub. |
| Branch request fails (5xx, network, 404) | That repo's `github_branch_head` event is skipped for the tick; the other repos and the runs/jobs pass are unaffected. A rate limit on this call still backs off the whole tick. |
| Loki error (5xx, 401/403, 429, other 400s, network) | Tick skipped and the seen-set is unchanged. Exit 1. |
| Loki 400 for out-of-order or too-old entries | Logged and dropped. The keys are marked seen so the batch isn't retried forever (Loki still ingests the valid entries in the batch). |

## Feed contract

drosera consumes this feed. Changing a label or a payload key is a breaking
change under [ADR-0006](../../docs/adr/0006-loki-label-contract.md) and needs a
matching drosera update.

**Stream labels.** These are exactly the six labels from drosera's
[`clients/README.md`](https://github.com/lentago/drosera/blob/main/clients/README.md),
so these events sit next to the `loki-event` emitters:

| Label | Value |
|---|---|
| `log_source` | `github_actions_run` / `github_actions_job` |
| `cluster` | the owner's `cluster` from config, e.g. `lentago` |
| `source` | `github_actions` |
| `pipeline` | `ci` |
| `stage` | `run` / `job` |
| `repo` | `owner/name`, e.g. `lentago/kalmia` |

That's two streams per repo, about 54 for `lentago`. Everything else goes in
the JSON line, never in a label.

**Timestamps.** Run events are stamped at `updated_at` (the run object has no
`completed_at`). Job events are stamped at `completed_at`, falling back to the
run's `updated_at` for a job that never ran. Nothing is ever stamped with the
poll time.

**Run line:** `repo`, `workflow`, `workflow_path`, `run_id`, `run_attempt`,
`run_number`, `event`, `branch`, `head_sha`, `actor`, `conclusion`,
`created_at`, `run_started_at`, `updated_at`, `duration_s`
(`updated_at − run_started_at`, approximate), `html_url`.

**Job line:** `repo`, `workflow`, `run_id`, `run_attempt`, `job_id`,
`job_name`, `conclusion`, `created_at`, `started_at`, `completed_at`, `queue_s`
(`started_at − created_at`), `duration_s`, `runner_name`, `runner_group`,
`runner_labels`, `self_hosted` (true when `runner_labels` contains
`self-hosted`), `html_url`. Skipped jobs are emitted too.

### Branch head

`github_branch_head` answers "what is the newest commit on each repo's default
branch?", which the Actions feed cannot: in repos with path-filtered push
workflows a docs-only merge produces no run
([drosera#251](https://github.com/lentago/drosera/issues/251), ADR-0010). It
backs the "stuck" alert.

**Stream labels:** only `log_source="github_branch_head"`, `cluster` (the
owner's cluster) and `repo` (`owner/name`). It does not carry the
`source`/`pipeline`/`stage` labels of the run and job events.

**Line:** `branch` (the repo's default branch), `sha` (full commit SHA),
`committed_at` (the commit's committer date, RFC 3339), `url` (the commit's
`html_url`), `observed_at` (tick time, RFC 3339).

One line per repo per tick, pushed even when the head is unchanged: consumers
read "the newest head as of now". It is not deduplicated and does not touch the
seen-set. It is stamped at the tick time, since it is a state sample rather than
an event. `--dry-run` prints it like the other events. It costs one extra GitHub
request per repo per tick (144/hour for twelve repos). Empty repos (no default
branch) are skipped.

```logql
{log_source="github_branch_head", cluster="lentago"} | json
```

## Config

[`config.json`](config.json) lives in git and is installed verbatim to
`/etc/betula-github/config.json`:

```json
{
  "owners": [{"owner": "lentago", "cluster": "lentago"}],
  "interval": "5m",
  "lookback": "24h"
}
```

- **Adding a second org is one more `owners` entry.** No code change and no
  second token: a fine-grained PAT with public-repository read access reads
  any public repository on GitHub, whatever its owner, so the one
  `BETULA_GITHUB_TOKEN` covers every public org you list. A private repo under
  another owner would need per-owner credentials, which the collector does not
  support.
- `interval` and `lookback` are systemd-style durations (`90s`, `5m`, `24h`,
  `2d`). `deploy.sh` copies `interval` into the timer. `lookback` must be
  longer than `interval`.

## Token setup

Both secrets go only in `/etc/default/betula-github` (0600 root:root, written
by `deploy.sh`), never in git.

### GitHub: fine-grained PAT, read-only

1. On GitHub, go to **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Set **Resource owner** to `lentago` and **Repository access** to **Public
   repositories (read-only)**. Add **no** extra permissions; every org repo is
   public, and public read is enough for the Actions runs and jobs APIs.
3. Pick an expiry and generate the token (`github_pat_…`). It becomes
   `BETULA_GITHUB_TOKEN`.

The limit is 5,000 requests/hour. Expect about 500/hour at a 5-minute interval
(one runs call and one branch call per repo per tick, plus one jobs call per
new run attempt). If a
private repo ever needs coverage, switch to selected repositories and grant
**Actions: read** and **Metadata: read** on just that repo.

### Grafana Cloud Loki: access-policy token, `logs:write` only

Use drosera's stack, as in drosera's `clients/README.md` § Token setup.

1. In the Grafana Cloud portal, open the stack and click **Details** on the
   **Loki** tile. Note the **URL** (`https://logs-prod-NNN.grafana.net`, which
   becomes `GRAFANA_CLOUD_LOGS_URL`) and the numeric **User**
   (`GRAFANA_CLOUD_LOGS_USER`).
2. Under **Security → Access Policies**, click **Create access policy**. Set
   the realm to that stack and the scope to **`logs:write`** only.
3. Click **Add token** and copy the `glc_…` value. It becomes
   `GRAFANA_CLOUD_LOGS_TOKEN`.

The collector only makes outbound HTTPS requests (to `api.github.com` and
`*.grafana.net`) and listens on no port.

## Deploy

[`deploy.sh`](deploy.sh) is idempotent. Run it from a betula checkout on
LXC 105 as a sudo-capable user:

```bash
git clone --depth 1 https://github.com/lentago/betula /tmp/betula && cd /tmp/betula
export BETULA_GITHUB_TOKEN=github_pat_… \
       GRAFANA_CLOUD_LOGS_URL=https://logs-prod-NNN.grafana.net \
       GRAFANA_CLOUD_LOGS_USER=123456 GRAFANA_CLOUD_LOGS_TOKEN=glc_…
./clients/github/deploy.sh
```

It validates `config.json` and creates the `betula-github` system user. It
installs the package to `/opt/betula-github/`, the config to
`/etc/betula-github/`, the secrets to `/etc/default/betula-github`, and the
[`systemd/`](systemd/) units. Then it enables the timer and runs one tick
synchronously. The first tick backfills the whole lookback window, roughly 300
GitHub requests and a minute or two. To ship a code or config change, re-run
the script. If the guest has no `python3` ≥ 3.9, the script stops; add the
package in kalmia rather than installing it by hand.

## How you know it worked

On the host:

```bash
systemctl list-timers betula-github.timer        # next run within 5 min
journalctl -u betula-github -n 20 --no-pager     # "[betula-github] INFO pushed N run(s), M job(s) …"
```

To watch a tick without pushing or touching the seen-set:

```bash
sudo env $(sudo grep -v '^#' /etc/default/betula-github | xargs) \
  STATE_DIRECTORY=/tmp/betula-github-dry PYTHONPATH=/opt/betula-github \
  python3 -B -m github_collector --config /etc/betula-github/config.json --dry-run
```

In Grafana, open **Explore** on the Loki datasource:

```logql
{log_source="github_actions_run", cluster="lentago"} | json
```

Recent runs show up with all six labels and the run fields. For a quick
per-repo failure count over the last day:

```logql
sum by (repo) (count_over_time({log_source="github_actions_run", cluster="lentago"} | json | conclusion="failure" [24h]))
```

## Tests

```bash
cd clients/github && python3 -m unittest discover -s tests -v
```

The tests run against an in-process fake GitHub and fake Loki
([`tests/fakes.py`](tests/fakes.py)), with no tokens and no network. They cover
repo enumeration and pagination, branch-head events, the window and dedupe logic (including
restarts and re-runs), attempt-scoped job fetch, payload mapping, batching, and
each failure case in the table above. CI runs them in
[`github-client-tests.yml`](../../.github/workflows/github-client-tests.yml).
