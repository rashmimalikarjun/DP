"""FixedCheckBaseline: runs the SAME tool set in one fixed order for every case.

Documented behaviour (also in README):
  - Uses BASELINE_ORDER from app/agent/tools/__init__.py.
  - App-applicability filtering IS applied (a tool whose related categories are
    all non-applicable for the complaint's app is recorded as 'not_applicable'
    and skipped) — this keeps the comparison fair, since the adaptive agent also
    skips non-applicable tools.
  - No early stopping: it always attempts all applicable tools (up to a hard cap
    equal to the number of applicable tools), never replans, and classifies with
    the same policy.classify thresholds. stop_reason is always 'baseline_complete'.
"""
from __future__ import annotations

import sqlite3
from typing import List

from app import models
from app.agent import policy
from app.agent.agent import AgentRun, TraceStep, hashlib_sig
from app.agent.tools import BASELINE_ORDER, TOOL_REGISTRY
from app.core.settings import applicable_categories
from app.services.intake import keyword_category_hints


class FixedCheckBaseline:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def run(self, complaint: models.Complaint) -> AgentRun:
        cats = applicable_categories(complaint.app_id)
        state = models.CaseState(complaint=complaint, applicable_categories=cats)
        policy.seed_beliefs(state, keyword_category_hints(complaint.text_redacted))
        policy.refresh_unresolved_questions(state)
        run = AgentRun(state=state)
        # Hard cap: never exceed max_steps actual tool executions (fairness note
        # in docstring: baseline stops when the cap is hit, not when confident).
        from app.core.settings import max_steps as _max_steps
        cap = _max_steps()

        for name in BASELINE_ORDER:
            if state.step_count >= cap:
                break
            tool = TOOL_REGISTRY[name]
            tool_cats = set(tool.related_categories) or set(cats)
            if not (tool_cats & set(cats)):
                # record non-applicable skip in the trace, like the agent does
                run.trace.append(TraceStep(
                    step_no=state.step_count + 1,
                    tool_name=name,
                    selection_reason="Baseline fixed order; category not applicable "
                                     "for this app — recorded as not_applicable.",
                    candidate_scores=[],
                    inputs={},
                    result={"tool_name": name, "status": "not_applicable",
                            "summary": "skipped by applicability filter",
                            "findings": {}, "signals": [], "evidence_items": []},
                    outcome_summary="[not_applicable] skipped by applicability filter",
                    state_changes={}, replanned=False))
                continue
            inputs = {"app_id": complaint.app_id,
                      "session_id": (state.matched_session or {}).get("session_id"),
                      "user_ref": complaint.user_ref}
            sig = hashlib_sig(name, inputs)
            if sig in state.run_signatures:
                continue
            state.run_signatures.add(sig)
            try:
                result = tool(self.conn, state)
            except Exception as exc:  # pragma: no cover - defensive
                result = models.CheckResult(name, "error", f"Tool crashed: {exc}",
                                            signals=[])
            deltas = policy.update_beliefs(self.conn, state, result)
            for e in result.evidence_items:
                e.tool_name = name
                state.evidence.append(e)
            new_conflicts = policy.detect_conflicts(state, result)
            policy.register_conflicts(state, new_conflicts)
            state.checks_run.append(name)
            state.step_count += 1
            policy.refresh_unresolved_questions(state)
            run.trace.append(TraceStep(
                step_no=state.step_count,
                tool_name=name,
                selection_reason="Baseline fixed order (no adaptive selection).",
                candidate_scores=[],
                inputs=inputs,
                result=result.to_dict(),
                outcome_summary=f"[{result.status}] {result.summary}",
                state_changes={
                    "belief_deltas": {k: round(v, 4) for k, v in deltas.items()},
                    "beliefs_after": state.belief_snapshot(),
                    "evidence_added": [e.to_dict() for e in result.evidence_items],
                    "conflicts_added": new_conflicts,
                },
                replanned=False))

        run.stop_reason = "baseline_complete"
        run.diagnosis = policy.classify(self.conn, state, run.stop_reason)
        return run
