"""Diagnosis orchestration: run agent, persist everything (case, evidence, checks,
diagnosis, recommendations) inside one transaction.

Synchronous execution was chosen: at this project's scale a diagnosis takes tens
of milliseconds, so POST /cases returns the full result directly (documented).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from app import models
from app.agent.agent import DiagnosticAgent
from app.core.logging_setup import get_logger
from app.services.recommendations import build_recommendations

log = get_logger("app.services.diagnosis")


def diagnose_and_persist(conn: sqlite3.Connection, complaint: models.Complaint,
                         case_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the adaptive agent and store all artefacts. Returns summary dict."""
    case_id = case_id or f"C-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
    agent = DiagnosticAgent(conn)
    run = agent.run(complaint)
    diag = run.diagnosis
    assert diag is not None
    recs = build_recommendations(diag, run.state)

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO cases(case_id, app_id, complaint_text_redacted, complaint_ts,"
            " reported_error, reported_payment_status, reported_session_ref,"
            " reported_station_id, user_ref, data_origin, status,"
            " ground_truth_category, scenario_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (case_id, complaint.app_id, complaint.text_redacted,
             complaint.complaint_ts, complaint.reported_error,
             complaint.reported_payment_status, complaint.session_ref,
             complaint.station_id, complaint.user_ref, complaint.data_origin,
             "awaiting_review", complaint.ground_truth_category,
             complaint.scenario_id))

        # evidence first so we can link ids into diagnosis/recommendations
        ev_ids: list[int] = []
        for e in run.state.evidence:
            cur = conn.execute(
                "INSERT INTO case_evidence(case_id, kind, description, source_table,"
                " source_ref, supports_category, weight, tool_name)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (case_id, e.kind, e.description, e.source_table, e.source_ref,
                 e.supports_category, e.weight, e.tool_name))
            e.evidence_id = cur.lastrowid
            ev_ids.append(cur.lastrowid)

        for step in run.trace:
            conn.execute(
                "INSERT INTO case_checks(case_id, step_no, tool_name,"
                " selection_reason, candidate_scores_json, inputs_json, result_json,"
                " outcome_summary, state_changes_json, replanned)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (case_id, step.step_no,
                 step.tool_name, step.selection_reason,
                 json.dumps(step.candidate_scores, default=str),
                 json.dumps(step.inputs, default=str),
                 json.dumps(step.result, default=str),
                 step.outcome_summary,
                 json.dumps(step.state_changes, default=str),
                 1 if step.replanned else 0))

        sup_ids = [e.evidence_id for e in run.state.evidence
                   if e.kind == "matching" and e.supports_category == diag.primary_category
                   and e.evidence_id is not None]
        cur = conn.execute(
            "INSERT INTO diagnoses(case_id, version, classification, primary_category,"
            " contributing_categories_json, evidence_strength, evidence_strength_label,"
            " supporting_evidence_ids_json, unresolved_questions_json, stop_reason)"
            " VALUES (?,1,?,?,?,?,?,?,?,?)",
            (case_id, diag.classification, diag.primary_category,
             json.dumps(diag.contributing_categories), diag.evidence_strength,
             diag.evidence_strength_label, json.dumps(sup_ids),
             json.dumps(diag.unresolved_questions), diag.stop_reason))
        diag_id = cur.lastrowid

        for r in recs:
            conn.execute(
                "INSERT INTO recommendations(case_id, diagnosis_id, action, rationale,"
                " priority, linked_evidence_ids_json) VALUES (?,?,?,?,?,?)",
                (case_id, diag_id, r["action"], r["rationale"], r["priority"],
                 json.dumps(r.get("linked_evidence_ids", []))))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    log.info("case %s diagnosed: %s / %s (strength %.2f, steps %d)",
             case_id, diag.classification, diag.primary_category,
             diag.evidence_strength, len(run.trace))
    return {
        "case_id": case_id,
        "run": run,
        "diagnosis_id": diag_id,
        "recommendations": recs,
    }
