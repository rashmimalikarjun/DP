"""Intake: validate + redact complaints, keyword->category priors, case creation helpers."""
from __future__ import annotations

import re
from typing import Dict, List

from app.models import Complaint
from app.redaction import redact_pii

# Keyword hints used ONLY to seed initial belief scores (a transparent prior).
# The agent must still verify with tools before concluding anything.
KEYWORD_HINTS: Dict[str, List[str]] = {
    "payment_or_billing_failure": [
        r"payment", r"charged", r"deduct", r"refund", r"billing", r"money",
        r"amount", r"double\s*charg", r"transaction", r"card",
    ],
    "session_fails_to_start": [
        r"(not|n't|never|didn.?t|did not)\s+(start|begin)", r"won.?t\s+start",
        r"fail(ed|s|ure)?\s+to\s+start", r"could\s*n.?t\s+start", r"start.*timeout",
        r"session.*not.*start", r"charging\s+never\s+started?",
    ],
    "session_stops_unexpectedly": [
        r"stop(ped|s)?\s+(unexpectedly|mid|automatically|in\s+between|suddenly|abruptly)",
        r"(cut|interrupt)ed?\s+(the\s+)?charg", r"stopped.*charg", r"charg.*stopped",
        r"terminated", r"paused\s+itself",
    ],
    "charger_availability_mismatch": [
        r"showed?\s+(as\s+)?available", r"shows?\s+(as\s+)?available",
        r"in\s+use", r"occupied", r"not\s+available", r"marked\s+(as\s+)?(free|available|empty)",
        r"availability", r"someone\s+else.*charg", r"gun\s+(was\s+)?(already\s+)?(in\s+use|being\s+used)",
    ],
    "incorrect_or_delayed_station_status": [
        r"status", r"offline", r"shown\s+as\s+online", r"showing\s+wrong",
        r"stale", r"not\s+updating", r"shows?\s+(the\s+)?(wrong|incorrect)",
    ],
    "app_crash_or_technical_error": [
        r"crash", r"force\s*d?closed?", r"freez", r"hung", r"blank\s+screen",
        r"app\s+error", r"technical\s+(issue|error|problem)", r"unable\s+to\s+open\s+the\s+app",
        r"app\s+not\s+opening", r"glitch",
    ],
    "network_or_connectivity_issue": [
        r"no\s+(internet|network|signal|connection)", r"connectivit", r"time[d]?\s*out",
        r"server\s+(is\s+)?down", r"could\s*n.?t\s+connect", r"lost\s+connection",
        r"heartbeat",
    ],
    "connector_or_charger_hardware_fault": [
        r"connector", r"gun", r"socket", r"cable", r"overheat", r"burn(t|ing)?\s+smell",
        r"spark", r"lock(ing|ed)?\s+(mechanism|fault)", r"hardware", r"physic(al|ally)\s+(damaged|broken|faulty)",
        r"port\s+(is\s+)?(broken|loose|damage)", r"pin",
    ],
    "vehicle_charger_communication_issue": [
        r"handshake", r"not\s+communicat", r"communication", r"protocol",
        r"vehicle\s+not\s+(detected|recogni[sz]ed)", r"not\s+detect(ing|ed)\s+(my\s+)?vehicle",
        r"negotiat",
    ],
    "incorrect_or_incomplete_charging_history": [
        r"history", r"past\s+session", r"log(s)?\s+(are\s+)?(missing|wrong|incorrect|incomplete)",
        r"missing\s+(from\s+)?(my\s+)?(charging\s+)?history", r"duplicat(e|ed)",
        r"transactions?\s+list",
    ],
    "customer_support_issue": [
        r"support", r"helpline", r"call\s*center", r"customer\s+care", r"ticket",
        r"no\s+response", r"nobody\s+respond", r"escalat", r"complaint.*(ignored|no action)",
    ],
    "vehicle_specific_or_telematics_issue": [
        r"state\s+of\s+charge|\bsoc\b", r"range\s+(showing|display)", r"telematic",
        r"vehicle\s+(is\s+)?offline", r"battery\s+(level\s+)?(not\s+updat|wrong|stuck)",
        r"bms", r"companion\s+app", r"odometer",
    ],
}


def keyword_category_hints(text: str) -> Dict[str, float]:
    """Return {category: 0..1 hint strength} from regex matches on redacted text."""
    out: Dict[str, float] = {}
    low = (text or "").lower()
    for cat, pats in KEYWORD_HINTS.items():
        hits = sum(1 for p in pats if re.search(p, low))
        if hits:
            out[cat] = min(1.0, 0.35 + 0.2 * hits)
    return out


def build_complaint(payload: dict) -> Complaint:
    """Create a Complaint from an already-validated request payload.

    complaint_text is best-effort redacted here (and again on any other ingest path).
    """
    raw = payload.get("complaint_text", "")
    return Complaint(
        app_id=payload["app_id"],
        text_redacted=redact_pii(raw),
        complaint_ts=payload.get("complaint_ts"),
        reported_error=payload.get("reported_error"),
        reported_payment_status=payload.get("reported_payment_status"),
        session_ref=payload.get("session_ref"),
        station_id=payload.get("station_id"),
        user_ref=payload.get("user_ref"),
        data_origin=payload.get("data_origin", "manual_test"),
        ground_truth_category=payload.get("ground_truth_category"),
        scenario_id=payload.get("scenario_id"),
    )
