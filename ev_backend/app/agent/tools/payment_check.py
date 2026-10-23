"""Tool: payment_check — reconcile payments against sessions.

Signals: payment_captured_without_session, double_charge_same_session,
refund_pending_over_days, amount_mismatch, payment_failed_but_charged,
complaint_deducted_no_payment_record, payment_ok.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "payment_check"
RELATED_CATEGORIES = ["payment_or_billing_failure",
                      "incorrect_or_incomplete_charging_history"]
DESCRIPTION = "Reconcile payment records with charging sessions and the complaint."


def _deducted_report(state: CaseState) -> bool:
    ps = (state.complaint.reported_payment_status or "").lower()
    return ps in ("deducted", "charged", "debited") or "deduct" in (
        state.complaint.text_redacted or "").lower()


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        s = state.matched_session or {}
        params, sql = [], ""
        if s.get("session_id"):
            sql = ("SELECT * FROM payment_records WHERE session_id=? OR "
                   "(user_ref IS NOT NULL AND user_ref=?) ORDER BY ts DESC LIMIT 20")
            params = [s["session_id"], c.user_ref or ""]
        elif c.user_ref:
            sql = ("SELECT * FROM payment_records WHERE user_ref=? "
                   "ORDER BY ts DESC LIMIT 20")
            params = [c.user_ref]
        else:
            return CheckResult(TOOL_NAME, "no_data",
                               "Cannot locate payment records without session or user ref.",
                               evidence_items=[Evidence(
                                   kind="missing", tool_name=TOOL_NAME,
                                   description="Payment data unavailable (no session/user ref).",
                                   weight=0.35)],
                               signals=["payment_data_unavailable"])
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        if s.get("session_id"):
            sess_rows = [r for r in rows if r.get("session_id") == s["session_id"]]
        else:
            sess_rows = rows

        signals: list[str] = []
        evidence: list[Evidence] = []
        captured = [r for r in sess_rows if (r["status"] or "").lower()
                    in ("captured", "success", "completed", "debited")]

        # deducted but no session started at all
        if not s and _deducted_report(c) and captured:
            signals.append("payment_captured_without_session")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Payment of {captured[0]['amount']} captured but no "
                            "charging session matched the complaint.",
                source_table="payment_records",
                source_ref=str(captured[0]["payment_id"]), weight=0.7))
        if not s and _deducted_report(c) and not rows:
            # conflict: customer says money gone, we cannot see any payment record
            signals.append("complaint_deducted_no_payment_record")
            evidence.append(Evidence(
                kind="conflicting", tool_name=TOOL_NAME,
                description="Complaint reports deduction but NO payment record exists "
                            "for this user; needs bank statement / transaction ref.",
                source_table="payment_records", weight=0.6))

        if len(captured) > 1 and s.get("session_id"):
            same_amt = {(r["amount"]) for r in captured}
            if len({str(r['amount']) for r in captured}) <= max(1, len(same_amt)):
                signals.append("double_charge_same_session")
                evidence.append(Evidence(
                    kind="matching", tool_name=TOOL_NAME,
                    description=f"{len(captured)} capture records for one session "
                                f"{s['session_id']} (possible double charge).",
                    source_table="payment_records",
                    source_ref=str(captured[0]["payment_id"]), weight=0.7))

        refunds_pending = [r for r in sess_rows
                           if (r["status"] or "").lower() == "refund_pending"
                           or (r.get("reconciliation_status") or "").lower()
                           in ("pending", "initiated")]
        if refunds_pending:
            ts = _parse(refunds_pending[0].get("ts"))
            if ts and (datetime.now() - ts).days >= 3:
                signals.append("refund_pending_over_days")
            else:
                signals.append("refund_pending_over_days")
            evidence.append(Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description="Refund is pending reconciliation "
                            f"(status={refunds_pending[0]['status']}, "
                            f"recon={refunds_pending[0].get('reconciliation_status')}).",
                source_table="payment_records",
                source_ref=str(refunds_pending[0]["payment_id"]), weight=0.6))

        failed_capture = [r for r in sess_rows
                          if (r["status"] or "").lower() in ("failed", "declined")]
        if failed_capture and s.get("session_id") and \
                (s.get("status") or "").lower() in ("failed", "aborted"):
            if _deducted_report(c):
                signals.append("payment_failed_but_charged")
                evidence.append(Evidence(
                    kind="matching", tool_name=TOOL_NAME,
                    description="Payment marked failed yet complaint says amount was "
                                "deducted.",
                    source_table="payment_records",
                    source_ref=str(failed_capture[0]["payment_id"]), weight=0.65))

        if s.get("session_id") and s.get("amount") is not None:
            total = sum(r["amount"] or 0 for r in captured)
            if total and abs(total - float(s["amount"])) > 1.0:
                signals.append("amount_mismatch")
                evidence.append(Evidence(
                    kind="matching", tool_name=TOOL_NAME,
                    description=f"Captured total {total} differs from session amount "
                                f"{s['amount']}.",
                    source_table="payment_records", weight=0.55))

        if not signals:
            signals.append("payment_ok")
            evidence.append(Evidence(
                kind="neutral", tool_name=TOOL_NAME,
                description="Payment records consistent with session outcome.",
                source_table="payment_records", weight=0.25))
        return CheckResult(TOOL_NAME, "ok",
                           f"{len(rows)} payment record(s); signals: "
                           + (", ".join(signals) or "none"),
                           findings={"records": rows[:10]},
                           evidence_items=evidence, signals=signals)
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
