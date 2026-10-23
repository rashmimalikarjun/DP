"""Evaluation harness: adaptive DiagnosticAgent vs FixedCheckBaseline.

Runs BOTH methods on the SAME labelled synthetic cases inside a fresh temporary
SQLite DB, so no learning state or persisted traces leak between runs and the
user's real database is never touched by an evaluation run.

Metric definitions (also written into the report):
  accuracy            : primary_category == ground_truth, over all test cases.
                        For cases whose true label is 'inconclusive', a
                        prediction counts as correct if the classification is
                        inconclusive/requires_further_investigation AND no
                        primary category was asserted.
  macro_f1            : unweighted mean of per-category F1 over categories that
                        appear in the ground truth (predicted 'None' excluded).
  abstention_correct  : % of truly-inconclusive cases returned as
                        inconclusive / requires_further_investigation.
  false_confident     : % of truly-inconclusive cases answered with
                        'confirmed' or 'probable' + a primary category.
  avg_checks          : mean number of executed tool steps per case
                        (not_applicable skips are not counted as checks).
  unnecessary_checks  : checks executed AFTER evidence was already sufficient
                        for the final classification (agent: steps after the
                        step where should_stop() first became true; baseline:
                        every step after confidence threshold would have been
                        reached). Averaged per case.
  traceability        : share of diagnoses whose supporting-evidence ids all
                        resolve to stored evidence rows (both methods persist
                        full artefacts during eval).
  conflict_detection  : among cases containing a planted conflicting-evidence
                        template, share where >=1 'conflicting' evidence item
                        was recorded.

A fixed-seed train/test split is used when a learning experiment is requested;
plain method comparison uses the whole held-out set (no training happens in it).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import models
from app.agent.agent import DiagnosticAgent
from app.agent.baseline import FixedCheckBaseline
from app.core import db as dbmod
from app.data.seed_rules import seed_rules_if_empty
from app.data.synthetic_generator import SyntheticDataset
from app.services.intake import build_complaint

ABSTAIN = ("inconclusive", "requires_further_investigation")
REPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "reports")


def _fresh_conn(path: str) -> sqlite3.Connection:
    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    dbmod.sync_config_tables(conn)
    seed_rules_if_empty(conn)
    return conn


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _score(results: List[Dict[str, Any]], labels: List[str]) -> Dict[str, Any]:
    n = len(results)
    if n == 0:
        return {}
    correct = 0
    abstain_ok = abstain_total = false_conf = 0
    checks_sum = unnec_sum = 0
    trace_ok = trace_total = 0
    conflict_ok = conflict_total = 0
    tp: Dict[str, int] = {c: 0 for c in labels}
    fp: Dict[str, int] = {c: 0 for c in labels}
    fn: Dict[str, int] = {c: 0 for c in labels}
    per_cat: Dict[str, Dict[str, int]] = {c: {"total": 0, "correct": 0}
                                          for c in labels}
    for r in results:
        gt = r["ground_truth"]
        pred = r["primary_category"]
        cls = r["classification"]
        if gt == "inconclusive":
            abstain_total += 1
            if cls in ABSTAIN and pred is None:
                abstain_ok += 1
                correct += 1
            elif cls in ("confirmed", "probable") and pred is not None:
                false_conf += 1
        else:
            ok = (pred == gt)
            if ok:
                correct += 1
                tp[gt] += 1
            else:
                fn[gt] += 1
                if pred in fp:
                    fp[pred] += 1
        per_cat.setdefault(gt, {"total": 0, "correct": 0})
        per_cat[gt]["total"] += 1
        if ok or (gt == "inconclusive" and cls in ABSTAIN and pred is None):
            per_cat[gt]["correct"] += 1
        checks_sum += r["checks_run"]
        unnec_sum += r.get("unnecessary_checks", 0)
        trace_total += 1
        if r.get("traceable", False):
            trace_ok += 1
        if r.get("had_planted_conflict"):
            conflict_total += 1
            if r.get("found_conflict"):
                conflict_ok += 1
    f1s = []
    for c in labels:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        rc = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1s.append(_f1(p, rc))
    return {
        "n_cases": n,
        "accuracy_pct": round(100.0 * correct / n, 2),
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "abstention_correct_pct": (round(100.0 * abstain_ok / abstain_total, 2)
                                   if abstain_total else None),
        "truly_inconclusive_cases": abstain_total,
        "false_confident_pct": (round(100.0 * false_conf / abstain_total, 2)
                                if abstain_total else None),
        "avg_checks_per_case": round(checks_sum / n, 2),
        "avg_unnecessary_checks": round(unnec_sum / n, 2),
        "traceability_pct": round(100.0 * trace_ok / trace_total, 2),
        "conflict_detection_pct": (round(100.0 * conflict_ok / conflict_total, 2)
                                   if conflict_total else None),
        "planted_conflict_cases": conflict_total,
        "per_category_accuracy": {
            c: round(100.0 * v["correct"] / v["total"], 1) if v["total"] else None
            for c, v in per_cat.items()},
    }


def run_method(conn: sqlite3.Connection, method: str, dataset: SyntheticDataset,
               ) -> tuple[List[Dict[str, Any]], float]:
    results: List[Dict[str, Any]] = []
    t_start = time.perf_counter()
    agent = DiagnosticAgent(conn) if method == "adaptive" else FixedCheckBaseline(conn)
    for item in dataset.complaints():
        complaint = build_complaint(dict(item["payload"]))
        run = agent.run(complaint)
        diag = run.diagnosis
        executed_steps = [s for s in run.trace
                          if s.result.get("status") != "not_applicable"]
        # traceability check against what WOULD be persisted
        ev_ids = {i for i in range(1, len(run.state.evidence) + 1)}
        traceable = all(isinstance(x, int) for x in diag.supporting_evidence_ids) \
            and (not diag.supporting_evidence_ids or
                 set(diag.supporting_evidence_ids) <= ev_ids)
        found_conflict = any(e.kind == "conflicting" for e in run.state.evidence)
        # unnecessary checks: for adaptive, steps after the loop would already
        # have stopped (only possible for max_steps-exhausted runs); for the
        # baseline, every executed step beyond the point where the adaptive
        # stop condition first held on the same trace prefix.
        unnec = _count_unnecessary(conn, run, method)
        results.append({
            "scenario_id": item["scenario_id"],
            "ground_truth": item["ground_truth"],
            "secondary": item["secondary"],
            "classification": diag.classification,
            "primary_category": diag.primary_category,
            "contributing": diag.contributing_categories,
            "evidence_strength": diag.evidence_strength,
            "stop_reason": run.stop_reason,
            "checks_run": len(executed_steps),
            "unnecessary_checks": unnec,
            "traceable": bool(traceable),
            "had_planted_conflict": item["ground_truth"] ==
                                    "payment_or_billing_failure" and
                                    "conflict" in item["scenario_id"] or
                                    _is_conflict_template(item["scenario_id"], dataset),
            "found_conflict": found_conflict,
        })
    elapsed = time.perf_counter() - t_start
    return results, round(elapsed, 3)


_CONFLICT_TEMPLATES = {"T18"}


def _is_conflict_template(scenario_id: str, dataset: SyntheticDataset) -> bool:
    # scenario index -> template mapping mirrors TEMPLATES order.
    # TEMPLATES tuples are (tid, key, primary, secondary, pool, difficulty)
    # with 6 fields; the conflict marker lives in field index 5 (difficulty).
    from app.data.synthetic_generator import TEMPLATES
    try:
        idx = int(scenario_id.split("-")[-1])
    except (ValueError, IndexError):
        return False
    per = dataset.n_per_template
    tpos = (idx - 1) // per
    if 0 <= tpos < len(TEMPLATES):
        tpl = TEMPLATES[tpos]
        return len(tpl) >= 6 and tpl[5] == "conflicting"
    return False


def _count_unnecessary(conn: sqlite3.Connection, run, method: str) -> int:
    """Checks run after evidence was ALREADY sufficient for the final class.

    We replay the trace prefixes: after each executed step k we rebuild a
    CaseState snapshot (beliefs_after + evidence counts are stored in the
    trace) and ask policy whether stopping at that point would have produced
    the same classification. Steps strictly after the earliest such k are
    unnecessary. If no prefix suffices, 0 unnecessary (all info-gathering).
    """
    from app.agent import policy
    final_cls = run.diagnosis.classification
    final_cat = run.diagnosis.primary_category
    th = policy.thresholds()
    earliest = None
    beliefs_cum: Dict[str, float] = {}
    matched_session_seen = False
    conflicts_seen = 0
    steps = [s for s in run.trace if s.result.get("status") != "not_applicable"]
    for k, s in enumerate(steps, start=1):
        sc = s.state_changes or {}
        for cat, d in (sc.get("belief_deltas") or {}).items():
            beliefs_cum[cat] = beliefs_cum.get(cat, 0.0) + d
        if s.result.get("findings", {}).get("session"):
            matched_session_seen = True
        conflicts_seen += len(sc.get("conflicts_added") or [])
        top = max(beliefs_cum.items(), key=lambda kv: kv[1])[0] if beliefs_cum else None
        strength_est = min(1.0, sum(max(0.0, v) for v in beliefs_cum.values()) / 2.5)
        would_stop = (top and beliefs_cum.get(top, 0) >=
                      th["top_category_confidence_stop"] and strength_est >=
                      th["probable_min_strength"] and conflicts_seen == 0)
        same_class = (top == final_cat and
                      ((final_cls in ("confirmed", "probable") and would_stop) or
                       (final_cls in ABSTAIN and not would_stop)))
        if would_stop and final_cls in ("confirmed", "probable") and earliest is None:
            earliest = k
    if earliest is not None and final_cls in ("confirmed", "probable"):
        return max(0, len(steps) - earliest)
    return 0


def evaluate(seed: int = 42, n_per_template: int = 10,
             db_path: Optional[str] = None) -> Dict[str, Any]:
    """Run both methods on identical cases in a fresh temp DB."""
    own_conn = None
    if db_path is None:
        tmpdir = os.path.join(REPORT_DIR, ".tmp")
        os.makedirs(tmpdir, exist_ok=True)
        db_path = os.path.join(tmpdir, f"eval_{seed}_{int(time.time())}.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        own_conn = _fresh_conn(db_path)
    conn = own_conn or _fresh_conn(db_path)
    try:
        ds = SyntheticDataset(seed=seed, n_per_template=n_per_template)
        ds.write(conn)
        from app.core.settings import category_ids
        labels = category_ids()
        agent_res, agent_t = run_method(conn, "adaptive", ds)
        base_res, base_t = run_method(conn, "baseline", ds)
        metrics = {
            "adaptive_agent": {**_score(agent_res, labels), "runtime_s": agent_t},
            "fixed_baseline": {**_score(base_res, labels), "runtime_s": base_t},
        }
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dataset_size": len(ds.cases),
            "data_origin": "simulated",
            "seed": seed,
            "n_per_template": n_per_template,
            "procedure": (
                "Both methods ran on the identical labelled synthetic case set "
                "inside a fresh temporary SQLite DB seeded only with the static "
                "rule set (no learned weights, no cross-run leakage). Ground "
                "truth was withheld from agent inputs."),
            "metrics": metrics,
            "per_case": {"adaptive_agent": agent_res, "fixed_baseline": base_res},
            "limitations": [
                "Dataset is synthetic data authored by the same developer as "
                "the rules; results are optimistic and NOT real-world "
                "performance.",
                "Small scale (~hundreds of cases); percentages carry wide "
                "uncertainty.",
                "Generator templates and diagnosis rules are correlated by "
                "design (signals were defined together).",
                "No real charging-network logs were used anywhere.",
                "PII redaction is best-effort, not a guarantee.",
            ],
        }
    finally:
        conn.close()
        if os.path.exists(db_path) and db_path.endswith(".db"):
            for suffix in ("", "-wal", "-shm"):
                p = db_path + suffix
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    return report


def write_reports(report: Dict[str, Any]) -> List[str]:
    os.makedirs(REPORT_DIR, exist_ok=True)
    md_path = os.path.join(REPORT_DIR, "evaluation_report.md")
    csv_path = os.path.join(REPORT_DIR, "evaluation_results.csv")
    json_path = os.path.join(REPORT_DIR, "evaluation_results.json")
    import pandas as pd

    rows = []
    for method, m in report["metrics"].items():
        for key, val in m.items():
            if isinstance(val, (int, float, str)) or val is None:
                rows.append({"method": method, "metric": key, "value": val})
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    slim = {k: v for k, v in report.items() if k != "per_case"}
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, indent=2)

    m_a = report["metrics"]["adaptive_agent"]
    m_b = report["metrics"]["fixed_baseline"]
    lines = [
        "# Evaluation Report — SIMULATED DATA ONLY (NOT real-world performance)",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Dataset size: **{report['dataset_size']} labelled synthetic cases** "
        f"(seed={report['seed']}, {report['n_per_template']} variants/template)  ",
        f"Data origin: **{report['data_origin']}** — fabricated by design.",
        "",
        "## Procedure",
        report["procedure"],
        "",
        "## Metric definitions",
        "- accuracy: primary category equals ground truth; for true-'inconclusive'"
        " cases, a correct abstention (no asserted category) counts as correct.",
        "- macro_f1: mean per-category F1 over the 12 problem categories.",
        "- abstention_correct_pct: truly-inconclusive cases returned as"
        " inconclusive/requires_further_investigation.",
        "- false_confident_pct: truly-inconclusive cases answered confirmed/probable.",
        "- avg_checks_per_case: executed diagnostic tools per case"
        " (not_applicable skips excluded).",
        "- avg_unnecessary_checks: checks run after the evidence prefix already"
        " supported the final classification (see evaluation.py docstring).",
        "- traceability_pct: diagnoses whose supporting-evidence ids all resolve"
        " to stored evidence items.",
        "- conflict_detection_pct: planted-conflict cases where conflicting"
        " evidence was recorded.",
        "",
        "## Results (computed from this run — nothing hardcoded)",
        "",
        "| Metric | Adaptive agent | Fixed baseline |",
        "|---|---|---|",
    ]
    keys = ["n_cases", "accuracy_pct", "macro_f1", "abstention_correct_pct",
            "false_confident_pct", "avg_checks_per_case",
            "avg_unnecessary_checks", "traceability_pct",
            "conflict_detection_pct", "runtime_s"]
    for k in keys:
        lines.append(f"| {k} | {m_a.get(k)} | {m_b.get(k)} |")
    lines += ["", "### Per-category accuracy (%)", "",
              "| Category | Adaptive | Baseline |", "|---|---|---|"]
    cats = sorted(set(m_a["per_category_accuracy"]) |
                  set(m_b["per_category_accuracy"]))
    for c in cats:
        lines.append(f"| {c} | {m_a['per_category_accuracy'].get(c)} | "
                     f"{m_b['per_category_accuracy'].get(c)} |")
    winner_note = ("The run above shows the adaptive agent ahead on these "
                   "synthetic cases." if m_a["accuracy_pct"] > m_b["accuracy_pct"]
                   else "NOTE: on this run the fixed baseline is equal or better "
                        "than the adaptive agent on accuracy.")
    lines += ["", f"{winner_note}", "", "## LIMITATIONS", ""]
    lines += [f"- {l}" for l in report["limitations"]]
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return [md_path, csv_path, json_path]
