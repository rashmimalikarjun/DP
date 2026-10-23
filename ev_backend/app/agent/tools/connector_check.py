"""Tool: connector_check — hardware fault events on the connector/charger.

Signals: connector_fault_event, overtemperature.
"""
from __future__ import annotations

import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "connector_check"
RELATED_CATEGORIES = ["connector_or_charger_hardware_fault",
                      "session_stops_unexpectedly"]
DESCRIPTION = "Look for connector/hardware fault events."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        s = state.matched_session or {}
        charger = s.get("charger_id")
        if not charger:
            return CheckResult(TOOL_NAME, "no_data",
                               "No charger reference; cannot inspect hardware events.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="Hardware events unavailable (no charger id).",
                                   weight=0.3)],
                               signals=["hardware_data_unavailable"])
        cur = conn.execute(
            "SELECT * FROM session_events WHERE charger_id=? AND event_type IN "
            "('connector_fault','overtemperature','hardware_fault','locking_fail') "
            "ORDER BY event_ts DESC LIMIT 20", (charger,))
        rows = [dict(r) for r in cur.fetchall()]
        signals, evidence = [], []
        types = {r["event_type"] for r in rows}
        if "connector_fault" in types or "hardware_fault" in types:
            signals.append("connector_fault_event")
            r0 = next(r for r in rows if r["event_type"] in
                      ("connector_fault", "hardware_fault"))
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Connector/hardware fault logged on {charger}: "
                            f"{r0['detail'] or 'no detail'}",
                source_table="session_events", source_ref=str(r0["event_id"]),
                weight=0.65))
        if "overtemperature" in types:
            signals.append("overtemperature")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Over-temperature event logged on {charger}.",
                source_table="session_events", source_ref=charger, weight=0.55))
        if not signals:
            evidence.append(Evidence(
                kind="neutral", tool_name=TOOL_NAME,
                description=f"No hardware fault events for {charger}.",
                source_table="session_events", source_ref=charger, weight=0.2))
            signals.append("no_connector_fault")
            return CheckResult(TOOL_NAME, "ok",
                               f"No connector faults logged for {charger}.",
                               findings={"events": rows},
                               evidence_items=evidence, signals=signals)
        return CheckResult(TOOL_NAME, "ok",
                           f"Hardware anomalies: {', '.join(signals)}",
                           findings={"events": rows},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
