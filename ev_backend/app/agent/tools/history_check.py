"""Tool: history_check — charging-history completeness vs backend records.

Simulated app-side history defects are recorded as session_events rows with
event_type in ('history_missing','history_duplicate','history_amount_mismatch').
Signals: missing_completed_session_in_history, duplicate_history_entry,
history_amount_mismatch_vs_payment.
"""
from __future__ import annotations

import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "history_check"
RELATED_CATEGORIES = ["incorrect_or_incomplete_charging_history",
                      "payment_or_billing_failure"]
REQUIRES_SESSION = False
DESCRIPTION = "Verify charging history entries against backend records."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        user = c.user_ref or (state.matched_session or {}).get("user_ref")
        if not user:
            return CheckResult(TOOL_NAME, "no_data",
                               "Cannot audit history without a user reference.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="History audit impossible (no user ref).",
                                   weight=0.3)],
                               signals=["history_data_unavailable"])
        cur = conn.execute(
            "SELECT * FROM charging_sessions WHERE app_id=? AND user_ref=? "
            "ORDER BY start_ts DESC LIMIT 50", (c.app_id, user))
        sessions = [dict(r) for r in cur.fetchall()]

        def _events(event_type: str):
            sql = ("SELECT e.* FROM session_events e JOIN charging_sessions s "
                   "ON e.session_id=s.session_id WHERE s.user_ref=? AND s.app_id=? "
                   "AND e.event_type=? ORDER BY e.event_ts DESC")
            return [dict(r) for r in conn.execute(sql, (user, c.app_id, event_type)).fetchall()]

        hm = _events("history_missing")
        hd = _events("history_duplicate")
        am = _events("history_amount_mismatch")

        signals, evidence = [], []
        if hm:
            signals.append("missing_completed_session_in_history")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"{len(hm)} completed session(s) present in backend but "
                            "missing from the app's history view.",
                source_table="session_events", source_ref=str(hm[0]["event_id"]),
                weight=0.65))
        if hd:
            signals.append("duplicate_history_entry")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"{len(hd)} duplicated entry/entries seen in history view.",
                source_table="session_events", source_ref=str(hd[0]["event_id"]),
                weight=0.6))
        if am:
            signals.append("history_amount_mismatch_vs_payment")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description="History amount shown differs from payment record amount.",
                source_table="session_events", source_ref=str(am[0]["event_id"]),
                weight=0.6))
        if not signals:
            evidence.append(Evidence(
                kind="neutral", tool_name=TOOL_NAME,
                description=f"History view consistent with {len(sessions)} backend "
                            "session(s).",
                source_table="charging_sessions", source_ref=user, weight=0.2))
            signals.append("history_ok")
            return CheckResult(TOOL_NAME, "ok", "History consistent.",
                               findings={"sessions": sessions[:10]},
                               evidence_items=evidence, signals=signals)
        return CheckResult(TOOL_NAME, "ok",
                           "History anomalies: " + ", ".join(signals),
                           findings={"sessions": sessions[:10]},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
