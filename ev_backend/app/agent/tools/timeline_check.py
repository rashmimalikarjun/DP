"""Tool: timeline_check — reconstruct the session event timeline.

Signals: start_request_without_started_event, authorize_then_no_start,
heartbeat_lost_mid_session, connector_fault_mid_session, unexpected_stop_reason,
short_duration_low_energy (from the session row).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "timeline_check"
RELATED_CATEGORIES = ["session_fails_to_start", "session_stops_unexpectedly",
                      "network_or_connectivity_issue",
                      "connector_or_charger_hardware_fault"]
DESCRIPTION = "Reconstruct charger/session events around the matched session."


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        s = state.matched_session
        if not s:
            return CheckResult(TOOL_NAME, "not_applicable",
                               "No matched session; cannot build a timeline.",
                               signals=["no_matched_session"])
        sid = s["session_id"]
        cur = conn.execute(
            "SELECT * FROM session_events WHERE session_id=? ORDER BY event_ts ASC",
            (sid,))
        events = [dict(r) for r in cur.fetchall()]
        if not events and s.get("charger_id"):
            # fall back to charger-level events within +/-24h of session start
            t0 = _parse(s.get("start_ts"))
            if t0 is not None:
                lo = t0.isoformat(sep=" ")
                cur = conn.execute(
                    "SELECT * FROM session_events WHERE charger_id=? AND event_ts "
                    "BETWEEN datetime(?, '-24 hours') AND datetime(?, '+24 hours') "
                    "ORDER BY event_ts ASC", (s["charger_id"], lo, lo))
                events = [dict(r) for r in cur.fetchall()]

        types = {e["event_type"] for e in events}
        signals: list[str] = []
        evidence: list[Evidence] = []

        if "authorize" in types and "started" not in types:
            signals.append("authorize_then_no_start")
            if "start_request" in types:
                signals.append("start_request_without_started_event")
        elif "start_request" in types and "started" not in types:
            signals.append("start_request_without_started_event")

        if "heartbeat_lost" in types and "started" in types:
            signals.append("heartbeat_lost_mid_session")
        if "connector_fault" in types and "started" in types:
            signals.append("connector_fault_mid_session")

        status_l = (s.get("status") or "").lower()
        if status_l in ("failed", "stopped_early", "aborted") and "started" in types:
            signals.append("unexpected_stop_reason")

        st, en = _parse(s.get("start_ts")), _parse(s.get("end_ts"))
        energy = s.get("energy_kwh")
        if st and en and energy is not None and "started" in types:
            minutes = (en - st).total_seconds() / 60.0
            if minutes < 5 and energy < 0.5:
                signals.append("short_duration_low_energy")

        for sig in signals:
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Event timeline shows '{sig}' for session {sid}.",
                source_table="session_events", source_ref=sid,
                supports_category=None, weight=0.5))
        if not events:
            evidence.append(Evidence(
                kind="missing", tool_name=TOOL_NAME,
                description=f"No event log rows exist for session {sid}.",
                source_table="session_events", source_ref=sid, weight=0.4))
            return CheckResult(TOOL_NAME, "no_data",
                               f"No events recorded for session {sid}.",
                               findings={"events": []}, evidence_items=evidence,
                               signals=["event_logs_missing"])

        summary = (f"{len(events)} events; key types: "
                   + ", ".join(sorted(types)[:6]))
        return CheckResult(TOOL_NAME, "ok", summary,
                           findings={"events": events[:50]},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
