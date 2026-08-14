# ADR-0001: On-appliance capture via Dockerized Fluent Bit

**Status:** Accepted (circa 2026-03-13; reconstructed 2026-08-13)

## Context

The Firewalla Gold SE is a home-network security appliance with a constrained
environment: a ~50 MB RAM budget available to this pipeline, a tmpfs at
`/bspool/manager/` where Zeek writes its log files (30 MB limit, recreated on
reboot), an overlay filesystem that wipes most path changes on firmware updates,
and `post_main.d/` as the reliable persistence hook (scripts there run after
every boot and firmware update). The appliance already runs Zeek internally and
already runs Docker.

The goal was to capture DNS queries, connection flows, TLS handshake metadata,
and ACL block events from those Zeek logs and ship them to a remote destination
for long-term search and dashboards.

The prior art for Firewalla log export was syslog forwarding (credited in the
README Acknowledgments to mbierman's syslog forwarding gist and the Firewalla
community forum).

## Decision

Run Fluent Bit in a Docker container on the Firewalla itself, reading Zeek
log files directly from the tmpfs via bind mount. All persistent state (config,
position trackers, cron jobs, the container start script) lives under
`/home/pi/.firewalla/config/`, which survives firmware updates. The container
is started and re-started via a script in `post_main.d/` and carries a
`--restart always` flag for normal reboots.

The no-new-dependencies rule applies: only `bash`, `docker`, `curl`,
`redis-cli`, `ssh`, `git`, and `flock` may be used in scripts. Fluent Bit runs
entirely inside the container; no additional host packages are needed.

## Alternatives

**Syslog forwarding** (the acknowledged prior art): simpler — no Docker
overhead, no container management — but produces unstructured text rather than
the parsed, label-enriched JSON that Fluent Bit delivers. Zeek writes structured
JSON on recent Firewalla firmware; a dedicated shipper that understands that
format was considered worth the overhead.

**Off-box Zeek sensor** *(retrospective — not considered at the time)*: mirror
the Firewalla's traffic to a separate host running Zeek and Fluent Bit. This is
lateral: it removes all resource pressure from the appliance and avoids the
overlay-FS complexity, but it requires a managed switch with a mirror port,
additional always-on hardware, and network topology changes. The Firewalla
already runs Zeek internally and already runs Docker; capture on-device was the
path of least additional infrastructure.

## Consequences

- Pipeline overhead is bounded at ~50 MB RAM (`storage.total_limit_size 50M`
  in the Fluent Bit config).
- Firmware updates that wipe Docker require `post_main.d/start_log_shipping.sh`
  to recreate the container; this is the expected behaviour and is tested.
- Position tracker files (`.db`) live on the overlay FS and are wiped on
  reboot; `start_log_shipping.sh` deletes them on every startup to prevent
  Fluent Bit from silently reading nothing (stale byte offsets into a tmpfs
  that was recreated).
- The tmpfs `/bspool` fills on busy networks; a 5-minute cron cleanup job
  deletes rotated Zeek logs before they exhaust it.
