#!/usr/bin/env python3
"""Seed the EV diagnosis database with reference rows + deterministic synthetic data.

Usage (from ev_backend/):
    python scripts/seed_database.py                 # seed default DB (ev_diagnosis.db)
    python scripts/seed_database.py --reset         # delete previous simulated rows first
    python scripts/seed_database.py --seed 7 --per-template 12
    python scripts/seed_database.py --db /tmp/demo.db

NEVER touches catering.db (guarded by app.core.db.assert_safe_db_path).
All generated rows are data_origin='simulated'.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db as dbmod                      # noqa: E402
from app.data.seed_rules import seed_rules_if_empty   # noqa: E402
from app.data.synthetic_generator import (            # noqa: E402
    SyntheticDataset, reset_simulated_rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None,
                    help="DB path (default: EV_DB_PATH env or ../ev_diagnosis.db)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--per-template", type=int, default=10,
                   help="variants per template (25 templates -> 250 cases at 10)")
    ap.add_argument("--reset", action="store_true",
                    help="delete previously seeded simulated rows first")
    args = ap.parse_args()

    path = args.db or dbmod.get_db_path()
    dbmod.assert_safe_db_path(path)
    conn = dbmod.connect(path)
    try:
        dbmod.init_db(conn)
        dbmod.sync_config_tables(conn)
        added = seed_rules_if_empty(conn)
        if not conn.execute("SELECT 1 FROM reviewers LIMIT 1").fetchone():
            conn.executemany(
                "INSERT INTO reviewers(reviewer_id, name, role, active)"
                " VALUES (?,?,?,?)",
                [("rev-001", "A. Analyst", "diagnosis_reviewer", 1),
                 ("rev-002", "B. Engineer", "field_engineer", 1),
                 ("rev-003", "C. Inactive", "diagnosis_reviewer", 0)])
            conn.commit()
        removed = reset_simulated_rows(conn) if args.reset else {}
        ds = SyntheticDataset(seed=args.seed, n_per_template=args.per_template)
        counts = ds.write(conn)
        print(f"Database: {path}")
        print(f"Knowledge rules inserted: {added} (idempotent)")
        if args.reset:
            print(f"Reset removed: {removed}")
        print(f"Synthetic rows written: {counts}")
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM cases WHERE data_origin='simulated'"
        ).fetchone()["n"]
        print(f"Total simulated labelled cases in DB: {total}")
        print("NOTE: all seeded data is SIMULATED (fabricated for testing); "
              "it does not describe any real network or company.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
