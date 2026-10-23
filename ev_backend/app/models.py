"""Internal dataclasses shared by the agent, services and evaluation.

These are plain in-memory structures; persistence lives in the services layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    """One evidence item collected for a case."""
    kind: str                      # matching | conflicting | missing | neutral
    description: str
    source_table: Optional[str] = None
    source_ref: Optional[str] = None
    supports_category: Optional[str] = None   # category id (for matching/neutral)
    contradicts_category: Optional[str] = None  # category id (for conflicting)
    weight: float = 0.5
    tool_name: str = ""
    evidence_id: Optional[int] = None          # filled after persistence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "source_table": self.source_table,
            "source_ref": self.source_ref,
            "supports_category": self.supports_category,
            "contradicts_category": self.contradicts_category,
            "weight": round(self.weight, 4),
            "tool_name": self.tool_name,
            "evidence_id": self.evidence_id,
        }


@dataclass
class CheckResult:
    """What a diagnostic tool returns. Tools NEVER raise into the agent loop."""
    tool_name: str
    status: str                    # ok | no_data | error | not_applicable
    summary: str
    findings: Dict[str, Any] = field(default_factory=dict)
    evidence_items: List[Evidence] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)  # signal_keys observed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "summary": self.summary,
            "findings": self.findings,
            "signals": self.signals,
            "evidence_items": [e.to_dict() for e in self.evidence_items],
        }


@dataclass
class Complaint:
    app_id: str
    text_redacted: str
    complaint_ts: Optional[str] = None
    reported_error: Optional[str] = None
    reported_payment_status: Optional[str] = None
    session_ref: Optional[str] = None
    station_id: Optional[str] = None
    user_ref: Optional[str] = None
    data_origin: str = "manual_test"
    ground_truth_category: Optional[str] = None  # ONLY labelled synthetic/test
    scenario_id: Optional[str] = None


@dataclass
class CaseState:
    """The agent's explicit working state during one diagnosis run."""
    complaint: Complaint
    applicable_categories: List[str]
    beliefs: Dict[str, float] = field(default_factory=dict)      # category -> score
    evidence: List[Evidence] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)          # tool names
    run_signatures: set = field(default_factory=set)             # tool+inputs already run
    step_count: int = 0
    stop_status: Optional[str] = None                            # stop reason when done
    matched_session: Optional[Dict[str, Any]] = None
    top_category_before_last_step: Optional[str] = None
    pending_conflict: bool = False
    info_gaps: List[str] = field(default_factory=list)           # required-info list

    def top_category(self) -> Optional[str]:
        if not self.beliefs:
            return None
        best = max(self.beliefs.items(), key=lambda kv: (kv[1], kv[0]))
        return best[0] if best[1] > 0 else None

    def belief_snapshot(self) -> Dict[str, float]:
        return {k: round(v, 4) for k, v in sorted(
            self.beliefs.items(), key=lambda kv: -kv[1]) if v > 0.001}


@dataclass
class DiagnosisResult:
    classification: str            # confirmed|probable|inconclusive|requires_further_investigation
    primary_category: Optional[str]
    contributing_categories: List[str]
    evidence_strength: float
    evidence_strength_label: str
    supporting_evidence_ids: List[int]
    unresolved_questions: List[str]
    stop_reason: str
    recommendations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "classification": self.classification,
            "primary_category": self.primary_category,
            "contributing_categories": self.contributing_categories,
            "evidence_strength": round(self.evidence_strength, 3),
            "evidence_strength_label": self.evidence_strength_label,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "unresolved_questions": self.unresolved_questions,
            "stop_reason": self.stop_reason,
            "recommendations": self.recommendations,
        }
