#!/usr/bin/env python3
"""
kindly backend runner — autonomous mode
=======================================
Wraps kindly_pipeline.py so it always executes on LOCAL disk (SQLite breaks on
synced/network mounts), then syncs results back. Also computes the Phase 1
scoreboard from the playbook.

What one run does:
  1. Copy pipeline + DB to a local temp dir.
  2. Ingest every new CSV dropped in growth/inbox/ (then archive to inbox/processed/).
  3. Re-score, rebuild funnel-dashboard.html + next_actions.json.
  4. Compute phase-scoreboard.md / .json (DB metrics + manual_metrics.json).
  5. Sync artifacts back next to this script.

Usage:  python3 run_backend.py
"""
import json, shutil, sqlite3, subprocess, sys, tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
INBOX = HERE / "inbox"
PROCESSED = INBOX / "processed"

# Phase 1 targets (Growth Playbook §8, month-6). metric -> (target, how measured)
TARGETS = [
    ("Leads in DB (scored)",        500, "db:leads"),
    ("Tier-A accounts",              60, "db:tier_a"),
    ("Discovery calls",              40, "db:calls"),
    ("Pilot audits sold",             3, "db:audits"),
    ("Paid Core deployment",          1, "db:core"),
    ("Case study published",          1, "manual:case_studies_published"),
    ("Newsletter subscribers",      400, "manual:newsletter_subscribers"),
    ("'The Needful' committed members", 8, "manual:needful_members"),
]
SOLD_STAGES = ("AUDIT_RUNNING", "CORE_PROPOSED", "CORE_DEPLOYED", "RETAINER")
CORE_STAGES = ("CORE_DEPLOYED", "RETAINER")


def db_metrics(db_path):
    con = sqlite3.connect(db_path)
    q = lambda sql, args=(): con.execute(sql, args).fetchone()[0]
    m = {
        "leads":  q("SELECT COUNT(*) FROM leads"),
        "tier_a": q("SELECT COUNT(*) FROM leads WHERE tier='A'"),
        "calls":  q("SELECT COUNT(*) FROM touches WHERE lower(channel) LIKE '%call%'"),
        "audits": q("SELECT COUNT(*) FROM leads WHERE stage IN (?,?,?,?)", SOLD_STAGES),
        "core":   q("SELECT COUNT(*) FROM leads WHERE stage IN (?,?)", CORE_STAGES),
    }
    stages = dict(con.execute("SELECT stage, COUNT(*) FROM leads GROUP BY stage").fetchall())
    con.close()
    return m, stages


def scoreboard(db_path):
    manual_file = HERE / "manual_metrics.json"
    manual = json.loads(manual_file.read_text()) if manual_file.exists() else {}
    dbm, stages = db_metrics(db_path)
    rows, board = [], []
    for name, target, src in TARGETS:
        kind, key = src.split(":")
        val = dbm.get(key, 0) if kind == "db" else int(manual.get(key, 0))
        pct = min(100, round(100 * val / target))
        status = "GREEN" if pct >= 100 else "AMBER" if pct >= 50 else "RED"
        rows.append(f"| {name} | {val} | {target} | {pct}% {status} |")
        board.append({"metric": name, "value": val, "target": target, "pct": pct})
    md = "\n".join([
        f"# kindly · Phase 1 scoreboard — {date.today().isoformat()}",
        "",
        "Phase 1 (0-6 months): autonomous pipeline, community, content.",
        "Rule: red two Fridays running -> change the tactic, not the target.",
        "",
        "| Metric | Now | Target (m6) | Progress |",
        "|---|---|---|---|",
        *rows,
        "",
        "**Funnel:** " + (", ".join(f"{k}: {v}" for k, v in stages.items()) or "empty"),
        "",
        "_Manual metrics (newsletter, community, case studies) live in manual_metrics.json — update them there._",
    ])
    (HERE / "phase-scoreboard.md").write_text(md, encoding="utf-8")
    (HERE / "phase-scoreboard.json").write_text(
        json.dumps({"date": date.today().isoformat(), "board": board, "funnel": stages}, indent=2),
        encoding="utf-8")

    # --- run history (trend tracking; last run of the day wins) ---
    hist = HERE / "history.csv"
    today = date.today().isoformat()
    header = "date," + ",".join(b["metric"] for b in board) + ",funnel"
    lines = [l for l in hist.read_text(encoding="utf-8").splitlines()
             if l and not l.startswith(today)] if hist.exists() else [header]
    lines.append(today + "," + ",".join(str(b["value"]) for b in board)
                 + "," + json.dumps(stages).replace(",", ";"))
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return board, stages


def main():
    INBOX.mkdir(exist_ok=True)
    PROCESSED.mkdir(exist_ok=True)
    new_csvs = sorted(p for p in INBOX.glob("*.csv"))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shutil.copy(HERE / "kindly_pipeline.py", tmp)
        if (HERE / "sources_sample.csv").exists():
            shutil.copy(HERE / "sources_sample.csv", tmp)  # default ingest is idempotent
        if (HERE / "leads.db").exists():
            shutil.copy(HERE / "leads.db", tmp)
        for c in new_csvs:
            shutil.copy(c, tmp)

        runs = [["python3", "kindly_pipeline.py"]] if not new_csvs else \
               [["python3", "kindly_pipeline.py", "--csv", c.name] for c in new_csvs]
        for cmd in runs:
            r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
            print(r.stdout.strip())
            if r.returncode != 0:
                print(r.stderr, file=sys.stderr)
                sys.exit(f"pipeline failed: {' '.join(cmd)}")

        board, stages = scoreboard(tmp / "leads.db")

        for f in ("leads.db", "funnel-dashboard.html", "next_actions.json"):
            shutil.copy(tmp / f, HERE / f)

    for c in new_csvs:
        shutil.move(str(c), PROCESSED / c.name)

    print(f"synced. ingested {len(new_csvs)} csv batch(es). funnel: {stages}")
    reds = [b["metric"] for b in board if b["pct"] < 50]
    if reds:
        print("red metrics: " + "; ".join(reds))


if __name__ == "__main__":
    main()
