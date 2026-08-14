# ADR-0007: Visitor-IP truncation at parse time; hashing rejected

**Status:** Accepted (2026-07-25; reconstructed 2026-08-13)

## Context

The ALB access-log collector (`clients/aws/alb-logs/`) captures one record per
HTTP request for four public hostnames. Each record includes a raw `client_ip`
field. Two of the four domains raise this above routine web-log housekeeping:

- **pondviewlane.com** and **essexcrossingatmontserrat.com** are public-record
  references for a specific residential subdivision. Visitors are
  disproportionately the couple dozen households that live there.

Issue #90 documented the sensitivity: an IP plus a timestamp plus a requested
path "is a reasonable proxy for 'which neighbor read the HOA covenants, and
when.' The site itself is explicitly built not to name residents; the access
log quietly undoes some of that."

Measured at the time of issue #90 (2026-07-24): 851 distinct client IPs over 7
days on pondviewlane.com alone, ~1,000 real requests per day. The data was
already live and accumulating in Axiom.

Issue #90 also flagged the forensics trade-off: raw IPs support single-host
abuse attribution (attributing a scraping or credential-stuffing pattern to a
specific address) that a truncated or hashed field would not.

## Decision

Truncate `client_ip` at parse time in `clients/aws/alb-logs/alb_shipper/parser.py`
(PR #91, merged 2026-07-25):

- IPv4: zero the last octet (`203.0.113.42` → `203.0.113.0`).
- IPv6: zero the last 64 bits.
- Malformed input: returned unchanged.

Applied before the record is shipped; raw IPs are never written to the Axiom
dataset. The implementation uses Python's stdlib `ipaddress` module (no new
dependencies).

## Alternatives

**Retain raw IPs**: forecloses the privacy benefit. Once in Axiom, the data is
queryable by anyone with dataset access. The sensitivity documented in issue #90
makes raw retention the wrong default for this workload.

**Hash the IP** (HMAC or salted hash): explicitly rejected. PR #91: "A hash
produces a stable pseudonymous identifier — linkable across requests — which is
arguably a worse privacy posture than a coarser field that degrades
gracefully." In the ADR's own gloss: truncation destroys the information
instead of disguising it — a hash retains re-identifiability across any request
window, while truncation is a one-way lossy transform.

**Drop `client_ip` entirely** *(retrospective — not considered at the time)*:
better for privacy, worse for analytics. Truncation retains coarse /24-level
geographic and repeat-visitor signal — useful for traffic volume patterns, rough
uniques, and status-code mix — without stable re-identifiability. For the
stated analytical use case, truncation is the appropriate middle point. Dropping
the field entirely would be the right call for a higher-sensitivity workload.

## Consequences

- Single-host abuse attribution (identifying the specific source IP of a
  scraper or credential-stuffing run) is not possible from stored records.
  Abuse patterns remain detectable by /24 prefix and volume.
- Traffic analytics (volumes, top paths, rough uniques, status-code mix) are
  unaffected; the /24 prefix is sufficient for all stated analytical purposes.
- Data already ingested before PR #91 carries raw IPs; the change applies only
  to newly parsed records. Historical data was not retroactively sanitised
  (noted plainly in PR #91 and `clients/aws/alb-logs/README.md`).
- A collector built for a different risk profile (e.g., a high-traffic public
  site where forensic attribution is operationally important) should re-evaluate
  this position rather than inherit it.
