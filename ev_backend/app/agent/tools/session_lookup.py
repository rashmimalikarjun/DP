"""Tool: session_lookup — find the charging session matching the complaint.

Inputs used: session_ref, app_id + user_ref, station_id, complaint_ts (±24h).
If exactly one plausible session is found it is stored on CaseState.matched_session.
Signals emitted: matched_session_found / no_matching_session / ambiguous_sessions.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "session_lookup"
RELATED_CATEGORIES = ["session_fails_to_start", "session_stops_unexpectedly",
                      "payment_or_billing_failure"]
REQUIRES_SESSION = False
DESCRIPTION = "Locate the charging session the complaint refers to."


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        rows: list[dict] = []
        if c.session_ref:
            cur = conn.execute(
                "SELECT * FROM charging_sessions WHERE session_id LIKE ?",
                (f"%{c.session_ref}%",))
            rows = [dict(r) for r in cur.fetchall()]
        if not rows and c.user_ref:
            cur = conn.execute(
                "SELECT * FROM charging_sessions WHERE app_id=? AND user_ref=? "
                "ORDER BY start_ts DESC LIMIT 10", (c.app_id, c.user_ref))
            rows = [dict(r) for r in cur.fetchall()]
            if c.station_id:
                rows = [r for r in rows if r["station_id"] == c.station_id] or rows
        if not rows and c.station_id and c.complaint_ts:
            t0 = _parse(c.complaint_ts)
            if t0:
                lo = (t0 - timedelta(hours=24)).isoformat(sep=" ")
                hi = (t0 + timedelta(hours=24)).isoformat(sep=" ")
                cur = conn.execute(
                    "SELECT * FROM charging_sessions WHERE app_id=? AND station_id=? "
                    "AND start_ts BETWEEN ? AND ? ORDER BY start_ts DESC LIMIT 10",
                    (c.app_id, c.station_id, lo, hi))
                rows = [dict(r) for r in cur.fetchall()]

        if len(rows) == 1:
            state.matched_session = rows[0]
            s = rows[0]
            ev = Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=(f"Matched session {s['session_id']} at station "
                             f"{s['station_id']} (status={s['status']}, "
                             f"energy={s['energy_kwh']} kWh)"),
                source_table="charging_sessions", source_ref=s["session_id"],
                weight=0.35)
            return CheckResult(TOOL_NAME, "ok",
                               f"Found exactly one matching session ({s['session_id']}).",
                               findings={"session": s}, evidence_items=[ev],
                               signals=["matched_session_found"])
        if len(rows) > 1:
            # Prefer a failed/abnormal session as candidate but flag ambiguity.
            abnormal = [r for r in rows if (r.get("status") or "").lower()
                        not in ("completed", "success")]
            chosen = abnormal[0] if len(abnormal) == 1 else rows[0]
            state.matched_session = chosen
            ev = Evidence(kind="neutral", tool_name=TOOL_NAME,
                          description=f"{len(rows)} candidate sessions found; "
                                      f"proceeding with {chosen['session_id']} "
                                      "(ambiguity noted)",
                          source_table="charging_sessions",
                          source_ref=chosen["session_id"], weight=0.2)
            return CheckResult(TOOL_NAME, "ok",
                               f"{len(rows)} candidate sessions; using {chosen['session_id']}.",
                               findings={"sessions": rows, "session": chosen},
                               evidence_items=[ev], signals=["ambiguous_sessions"])
        ev = Evidence(kind="missing", tool_name=TOOL_NAME,
                      description="No charging session matches the complaint details "
                                  "(session ref / user / station+time).",
                      source_table="charging_sessions", weight=0.4)
        return CheckResult(TOOL_NAME, "no_data",
                           "No matching session found.",
                           findings={"sessions": []}, evidence_items=[ev],
                           signals=["no_matching_session"])
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}",
                           findings={}, evidence_items=[], signals=[])
