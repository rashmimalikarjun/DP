"""Tool: telematics_check — companion-app vehicle telemetry events.

Telemetry rows use charger_id prefix 'app:<app_id>' (same convention as
app_error_check). Signals: telemetry_stale_last_seen, soc_not_updating,
bms_error_signal.
"""
from __future__ import annotations

import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "telematics_check"
RELATED_CATEGORIES = ["vehicle_specific_or_telematics_issue"]
REQUIRES_SESSION = False
DESCRIPTION = "Check vehicle telemetry freshness/BMS events (companion apps)."


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        sql = ("SELECT * FROM session_events WHERE event_type IN "
               "('telemetry_stale','soc_not_updating','bms_error') AND charger_id=? "
               "ORDER BY event_ts DESC LIMIT 10")
        rows = [dict(r) for r in conn.execute(
            sql, (f"app:{c.app_id}",)).fetchall()]
        if c.user_ref:
            rows = [r for r in rows
                    if (r.get("detail") is None) or (c.user_ref in str(r.get("detail")))]
        types = {r["event_type"] for r in rows}
        signals, evidence = [], []
        mapping = {"telemetry_stale": "telemetry_stale_last_seen",
                   "soc_not_updating": "soc_not_updating",
                   "bms_error": "bms_error_signal"}
        for et, sig in mapping.items():
            if et in types:
                signals.append(sig)
                r0 = next(r for r in rows if r["event_type"] == et)
                evidence.append(Evidence(
                    kind="matching", tool_name=TOOL_NAME,
                    description=f"Telemetry event '{et}' logged: "
                                f"{r0['detail'] or 'no detail'}",
                    source_table="session_events", source_ref=str(r0["event_id"]),
                    weight=0.6))
        status = "ok"
        if not signals:
            ev_kind = "missing" if not rows else "neutral"
            evidence.append(Evidence(
                kind=ev_kind, tool_name=TOOL_NAME,
                description="No vehicle telemetry anomalies found.",
                weight=0.3 if ev_kind == "missing" else 0.2))
            signals.append("no_telematics_events" if not rows else "telematics_ok")
            status = "no_data" if not rows else "ok"
        return CheckResult(TOOL_NAME, status,
                           f"{len(rows)} telemetry event(s); signals: "
                           + ", ".join(signals),
                           findings={"events": rows},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
