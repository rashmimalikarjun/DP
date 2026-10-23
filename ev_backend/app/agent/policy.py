"""Policy: transparent scoring, tool selection, belief updates and stopping rules.

score(tool) = information_gain_estimate + learned_prior_bonus
              - cost(tool) + conflict_resolution_bonus

All components are recorded per candidate so the trace can show WHY a check was
chosen. Belief updates use knowledge_rules weights (signal -> category).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from app.core.settings import (applicable_categories, learning_params,
                               min_expected_gain, thresholds, tool_cost)
from app.models import CaseState, CheckResult, Evidence
from app.agent.tools import TOOL_REGISTRY

# Which unresolved question(s) each tool can answer (for gain estimates & reasons)
TOOL_QUESTIONS: Dict[str, List[str]] = {
    "session_lookup": ["Which charging session does this complaint refer to?"],
    "timeline_check": ["What did the charger/session event timeline show?",
                       "Did the session start, then stop early?"],
    "error_code_check": ["Is there a known error code pattern?"],
    "payment_check": ["Was money actually captured/refunded for this session?",
                      "Does the payment trail match the complaint claim?"],
    "connectivity_check": ["Was the charger connected to the cloud at that time?"],
    "connector_check": ["Any connector/hardware fault events?"],
    "handshake_check": ["Did vehicle-charger handshake succeed?"],
    "station_status_check": ["Did the network status log agree with what the app showed?"],
    "history_check": ["Is the app's charging history consistent with backend records?"],
    "app_error_check": ["Did the mobile app itself crash or throw errors?"],
    "telematics_check": ["Is vehicle telemetry fresh and correct in the companion app?"],
    "similar_cases_check": ["Have similar complaints been resolved before?"],
}

# Conflict kinds this tool can resolve (used for conflict_resolution_bonus)
TOOL_CONFLICT_RESOLVERS: Dict[str, List[str]] = {
    "payment_check": ["deducted_vs_no_payment_record", "amount_mismatch"],
    "session_lookup": ["session_ref_unmatched"],
    "station_status_check": ["availability_claim_vs_status_log"],
    "connectivity_check": ["offline_claim_vs_heartbeat"],
    "timeline_check": ["start_claim_vs_events"],
    "app_error_check": ["crash_claim_vs_telemetry"],
    "handshake_check": ["no_start_vs_handshake"],
    "connector_check": ["hardware_claim_vs_fault_log"],
    "history_check": ["history_claim_vs_backend_records"],
    "telematics_check": ["telemetry_claim_vs_vehicle_data"],
}


def load_active_rules(conn: sqlite3.Connection) -> Dict[str, List[Tuple[int, float]]]:
    """signal_key -> [(rule_id, weight), ...] for active rules."""
    cur = conn.execute(
        "SELECT rule_id, signal_key, weight FROM knowledge_rules WHERE active=1")
    out: Dict[str, List[Tuple[int, float]]] = {}
    for r in cur.fetchall():
        out.setdefault(r["signal_key"], []).append((r["rule_id"], r["weight"]))
    return out


def load_tool_priors(conn: sqlite3.Connection) -> Dict[Tuple[str, str], Tuple[float, int]]:
    cur = conn.execute("SELECT tool_name, category, usefulness_score, n_observations "
                       "FROM tool_priors")
    return {(r["tool_name"], r["category"]):
            (r["usefulness_score"], r["n_observations"]) for r in cur.fetchall()}


def seed_beliefs(state: CaseState, hints: Dict[str, float]) -> None:
    """Initial beliefs come ONLY from keyword hints on the redacted complaint."""
    for cat, h in hints.items():
        if cat in state.applicable_categories:
            state.beliefs[cat] = state.beliefs.get(cat, 0.0) + 0.25 * h


def update_beliefs(conn: sqlite3.Connection, state: CaseState,
                   result: CheckResult) -> Dict[str, float]:
    """Apply knowledge-rule weights for the signals emitted by a tool.

    Returns the per-category belief delta applied (for the trace).
    Neutral evidence does not raise beliefs; missing evidence slightly lowers
    the confidence of categories that would have needed that data.
    """
    rules = load_active_rules(conn)
    deltas: Dict[str, float] = {}
    for sig in result.signals:
        for rule_id, w in rules.get(sig, []):
            cat = _rule_category(conn, rule_id)
            if cat not in state.applicable_categories:
                continue
            # diminishing returns: same signal twice adds less
            already = sum(1 for e in state.evidence
                          if e.tool_name == result.tool_name and e.kind == "matching")
            damp = 1.0 / (1.0 + 0.35 * max(0, already - 1))
            inc = 0.45 * w * damp
            state.beliefs[cat] = state.beliefs.get(cat, 0.0) + inc
            deltas[cat] = deltas.get(cat, 0.0) + inc
            ev = next((e for e in result.evidence_items
                       if e.supports_category is None and e.kind == "matching"), None)
            if ev is not None:
                ev.supports_category = ev.supports_category or cat
    # explicit supports_category set by tools (e.g. similar cases)
    for e in result.evidence_items:
        if e.kind == "matching" and e.supports_category and \
                e.supports_category in state.applicable_categories:
            if e.supports_category not in [c for c in _cats_of_signals(conn, result.signals)]:
                inc = 0.25 * e.weight
                state.beliefs[e.supports_category] = state.beliefs.get(
                    e.supports_category, 0.0) + inc
                deltas[e.supports_category] = deltas.get(e.supports_category, 0.0) + inc
    if result.status in ("no_data", "error"):
        # data we cannot see should reduce certainty for top hypotheses
        for cat in list(state.beliefs):
            state.beliefs[cat] *= 0.98
    # normalise to keep scores bounded in [0, ~1]
    for cat in state.beliefs:
        state.beliefs[cat] = min(1.0, state.beliefs[cat])
    return deltas


def _rule_category(conn: sqlite3.Connection, rule_id: int) -> str:
    r = conn.execute("SELECT category FROM knowledge_rules WHERE rule_id=?",
                     (rule_id,)).fetchone()
    return r["category"] if r else ""


def _cats_of_signals(conn: sqlite3.Connection, signals: List[str]) -> List[str]:
    cats = []
    for sig in signals:
        for rid, _w in load_active_rules(conn).get(sig, []):
            c = _rule_category(conn, rid)
            if c:
                cats.append(c)
    return cats


def detect_conflicts(state: CaseState, result: CheckResult) -> List[str]:
    """Return new conflict keys created by this check's evidence."""
    out = []
    for e in result.evidence_items:
        if e.kind == "conflicting":
            key = f"{result.tool_name}:{e.description[:60]}"
            out.append(key)
    # specific semantic conflicts
    text = (state.complaint.text_redacted or "").lower()
    ps = (state.complaint.reported_payment_status or "").lower()
    if ("deduct" in text or ps == "deducted") and result.tool_name == "payment_check" \
            and "complaint_deducted_no_payment_record" in result.signals:
        out.append("deducted_vs_no_payment_record")
    if result.tool_name == "connectivity_check" and \
            ("offline" in text or "not working" in text) and \
            "connectivity_normal" in result.signals:
        out.append("offline_claim_vs_heartbeat")
    if result.tool_name == "station_status_check" and \
            "status_consistent" in result.signals and "availab" in text:
        out.append("availability_claim_vs_status_log")
    return out


def information_gain(tool_name: str, state: CaseState) -> float:
    """Estimate how much a tool could move the diagnosis forward (0..1)."""
    tool = TOOL_REGISTRY[tool_name]
    q_overlap = 0.0
    questions = TOOL_QUESTIONS.get(tool_name, [])
    open_q = " ".join(state.unresolved_questions).lower()
    for q in questions:
        if q.lower() in open_q or any(
                tok in open_q for tok in q.lower().split()[:3]):
            q_overlap += 0.12
    # relevance to current top hypotheses
    top = sorted(state.beliefs.items(), key=lambda kv: -kv[1])[:2]
    rel = 0.0
    related = set(tool.related_categories) or set(applicable_categories(state.complaint.app_id))
    for cat, score in top:
        if cat in related:
            rel += 0.25 * max(0.2, score)
    # tools relevant to an applicable-but-zero-belief category still have some value
    unseen = [c for c in state.applicable_categories
              if c in related and state.beliefs.get(c, 0.0) < 0.05]
    if unseen and not top:
        rel += 0.10
    base = 0.10
    return min(1.0, base + q_overlap + rel)


def learned_prior_bonus(tool_name: str, state: CaseState,
                        priors: Dict[Tuple[str, str], Tuple[float, int]]) -> float:
    top = sorted(state.beliefs.items(), key=lambda kv: -kv[1])[:2]
    bonus = 0.0
    for cat, _score in top:
        val, n = priors.get((tool_name, cat), (0.5, 0))
        # shrink towards 0.5 when few observations
        shrunk = 0.5 + (val - 0.5) * min(1.0, n / 5.0)
        bonus += 0.15 * (shrunk - 0.5)
    return bonus


def score_candidates(conn: sqlite3.Connection, state: CaseState
                     ) -> List[Dict[str, object]]:
    """Score every applicable, not-yet-run tool. Fully transparent components."""
    priors = load_tool_priors(conn)
    rows = []
    for name, tool in TOOL_REGISTRY.items():
        if name in state.checks_run:
            continue
        tool_cats = set(tool.related_categories) or set(state.applicable_categories)
        if not (tool_cats & set(state.applicable_categories)):
            continue  # not applicable for this app's category set
        if tool.requires_session and state.matched_session is None and \
                name != "session_lookup":
            # can't run meaningfully yet — still scored but heavily discounted
            blocked_penalty = 0.25
        else:
            blocked_penalty = 0.0
        ig = information_gain(name, state)
        lp = learned_prior_bonus(name, state, priors)
        cost = tool_cost(name)
        conflict_bonus = 0.0
        for cf in state.info_gaps:
            pass
        resolvers = TOOL_CONFLICT_RESOLVERS.get(name, [])
        active_conflicts = [k for k in resolvers if any(k in g for g in _gap_keys(state))]
        if active_conflicts:
            conflict_bonus = 0.20
        score = ig + lp - cost + conflict_bonus - blocked_penalty
        reason_bits = []
        addressed = [q for q in TOOL_QUESTIONS.get(name, [])
                     if any(t in " ".join(state.unresolved_questions).lower()
                            for t in q.lower().split()[:3])]
        if addressed:
            reason_bits.append(f"addresses: {addressed[0]}")
        elif state.step_count == 0:
            reason_bits.append("establishes baseline evidence")
        else:
            reason_bits.append("tests current top hypotheses "
                               f"({', '.join(sorted(state.applicable_categories)[:0]) or 'broad coverage'})")
        if active_conflicts:
            reason_bits.append(f"resolves conflict '{active_conflicts[0]}'")
        rows.append({
            "tool": name,
            "score": round(score, 4),
            "components": {
                "information_gain": round(ig, 4),
                "learned_prior": round(lp, 4),
                "cost": round(cost, 4),
                "conflict_bonus": round(conflict_bonus, 4),
                "blocked_penalty": blocked_penalty,
            },
            "reason_bits": reason_bits,
        })
    rows.sort(key=lambda r: (-r["score"], r["tool"]))
    return rows


def _gap_keys(state: CaseState) -> List[str]:
    """Conflict keys currently present, derived from stored info_gaps strings."""
    return list(state.info_gaps)


def select_next_tool(conn: sqlite3.Connection, state: CaseState
                     ) -> Tuple[Optional[str], str, List[Dict[str, object]]]:
    """Return (tool_name|None, selection_reason, all_candidate_scores)."""
    rows = score_candidates(conn, state)
    if not rows:
        return None, "No applicable unrun tools remain.", rows
    best = rows[0]
    if best["score"] < min_expected_gain():
        return None, (f"All remaining tools scored below minimum expected gain "
                      f"({min_expected_gain()}); best was {best['tool']} at "
                      f"{best['score']}."), rows
    comp = best["components"]
    reason = (f"Selected '{best['tool']}' (score {best['score']}: gain {comp['information_gain']}"
              f" + prior {comp['learned_prior']} - cost {comp['cost']}"
              f" + conflict bonus {comp['conflict_bonus']}). ")
    if best["reason_bits"]:
        reason += "; ".join(best["reason_bits"]) + "."
    else:
        reason += "Broadens evidence for the leading hypotheses."
    return str(best["tool"]), reason, rows


def register_conflicts(state: CaseState, new_conflicts: List[str]) -> bool:
    added = False
    for k in new_conflicts:
        if k not in state.info_gaps:
            state.info_gaps.append(k)
            added = True
    if new_conflicts:
        state.pending_conflict = True
    return added


def refresh_unresolved_questions(state: CaseState) -> None:
    """Recompute open questions from evidence gaps."""
    qs: List[str] = []
    if state.matched_session is None:
        qs.append("Which charging session does this complaint refer to?")
    if any("deducted_vs_no_payment_record" in g for g in state.info_gaps):
        qs.append("Payment claimed deducted but no payment record found — "
                  "need transaction reference/bank statement.")
    if any("offline_claim_vs_heartbeat" in g for g in state.info_gaps):
        qs.append("Customer claims charger offline but heartbeats look normal — "
                  "verify station-side connectivity at exact time.")
    if any("availability_claim_vs_status_log" in g for g in state.info_gaps):
        qs.append("App-availability claim not corroborated by status log — "
                  "need screenshot/time of the app display.")
    has_matching = any(e.kind == "matching" for e in state.evidence)
    if not has_matching and state.step_count > 0:
        qs.append("No positive matching evidence collected yet.")
    state.unresolved_questions = qs


# ---------------------------------------------------------------------------
# Stopping + classification
# ---------------------------------------------------------------------------

def should_stop(conn: sqlite3.Connection, state: CaseState) -> Optional[str]:
    th = thresholds()
    top = state.top_category()
    strength = evidence_strength(conn, state)[0] if top else 0.0
    unresolved_critical = any(
        e.kind == "conflicting" and (
            e.contradicts_category == top or e.contradicts_category is None)
        for e in state.evidence)
    if top and state.beliefs.get(top, 0) >= th["top_category_confidence_stop"] \
            and not unresolved_critical and strength >= th["probable_min_strength"]:
        return "confidence_threshold_reached"
    from app.core.settings import max_steps as _max_steps
    if state.step_count >= _max_steps():
        return "max_steps_reached"
    nxt, reason, rows = select_next_tool(conn, state)
    if nxt is None:
        if rows:
            return "no_remaining_gain"
        return "all_applicable_tools_exhausted"
    if state.matched_session is None and state.step_count >= 2 and \
            "session_lookup" in state.checks_run:
        # safe stop: required information absent
        return "required_information_absent"
    return None


def evidence_strength(conn: sqlite3.Connection, state: CaseState
                      ) -> Tuple[float, str, int, int]:
    """Compute (strength 0..1, label, n_supporting_items, n_source_tools)."""
    top = state.top_category()
    if not top:
        return 0.0, "none", 0, 0
    support = [e for e in state.evidence
               if e.kind == "matching" and (e.supports_category == top)]
    # also count matching evidence whose tool relates to top category via rules
    tools = {e.tool_name for e in support}
    raw = sum(min(1.0, e.weight) for e in support)
    conflict_pen = 0.25 * sum(1 for e in state.evidence if e.kind == "conflicting")
    missing_pen = 0.10 * sum(1 for e in state.evidence if e.kind == "missing")
    strength = (raw / (raw + 1.2)) * (1.0 if len(tools) >= 2 else 0.75)
    strength = max(0.0, min(1.0, strength - conflict_pen * 0.12 - missing_pen * 0.05))
    th = thresholds()
    if strength >= th["confirmed_min_strength"] and len(support) >= th["confirmed_min_evidence_items"] \
            and len(tools) >= th["confirmed_min_source_tools"] and conflict_pen == 0:
        label = "strong"
    elif strength >= th["probable_min_strength"]:
        label = "moderate"
    elif strength > 0.2:
        label = "weak"
    else:
        label = "insufficient"
    return strength, label, len(support), len(tools)


def classify(conn: sqlite3.Connection, state: CaseState, stop_reason: str
             ) -> "DiagnosisResultLike":
    """Produce classification + contributing categories using settings thresholds.

    Rules (also documented in README):
      confirmed  : strength>=confirmed_min AND >=N supporting items from >=2 tools
                   AND zero unresolved conflicts
      probable   : strength>=probable_min AND no unresolved critical conflict
      requires_further_investigation : stop_reason=='required_information_absent'
                   OR no matched session after lookup attempts
      otherwise  : inconclusive
    """
    from app.models import DiagnosisResult
    th = thresholds()
    top = state.top_category()
    strength, label, n_items, n_tools = evidence_strength(conn, state)
    conflicts = [e for e in state.evidence if e.kind == "conflicting"]

    contributing: List[str] = []
    if top:
        for cat, b in sorted(state.beliefs.items(), key=lambda kv: -kv[1]):
            if cat != top and cat in state.applicable_categories and \
                    b >= th["secondary_contributing_min_strength"] and \
                    any(e.supports_category == cat and e.kind == "matching"
                        for e in state.evidence):
                contributing.append(cat)

    if stop_reason == "required_information_absent" or (
            state.matched_session is None and "session_lookup" in state.checks_run
            and strength < th["probable_min_strength"]):
        cls = "requires_further_investigation"
    elif top and strength >= th["confirmed_min_strength"] and \
            n_items >= th["confirmed_min_evidence_items"] and \
            n_tools >= th["confirmed_min_source_tools"] and not conflicts:
        cls = "confirmed"
    elif top and strength >= th["probable_min_strength"] and not conflicts:
        cls = "probable"
    elif top and strength >= th["probable_min_strength"] and conflicts:
        # conflict present -> never confirmed; downgrade to inconclusive
        cls = "inconclusive"
    else:
        cls = "inconclusive"

    if cls in ("inconclusive", "requires_further_investigation") and conflicts:
        primary = None  # do not assert a cause without resolving conflicts
    elif cls in ("inconclusive", "requires_further_investigation") and not top:
        primary = None
    else:
        primary = top
    support_ids = [e.evidence_id for e in state.evidence
                   if e.kind == "matching" and e.supports_category == primary
                   and e.evidence_id is not None]
    return DiagnosisResult(
        classification=cls,
        primary_category=primary,
        contributing_categories=contributing,
        evidence_strength=strength,
        evidence_strength_label=label if top else "none",
        supporting_evidence_ids=support_ids,
        unresolved_questions=list(state.unresolved_questions),
        stop_reason=stop_reason,
    )


# helper used by agent to persist gap keys as readable strings
def gap_to_question(gap_key: str) -> str:
    return gap_key.replace("_", " ")
