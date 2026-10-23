"""Evaluation router: run/serve the adaptive-vs-baseline evaluation.

All numbers come from actual runs; nothing is hardcoded. Results are kept in
process memory (latest report) and also written to ev_backend/reports/.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter

from app.evaluation.evaluation import evaluate, write_reports
from app.routers.errors import ApiHTTP
from app.schemas import EvaluationRunRequest
from app.core.logging_setup import get_logger

log = get_logger("app.routers.evaluation")
router = APIRouter(prefix="/evaluation", tags=["evaluation"])

_lock = threading.Lock()
_latest: Optional[Dict[str, Any]] = None


def _slim(report: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in report.items() if k != "per_case"}


@router.get("/summary", summary="Latest evaluation summary (or null if never run)")
def summary():
    if _latest is None:
        return {"available": False,
                "note": "No evaluation has been run in this server session. "
                        "POST /api/v1/evaluation/run or run scripts/run_evaluation.py."}
    rep = _slim(_latest)
    rep["available"] = True
    return rep


@router.post("/run", summary="Run adaptive agent vs fixed baseline on identical "
                             "labelled synthetic cases (fresh temp DB; may take "
                             "a few seconds)")
def run_eval(req: EvaluationRunRequest):
    global _latest
    if not _lock.acquire(blocking=False):
        raise ApiHTTP(409, "evaluation_running",
                      "An evaluation is already running; try again shortly.")
    try:
        seed = req.seed if req.seed is not None else 42
        report = evaluate(seed=seed, n_per_template=req.cases_per_category)
        write_reports(report)
        _latest = report
        log.info("evaluation run complete: %d cases", report["dataset_size"])
        return _slim(report)
    except Exception as exc:  # surface a clean error, no stack trace
        log.exception("evaluation failed")
        raise ApiHTTP(500, "evaluation_failed",
                      "Evaluation could not be completed.",
                      {"reason": str(exc)[:200]})
    finally:
        _lock.release()


@router.get("/results", summary="Full latest results incl. per-case rows")
def results():
    if _latest is None:
        raise ApiHTTP(404, "no_evaluation",
                      "No evaluation results available yet. POST /evaluation/run first.")
    return _latest
