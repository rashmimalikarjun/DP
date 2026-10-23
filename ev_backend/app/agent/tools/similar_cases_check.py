"""Tool: similar_cases_check — look up earlier RESOLVED cases with similar complaints.

Uses keyword overlap (Jaccard) of the redacted complaint against resolved cases
for the same app that have a reviewer-verified ground truth. Provides WEAK
corroboration only; never enough on its own to confirm a cause.
Signal: similar_resolved_case_found.
"""
from __future__ import annotations

import re
import sqlite3

from app.models import CaseState, CheckResult, Evidence

TOOL_NAME = "similar_cases_check"
RELATED_CATEGORIES = []   # informs all categories equally; handled specially in policy
REQUIRES_SESSION = False
DESCRIPTION = "Find earlier resolved cases with similar complaints (same app)."

_STOP = set(("the a an and or but for with without not was were are is my me i to "
             "of at in on it this that have has had been when while after before").split())


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP}


def run(conn: sqlite3.Connection, state: CaseState) -> CheckResult:
    try:
        c = state.complaint
        mine = _tokens(c.text_redacted)
        if not mine:
            return CheckResult(TOOL_NAME, "no_data", "Complaint too short to match.",
                               signals=["no_similar_cases"])
        cur = conn.execute(
            "SELECT case_id, complaint_text_redacted, ground_truth_category "
            "FROM cases WHERE app_id=? AND status='resolved' AND "
            "ground_truth_category IS NOT NULL AND case_id != ? "
            "ORDER BY created_at DESC LIMIT 200",
            (c.app_id, c.scenario_id or ""))
        best, best_score = None, 0.0
        for r in cur.fetchall():
            other = _tokens(r["complaint_text_redacted"])
            if not other:
                continue
            j = len(mine & other) / len(mine | other)
            if j > best_score:
                best_score, best = j, dict(r)
        if best and best_score >= 0.18:
            ev = Evidence(
                kind="matching", tool_name=TOOL_NAME,
                description=f"Similar resolved case {best['case_id']} "
                            f"(similarity {best_score:.2f}, verified cause "
                            f"{best['ground_truth_category']}).",
                source_table="cases", source_ref=best["case_id"],
                supports_category=best["ground_truth_category"], weight=0.35)
            return CheckResult(TOOL_NAME, "ok",
                               f"Found similar resolved case ({best_score:.2f}).",
                               findings={"similar": best, "similarity": round(best_score, 3)},
                               evidence_items=[ev],
                               signals=["similar_resolved_case_found"])
        ev = Evidence(kind="neutral", tool_name=TOOL_NAME,
                      description="No sufficiently similar resolved case found.",
                      weight=0.1)
        return CheckResult(TOOL_NAME, "no_data", "No similar resolved cases.",
                           findings={}, evidence_items=[ev],
                           signals=["no_similar_cases"])
    except Exception as exc:  # pragma: no cover
        return CheckResult(TOOL_NAME, "error", f"Tool error: {exc}", signals=[])
