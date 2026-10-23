"""Pydantic request/response models — the single source of the API contract.

All endpoints under /api/v1 use these models. Error responses use ApiError.
"""
from __future__ import annotations

from datetime import datetime  # noqa: F401  (used in docstrings/types below)
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

DATA_ORIGINS = ("real_public_review", "simulated", "manual_test")


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ApiError(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Apps & categories
# ---------------------------------------------------------------------------

class AppInfo(BaseModel):
    id: str
    display_name: str
    app_type: str
    applicable_categories: List[str]


class CategoryInfo(BaseModel):
    id: str
    label: str
    description: str
    example_signals: List[str]


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

class ComplaintSubmit(BaseModel):
    app_id: str = Field(min_length=1)
    complaint_text: str = Field(min_length=10, max_length=5000,
                                description="Free-text complaint; PII is best-effort redacted on ingest.")
    complaint_ts: Optional[str] = Field(
        default=None, description="ISO-8601 timestamp of when the problem occurred")
    reported_error: Optional[str] = Field(default=None, max_length=200)
    reported_payment_status: Optional[str] = Field(
        default=None, description="e.g. deducted | not_deducted | refund_pending | unknown")
    session_ref: Optional[str] = Field(default=None, max_length=100)
    station_id: Optional[str] = Field(default=None, max_length=100)
    user_ref: Optional[str] = Field(default=None, max_length=100,
                                    description="Pseudonymous user reference (no phone/email)")
    data_origin: str = Field(default="manual_test")

    @field_validator("data_origin")
    @classmethod
    def _check_origin(cls, v: str) -> str:
        if v not in DATA_ORIGINS:
            raise ValueError(
                f"data_origin must be one of {list(DATA_ORIGINS)}")
        return v

    @field_validator("complaint_ts")
    @classmethod
    def _check_ts(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return None
        from datetime import datetime as _dt
        try:
            _dt.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("complaint_ts must be an ISO-8601 timestamp")
        return v


class EvidenceOut(BaseModel):
    id: int
    kind: str
    description: str
    source_table: Optional[str] = None
    source_ref: Optional[str] = None
    supports_category: Optional[str] = None
    weight: float
    tool_name: Optional[str] = None


class RecommendationOut(BaseModel):
    id: int
    action: str
    rationale: str
    priority: int
    linked_evidence_ids: List[int] = []


class DiagnosisOut(BaseModel):
    id: int
    version: int
    classification: str
    primary_category: Optional[str] = None
    contributing_categories: List[str] = []
    evidence_strength: float
    evidence_strength_label: str
    supporting_evidence_ids: List[int] = []
    unresolved_questions: List[str] = []
    stop_reason: str


class CaseSummary(BaseModel):
    case_id: str
    created_at: str
    app_id: str
    app_display_name: Optional[str] = None
    status: str
    data_origin: str
    complaint_text_redacted: str
    diagnosis: Optional[DiagnosisOut] = None


class CaseFull(BaseModel):
    case: CaseSummary
    complaint: Dict[str, Any]
    evidence: Dict[str, List[EvidenceOut]]  # grouped by kind
    diagnosis: Optional[DiagnosisOut] = None
    recommendations: List[RecommendationOut] = []
    ground_truth_category: Optional[str] = None
    resolution: Optional[Dict[str, Any]] = None


class CheckTraceStep(BaseModel):
    step_no: int
    tool_name: str
    selection_reason: str
    candidate_scores: List[Dict[str, Any]]
    inputs: Dict[str, Any]
    result: Dict[str, Any]
    outcome_summary: str
    state_changes: Dict[str, Any]
    replanned: bool
    created_at: str


class CaseTrace(BaseModel):
    case_id: str
    steps: List[CheckTraceStep]
    stop_reason: str
    final_beliefs: Dict[str, float]


class PaginatedCases(BaseModel):
    items: List[CaseSummary]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Resolution / reviewers
# ---------------------------------------------------------------------------

class ResolutionSubmit(BaseModel):
    reviewer_id: str = Field(min_length=1)
    verified_root_cause: str = Field(min_length=1)
    verified_contributing: List[str] = []
    corrective_action: str = Field(min_length=3, max_length=2000)
    resolution_notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("verified_root_cause", "verified_contributing")
    @classmethod
    def _check_cat(cls, v):
        from app.core.settings import category_ids
        valid = set(category_ids())
        items = v if isinstance(v, list) else [v]
        bad = [c for c in items if c not in valid]
        if bad:
            raise ValueError(f"Unknown category id(s): {bad}")
        return v


class ReviewerOut(BaseModel):
    reviewer_id: str
    name: str
    role: str
    active: bool


class LearningChangePreview(BaseModel):
    change_type: str
    rule_id: Optional[int] = None
    tool_name: Optional[str] = None
    category: Optional[str] = None
    signal_key: Optional[str] = None
    old_value: Optional[float] = None
    new_value: Optional[float] = None
    reason: str


class LearningResult(BaseModel):
    applied: bool
    dry_run: bool
    resolution_id: int
    changes: List[LearningChangePreview]
    already_applied: bool = False


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------

class KnowledgeRuleOut(BaseModel):
    rule_id: int
    version: int
    category: str
    signal_key: str
    weight: float
    source: str
    active: bool
    created_at: str
    created_from_resolution_id: Optional[int] = None


class ToolPriorOut(BaseModel):
    tool_name: str
    category: str
    usefulness_score: float
    n_observations: int
    updated_at: str


class KnowledgeChangeOut(BaseModel):
    change_id: int
    rule_id: Optional[int] = None
    tool_name: Optional[str] = None
    category: Optional[str] = None
    change_type: str
    old_value_json: Optional[str] = None
    new_value_json: Optional[str] = None
    reason: str
    triggered_by_resolution_id: Optional[int] = None
    created_at: str
    reverted: bool
    reverted_at: Optional[str] = None


class RollbackRequest(BaseModel):
    to_change_id: int = Field(description="Roll back all learning changes AFTER this change id")
    reason: str = Field(default="manual rollback", min_length=3, max_length=500)


# ---------------------------------------------------------------------------
# Reviews import
# ---------------------------------------------------------------------------

class ReviewImportResult(BaseModel):
    imported: int
    skipped: int
    errors: List[str]
    note: str = ("Reviews are stored with best-effort PII redaction. data_origin "
                 "must honestly reflect provenance.")


class ReviewOut(BaseModel):
    review_id: int
    app_id: str
    source: str
    source_ref: Optional[str] = None
    review_date: Optional[str] = None
    redacted_text: str
    problem_category: Optional[str] = None
    data_origin: str
    imported_at: str


# ---------------------------------------------------------------------------
# Metrics / evaluation
# ---------------------------------------------------------------------------

class MetricsPerformance(BaseModel):
    total_resolved_cases: int
    small_sample_warning: bool
    sample_size_note: str
    filters_applied: Dict[str, Any]
    primary_accuracy_pct: Optional[float] = None
    partial_match_pct: Optional[float] = None
    inconclusive_rate_pct: Optional[float] = None
    counts: Dict[str, int]
    failure_category_frequency: Dict[str, int]


class RecurringCategoryRow(BaseModel):
    app_id: str
    category: str
    count: int


class RecurringProblem(BaseModel):
    kind: str          # station | charger
    identifier: str
    category: str
    count: int


class RecurringOut(BaseModel):
    by_app_category: List[RecurringCategoryRow]
    over_time: List[Dict[str, Any]]
    recurring_stations: List[RecurringProblem]
    recurring_chargers: List[RecurringProblem]
    data_origin_counts: Dict[str, int]
    note: str


class EvaluationRunRequest(BaseModel):
    seed: Optional[int] = None
    cases_per_category: int = Field(default=8, ge=1, le=40)


class EvaluationReportOut(BaseModel):
    generated_at: str
    dataset_size: int
    data_origin: str
    seed: int
    procedure: str
    metrics: Dict[str, Dict[str, Any]]           # method -> metric dict
    per_category_accuracy: Dict[str, Dict[str, float]]
    limitations: List[str]
    markdown_available: bool = True


class HealthOut(BaseModel):
    status: str
    db_ok: bool
    schema_version: Optional[int] = None
    time: str
