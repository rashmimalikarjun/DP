"""Tool: handshake_check — vehicle-charger negotiation events.

Signals: handshake_failed, protocol_mismatch, no_response_from_vehicle.
"""
from __future__ import annotations

import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "handshake_check"
RELATED_CATEGORIES = ["vehicle_charger_communication_issue", "session_fails_to_start"]
DESCRIPTION = "Inspect vehicle<->charger handshake events."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        s = state.matched_session or {}
        sid, charger = s.get("session_id"), s.get("charger_id")
        if not sid and not charger:
            return CheckResult(TOOL_NAME, "no_data",
                               "No session/charger reference for handshake inspection.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="Handshake logs unavailable.",
                                   weight=0.3)],
                               signals=["handshake_data_unavailable"])
        if sid:
            cur = conn.execute(
                "SELECT * FROM session_events WHERE session_id=? AND event_type IN "
                "('handshake_failed','handshake_ok','protocol_mismatch','no_response_from_vehicle') "
                "ORDER BY event_ts ASC", (sid,))
        else:
            cur = conn.execute(
                "SELECT * FROM session_events WHERE charger_id=? AND event_type IN "
                "('handshake_failed','handshake_ok','protocol_mismatch','no_response_from_vehicle') "
                "ORDER BY event_ts ASC LIMIT 20", (charger,))
        rows = [dict(r) for r in cur.fetchall()]
        types = {r["event_type"] for r in rows}
        signals, evidence = [], []
        if "handshake_failed" in types:
            signals.append("handshake_failed")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description="Handshake failure event recorded between vehicle and charger.",
                source_table="session_events",
                source_ref=str(next(r for r in rows if r['event_type'] == 'handshake_failed')['event_id']),
                weight=0.7))
        if "protocol_mismatch" in types:
            signals.append("protocol_mismatch")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description="Protocol mismatch during negotiation.",
                source_table="session_events", source_ref=sid or charger, weight=0.6))
        if "no_response_from_vehicle" in types:
            signals.append("no_response_from_vehicle")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description="Vehicle did not respond during negotiation.",
                source_table="session_events", source_ref=sid or charger, weight=0.5))
        if not signals:
            if rows:
                evidence.append(Evidence(
                    kind="neutral", tool_name=TOOL_NAME,
                    description="Handshake events present with no failures.",
                    source_table="session_events", source_ref=sid or charger, weight=0.2))
                signals.append("handshake_ok")
                return CheckResult(TOOL_NAME, "ok", "Handshake normal.",
                                   findings={"events": rows},
                                   evidence_items=evidence, signals=signals)
            evidence.append(Evidence(
                kind="missing", tool_name=TOOL_NAME,
                description="No handshake events recorded at all.",
                source_table="session_events", source_ref=sid or charger, weight=0.3))
            signals.append("no_handshake_events")
            return CheckResult(TOOL_NAME, "no_data", "No handshake events found.",
                               findings={"events": []},
                               evidence_items=evidence, signals=signals)
        return CheckResult(TOOL_NAME, "ok",
                           f"Handshake anomalies: {', '.join(signals)}",
                           findings={"events": rows},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
