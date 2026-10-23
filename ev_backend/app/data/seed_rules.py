"""Seed knowledge rules: signal_key -> category weights (source='seed').

These are the transparent, human-authored priors the agent uses to convert
tool signals into category belief scores. Feedback learning may later adjust
their weights in bounded steps (see services/feedback.py).
"""
from __future__ import annotations

import sqlite3
from typing import List, Tuple

# (category, signal_key, weight)
SEED_RULES: List[Tuple[str, str, float]] = [
    # --- session lifecycle -------------------------------------------------
    ("session_fails_to_start", "start_request_without_started_event", 0.85),
    ("session_fails_to_start", "error_code_E101_START_TIMEOUT", 0.80),
    ("session_fails_to_start", "authorize_then_no_start", 0.70),
    ("session_fails_to_start", "zero_energy_kwh", 0.45),
    ("session_stops_unexpectedly", "unexpected_stop_reason", 0.75),
    ("session_stops_unexpectedly", "short_duration_low_energy", 0.60),
    ("session_stops_unexpectedly", "connector_fault_mid_session", 0.70),
    ("session_stops_unexpectedly", "heartbeat_lost_mid_session", 0.65),
    # --- payment -----------------------------------------------------------
    ("payment_or_billing_failure", "payment_captured_without_session", 0.90),
    ("payment_or_billing_failure", "double_charge_same_session", 0.90),
    ("payment_or_billing_failure", "refund_pending_over_days", 0.75),
    ("payment_or_billing_failure", "amount_mismatch", 0.70),
    ("payment_or_billing_failure", "payment_failed_but_charged", 0.85),
    ("payment_or_billing_failure", "complaint_deducted_no_payment_record", 0.55),
    # --- availability / status --------------------------------------------
    ("charger_availability_mismatch", "status_log_shows_offline_while_app_showed_available", 0.85),
    ("charger_availability_mismatch", "reported_occupied_at_visit", 0.55),
    ("charger_availability_mismatch", "community_report_conflict", 0.60),
    ("incorrect_or_delayed_station_status", "status_update_lag_minutes", 0.80),
    ("incorrect_or_delayed_station_status", "no_status_update_since_ts", 0.70),
    ("incorrect_or_delayed_station_status", "reported_status_differs_from_source", 0.75),
    # --- app / connectivity -------------------------------------------------
    ("app_crash_or_technical_error", "app_error_event", 0.85),
    ("app_crash_or_technical_error", "crash_report_signal", 0.80),
    ("app_crash_or_technical_error", "error_code_APP500", 0.75),
    ("network_or_connectivity_issue", "heartbeat_lost", 0.80),
    ("network_or_connectivity_issue", "connection_timeout_events", 0.75),
    ("network_or_connectivity_issue", "charger_offline_period", 0.70),
    # --- hardware / handshake ----------------------------------------------
    ("connector_or_charger_hardware_fault", "connector_fault_event", 0.85),
    ("connector_or_charger_hardware_fault", "error_code_HW2xx", 0.80),
    ("connector_or_charger_hardware_fault", "overtemperature", 0.70),
    ("vehicle_charger_communication_issue", "handshake_failed", 0.90),
    ("vehicle_charger_communication_issue", "protocol_mismatch", 0.75),
    ("vehicle_charger_communication_issue", "error_code_HS3xx", 0.80),
    # --- history / support / telematics ------------------------------------
    ("incorrect_or_incomplete_charging_history", "missing_completed_session_in_history", 0.80),
    ("incorrect_or_incomplete_charging_history", "duplicate_history_entry", 0.75),
    ("incorrect_or_incomplete_charging_history", "history_amount_mismatch_vs_payment", 0.70),
    ("customer_support_issue", "support_ticket_open_over_days", 0.70),
    ("customer_support_issue", "no_response_signal", 0.65),
    ("vehicle_specific_or_telematics_issue", "telemetry_stale_last_seen", 0.80),
    ("vehicle_specific_or_telematics_issue", "soc_not_updating", 0.75),
    ("vehicle_specific_or_telematics_issue", "bms_error_signal", 0.70),
]


def seed_rules_if_empty(conn: sqlite3.Connection) -> int:
    """Insert seed rules only when the table is empty (idempotent)."""
    row = conn.execute("SELECT COUNT(*) AS c FROM knowledge_rules").fetchone()
    if row["c"] > 0:
        return 0
    added = 0
    for cat, sig, w in SEED_RULES:
        conn.execute(
            "INSERT INTO knowledge_rules(category, signal_key, weight, source, active)"
            " VALUES (?,?,?, 'seed', 1)", (cat, sig, w))
        added += 1
    conn.commit()
    return added
