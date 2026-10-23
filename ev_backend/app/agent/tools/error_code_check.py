"""Tool: error_code_check — map session/reported error codes to signals.

Signals: error_code_E101_START_TIMEOUT, error_code_HW2xx, error_code_HS3xx,
error_code_APP500, unknown_error_code.
"""
from __future__ import annotations

import re
import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "error_code_check"
RELATED_CATEGORIES = ["session_fails_to_start", "connector_or_charger_hardware_fault",
                      "vehicle_charger_communication_issue", "app_crash_or_technical_error"]
DESCRIPTION = "Inspect error codes on the session and in the complaint."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        codes: list[str] = []
        s = state.matched_session or {}
        if s.get("error_code"):
            codes.append(str(s["error_code"]))
        rep = (state.complaint.reported_error or "").strip()
        if rep:
            codes.append(rep)
        # also scan events for error details
        if s.get("session_id"):
            cur = conn.execute(
                "SELECT detail FROM session_events WHERE session_id=? AND "
                "(event_type IN ('error','fault')) LIMIT 10", (s["session_id"],))
            for r in cur.fetchall():
                if r["detail"]:
                    m = re.search(r"\b([A-Z]{1,3}\d{2,4})\b", str(r["detail"]))
                    if m:
                        codes.append(m.group(1))
        codes = [c for c in dict.fromkeys(codes) if c]
        if not codes:
            return CheckResult(TOOL_NAME, "no_data",
                               "No error code available from session or complaint.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="No error code reported or logged.",
                                   weight=0.3)],
                               signals=["no_error_code"])

        signals: list[str] = []
        evidence: list[Evidence] = []
        known = False
        for c in codes:
            cu = c.upper()
            sig = None
            if re.match(r"^E?101\b|E101|START.?TIMEOUT", cu):
                sig = "error_code_E101_START_TIMEOUT"
            elif re.match(r"^HW2\d{2}", cu):
                sig = "error_code_HW2xx"
            elif re.match(r"^HS3\d{2}", cu):
                sig = "error_code_HS3xx"
            elif re.match(r"^APP5\d{2}", cu):
                sig = "error_code_APP500"
            if sig:
                known = True
                signals.append(sig)
                evidence.append(Evidence(
                    kind="matching", tool_name=TOOL_NAME,
                    description=f"Error code '{c}' maps to known failure pattern '{sig}'.",
                    source_table="charging_sessions", source_ref=s.get("session_id"),
                    weight=0.6))
        if not known:
            signals.append("unknown_error_code")
            evidence.append(Evidence(
                kind="neutral", tool_name=TOOL_NAME,
                description=f"Error code(s) {codes} not recognised by rule set.",
                weight=0.25))
        return CheckResult(TOOL_NAME, "ok", f"Codes examined: {', '.join(codes)}",
                           findings={"codes": codes}, evidence_items=evidence,
                           signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
