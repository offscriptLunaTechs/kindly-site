#!/usr/bin/env python3
"""
kindly growth pipeline — lead engine v1
========================================
sources -> normalize -> dedupe -> ICP score -> funnel stage -> triggers -> dashboard

Design:
  * Every lead source implements Source.fetch() -> list[RawLead].
  * CSVSeedSource ships as the reference implementation (sample data included).
    Add real sources (registry APIs, job boards, RSS/press feeds, tender portals)
    by subclassing Source — one class per feed. Respect robots.txt/ToS/GDPR:
    collect business-contact data only, from public sources, with a lawful basis.
  * SQLite is the system of record (leads.db). The dashboard is regenerated from
    the DB on every run — self-contained HTML, no server needed.
  * TriggerEngine encodes the CRM playbook (stage x condition -> next action).
    Wire the same rules into HubSpot workflows when the connector is live.

Usage:
  python3 kindly_pipeline.py            # ingest sample CSV, score, build dashboard
  python3 kindly_pipeline.py --csv f.csv  # ingest another CSV batch
"""
import csv, json, sqlite3, sys, html
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "leads.db"

# ----------------------------------------------------------------------------- model
@dataclass
class RawLead:
    company: str; domain: str; sector: str = ""; employees: int = 0
    country: str = ""; contact_name: str = ""; contact_role: str = ""
    signal: str = ""; source: str = ""

STAGES = ["COLD", "HOOKED", "IN_CONVERSATION", "AUDIT_PROPOSED", "AUDIT_RUNNING",
          "CORE_PROPOSED", "CORE_DEPLOYED", "RETAINER", "LOST"]

# ----------------------------------------------------------------------------- sources
class Source:
    name = "base"
    def fetch(self) -> list:
        raise NotImplementedError

class CSVSeedSource(Source):
    """Reference source: any CSV with the sample header. Swap in real feeds later."""
    name = "csv_seed"
    def __init__(self, path):
        self.path = Path(path)
    def fetch(self):
        out = []
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out.append(RawLead(
                    company=row["company"].strip(), domain=row["domain"].strip().lower(),
                    sector=row.get("sector", "").strip().lower(),
                    employees=int(row.get("employees") or 0),
                    country=row.get("country", "").strip(),
                    contact_name=row.get("contact_name", "").strip(),
                    contact_role=row.get("contact_role", "").strip(),
                    signal=row.get("signal", "").strip(),
                    source=row.get("source", "csv").strip()))
        return out

# Stubs to implement next — each returns RawLead objects from a public feed:
#   class TenderPortalSource(Source): ...   # TED / national tender portals (no-cloud clauses = gold)
#   class JobBoardSource(Source): ...       # postings for process analysts / RPA / knowledge managers
#   class PressRSSSource(Source): ...       # trade press: transformation programs, cost programs
#   class EventSpeakerSource(Source): ...   # ops/compliance conference speaker lists (public pages)

# ----------------------------------------------------------------------------- scoring
REGULATED = {"banking": 25, "insurance": 25, "healthcare": 25, "legal": 22,
             "public sector": 22, "energy": 18, "manufacturing": 15, "logistics": 10}
SIGNAL_KEYWORDS = [  # (keyword, points, why)
    ("no-cloud", 25, "explicit cloud prohibition"), ("cloud-ai ban", 25, "explicit cloud prohibition"),
    ("data residency", 20, "sovereignty pressure"), ("on-prem", 20, "on-prem intent"),
    ("tender", 15, "active procurement"), ("rfp", 15, "active procurement"),
    ("hiring", 12, "ops hiring signal"), ("backlog", 12, "visible ops pain"),
    ("manual", 12, "visible ops pain"), ("cost program", 12, "cost pressure"),
    ("compliance", 10, "compliance pressure"), ("modernization", 10, "transformation budget"),
    ("transformation", 10, "transformation budget"), ("defense", 15, "strict data rules"),
    ("gxp", 12, "regulated quality process"), ("audit", 10, "auditor pressure"),
]
ROLE_BONUS = {"coo": 10, "ciso": 10, "cio": 8, "cdo": 8, "operations": 8, "compliance": 8,
              "transformation": 8, "managing partner": 6, "ceo": 6, "cto": 6, "quality": 5}

def score(lead: RawLead):
    s, why = 0, []
    if lead.sector in REGULATED:
        s += REGULATED[lead.sector]; why.append(f"regulated sector: {lead.sector}")
    if 500 <= lead.employees <= 10000:
        s += 20; why.append("ICP size band 500–10k")
    elif lead.employees:
        s += 5
    sig = lead.signal.lower()
    for kw, pts, label in SIGNAL_KEYWORDS:
        if kw in sig:
            s += pts; why.append(label)
    role = lead.contact_role.lower()
    for kw, pts in ROLE_BONUS.items():
        if kw in role:
            s += pts; why.append(f"buyer-map role: {lead.contact_role}"); break
    return min(s, 100), "; ".join(dict.fromkeys(why))

def tier(s):
    return "A" if s >= 70 else "B" if s >= 50 else "C"

# ----------------------------------------------------------------------------- triggers
TRIGGER_RULES = [
    # (stage, condition description, action) — mirror these in the CRM as workflows
    ("COLD", "tier A", "Send personalized hook #1 (sector-specific 'kindly reminder' card) within 48h"),
    ("COLD", "tier B", "Enroll in 4-touch nurture sequence (value posts, no pitch)"),
    ("COLD", "tier C", "Newsletter only; re-score monthly"),
    ("HOOKED", "engaged with content", "Send Ops Waste Self-Check (lead magnet); book-a-call link"),
    ("HOOKED", "no reply after 7d", "One witty kindly-nudge follow-up, then park 30d"),
    ("IN_CONVERSATION", "call done", "Send tailored audit one-pager within 24h"),
    ("AUDIT_PROPOSED", "no decision after 10d", "Escalate: offer scope-down option (2-week mini-audit)"),
    ("AUDIT_RUNNING", "week 3 of audit", "Pre-brief Core deployment proposal with champion"),
    ("CORE_PROPOSED", "CISO objection", "Send security whitepaper + offer air-gap reference call"),
    ("CORE_DEPLOYED", "day 30", "Adoption review + People program proposal"),
    ("RETAINER", "quarterly", "QBR: telemetry findings -> next audit module"),
]

def next_action(stage, tr, days_idle):
    for st, cond, action in TRIGGER_RULES:
        if st != stage:
            continue
        if "tier A" in cond and tr == "A": return action
        if "tier B" in cond and tr == "B": return action
        if "tier C" in cond and tr == "C": return action
        if "7d" in cond and days_idle >= 7: return action
        if "10d" in cond and days_idle >= 10: return action
        if st == stage and "tier" not in cond and "d" not in cond: return action
    return "Monitor; re-score on next signal"

# ----------------------------------------------------------------------------- db
SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY, company TEXT, domain TEXT UNIQUE, sector TEXT,
  employees INT, country TEXT, contact_name TEXT, contact_role TEXT,
  signal TEXT, source TEXT, score INT, tier TEXT, score_why TEXT,
  stage TEXT DEFAULT 'COLD', last_touch TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS touches(
  id INTEGER PRIMARY KEY, lead_id INT, ts TEXT, channel TEXT, note TEXT);
"""

def ingest(sources):
    con = sqlite3.connect(DB); con.executescript(SCHEMA)
    n_new = n_upd = 0
    for src in sources:
        for L in src.fetch():
            s, why = score(L)
            row = (L.company, L.domain, L.sector, L.employees, L.country, L.contact_name,
                   L.contact_role, L.signal, L.source, s, tier(s), why, date.today().isoformat())
            cur = con.execute("SELECT id FROM leads WHERE domain=?", (L.domain,))
            if cur.fetchone():
                con.execute("""UPDATE leads SET signal=?, source=?, score=?, tier=?, score_why=?
                               WHERE domain=?""", (L.signal, L.source, s, tier(s), why, L.domain))
                n_upd += 1
            else:
                con.execute("""INSERT INTO leads(company,domain,sector,employees,country,contact_name,
                               contact_role,signal,source,score,tier,score_why,created)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
                n_new += 1
    con.commit(); con.close()
    return n_new, n_upd

# ----------------------------------------------------------------------------- dashboard
def build_dashboard():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    leads = [dict(r) for r in con.execute("SELECT * FROM leads ORDER BY score DESC")]
    con.close()
    today = date.today()
    for L in leads:
        idle = (today - datetime.fromisoformat(L["last_touch"] or L["created"]).date()).days
        L["next_action"] = next_action(L["stage"], L["tier"], idle)
    (HERE / "next_actions.json").write_text(json.dumps(
        [{"company": L["company"], "stage": L["stage"], "tier": L["tier"],
          "action": L["next_action"]} for L in leads], indent=2), encoding="utf-8")

    data = json.dumps(leads).replace("</", "<\\/")
    stages = json.dumps(STAGES)
    page = DASH_TEMPLATE.replace("__DATA__", data).replace("__STAGES__", stages) \
                        .replace("__DATE__", today.isoformat())
    (HERE / "funnel-dashboard.html").write_text(page, encoding="utf-8")

DASH_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>kindly · funnel</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--coral:#FF6F61;--amber:#FFB25E;--ink:#23253A;--cream:#FFF6EE;--clay:#8A6F66;--green:#2F6D5F;}
*{margin:0;padding:0;box-sizing:border-box}body{font-family:Inter,sans-serif;background:var(--cream);color:var(--ink);padding:36px}
h1{font-family:Nunito;font-size:30px}h2{font-family:Nunito;font-size:18px;margin:28px 0 12px}
.meta{color:var(--clay);font-size:13px;margin-top:4px}
.kpis{display:flex;gap:14px;margin-top:22px;flex-wrap:wrap}
.kpi{background:#fff;border:1px solid #EDDCCF;border-radius:14px;padding:16px 22px;min-width:140px}
.kpi b{font-family:Nunito;font-size:30px;color:var(--coral)}.kpi span{font-size:12px;color:var(--clay);display:block}
.funnel{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.stagebox{flex:1;min-width:100px;background:#fff;border:1px solid #EDDCCF;border-radius:12px;padding:12px;text-align:center}
.stagebox b{font-family:Nunito;font-size:24px;display:block}.stagebox span{font-size:10px;letter-spacing:1px;color:var(--clay)}
.bar{height:6px;border-radius:3px;background:linear-gradient(90deg,var(--coral),var(--amber));margin-top:8px}
.controls{margin:10px 0 12px;display:flex;gap:10px;flex-wrap:wrap}
input,select{padding:9px 12px;border:1.5px solid #E5D5C8;border-radius:10px;background:#fff;font-family:Inter;font-size:13px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:14px;overflow:hidden;font-size:13px}
th{background:var(--ink);color:var(--cream);text-align:left;padding:10px 12px;font-family:Nunito;font-size:12px;cursor:pointer;white-space:nowrap}
td{padding:9px 12px;border-top:1px solid #F3E7DB;vertical-align:top}
tr:hover td{background:#FBF1E8}
.tierA{background:var(--green);color:#fff;border-radius:99px;padding:2px 10px;font-weight:600;font-size:11px}
.tierB{background:var(--amber);color:var(--ink);border-radius:99px;padding:2px 10px;font-weight:600;font-size:11px}
.tierC{background:#E5D5C8;color:var(--ink);border-radius:99px;padding:2px 10px;font-weight:600;font-size:11px}
.score{font-family:Nunito;font-weight:800}
.action{color:var(--green);font-size:12px}
.why{color:var(--clay);font-size:11px}
</style></head><body>
<h1>kindly · lead funnel</h1>
<div class="meta">System of record: leads.db · regenerated __DATE__ · rerun kindly_pipeline.py to refresh</div>
<div class="kpis" id="kpis"></div>
<h2>Funnel</h2><div class="funnel" id="funnel"></div>
<h2>Leads</h2>
<div class="controls">
  <input id="q" placeholder="Search company / signal…" oninput="draw()">
  <select id="ftier" onchange="draw()"><option value="">All tiers</option><option>A</option><option>B</option><option>C</option></select>
  <select id="fstage" onchange="draw()"><option value="">All stages</option></select>
  <select id="fsector" onchange="draw()"><option value="">All sectors</option></select>
</div>
<table id="tbl"><thead><tr>
<th onclick="sortBy('score')">Score</th><th>Tier</th><th onclick="sortBy('company')">Company</th>
<th>Sector</th><th>Size</th><th>Contact</th><th>Signal</th><th>Stage</th><th>Next action (trigger)</th>
</tr></thead><tbody></tbody></table>
<script>
const DATA=__DATA__, STAGES=__STAGES__;
let sortKey='score', sortDir=-1;
const sel=(id)=>document.getElementById(id);
STAGES.forEach(s=>sel('fstage').insertAdjacentHTML('beforeend',`<option>${s}</option>`));
[...new Set(DATA.map(l=>l.sector))].sort().forEach(s=>sel('fsector').insertAdjacentHTML('beforeend',`<option>${s}</option>`));
function sortBy(k){sortDir=(sortKey===k)?-sortDir:-1;sortKey=k;draw();}
function draw(){
  const q=sel('q').value.toLowerCase(), t=sel('ftier').value, st=sel('fstage').value, sec=sel('fsector').value;
  let rows=DATA.filter(l=>(!q||(l.company+l.signal+l.contact_name).toLowerCase().includes(q))&&(!t||l.tier===t)&&(!st||l.stage===st)&&(!sec||l.sector===sec));
  rows.sort((a,b)=>(a[sortKey]>b[sortKey]?1:-1)*sortDir);
  sel('tbl').querySelector('tbody').innerHTML=rows.map(l=>`<tr>
    <td class="score">${l.score}</td><td><span class="tier${l.tier}">${l.tier}</span></td>
    <td><b>${l.company}</b><div class="why">${l.score_why||''}</div></td>
    <td>${l.sector}</td><td>${l.employees.toLocaleString()}</td>
    <td>${l.contact_name}<div class="why">${l.contact_role}</div></td>
    <td>${l.signal}</td><td>${l.stage}</td><td class="action">${l.next_action}</td></tr>`).join('');
  const ka=DATA.filter(l=>l.tier==='A').length, avg=Math.round(DATA.reduce((s,l)=>s+l.score,0)/DATA.length);
  sel('kpis').innerHTML=`
    <div class="kpi"><b>${DATA.length}</b><span>leads in DB</span></div>
    <div class="kpi"><b>${ka}</b><span>tier-A (call now)</span></div>
    <div class="kpi"><b>${avg}</b><span>avg ICP score</span></div>
    <div class="kpi"><b>${DATA.filter(l=>l.stage!=='COLD'&&l.stage!=='LOST').length}</b><span>active in funnel</span></div>`;
  const mx=Math.max(...STAGES.map(s=>DATA.filter(l=>l.stage===s).length),1);
  sel('funnel').innerHTML=STAGES.map(s=>{const n=DATA.filter(l=>l.stage===s).length;
    return `<div class="stagebox"><b>${n}</b><span>${s.replace(/_/g,' ')}</span><div class="bar" style="width:${Math.max(n/mx*100,4)}%"></div></div>`}).join('');
}
draw();
</script></body></html>"""

# ----------------------------------------------------------------------------- main
if __name__ == "__main__":
    csv_path = HERE / "sources_sample.csv"
    if "--csv" in sys.argv:
        csv_path = Path(sys.argv[sys.argv.index("--csv") + 1])
    new, upd = ingest([CSVSeedSource(csv_path)])
    build_dashboard()
    print(f"ingested: {new} new, {upd} updated -> {DB.name}")
    print("dashboard: funnel-dashboard.html · actions: next_actions.json")
