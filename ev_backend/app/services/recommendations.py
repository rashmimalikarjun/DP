"""Recommendations: practical next actions per category, linked to evidence ids."""
from __future__ import annotations

from typing import Any, Dict, List

from app.models import CaseState, DiagnosisResult

CATEGORY_ACTIONS: Dict[str, List[Dict[str, Any]]] = {
    "payment_or_billing_failure": [
        {"action": "Initiate payment reconciliation for the affected transaction(s) "
                   "and verify capture/refund status with the payment gateway.",
         "rationale": "Payment records and session outcome disagree; reconciliation "
                      "confirms where the money is.", "priority": 1},
        {"action": "If deduction is confirmed without service, raise an automatic "
                   "refund and notify the customer with the refund reference.",
         "rationale": "Customer-visible resolution while back-office reconciliation runs.",
         "priority": 1},
    ],
    "session_fails_to_start": [
        {"action": "Verify session-start events on the charger (authorization -> "
                   "start_request -> started) and retry a session after power-cycling "
                   "the charger.",
         "rationale": "Timeline shows the start sequence never completed.",
         "priority": 1},
        {"action": "Escalate to station technician if start failures repeat on this "
                   "charger.",
         "rationale": "Repeated start timeouts usually indicate charger-side faults.",
         "priority": 2},
    ],
    "session_stops_unexpectedly": [
        {"action": "Inspect charger heartbeat continuity and connector lock status "
                   "during the session window.",
         "rationale": "Mid-session stop correlates with connectivity/connector events.",
         "priority": 1},
        {"action": "Check firmware logs on the charger for protective shutdowns "
                   "(over-temperature, earth-leakage).",
         "rationale": "Hardware protection trips commonly cause abrupt stops.",
         "priority": 2},
    ],
    "charger_availability_mismatch": [
        {"action": "Force a status refresh from the network API for the station and "
                   "audit the availability sync job.",
         "rationale": "Status log disagrees with what the app displayed.",
         "priority": 1},
    ],
    "incorrect_or_delayed_station_status": [
        {"action": "Audit the status-update pipeline latency for this station "
                   "(last successful update vs complaint time).",
         "rationale": "Evidence indicates stale/delayed status propagation.",
         "priority": 2},
    ],
    "app_crash_or_technical_error": [
        {"action": "Pull the app crash telemetry for the user's device/app version "
                   "and check whether the current release has a known regression.",
         "rationale": "Crash/error telemetry corroborates the complaint.",
         "priority": 1},
        {"action": "Ask the customer for app version, device model and a screenshot "
                   "of the error screen.",
         "rationale": "Needed to reproduce client-side issues.", "priority": 2},
    ],
    "network_or_connectivity_issue": [
        {"action": "Check charger SIM/link uptime at the complaint time and restart "
                   "the charger's communication module remotely.",
         "rationale": "Heartbeat-loss windows match the complaint.", "priority": 1},
    ],
    "connector_or_charger_hardware_fault": [
        {"action": "Dispatch a technician to inspect connector locking mechanism, "
                   "pins and fault codes on the specific charger.",
         "rationale": "Hardware fault events were logged for this charger.",
         "priority": 1},
        {"action": "Mark the charger unavailable until inspection completes.",
         "rationale": "Prevents further failed sessions for other customers.",
         "priority": 2},
    ],
    "vehicle_charger_communication_issue": [
        {"action": "Review handshake/negotiation logs (ISO 15118 / CAN) for the "
                   "session; test with another vehicle at the same charger.",
         "rationale": "Handshake failure recorded; need to separate vehicle vs "
                      "charger side.", "priority": 1},
        {"action": "Ask the customer to confirm vehicle model/firmware and retry "
                   "with a different connector.",
         "rationale": "Protocol compatibility issues are often vehicle-specific.",
         "priority": 2},
    ],
    "incorrect_or_incomplete_charging_history": [
        {"action": "Re-sync the user's charging history from backend records and "
                   "verify the history API pagination/dedup logic.",
         "rationale": "Backend sessions differ from the app history view.",
         "priority": 2},
    ],
    "customer_support_issue": [
        {"action": "Escalate the open support ticket to a supervisor queue and set "
                   "an SLA-bound follow-up.",
         "rationale": "Ticket age/response evidence shows stalled support.",
         "priority": 2},
    ],
    "vehicle_specific_or_telematics_issue": [
        {"action": "Refresh vehicle telemetry link (T-Box/OTA connectivity) and ask "
                   "the customer to wake the vehicle and retry syncing.",
         "rationale": "Telemetry staleness/BMS signals observed.", "priority": 1},
    ],
}

INFO_GATHER_ACTIONS = [
    {"action": "Request the exact session ID or transaction reference from the "
               "customer (visible in the app's 'My Sessions' screen).",
     "rationale": "No session could be matched to the complaint.", "priority": 1},
    {"action": "Request a screenshot of the app error/status display with visible "
               "time.",
     "rationale": "Needed to reconcile app-display claims with backend logs.",
     "priority": 2},
    {"action": "Request the exact date/time and station/charger identifier where "
               "the problem occurred.",
     "rationale": "Time+location narrows log matching drastically.", "priority": 1},
]


def build_recommendations(diag: DiagnosisResult, state: CaseState) -> List[Dict[str, Any]]:
    """Return recommendation dicts with linked evidence ids.

    Traceability chain: recommendation -> diagnosis -> evidence ids -> source row.
    """
    recs: List[Dict[str, Any]] = []

    def ev_ids_for(cat: str | None) -> List[int]:
        return [e.evidence_id for e in state.evidence
                if e.evidence_id is not None and e.kind == "matching"
                and (cat is None or e.supports_category == cat)]

    if diag.classification in ("confirmed", "probable") and diag.primary_category:
        for a in CATEGORY_ACTIONS.get(diag.primary_category, []):
            recs.append({**a, "linked_evidence_ids": ev_ids_for(diag.primary_category)})
        for cat in diag.contributing_categories:
            for a in CATEGORY_ACTIONS.get(cat, [])[:1]:
                recs.append({**a, "linked_evidence_ids": ev_ids_for(cat)})
    else:
        # inconclusive / requires_further_investigation: focus on gathering info
        for a in INFO_GATHER_ACTIONS:
            recs.append({**a, "linked_evidence_ids": []})
        missing = [e for e in state.evidence if e.kind == "missing"]
        if missing:
            recs.append({
                "action": "Restore access to the missing data sources listed under "
                          "'missing evidence' before re-running diagnosis.",
                "rationale": f"{len(missing)} required data source(s) had no rows.",
                "priority": 2,
                "linked_evidence_ids": [e.evidence_id for e in missing
                                        if e.evidence_id is not None]})
        if diag.primary_category and diag.classification != "requires_further_investigation":
            for a in CATEGORY_ACTIONS.get(diag.primary_category, [])[:1]:
                recs.append({**a, "linked_evidence_ids": ev_ids_for(diag.primary_category)})
    # de-duplicate by action text, keep order
    seen, out = set(), []
    for r in recs:
        if r["action"] in seen:
            continue
        seen.add(r["action"])
        out.append(r)
    return out
