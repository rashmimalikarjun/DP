"""DiagnosticAgent: bounded plan-act-observe-replan loop over diagnostic tools.

Loop per step:
  OBSERVE CaseState -> SELECT next tool (policy) -> ACT (run tool, never raises)
  -> INTERPRET/UPDATE (evidence, beliefs via knowledge rules, conflicts)
  -> REPLAN flag if top hypothesis changed or a conflict appeared
  -> STOP when policy says so (confidence / max_steps / no gain / info absent).

The agent itself does not touch the DB except through read-only tools and the
rules/priors tables; persistence is done by services/case_service.py.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app import models
from app.agent import policy
from app.agent.tools import TOOL_REGISTRY
from app.core.settings import applicable_categories, max_steps
from app.services.intake import keyword_category_hints


@dataclass
class TraceStep:
    step_no: int
    tool_name: str
    selection_reason: str
    candidate_scores: List[Dict[str, Any]]
    inputs: Dict[str, Any]
    result: Dict[str, Any]
    outcome_summary: str
    state_changes: Dict[str, Any]
    replanned: bool


@dataclass
class AgentRun:
    state: models.CaseState
    trace: List[TraceStep] = field(default_factory=list)
    diagnosis: Optional[models.DiagnosisResult] = None
    stop_reason: str = ""


class DiagnosticAgent:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------
    @staticmethod
    def _tool_inputs(state: models.CaseState) -> Dict[str, Any]:
        """The inputs a tool will actually read — used for duplicate-run guard."""
        s = state.matched_session or {}
        c = state.complaint
        return {
            "app_id": c.app_id,
            "session_id": s.get("session_id"),
            "charger_id": s.get("charger_id"),
            "station_id": s.get("station_id") or c.station_id,
            "user_ref": c.user_ref,
            "complaint_ts": c.complaint_ts,
        }

    def run(self, complaint: models.Complaint) -> AgentRun:
        cats = applicable_categories(complaint.app_id)
        state = models.CaseState(complaint=complaint, applicable_categories=cats)
        policy.seed_beliefs(state, keyword_category_hints(complaint.text_redacted))
        policy.refresh_unresolved_questions(state)
        run = AgentRun(state=state)

        while True:
            stop = policy.should_stop(self.conn, state)
            if stop:
                run.stop_reason = stop
                break
            tool_name, reason, scores = policy.select_next_tool(self.conn, state)
            if tool_name is None:
                run.stop_reason = "no_actionable_tool"
                break
            tool = TOOL_REGISTRY[tool_name]
            inputs = self._tool_inputs(state)
            sig = hashlib_sig(tool_name, inputs)
            if sig in state.run_signatures:
                run.stop_reason = "duplicate_input_guard"
                break
            state.run_signatures.add(sig)

            top_before = state.top_category()
            try:
                result = tool(self.conn, state)
                if result.status not in ("ok", "no_data", "error", "not_applicable"):
                    raise ValueError(f"bad status {result.status}")
            except Exception as exc:  # defensive: tool must never crash the loop
                result = models.CheckResult(tool_name, "error",
                                            f"Tool crashed: {exc}", signals=[])

            deltas = policy.update_beliefs(self.conn, state, result)
            for e in result.evidence_items:
                e.tool_name = tool_name
                state.evidence.append(e)
            new_conflicts = policy.detect_conflicts(state, result)
            had_conflict_added = policy.register_conflicts(state, new_conflicts)
            state.checks_run.append(tool_name)
            state.step_count += 1
            policy.refresh_unresolved_questions(state)

            top_after = state.top_category()
            replanned = bool(had_conflict_added or
                             (top_before and top_after and top_before != top_after))

            run.trace.append(TraceStep(
                step_no=state.step_count,
                tool_name=tool_name,
                selection_reason=reason,
                candidate_scores=scores,
                inputs=inputs,
                result=result.to_dict(),
                outcome_summary=f"[{result.status}] {result.summary}",
                state_changes={
                    "belief_deltas": {k: round(v, 4) for k, v in deltas.items()},
                    "beliefs_after": state.belief_snapshot(),
                    "evidence_added": [e.to_dict() for e in result.evidence_items],
                    "conflicts_added": new_conflicts,
                },
                replanned=replanned,
            ))
            if state.step_count >= max_steps():
                run.stop_reason = "max_steps_reached"
                break

        if run.stop_reason == "":
            run.stop_reason = "loop_exit"
        run.diagnosis = policy.classify(self.conn, state, run.stop_reason)
        return run


def hashlib_sig(tool_name: str, inputs: Dict[str, Any]) -> str:
    """Deterministic signature of tool+inputs to prevent identical repeats."""
    blob = json.dumps(inputs, sort_keys=True, default=str)
    import hashlib
    return f"{tool_name}:{hashlib.sha1(blob.encode()).hexdigest()[:12]}"
