# kindly growth pipeline

Lead engine for Phase 1: sources → SQLite → ICP scoring → funnel stages → trigger actions → dashboard.

## Files

| File | What |
|---|---|
| `kindly_pipeline.py` | The whole engine. Run it to (re)ingest and rebuild the dashboard. |
| `sources_sample.csv` | Reference lead batch (fictional `.example` companies). Replace with real exports. |
| `leads.db` | SQLite system of record (leads + touches). |
| `funnel-dashboard.html` | Self-contained dashboard — open in any browser. Search, filter, sort. |
| `next_actions.json` | Per-lead next action from the trigger rules — feed to CRM or a task list. |

## Run

```bash
python3 kindly_pipeline.py              # ingest sample + rebuild dashboard
python3 kindly_pipeline.py --csv my_batch.csv
```

Note: SQLite needs a filesystem with locking — run from a local folder, not a synced/network mount.

## Adding real sources

Subclass `Source` (one class per feed) and add it to the `ingest([...])` call:

- **Tender portals** (TED, national portals) — tenders with no-cloud/data-residency clauses are tier-A gold.
- **Job boards** — companies hiring process analysts, RPA devs, knowledge managers are feeling the pain.
- **Trade press / RSS** — cost programs, transformation budgets, audit findings.
- **Conference speaker pages** — ops/compliance leaders who speak publicly are reachable.

## Compliance guardrails (read before scraping)

- Public **business** data only (companies, roles, published signals). No personal scraping.
- Respect robots.txt and site ToS; prefer official APIs and exports.
- GDPR: B2B outreach on legitimate-interest basis — keep the signal that justifies contact
  in `score_why`, honor opt-outs immediately, and don't buy shady lists.
- Communities: participate under your real name and affiliation. Value first, no astroturfing —
  it's also just better marketing.

## Wiring the CRM

`TRIGGER_RULES` in the script is the playbook (stage × condition → action). When the HubSpot
connector is authorized in Claude settings, these map 1:1 to workflows: lifecycle stage =
funnel stage, lead score property = ICP score, tasks = next_actions.json.
