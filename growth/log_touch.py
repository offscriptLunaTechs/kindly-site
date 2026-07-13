#!/usr/bin/env python3
"""
kindly touch logger — record outreach + move funnel stages
===========================================================
Mount-safe (runs SQLite on local disk, syncs back).

Usage:
  python3 log_touch.py <domain-or-company> [--channel call] [--note "..."] [--stage IN_CONVERSATION]
  python3 log_touch.py --list [STAGE]          # show leads (optionally by stage)

Examples:
  python3 log_touch.py lukb.ch --channel call --note "discovery call booked" --stage IN_CONVERSATION
  python3 log_touch.py "Bird & Bird" --channel email --note "sent hook #1"
"""
import argparse, shutil, sqlite3, sys, tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "leads.db"
STAGES = ["COLD", "HOOKED", "IN_CONVERSATION", "AUDIT_PROPOSED", "AUDIT_RUNNING",
          "CORE_PROPOSED", "CORE_DEPLOYED", "RETAINER", "LOST"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="lead domain or company name (fuzzy)")
    ap.add_argument("--channel", default="note")
    ap.add_argument("--note", default="")
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--list", nargs="?", const="ALL", metavar="STAGE")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        tmp_db = Path(td) / "leads.db"
        shutil.copy(DB, tmp_db)
        con = sqlite3.connect(tmp_db)
        con.row_factory = sqlite3.Row

        if a.list:
            where = "" if a.list == "ALL" else f" WHERE stage='{a.list}'"
            for r in con.execute(f"SELECT company,domain,tier,score,stage,last_touch FROM leads{where} ORDER BY score DESC"):
                print(f"{r['tier']} {r['score']:>3}  {r['stage']:<16} {r['company']}  ({r['domain']})  last: {r['last_touch'] or '-'}")
            return

        if not a.query:
            ap.error("give a domain/company, or use --list")

        q = f"%{a.query.lower()}%"
        rows = con.execute("SELECT * FROM leads WHERE lower(domain) LIKE ? OR lower(company) LIKE ?", (q, q)).fetchall()
        if not rows:
            sys.exit(f"no lead matching '{a.query}'")
        if len(rows) > 1:
            for r in rows:
                print(f"  {r['company']} ({r['domain']})")
            sys.exit("ambiguous — be more specific")
        lead = rows[0]

        ts = datetime.now().isoformat(timespec="seconds")
        con.execute("INSERT INTO touches(lead_id, ts, channel, note) VALUES(?,?,?,?)",
                    (lead["id"], ts, a.channel, a.note))
        con.execute("UPDATE leads SET last_touch=? WHERE id=?", (ts, lead["id"]))
        moved = ""
        if a.stage and a.stage != lead["stage"]:
            con.execute("UPDATE leads SET stage=? WHERE id=?", (a.stage, lead["id"]))
            moved = f"  stage: {lead['stage']} -> {a.stage}"
        con.commit()
        con.close()
        shutil.copy(tmp_db, DB)
        print(f"logged {a.channel} touch on {lead['company']}{moved}")
        print("tip: python3 run_backend.py to refresh dashboard + scoreboard")


if __name__ == "__main__":
    main()
