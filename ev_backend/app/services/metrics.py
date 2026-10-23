"""Performance and recurring-problem metrics.

Computed from RESOLVED cases only (verified outcomes are the ground truth for
performance). Counts are always returned alongside percentages, and a
small-sample warning flag is set when n < settings-defined threshold (30).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

SMALL_SAMPLE_N = 30


def _where_filters(app_id: Optional[str], data_origin: Optional[str],
                   date_from: Optional[str], date_to: Optional[str]) -> tuple:
    clauses, params = ["c.status = 'resolved'"], []
    if app_id:
        clauses.append("c.app_id = ?")
        params.append(app_id)
    if data_origin:
        clauses.append("c.data_origin = ?")
        params.append(data_origin)
    if date_from:
        clauses.append("r.resolved_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("r.resolved_at <= ?")
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)
    return " AND ".join(clauses), tuple(params)


def performance_metrics(conn: sqlite3.Connection, *, app_id: Optional[str] = None,
                        data_origin: Optional[str] = None,
                        date_from: Optional[str] = None,
                        date_to: Optional[str] = None) -> Dict[str, Any]:
    where, params = _where_filters(app_id, data_origin, date_from, date_to)
    rows = conn.execute(
        f"SELECT r.*, c.app_id, c.data_origin FROM resolutions r "
        f"JOIN cases c ON c.case_id = r.case_id WHERE {where}", params).fetchall()

    total = len(rows)
    counts = {
        "resolved": total,
        "prediction_correct_primary": 0,
        "partial_match": 0,          # primary wrong but prediction in verified set
        "agent_inconclusive": 0,     # diagnosis classification inconclusive/RFI
        "verified_but_agent_inconclusive": 0,
    }
    cat_freq: Dict[str, int] = {}
    for r in rows:
        verified = r["verified_root_cause"]
        cat_freq[verified] = cat_freq.get(verified, 0) + 1
        contributing = json.loads(r["verified_contributing_json"] or "[]")
        pred = r["agent_prediction_category"]
        if r["prediction_correct"] == 1 and pred == verified:
            counts["prediction_correct_primary"] += 1
        elif pred is not None and (pred in contributing):
            counts["partial_match"] += 1
        diag = conn.execute(
            "SELECT classification FROM diagnoses WHERE case_id=? "
            "ORDER BY version DESC, id DESC LIMIT 1", (r["case_id"],)).fetchone()
        if diag and diag["classification"] in ("inconclusive",
                                               "requires_further_investigation"):
            counts["agent_inconclusive"] += 1
            counts["verified_but_agent_inconclusive"] += 1

    def pct(n: int) -> Optional[float]:
        return round(100.0 * n / total, 2) if total else None

    return {
        "total_resolved_cases": total,
        "small_sample_warning": total < SMALL_SAMPLE_N,
        "sample_size_note": (f"{total} resolved case(s). Percentages on small samples "
                             "are unstable; treat as indicative only."
                             if total < SMALL_SAMPLE_N else
                             f"{total} resolved cases."),
        "filters_applied": {"app_id": app_id, "data_origin": data_origin,
                            "date_from": date_from, "date_to": date_to},
        "primary_accuracy_pct": pct(counts["prediction_correct_primary"]),
        "partial_match_pct": pct(counts["prediction_correct_primary"] +
                                 counts["partial_match"]),
        "inconclusive_rate_pct": pct(counts["agent_inconclusive"]),
        "counts": counts,
        "failure_category_frequency": dict(sorted(cat_freq.items(),
                                                  key=lambda kv: -kv[1])),
    }


def recurring_problems(conn: sqlite3.Connection, min_count: int = 2) -> Dict[str, Any]:
    """Category frequency by app / over time and recurring station/charger issues.

    Uses diagnosed+resolved cases' latest diagnosis primary category plus
    matching evidence linked to stations/chargers where available. Cases of ALL
    statuses count towards recurrence patterns (this describes complaint volume,
    not accuracy); data_origin breakdown is returned so mixed provenance is visible.
    """
    by_app = [dict(r) for r in conn.execute(
        "SELECT c.app_id, d.primary_category AS category, COUNT(*) AS count "
        "FROM cases c JOIN diagnoses d ON d.id = ("
        "  SELECT id FROM diagnoses WHERE case_id=c.case_id ORDER BY version DESC, id DESC LIMIT 1) "
        "WHERE d.primary_category IS NOT NULL "
        "GROUP BY c.app_id, d.primary_category ORDER BY count DESC").fetchall()]

    over_time = [dict(r) for r in conn.execute(
        "SELECT substr(c.created_at,1,10) AS date, d.primary_category AS category, "
        "COUNT(*) AS count FROM cases c JOIN diagnoses d ON d.id = ("
        "  SELECT id FROM diagnoses WHERE case_id=c.case_id ORDER BY version DESC, id LIMIT 1) "
        "WHERE d.primary_category IS NOT NULL GROUP BY date, category ORDER BY date").fetchall()]

    recurring_stations = [dict(r) for r in conn.execute(
        "SELECT 'station' AS kind, c.reported_station_id AS identifier, "
        "d.primary_category AS category, COUNT(*) AS count "
        "FROM cases c JOIN diagnoses d ON d.id = ("
        "  SELECT id FROM diagnoses WHERE case_id=c.case_id ORDER BY version DESC, id DESC LIMIT 1) "
        "WHERE c.reported_station_id IS NOT NULL AND d.primary_category IS NOT NULL "
        "GROUP BY identifier, category HAVING count >= ? ORDER BY count DESC",
        (min_count,)).fetchall()]

    # charger ids appear in evidence source_ref like 'charger:CH-12' or session rows
    recurring_chargers: List[Dict[str, Any]] = []
    seen = conn.execute(
        "SELECT e.source_ref, e.supports_category, e.case_id FROM case_evidence e "
        "WHERE e.kind='matching' AND e.source_table IN ('charging_sessions','session_events',"
        "'charger_status_log') AND e.source_ref LIKE 'charger:%'").fetchall()
    agg: Dict[tuple, int] = {}
    for r in seen:
        key = (r["source_ref"].split(":", 1)[1], r["supports_category"])
        agg[key] = agg.get(key, 0) + 1
    for (cid, cat), n in sorted(agg.items(), key=lambda kv: -kv[1]):
        if n >= min_count and cat:
            recurring_chargers.append({"kind": "charger", "identifier": cid,
                                       "category": cat, "count": n})

    origin_counts = {r["data_origin"]: r["c"] for r in conn.execute(
        "SELECT data_origin, COUNT(*) AS c FROM cases GROUP BY data_origin").fetchall()}

    return {
        "by_app_category": by_app,
        "over_time": over_time,
        "recurring_stations": recurring_stations,
        "recurring_chargers": recurring_chargers,
        "data_origin_counts": origin_counts,
        "note": ("Complaint-volume patterns across all diagnosed cases (not only "
                 "resolved ones). Always check data_origin composition before "
                 "drawing conclusions; simulated data says nothing about real networks."),
    }
