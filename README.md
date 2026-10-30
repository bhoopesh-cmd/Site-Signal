# Site-Signal

**A monitoring layer that decides whether a website's location data actually changed before ChainXY re-extracts and QA-checks it.**

---

## The problem

ChainXY maintains business-location data for thousands of websites. Today it re-extracts and re-validates that data on a schedule, regardless of whether anything actually changed. Most of the time it hasn't — so a large share of extraction and QA effort is spent re-confirming data that is already correct.

## The idea

Site-Signal sits *in front of* the existing extraction process. Before triggering a full extraction, it asks a cheaper question: **"did this source's location data probably change?"**

- **Likely unchanged** → the source can potentially skip extraction and QA.
- **Changed, uncertain, or failed** → the source goes through the existing extraction process as normal.

The key safety property: **uncertainty never results in a skip.** If Site-Signal cannot confidently determine the state of a source, that source is sent through the existing pipeline — exactly as it would be today.

There are two complementary ways to answer that question, in order of preference:

1. **Event-driven** — the source *tells us* when a location changes (via an authorized integration, webhook, or change feed). *Preferred wherever it is available.*
2. **Check-based polling** — we periodically *ask* the source, or compare what we already have. Used as a fallback when no event access exists.

## How it works

### Tier 1 — Event-driven monitoring (preferred where available)

Many location platforms — **Yext**, and other location APIs that expose **webhooks or change feeds** — can *push* a notification when a location record is created, updated, or deleted. Instead of polling every site on a schedule, Site-Signal can react to the events that actually matter:

1. **Receive the event** from an authorized integration (e.g. a Yext webhook or a location API change feed).
2. **Map the event to ChainXY's world** — resolve which brand and which specific location record it refers to.
3. **Filter for meaningful change** — keep only changes to meaningful location fields (name, address, coordinates, hours, open/closed status). Ignore unrelated updates.
4. **Trigger targeted extraction and QA** — process only that brand/location, immediately, rather than waiting for the next polling cycle.

Done well, this is the cheapest and most precise option: near-real-time updates, targeted work, and fewer missed changes.

> **Status: proposed architecture — not implemented or validated in the prototype.** Event-driven monitoring depends on authorized integrations, agreed event schemas, and provider support. It is a design direction, not something ChainXY has built or tested yet. See the status summary below.

### Tier 2 — Check-based polling (implemented fallback)

Where no event access exists, Site-Signal falls back to checking sources directly, choosing a method based on what each site supports:

| Approach | Plain-language description |
|---|---|
| **Conditional request** | Ask the website "has this changed since my last copy?" using standard HTTP validators (ETag / Last-Modified). If the server replies "not modified," that's a strong hint nothing changed. |
| **Payload comparison** | Fetch the page and compare a **normalized location fingerprint** — a compact signature of the actual store locations (names, addresses, coordinates). It ignores irrelevant page changes (banners, timestamps) and stable IDs that get regenerated. |
| **Alternative metadata** | Some sites expose sitemaps or feeds that can act as a change hint. |
| **Browser fallback** | For JavaScript-heavy sites with no direct data endpoint. |
| **Unresolved** | No reliable signal available yet — these always fall through to normal extraction. |

### Decision logic

The decision logic is deliberately conservative, for both tiers:

- **Unchanged** — only when there is a trustworthy signal *and* the location fingerprint matches the last known good baseline. → Skip extraction/QA.
- **Changed** — the location data differs. → Trigger extraction and QA.
- **Unknown / Failed** — incomplete data, a partial endpoint, or a request error. → Route through the existing extraction process (never skipped).

### Implementation status (read this before anything else)

| Monitoring tier | Implemented in prototype? | Validated? |
|---|---|---|
| **Event-driven** (Yext / webhooks / change feeds) | **No** — proposed architecture | **Not validated** |
| Conditional requests, payload fingerprints, metadata, browser (check-based) | **Yes** | Retrospectively validated (see below); live validation in progress |

## What the historical validation shows

> **These results are retrospective, and they cover the check-based (Tier 2) methods only.** They show the *comparison method* works on real data. They are not achieved operational savings, and they say nothing about the proposed event-driven tier.

We used two consecutive weekly AllThePlaces published datasets (public reference data; no re-scraping of ChainXY), giving two weekly comparison cycles and **155 comparable spider-cycle observations**.

| Metric | Result |
|---|---:|
| Comparable observations | 155 |
| Correctly identified **unchanged** | 109 |
| Actual changes **detected** | 45 |
| **Missed changes** | **0** |
| False alarms (conservative) | 1 |
| **Overall classification accuracy** | **99.35%** (154/155) |
| Potential extraction/QA workload reduction | **70.3%** (109/155) |

**What this means:** across two real weekly cycles, the location-fingerprint method correctly flagged every actual change and correctly identified 109 unchanged observations — with zero missed changes.

**What it does not mean:** the 70.3% is a *retrospective workload opportunity* — the share of comparable observations that were unchanged and could have been skipped. It is **not** savings already realized in operation. The single false alarm was a location that gained address details; Site-Signal flagged it as "changed" to be safe, which is the conservative direction.

## Live validation status

> **This is still being tested.** The ability of live monitoring to realize the retrospective opportunity is **not yet proven.**

On **October 30, 2026** we ran a live monitoring capture against the current sources and froze its predictions *before* looking at any new data:

| Prediction | Count |
|---|---:|
| Changed | 4 |
| Unknown | 86 |
| Unchanged (safe skip) | 0 |
| Failed | 0 |
| **Total** | **90** |

The next weekly AllThePlaces dataset **has not yet been published**, so these frozen predictions cannot be scored against ground truth yet.

Two honest observations from this capture:

- Most sources are HTML or JavaScript pages, so live monitoring could not extract a usable location fingerprint — they correctly returned **Unknown** and are routed to the existing extraction process rather than being skipped.
- Only a minority of sources (mostly clean JSON APIs) produced a definitive live signal.

## What this means — and what it doesn't

**Encouraging:**
- The check-based location-comparison method is sound on real, independently published data.
- Zero missed changes across 155 comparable observations.
- Unknowns are handled safely (sent to extraction, not skipped).

**The proposed event-driven tier (not yet built):**
- If authorized Yext / location-API integrations are available, event-driven monitoring could deliver faster, targeted triggers and reduce reliance on polling.
- It requires integration agreements, event schema mapping to ChainXY brands and locations, and meaningful-field filtering — none of which is implemented or validated yet.

**Not yet established:**
- Whether live check-based monitoring can realize recurring savings in production.
- Whether event-driven integrations can be secured and validated.
- Behaviour at ChainXY's full scale (this work covers ~100 sample sites, not the full estate).

**Not claimed:**
- No production-readiness claim.
- No 8,000+ website scalability claim.
- No bandwidth savings: check-based comparison still downloads the payload, so any benefit is in **processing** (extraction and QA), not network.

## Limitations and open questions

- **Conditional-request reliability** (does the site's "not modified" actually track location data?) is not yet proven with live evidence.
- **Event-driven monitoring is unbuilt.** It must be scoped against real provider integrations (Yext and others) before any value can be claimed.
- **One more weekly cycle** is required to score the frozen live predictions.

## Validation reports

- Technical README (engineering): [`docs/README.tech.md`](docs/README.tech.md)
- Retrospective two-cycle report: [`docs/two_run_validation.md`](docs/two_run_validation.md)
- Prospective (live) validation report: [`docs/prospective_validation.md`](docs/prospective_validation.md)
- Raw results: [`data/seed/two_run_summary.json`](data/seed/two_run_summary.json), [`data/seed/cycle2/two_run_summary.json`](data/seed/cycle2/two_run_summary.json), [`data/seed/prospective/predictions.jsonl`](data/seed/prospective/predictions.jsonl)

## Reproduce

```bash
# Historical comparison of two consecutive AllThePlaces runs (check-based tier)
python3 scripts/validate_two_runs.py --download-b \
  --data-a data/seed/raw --run-a 2026-10-17-13-32-31 --run-b 2026-10-10-13-32-41

# Synchronized live baseline + frozen predictions
python3 scripts/prospective_validation.py baseline --run-b 2026-10-17-13-32-31
python3 scripts/prospective_validation.py monitor  --run-b 2026-10-17-13-32-31
```
