"""Reviewer-verified outcomes and bounded rule-weight learning.

HONEST SCOPE STATEMENT: this is NOT deep learning or autonomous model training.
It performs small, bounded adjustments to human-readable signal->category rule
weights and per-tool usefulness priors, derived ONLY from reviewer-verified
resolutions. Every change is logged in knowledge_changes with old/new values
and can be reverted individually or rolled back wholesale. An agent prediction
is NEVER treated as ground truth.

Learning update (apply_verified_outcome), driven by one verified resolution:
 1. For every signal observed in the case (via case_evidence tool linkage and
    the stored check results' 'signals' lists):
      - if the signal's rule category == verified root cause and the agent
        predicted correctly  -> weight += learning_rate (reinforce)
      - if the signal's rule category != verified root cause -> weight -=
        learning_rate (shift away from wrong category); additionally a rule for
        (signal, verified_category) is reinforced/created if one exists.
    All weights clamped to [weight_min, weight_max].
 2. Tool priors: for each tool that emitted a signal pointing at the verified
    category, usefulness moves toward 1; tools whose signals pointed elsewhere
    move toward 0. Moving-average style step (prior_step), clamped.
 3. New-rule proposals: if a signal appears in >= new_rule_proposal_min_cases
    verified resolutions under the same category but has no active rule, insert
    a source='learned' rule. Per settings new_rules_start_active=false it is
    created INACTIVE (pending approval) and clearly logged.
Idempotency: resolutions.learning_applied flag guards double application.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from app.core.logging_setup import get_logger
from app.core.settings import learning_params

log = get_logger("app.services.feedback")


class FeedbackError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


# ---------------------------------------------------------------------------
# Reviewer authorisation & resolution creation
# ---------------------------------------------------------------------------

def get_active_reviewer(conn: sqlite3.Connection, reviewer_id: str) -> Optional[Dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM reviewers WHERE reviewer_id=? AND active=1",
        (reviewer_id,)).fetchone()


def create_resolution(conn: sqlite3.Connection, case_id: str, payload: Dict[str, Any]
                      ) -> Dict[str, Any]:
    """Store a verified resolution. Only this creates ground truth for a real case."""
    reviewer = get_active_reviewer(conn, payload["reviewer_id"])
    if reviewer is None:
        raise FeedbackError("unauthorised_reviewer",
                            f"Reviewer '{payload['reviewer_id']}' not found or inactive.")
    case = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if case is None:
        raise FeedbackError("case_not_found", f"Case {case_id} not found.")
    if conn.execute("SELECT id FROM resolutions WHERE case_id=?", (case_id,)).fetchone():
        raise FeedbackError("already_resolved",
                            f"Case {case_id} already has a verified resolution.",
                            {"resolution_id": conn.execute(
                                "SELECT id FROM resolutions WHERE case_id=?",
                                (case_id,)).fetchone()["id"]})

    # Agent prediction (latest diagnosis) — recorded, never used as ground truth.
    diag = conn.execute(
        "SELECT primary_category FROM diagnoses WHERE case_id=? ORDER BY version DESC, id DESC LIMIT 1",
        (case_id,)).fetchone()
    pred = diag["primary_category"] if diag else None
    correct = 1 if (pred is not None and (
        pred == payload["verified_root_cause"] or
        pred in payload.get("verified_contributing", []))) else 0

    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "INSERT INTO resolutions(case_id, reviewer_id, verified_root_cause,"
            " verified_contributing_json, corrective_action, resolution_notes,"
            " agent_prediction_category, prediction_correct) VALUES (?,?,?,?,?,?,?,?)",
            (case_id, payload["reviewer_id"], payload["verified_root_cause"],
             json.dumps(payload.get("verified_contributing", [])),
             payload["corrective_action"], payload.get("resolution_notes"),
             pred, correct))
        res_id = cur.lastrowid
        conn.execute("UPDATE cases SET status='resolved' WHERE case_id=?", (case_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.info("resolution %s stored for case %s by %s (prediction_correct=%s)",
             res_id, case_id, payload["reviewer_id"], correct)
    return {"resolution_id": res_id, "agent_prediction_category": pred,
            "prediction_correct": bool(correct)}


# ---------------------------------------------------------------------------
# Signal extraction from persisted checks
# ---------------------------------------------------------------------------

def _case_signals(conn: sqlite3.Connection, case_id: str) -> Dict[str, List[str]]:
    """Map signal_key -> list of tool names that produced it in this case."""
    out: Dict[str, List[str]] = {}
    rows = conn.execute(
        "SELECT tool_name, result_json FROM case_checks WHERE case_id=?", (case_id,))
    for r in rows:
        try:
            res = json.loads(r["result_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for sig in res.get("signals", []):
            out.setdefault(sig, [])
            if r["tool_name"] not in out[sig]:
                out[sig].append(r["tool_name"])
    return out


def _rules_for_signals(conn: sqlite3.Connection, signals: List[str]
                       ) -> List[Dict[str, Any]]:
    if not signals:
        return []
    q = ("SELECT rule_id, category, signal_key, weight, active FROM knowledge_rules "
         f"WHERE signal_key IN ({','.join('?' * len(signals))})")
    return [dict(r) for r in conn.execute(q, tuple(signals))]


# ---------------------------------------------------------------------------
# Preview / apply (pure computation shared by both)
# ---------------------------------------------------------------------------

def compute_updates(conn: sqlite3.Connection, resolution_id: int
                    ) -> List[Dict[str, Any]]:
    """Compute the proposed change list without writing anything."""
    p = learning_params()
    lr, wmin, wmax = p["learning_rate"], p["weight_min"], p["weight_max"]
    lstep, lmin, lmax = p["prior_step"], p["prior_min"], p["prior_max"]

    res = conn.execute("SELECT * FROM resolutions WHERE id=?", (resolution_id,)).fetchone()
    if res is None:
        raise FeedbackError("resolution_not_found", f"Resolution {resolution_id} not found.")
    case_id = res["case_id"]
    verified = res["verified_root_cause"]
    contributing = json.loads(res["verified_contributing_json"] or "[]")
    verified_set = {verified} | set(contributing)
    correct = bool(res["prediction_correct"])

    signals = _case_signals(conn, case_id)
    rules = _rules_for_signals(conn, list(signals.keys()))
    changes: List[Dict[str, Any]] = []

    # 1. Rule weight updates -------------------------------------------------
    covered_signals = set()
    for rule in rules:
        if not rule["active"]:
            continue
        sig, cat, w = rule["signal_key"], rule["category"], rule["weight"]
        covered_signals.add(sig)
        # No-op when already at a clamp boundary (keeps audit log clean).
        if cat in verified_set and w >= wmax - 1e-9:
            continue
        if cat not in verified_set and w <= wmin + 1e-9:
            continue
        if cat in verified_set:
            new_w = min(wmax, round(w + lr, 4)) if correct else min(wmax, round(w + lr / 2, 4))
            reason = ("Reinforced: verified resolution confirms this signal supports "
                      f"'{cat}' and the agent predicted correctly."
                      if correct else
                      f"Partially reinforced: verified resolution confirms '{cat}' "
                      "(agent missed it, so reinforcement is halved).")
        else:
            new_w = max(wmin, round(w - lr, 4))
            reason = (f"Reduced: signal '{sig}' was associated with '{cat}', but the "
                      f"verified root cause is '{verified}'.")
        if abs(new_w - w) > 1e-9:
            changes.append({"change_type": "update", "rule_id": rule["rule_id"],
                            "tool_name": None, "category": cat, "signal_key": sig,
                            "old_value": w, "new_value": new_w, "reason": reason})

    # 2. Tool priors ---------------------------------------------------------
    tool_points: Dict[str, Dict[str, int]] = {}   # tool -> {category: n_signals}
    for sig, tools in signals.items():
        cats = [r["category"] for r in rules if r["signal_key"] == sig]
        for t in tools:
            for c in cats:
                tool_points.setdefault(t, {})
                tool_points[t][c] = tool_points[t].get(c, 0) + 1
    for tool, catmap in sorted(tool_points.items()):
        for cat, n in sorted(catmap.items()):
            row = conn.execute(
                "SELECT usefulness_score, n_observations FROM tool_priors "
                "WHERE tool_name=? AND category=?", (tool, cat)).fetchone()
            cur_score = row["usefulness_score"] if row else 0.5
            n_obs = row["n_observations"] if row else 0
            target = 1.0 if cat in verified_set else 0.0
            direction = lstep if target > cur_score else -lstep
            new_score = max(lmin, min(lmax, round(cur_score + direction, 4)))
            if abs(new_score - cur_score) > 1e-9:
                changes.append({"change_type": "prior_update", "rule_id": None,
                                "tool_name": tool, "category": cat, "signal_key": None,
                                "old_value": cur_score, "new_value": new_score,
                                "reason": (f"Tool '{tool}' emitted {n} signal(s) that "
                                           f"{'match' if cat in verified_set else 'do not match'} "
                                           f"the verified category '{cat}'.")})

    # 3. New-rule proposals for unseen signals -------------------------------
    k = p["new_rule_proposal_min_cases"]
    for sig in sorted(signals.keys()):
        has_rule = conn.execute(
            "SELECT 1 FROM knowledge_rules WHERE signal_key=? AND category=? AND active=1",
            (sig, verified)).fetchone()
        if has_rule:
            continue
        cnt = 0
        for r in conn.execute(
                "SELECT cc.result_json FROM resolutions rs JOIN case_checks cc "
                "ON cc.case_id=rs.case_id WHERE rs.verified_root_cause=?", (verified,)):
            try:
                if sig in json.loads(r["result_json"] or "{}").get("signals", []):
                    cnt += 1
            except json.JSONDecodeError:
                pass
        if cnt >= k:
            changes.append({"change_type": "create", "rule_id": None, "tool_name": None,
                            "category": verified, "signal_key": sig,
                            "old_value": None,
                            "new_value": round((wmin + wmax) / 2 * 0.6, 4),
                            "reason": (f"Signal '{sig}' appeared in {cnt} verified "
                                       f"resolutions of '{verified}' with no existing rule. "
                                       "Created as source='learned', "
                                       + ("ACTIVE." if p["new_rules_start_active"]
                                          else "INACTIVE pending approval."))})
    return changes


def preview_learning(conn: sqlite3.Connection, case_id: str) -> Dict[str, Any]:
    res = conn.execute("SELECT id FROM resolutions WHERE case_id=?", (case_id,)).fetchone()
    if res is None:
        raise FeedbackError("no_resolution",
                            f"Case {case_id} has no verified resolution; nothing to learn.")
    return {"applied": False, "dry_run": True, "resolution_id": res["id"],
            "changes": compute_updates(conn, res["id"]),
            "already_applied": bool(conn.execute(
                "SELECT learning_applied FROM resolutions WHERE id=?",
                (res["id"],)).fetchone()["learning_applied"])}


def apply_verified_outcome(conn: sqlite3.Connection, resolution_id: int) -> Dict[str, Any]:
    """Apply the bounded learning update for one verified resolution. Idempotent."""
    res = conn.execute("SELECT * FROM resolutions WHERE id=?", (resolution_id,)).fetchone()
    if res is None:
        raise FeedbackError("resolution_not_found", f"Resolution {resolution_id} not found.")
    if res["learning_applied"]:
        return {"applied": False, "dry_run": False, "resolution_id": resolution_id,
                "changes": [], "already_applied": True}

    changes = compute_updates(conn, resolution_id)
    p = learning_params()
    try:
        conn.execute("BEGIN")
        for ch in changes:
            if ch["change_type"] == "update":
                conn.execute(
                    "UPDATE knowledge_rules SET weight=?, version=version+1 WHERE rule_id=?",
                    (ch["new_value"], ch["rule_id"]))
                conn.execute(
                    "INSERT INTO knowledge_changes(rule_id, change_type, old_value_json,"
                    " new_value_json, reason, triggered_by_resolution_id) VALUES (?,?,?,?,?,?)",
                    (ch["rule_id"], "update",
                     json.dumps({"weight": ch["old_value"]}),
                     json.dumps({"weight": ch["new_value"]}),
                     ch["reason"], resolution_id))
            elif ch["change_type"] == "prior_update":
                conn.execute(
                    "INSERT INTO tool_priors(tool_name, category, usefulness_score,"
                    " n_observations, updated_at) VALUES (?,?,?,?,datetime('now')) "
                    "ON CONFLICT(tool_name, category) DO UPDATE SET "
                    "usefulness_score=excluded.usefulness_score, "
                    "n_observations=n_observations+1, updated_at=datetime('now')",
                    (ch["tool_name"], ch["category"], ch["new_value"]))
                conn.execute(
                    "INSERT INTO knowledge_changes(tool_name, category, change_type,"
                    " old_value_json, new_value_json, reason, triggered_by_resolution_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (ch["tool_name"], ch["category"], "prior_update",
                     json.dumps({"usefulness_score": ch["old_value"]}),
                     json.dumps({"usefulness_score": ch["new_value"]}),
                     ch["reason"], resolution_id))
            elif ch["change_type"] == "create":
                cur = conn.execute(
                    "INSERT INTO knowledge_rules(version, category, signal_key, weight,"
                    " source, active, created_from_resolution_id) VALUES (1,?,?,?,'learned',?,?)",
                    (ch["category"], ch["signal_key"], ch["new_value"],
                     1 if p["new_rules_start_active"] else 0, resolution_id))
                conn.execute(
                    "INSERT INTO knowledge_changes(rule_id, category, change_type,"
                    " old_value_json, new_value_json, reason, triggered_by_resolution_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (cur.lastrowid, ch["category"], "create", None,
                     json.dumps({"weight": ch["new_value"], "signal_key": ch["signal_key"],
                                 "category": ch["category"]}),
                     ch["reason"], resolution_id))
        conn.execute("UPDATE resolutions SET learning_applied=1 WHERE id=?", (resolution_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.info("learning applied for resolution %s: %d changes", resolution_id, len(changes))
    return {"applied": True, "dry_run": False, "resolution_id": resolution_id,
            "changes": changes, "already_applied": False}


# ---------------------------------------------------------------------------
# Safety: list / revert single change / rollback to a prior version
# ---------------------------------------------------------------------------

def list_changes(conn: sqlite3.Connection, limit: int = 200) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM knowledge_changes ORDER BY change_id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def revert_change(conn: sqlite3.Connection, change_id: int) -> Dict[str, Any]:
    ch = conn.execute("SELECT * FROM knowledge_changes WHERE change_id=?",
                      (change_id,)).fetchone()
    if ch is None:
        raise FeedbackError("change_not_found", f"Change {change_id} not found.")
    if ch["reverted"]:
        raise FeedbackError("already_reverted", f"Change {change_id} already reverted.")
    ct = ch["change_type"]
    old = json.loads(ch["old_value_json"]) if ch["old_value_json"] else None
    try:
        conn.execute("BEGIN")
        if ct == "update" and ch["rule_id"] is not None:
            conn.execute("UPDATE knowledge_rules SET weight=?, version=version+1 WHERE rule_id=?",
                         (old["weight"], ch["rule_id"]))
        elif ct == "prior_update" and ch["tool_name"]:
            conn.execute(
                "UPDATE tool_priors SET usefulness_score=?, updated_at=datetime('now') "
                "WHERE tool_name=? AND category=?",
                (old["usefulness_score"], ch["tool_name"], ch["category"]))
        elif ct == "create" and ch["rule_id"] is not None:
            conn.execute("UPDATE knowledge_rules SET active=0, version=version+1 WHERE rule_id=?",
                         (ch["rule_id"],))
        elif ct == "deactivate" and ch["rule_id"] is not None:
            conn.execute("UPDATE knowledge_rules SET active=1, version=version+1 WHERE rule_id=?",
                         (ch["rule_id"],))
        else:
            raise FeedbackError("unrevertable", f"Cannot revert change type '{ct}'.")
        conn.execute(
            "INSERT INTO knowledge_changes(rule_id, tool_name, category, change_type,"
            " old_value_json, new_value_json, reason, triggered_by_resolution_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ch["rule_id"], ch["tool_name"], ch["category"], "rollback",
             ch["new_value_json"], ch["old_value_json"],
             f"Manual revert of change {change_id}.", ch["triggered_by_resolution_id"]))
        conn.execute("UPDATE knowledge_changes SET reverted=1, reverted_at=datetime('now') "
                     "WHERE change_id=?", (change_id,))
        conn.commit()
    except FeedbackError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    return {"reverted_change_id": change_id}


def rollback_to(conn: sqlite3.Connection, to_change_id: int, reason: str) -> Dict[str, Any]:
    """Roll back ALL learning changes with change_id > to_change_id (newest first)."""
    anchor = conn.execute("SELECT change_id FROM knowledge_changes WHERE change_id=?",
                          (to_change_id,)).fetchone()
    if anchor is None:
        raise FeedbackError("change_not_found", f"Anchor change {to_change_id} not found.")
    targets = [r["change_id"] for r in conn.execute(
        "SELECT change_id FROM knowledge_changes WHERE change_id>? "
        "AND reverted=0 AND change_type!='rollback' ORDER BY change_id DESC",
        (to_change_id,))]
    reverted_n = 0
    for cid in targets:
        try:
            revert_change(conn, cid)
            reverted_n += 1
        except FeedbackError:
            continue
    conn.execute(
        "INSERT INTO knowledge_changes(change_type, reason) VALUES ('rollback', ?)",
        (f"Rollback to before change {to_change_id}: {reason}",))
    conn.commit()
    log.info("rollback to %s completed (%d changes reverted)", to_change_id, reverted_n)
    return {"rolled_back_changes": reverted_n, "anchor_change_id": to_change_id}
