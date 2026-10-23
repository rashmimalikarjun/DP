"""Tool registry.

Each tool is a module exposing TOOL_NAME, RELATED_CATEGORIES, run(conn, state) -> CheckResult.
Tools must NEVER raise; they catch exceptions and return status='error'.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Dict, List

from app.models import CaseState, CheckResult

from app.agent.tools import (
    session_lookup, timeline_check, error_code_check, payment_check,
    connectivity_check, connector_check, handshake_check,
    station_status_check, history_check, app_error_check, telematics_check,
    similar_cases_check,
)

TOOL_MODULES = [
    session_lookup,
    timeline_check,
    error_code_check,
    payment_check,
    connectivity_check,
    connector_check,
    handshake_check,
    station_status_check,
    history_check,
    app_error_check,
    telematics_check,
    similar_cases_check,
]


class DiagnosticTool:
    def __init__(self, name: str, fn: "Callable[[sqlite3.Connection, CaseState], CheckResult]",
                 related_categories: List[str], requires_session: bool = True,
                 description: str = ""):
        self.name = name
        self.fn = fn
        self.related_categories = related_categories
        self.requires_session = requires_session
        self.description = description

    def __call__(self, conn: sqlite3.Connection, state: CaseState) -> CheckResult:
        return self.fn(conn, state)


def _wrap(mod) -> DiagnosticTool:
    return DiagnosticTool(
        name=mod.TOOL_NAME,
        fn=mod.run,
        related_categories=list(mod.RELATED_CATEGORIES),
        requires_session=getattr(mod, "REQUIRES_SESSION", True),
        description=getattr(mod, "DESCRIPTION", ""),
    )


TOOL_REGISTRY: Dict[str, DiagnosticTool] = {m.TOOL_NAME: _wrap(m) for m in TOOL_MODULES}

# Fixed order used by the baseline (documented: same tools, same order; the
# baseline still filters by app applicability so the comparison stays fair).
BASELINE_ORDER: List[str] = [
    "session_lookup",
    "timeline_check",
    "error_code_check",
    "payment_check",
    "connectivity_check",
    "connector_check",
    "handshake_check",
    "station_status_check",
    "history_check",
    "app_error_check",
    "telematics_check",
    "similar_cases_check",
]
