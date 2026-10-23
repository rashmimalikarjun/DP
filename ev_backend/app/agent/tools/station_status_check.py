"""Tool: station_status_check — compare app-shown status vs network status log.

Signals: status_log_shows_offline_while_app_showed_available,
status_update_lag_minutes, no_status_update_since_ts,
reported_status_differs_from_source.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "station_status_check"
RELATED_CATEGORIES = ["charger_availability_mismatch",
                      "incorrect_or_delayed_station_status"]
REQUIRES_SESSION = False
DESCRIPTION = "Compare charger status log with what the customer saw in the app."


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None


def _gap_hours(t0: datetime, ts: str) -> float:
    t1 = _parse(ts)
    if t1 is None:
        return 999.0
    if t0.tzinfo is None and t1.tzinfo is not None:
        t0 = t0.replace(tzinfo=t1.tzinfo)
    if t1.tzinfo is None and t0.tzinfo is not None:
        t1 = t1.replace(tzinfo=t0.tzinfo)
    return (t0 - t1).total_seconds() / 3600.0


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        s = state.matched_session or {}
        charger = s.get("charger_id")
        station = s.get("station_id") or c.station_id
        if not charger and station:
            cur = conn.execute(
                "SELECT DISTINCT charger_id FROM charger_status_log WHERE station_id=? "
                "LIMIT 5", (station,))
            ids = [r["charger_id"] for r in cur.fetchall()]
            charger = ids[0] if ids else None
        if not charger:
            return CheckResult(TOOL_NAME, "no_data",
                               "No charger/station reference for status comparison.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="Station status logs unavailable.",
                                   weight=0.3)],
                               signals=["status_data_unavailable"])
        cur = conn.execute(
            "SELECT * FROM charger_status_log WHERE charger_id=? ORDER BY ts DESC LIMIT 20",
            (charger,))
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return CheckResult(TOOL_NAME, "no_data",
                               f"No status log entries for charger {charger}.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description=f"No status history for charger {charger}.",
                                   source_table="charger_status_log",
                                   source_ref=charger, weight=0.3)],
                               signals=["no_status_history"])

        text = (c.text_redacted or "").lower()
        t0 = _parse(c.complaint_ts) or _parse(s.get("start_ts"))
        latest = rows[0]
        signals, evidence = [], []

        claimed_available = ("availab" in text or "showed as free" in text
                             or "marked available" in text or "shows available" in text)
        near = [r for r in rows if r["source"] == "network"]
        worst = next((r for r in near if (r["reported_status"] or "").lower()
                      in ("offline", "fault", "occupied", "out_of_order")), None)
        if claimed_available and worst and (
                t0 is None or abs(_gap_hours(t0, worst["ts"])) < 48):
            sig = "status_log_shows_offline_while_app_showed_available"
            signals.append(sig)
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"App showed charger available but network status log says "
                            f"'{worst['reported_status']}' at {worst['ts']}.",
                source_table="charger_status_log", source_ref=str(worst["id"]),
                supports_category="charger_availability_mismatch", weight=0.7))

        srcs = {r["source"] for r in rows}
        if len(srcs) > 1:
            net = [r for r in rows if r["source"] == "network"]
            appv = [r for r in rows if r["source"] == "app_display"]
            if net and appv:
                tn, ta = _parse(net[0]["ts"]), _parse(appv[0]["ts"])
                if tn and ta:
                    lag_min = abs((tn - ta).total_seconds()) / 60.0
                    if lag_min > 30:
                        signals.append("status_update_lag_minutes")
                        evidence.append(Evidence(
                            kind="matching", tool_name=TOOL_NAME,
                            description=f"App display status lags network status by "
                                        f"{int(lag_min)} minutes.",
                            source_table="charger_status_log",
                            source_ref=str(appv[0]["id"]), weight=0.6))
                if (net[0]["reported_status"] or "").lower() != \
                        (appv[0]["reported_status"] or "").lower():
                    signals.append("reported_status_differs_from_source")
                    evidence.append(Evidence(
                        kind="matching", tool_name=TOOL_NAME,
                        description="Latest app-displayed status differs from network "
                                    "status source.",
                        source_table="charger_status_log", source_ref=str(net[0]["id"]),
                        weight=0.6))
        if t0:
            recent = [r for r in rows if (_parse(r["ts"]) and
                                          abs((_parse(r["ts"]) - t0).total_seconds())
                                          < 6 * 3600)]
            if not recent:
                signals.append("no_status_update_since_ts")
                evidence.append(Evidence(
                    kind="matching", tool_name=TOOL_NAME,
                    description="No status update within 6h of the complaint time "
                                "(possible stale feed).",
                    source_table="charger_status_log", source_ref=str(latest["id"]),
                    weight=0.45))

        if not signals:
            evidence.append(Evidence(
                kind="neutral", tool_name=TOOL_NAME,
                description=f"Status log for {charger} consistent with complaint claims.",
                source_table="charger_status_log", source_ref=str(latest["id"]),
                weight=0.2))
            signals.append("status_consistent")
        return CheckResult(TOOL_NAME, "ok",
                           f"{len(rows)} status entries; signals: "
                           + ", ".join(signals),
                           findings={"entries": rows[:10]},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
