"""SQLite access layer for the EV diagnosis database.

- DB path from env var EV_DB_PATH (default ev_diagnosis.db next to ev_backend/).
- NEVER opens catering.db: `assert_safe_db_path` guards every connection.
- Parameterised queries only; foreign keys ON; WAL mode; per-request connections.
- Idempotent schema creation with a schema_version table and ordered migrations.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

DEFAULT_DB_NAME = "ev_diagnosis.db"
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))  # .../ev_backend
DEFAULT_DB_PATH = os.path.join(os.path.dirname(BACKEND_DIR), DEFAULT_DB_NAME)

DATA_ORIGINS = ("real_public_review", "simulated", "manual_test")


class DbPathError(RuntimeError):
    """Raised when a configured DB path is not allowed (e.g. catering.db)."""


def assert_safe_db_path(path: str) -> None:
    """Refuse any path whose filename is catering.db (the unrelated legacy app)."""
    name = os.path.basename(path).lower()
    if name == "catering.db":
        raise DbPathError(
            "Refusing to open catering.db. The EV diagnosis system uses its own "
            "database file (EV_DB_PATH / ev_diagnosis.db).")


def get_db_path() -> str:
    path = os.environ.get("EV_DB_PATH") or DEFAULT_DB_PATH
    assert_safe_db_path(path)
    return path


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection with sane defaults. Caller manages lifecycle."""
    db_path = path if path is not None else get_db_path()
    assert_safe_db_path(db_path)
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ---------------------------------------------------------------------------
# Schema (versioned, ordered migrations)
# ---------------------------------------------------------------------------

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS apps (
    app_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    app_type TEXT NOT NULL CHECK (app_type IN ('charging_network','discovery','vehicle_companion'))
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL REFERENCES apps(app_id),
    source TEXT NOT NULL,
    source_ref TEXT,
    review_date TEXT,
    original_text TEXT NOT NULL,
    redacted_text TEXT NOT NULL,
    problem_category TEXT,
    data_origin TEXT NOT NULL CHECK (data_origin IN ('real_public_review','simulated','manual_test')),
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS charging_sessions (
    session_id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    user_ref TEXT NOT NULL,
    station_id TEXT NOT NULL,
    charger_id TEXT NOT NULL,
    connector_id TEXT,
    start_ts TEXT,
    end_ts TEXT,
    status TEXT,
    energy_kwh REAL CHECK (energy_kwh IS NULL OR energy_kwh >= 0),
    error_code TEXT,
    stop_reason TEXT,
    payment_status TEXT,
    payment_ref TEXT,
    amount REAL CHECK (amount IS NULL OR amount >= 0),
    data_origin TEXT NOT NULL CHECK (data_origin IN ('real_public_review','simulated','manual_test')),
    scenario_id TEXT
);

CREATE TABLE IF NOT EXISTS session_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES charging_sessions(session_id),
    charger_id TEXT,
    event_ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    data_origin TEXT NOT NULL CHECK (data_origin IN ('real_public_review','simulated','manual_test'))
);

CREATE TABLE IF NOT EXISTS payment_records (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES charging_sessions(session_id),
    app_id TEXT,
    user_ref TEXT,
    status TEXT NOT NULL,
    amount REAL CHECK (amount IS NULL OR amount >= 0),
    ts TEXT NOT NULL,
    reconciliation_status TEXT,
    data_origin TEXT NOT NULL CHECK (data_origin IN ('real_public_review','simulated','manual_test'))
);

CREATE TABLE IF NOT EXISTS charger_status_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    charger_id TEXT NOT NULL,
    station_id TEXT,
    ts TEXT NOT NULL,
    reported_status TEXT NOT NULL,
    source TEXT NOT NULL,
    data_origin TEXT NOT NULL CHECK (data_origin IN ('real_public_review','simulated','manual_test'))
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    app_id TEXT NOT NULL REFERENCES apps(app_id),
    complaint_text_redacted TEXT NOT NULL,
    complaint_ts TEXT,
    reported_error TEXT,
    reported_payment_status TEXT,
    reported_session_ref TEXT,
    reported_station_id TEXT,
    user_ref TEXT,
    data_origin TEXT NOT NULL CHECK (data_origin IN ('real_public_review','simulated','manual_test')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','diagnosed','awaiting_review','resolved')),
    ground_truth_category TEXT,
    scenario_id TEXT
);

CREATE TABLE IF NOT EXISTS case_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('matching','conflicting','missing','neutral')),
    description TEXT NOT NULL,
    source_table TEXT,
    source_ref TEXT,
    supports_category TEXT,
    weight REAL NOT NULL DEFAULT 0.5,
    tool_name TEXT
);

CREATE TABLE IF NOT EXISTS case_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    selection_reason TEXT NOT NULL,
    candidate_scores_json TEXT NOT NULL,
    inputs_json TEXT,
    result_json TEXT,
    outcome_summary TEXT,
    state_changes_json TEXT,
    replanned INTEGER NOT NULL DEFAULT 0 CHECK (replanned IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    classification TEXT NOT NULL CHECK (classification IN ('confirmed','probable','inconclusive','requires_further_investigation')),
    primary_category TEXT,
    contributing_categories_json TEXT,
    evidence_strength REAL NOT NULL CHECK (evidence_strength >= 0 AND evidence_strength <= 1),
    evidence_strength_label TEXT NOT NULL,
    supporting_evidence_ids_json TEXT,
    unresolved_questions_json TEXT,
    stop_reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    diagnosis_id INTEGER NOT NULL REFERENCES diagnoses(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    rationale TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 2,
    linked_evidence_ids_json TEXT
);

CREATE TABLE IF NOT EXISTS reviewers (
    reviewer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    reviewer_id TEXT NOT NULL REFERENCES reviewers(reviewer_id),
    verified_root_cause TEXT NOT NULL,
    verified_contributing_json TEXT,
    corrective_action TEXT NOT NULL,
    resolution_notes TEXT,
    resolved_at TEXT NOT NULL DEFAULT (datetime('now')),
    agent_prediction_category TEXT,
    prediction_correct INTEGER CHECK (prediction_correct IN (0,1)),
    learning_applied INTEGER NOT NULL DEFAULT 0 CHECK (learning_applied IN (0,1))
);

CREATE TABLE IF NOT EXISTS knowledge_rules (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL DEFAULT 1,
    category TEXT NOT NULL,
    signal_key TEXT NOT NULL,
    weight REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
    source TEXT NOT NULL DEFAULT 'seed' CHECK (source IN ('seed','learned')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_from_resolution_id INTEGER REFERENCES resolutions(id)
);

CREATE TABLE IF NOT EXISTS knowledge_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER,
    tool_name TEXT,
    category TEXT,
    change_type TEXT NOT NULL CHECK (change_type IN ('create','update','deactivate','rollback','prior_update')),
    old_value_json TEXT,
    new_value_json TEXT,
    reason TEXT NOT NULL,
    triggered_by_resolution_id INTEGER REFERENCES resolutions(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    reverted INTEGER NOT NULL DEFAULT 0 CHECK (reverted IN (0,1)),
    reverted_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_priors (
    tool_name TEXT NOT NULL,
    category TEXT NOT NULL,
    usefulness_score REAL NOT NULL DEFAULT 0.5 CHECK (usefulness_score >= 0 AND usefulness_score <= 1),
    n_observations INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tool_name, category)
);

CREATE INDEX IF NOT EXISTS idx_sessions_app_user ON charging_sessions(app_id, user_ref);
CREATE INDEX IF NOT EXISTS idx_sessions_station ON charging_sessions(station_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_charger ON session_events(charger_id);
CREATE INDEX IF NOT EXISTS idx_cases_app ON cases(app_id);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_evidence_case ON case_evidence(case_id);
CREATE INDEX IF NOT EXISTS idx_checks_case ON case_checks(case_id, step_no);
CREATE INDEX IF NOT EXISTS idx_status_charger ON charger_status_log(charger_id, ts);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payment_records(user_ref, ts);
"""

MIGRATIONS: List[str] = [MIGRATION_001]


SCHEMA_VERSION_KEY = 1  # bump when adding MIGRATION_002 etc.


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotently create/upgrade the schema."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] if row and row["v"] is not None else 0
    for idx, migration in enumerate(MIGRATIONS, start=1):
        if current < idx:
            conn.executescript(migration)
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (idx,))
            conn.commit()


def ensure_schema(path: Optional[str] = None) -> sqlite3.Connection:
    conn = connect(path)
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def query_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def sync_config_tables(conn: sqlite3.Connection) -> None:
    """Upsert apps from config/apps.json so the DB always matches configuration."""
    from app.core import settings as cfg
    for a in cfg.apps_raw():
        conn.execute(
            "INSERT INTO apps(app_id, display_name, app_type) VALUES (?,?,?) "
            "ON CONFLICT(app_id) DO UPDATE SET display_name=excluded.display_name, "
            "app_type=excluded.app_type",
            (a["id"], a["display_name"], a["app_type"]))
    conn.commit()
