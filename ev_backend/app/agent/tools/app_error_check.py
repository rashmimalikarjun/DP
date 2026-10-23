"""Tool: app_error_check — app crash/error telemetry events.

Signals: app_error_event, crash_report_signal, error_code_APP500.
App-side telemetry rows use session_id NULL and carry the app id in charger_id
field prefix 'app:' (documented simplification for simulated telemetry).
"""
from __future__ import annotations

import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "app_error_check"
RELATED_CATEGORIES = ["app_crash_or_technical_error"]
REQUIRES_SESSION = False
DESCRIPTION = "Check app-side error/crash telemetry for this app/user."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        sql = ("SELECT * FROM session_events WHERE event_type IN "
               "('app_error','app_crash') AND charger_id=? ")
        params = [f"app:{c.app_id}"]
        if c.user_ref:
            sql += "AND (detail LIKE ? OR detail IS NULL) "
            params.append(f"%{c.user_ref}%")
        sql += "ORDER BY event_ts DESC LIMIT 10"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        signals, evidence = [], []
        crash = [r for r in rows if r["event_type"] == "app_crash"]
        err = [r for r in rows if r["event_type"] == "app_error"]
        if crash:
            signals.append("crash_report_signal")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Crash telemetry found ({len(crash)} event(s)) for app "
                            f"{c.app_id}.",
                source_table="session_events", source_ref=str(crash[0]["event_id"]),
                weight=0.65))
        if err:
            signals.append("app_error_event")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"App-error telemetry found ({len(err)} event(s)); last: "
                            f"{err[0]['detail'] or 'no detail'}",
                source_table="session_events", source_ref=str(err[0]["event_id"]),
                weight=0.6))
            if err[0].get("detail") and "APP5" in str(err[0]["detail"]).upper():
                signals.append("error_code_APP500")
        status = "ok"
        if not signals:
            ev_kind = "missing" if not rows else "neutral"
            evidence.append(Evidence(
                kind=ev_kind, tool_name=TOOL_NAME,
                description="No app crash/error telemetry for this app/user.",
                weight=0.3 if ev_kind == "missing" else 0.2))
            signals.append("no_app_errors" if rows else "app_telemetry_unavailable")
            status = "no_data" if not rows else "ok"
        return CheckResult(TOOL_NAME, status,
                           f"{len(rows)} app telemetry event(s).",
                           findings={"events": rows},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
