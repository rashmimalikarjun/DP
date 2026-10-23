"""Case service: CRUD + trace retrieval for cases (read side of the API)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from app.core.db import query_all, query_one


def get_case_full(conn: sqlite3.Connection, case_id: str) -> Optional[Dict[str, Any]]:
    # NOTE: app.core.db.query_all/query_one take (conn, sql, params). Several
    # calls below previously passed only (sql, params), which made the SQL
    # string land in the `conn` position -> AttributeError
    # ('str' object has no attribute 'execute'). Fixed by passing conn first.
    c = query_one(conn, "SELECT * FROM cases WHERE case_id=?", (case_id,))
    if not c:
        return None
    app = query_one(conn, "SELECT display_name FROM apps WHERE app_id=?", (c["app_id"],))
    ev_rows = query_all(conn,
        "SELECT * FROM case_evidence WHERE case_id=? ORDER BY id", (case_id,))
    grouped: Dict[str, List[Dict[str, Any]]] = {
        "matching": [], "conflicting": [], "missing": [], "neutral": []}
    for e in ev_rows:
        grouped.setdefault(e["kind"], []).append(e)
    diag = query_one(conn,
        "SELECT * FROM diagnoses WHERE case_id=? ORDER BY version DESC, id DESC LIMIT 1",
        (case_id,))
    recs = query_all(conn,
        "SELECT * FROM recommendations WHERE case_id=? ORDER BY priority, id",
        (case_id,))
    res = query_one(conn,
        "SELECT * FROM resolutions WHERE case_id=? ORDER BY id DESC LIMIT 1",
        (case_id,))
    if diag:
        diag = dict(diag)
        diag["contributing_categories"] = json.loads(
            diag.get("contributing_categories_json") or "[]")
        diag["supporting_evidence_ids"] = json.loads(
            diag.get("supporting_evidence_ids_json") or "[]")
        diag["unresolved_questions"] = json.loads(
            diag.get("unresolved_questions_json") or "[]")
    out_recs = [{**r, "linked_evidence_ids": json.loads(
        r.get("linked_evidence_ids_json") or "[]")} for r in recs]
    summary = {
        "case_id": c["case_id"], "created_at": c["created_at"],
        "app_id": c["app_id"],
        "app_display_name": app["display_name"] if app else None,
        "status": c["status"], "data_origin": c["data_origin"],
        "complaint_text_redacted": c["complaint_text_redacted"],
        "diagnosis": diag,
    }
    return {
        "case": summary,
        "complaint": c,
        "evidence": grouped,
        "diagnosis": diag,
        "recommendations": out_recs,
        "ground_truth_category": c.get("ground_truth_category"),
        "resolution": res,
    }


def list_cases(conn: sqlite3.Connection, *, app_id: Optional[str] = None,
               category: Optional[str] = None, status: Optional[str] = None,
               data_origin: Optional[str] = None, date_from: Optional[str] = None,
               date_to: Optional[str] = None, page: int = 1,
               page_size: int = 20) -> Dict[str, Any]:
    where, params = ["1=1"], []
    if app_id:
        where.append("c.app_id=?"); params.append(app_id)
    if status:
        where.append("c.status=?"); params.append(status)
    if data_origin:
        where.append("c.data_origin=?"); params.append(data_origin)
    if date_from:
        where.append("c.created_at >= ?"); params.append(date_from)
    if date_to:
        where.append("c.created_at <= ?"); params.append(date_to + " 23:59:59")
    join_diag = ""
    if category:
        join_diag = (" JOIN diagnoses d ON d.case_id=c.case_id AND "
                     "(d.primary_category=? OR d.contributing_categories_json LIKE ?)")
        params_pre = [category, f'%"{category}"%']
    else:
        params_pre = []
    sql_count = (f"SELECT COUNT(*) AS n FROM cases c{join_diag} WHERE " +
                 " AND ".join(where))
    total = conn.execute(sql_count, tuple(params_pre + params)).fetchone()["n"]
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    sql = (f"SELECT c.* FROM cases c{join_diag} WHERE " + " AND ".join(where) +
           " ORDER BY c.created_at DESC, c.rowid DESC LIMIT ? OFFSET ?")
    rows = query_all(conn, sql, tuple(params_pre + params +
                                       [page_size, (page - 1) * page_size]))
    items = []
    for c in rows:
        app = query_one(conn, "SELECT display_name FROM apps WHERE app_id=?",
                        (c["app_id"],))
        diag = query_one(conn,
            "SELECT * FROM diagnoses WHERE case_id=? ORDER BY version DESC LIMIT 1",
            (c["case_id"],))
        if diag:
            diag = dict(diag)
            diag["contributing_categories"] = json.loads(
                diag.get("contributing_categories_json") or "[]")
            diag["supporting_evidence_ids"] = json.loads(
                diag.get("supporting_evidence_ids_json") or "[]")
            diag["unresolved_questions"] = json.loads(
                diag.get("unresolved_questions_json") or "[]")
        items.append({
            "case_id": c["case_id"], "created_at": c["created_at"],
            "app_id": c["app_id"],
            "app_display_name": app["display_name"] if app else None,
            "status": c["status"], "data_origin": c["data_origin"],
            "complaint_text_redacted": c["complaint_text_redacted"],
            "diagnosis": diag,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_trace(conn: sqlite3.Connection, case_id: str) -> Optional[Dict[str, Any]]:
    c = query_one(conn, "SELECT case_id FROM cases WHERE case_id=?", (case_id,))
    if not c:
        return None
    rows = query_all(conn,
        "SELECT * FROM case_checks WHERE case_id=? ORDER BY step_no, id", (case_id,))
    steps = []
    for r in rows:
        steps.append({
            "step_no": r["step_no"],
            "tool_name": r["tool_name"],
            "selection_reason": r["selection_reason"],
            "candidate_scores": json.loads(r["candidate_scores_json"] or "[]"),
            "inputs": json.loads(r["inputs_json"] or "{}"),
            "result": json.loads(r["result_json"] or "{}"),
            "outcome_summary": r["outcome_summary"],
            "state_changes": json.loads(r["state_changes_json"] or "{}"),
            "replanned": bool(r["replanned"]),
            "created_at": r["created_at"],
        })
    diag = query_one(conn,
        "SELECT stop_reason FROM diagnoses WHERE case_id=? ORDER BY version DESC LIMIT 1",
        (case_id,))
    beliefs: Dict[str, float] = {}
    if steps:
        last_sc = steps[-1]["state_changes"] or {}
        beliefs = last_sc.get("beliefs_after", {})
    return {
        "case_id": case_id,
        "steps": steps,
        "stop_reason": diag["stop_reason"] if diag else "",
        "final_beliefs": beliefs,
    }
