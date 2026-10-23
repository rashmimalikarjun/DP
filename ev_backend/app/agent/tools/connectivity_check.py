"""Tool: connectivity_check — charger heartbeat / cloud connectivity evidence.

Signals: heartbeat_lost, connection_timeout_events, charger_offline_period.
"""
from __future__ import annotations

import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "connectivity_check"
RELATED_CATEGORIES = ["network_or_connectivity_issue", "session_stops_unexpectedly",
                      "incorrect_or_delayed_station_status"]
DESCRIPTION = "Check charger-to-cloud connectivity events (heartbeats/timeouts)."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        s = state.matched_session or {}
        charger = s.get("charger_id") or (
            state.complaint.station_id and None)
        if not charger:
            # try station-level lookup of any recent charger events
            if state.complaint.station_id:
                cur = conn.execute(
                    "SELECT DISTINCT charger_id FROM session_events WHERE charger_id "
                    "IN (SELECT charger_id FROM charging_sessions WHERE station_id=?) "
                    "LIMIT 1", (state.complaint.station_id,))
                r = cur.fetchone()
                charger = r["charger_id"] if r else None
        if not charger:
            return CheckResult(TOOL_NAME, "no_data",
                               "No charger reference available to check connectivity.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="Connectivity logs unavailable (no charger id).",
                                   weight=0.3)],
                               signals=["connectivity_data_unavailable"])
        cur = conn.execute(
            "SELECT * FROM session_events WHERE charger_id=? AND event_type IN "
            "('heartbeat_lost','connection_timeout','charger_offline','heartbeat') "
            "ORDER BY event_ts DESC LIMIT 30", (charger,))
        rows = [dict(r) for r in cur.fetchall()]
        types = {r["event_type"] for r in rows}
        signals, evidence = [], []
        if "heartbeat_lost" in types:
            signals.append("heartbeat_lost")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Heartbeat-lost events logged for charger {charger}.",
                source_table="session_events", source_ref=charger, weight=0.6))
        if "connection_timeout" in types:
            signals.append("connection_timeout_events")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Connection-timeout events logged for charger {charger}.",
                source_table="session_events", source_ref=charger, weight=0.5))
        if "charger_offline" in types:
            signals.append("charger_offline_period")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Charger {charger} reported offline periods.",
                source_table="session_events", source_ref=charger, weight=0.5))
        if not signals:
            if rows:
                evidence.append(Evidence(
                    kind="neutral", tool_name=TOOL_NAME,
                    description=f"Connectivity events exist for {charger}; no loss detected.",
                    source_table="session_events", source_ref=charger, weight=0.2))
                summary = f"No connectivity anomalies for charger {charger}."
                status = "ok"
            else:
                evidence.append(Evidence(
                    kind="missing", tool_name=TOOL_NAME,
                    description=f"No connectivity events recorded for charger {charger}.",
                    source_table="session_events", source_ref=charger, weight=0.3))
                summary = f"No connectivity events found for charger {charger}."
                status = "no_data"
            signals.append("connectivity_normal" if rows else "no_connectivity_data")
        else:
            status = "ok"
            summary = f"Anomalies: {', '.join(sorted(types))}"
        return CheckResult(TOOL_NAME, status, summary,
                           findings={"events": rows[:20]},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
