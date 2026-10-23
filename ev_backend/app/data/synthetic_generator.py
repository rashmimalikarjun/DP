"""Deterministic synthetic EV-charging failure dataset generator.

EVERYTHING PRODUCED HERE IS FABRICATED BY DESIGN. It says nothing about any
real charging network, app or company. All rows are written with
data_origin='simulated' and a scenario_id so they can be filtered/reset.

Design notes
------------
* Fixed seed (default 42) -> byte-identical output across runs.
* Each template defines: ground-truth primary category (+ optional secondary),
  the complaint text variants, and which backend evidence rows exist.
* The agent only ever sees the complaint + evidence tables; ground truth lives
  in cases.ground_truth_category / sessions.scenario_id and is NEVER leaked
  into complaint text or tool-visible fields.
* Deliberate hard cases: conflicting evidence, missing logs, underdetermined
  (true label 'inconclusive'), non-applicable traps (billing on PlugShare,
  hardware on Bajaj Chetak), noisy distractor events, PII in text to exercise
  redaction.
* Idempotent seeding: rows for a given (seed, n_per_template) are keyed by
  deterministic scenario/session ids, so re-running does not duplicate data.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

SEED_DEFAULT = 42
DATA_ORIGIN = "simulated"

NETWORK_APPS = ["ehub_mg", "tata_power_ez_charge", "statiq", "chargezone",
                "kazam_ev", "zeon_charging"]
ALL_APP_IDS = NETWORK_APPS + ["plugshare", "bajaj_chetak"]

# ---------------------------------------------------------------------------
# Complaint wording pools (varied phrasing per template). {placeholders}
# ---------------------------------------------------------------------------
W = {
    "start_fail": [
        "I plugged in at station {station} but charging never started. The session failed to start and I got error {code}.",
        "Charging did not begin at all today at {station}. Session start failed repeatedly (error {code}).",
        "My session wouldn't start at {station}; the app kept saying starting then failed with code {code}.",
    ],
    "stop_early": [
        "Charging stopped unexpectedly after a few minutes at {station}. It was interrupted mid-session.",
        "The session terminated itself early at {station} — charging stopped suddenly even though the battery was not full.",
        "My charge got cut off in between at {station}; the session stopped automatically with little energy delivered.",
    ],
    "payment_deducted_no_session": [
        "Money was deducted from my account but the charging session never started at {station}. Please refund Rs {amt}.",
        "Payment of Rs {amt} deducted, no session created at {station}. I need this reconciled urgently.",
        "Amount Rs {amt} charged although charging failed to start at {station}. Where is my money?",
    ],
    "double_charge": [
        "I was double charged for one session {session} at {station}. Two payments of Rs {amt} went out.",
        "Two identical deductions of Rs {amt} for the same charging session {session} at {station}.",
    ],
    "refund_pending": [
        "Refund for session {session} was promised days ago but still pending. Amount Rs {amt} not returned.",
        "My refund of Rs {amt} for the failed session {session} is stuck as 'pending' for over a week.",
    ],
    "availability": [
        "The app showed the charger as available at {station} but when I reached it was offline/out of order.",
        "Station {station} shows available in the app yet the charger was actually not available when I arrived.",
        "Marked free in the app, but someone else was charging there — availability mismatch at {station}.",
    ],
    "status_stale": [
        "The station status for {station} is wrong in the app; it has been offline since yesterday but still shows online.",
        "Status of charger at {station} is stale and not updating — the app shows the wrong state.",
    ],
    "app_crash": [
        "The app keeps crashing whenever I try to start a session. Force closed twice, blank screen.",
        "App crashed repeatedly today (technical error); I could not open the app at all.",
        "The application freezes and hangs every time I tap 'start charging'. Glitchy behaviour.",
    ],
    "connectivity": [
        "The charger lost connection mid-way — heartbeat lost and the session dropped at {station}.",
        "Network issue at {station}: the charger went offline due to connectivity loss and timed out.",
    ],
    "hardware": [
        "The connector gun at {station} looks physically damaged and overheated; it tripped fault code {code}.",
        "Hardware problem: connector locking failed and there was a burning smell from the charger at {station}, error {code}.",
    ],
    "handshake": [
        "The vehicle and charger could not complete the handshake at {station}; communication failed with code {code}.",
        "No negotiation happened between my car and the charger at {station} — handshake failed ({code}).",
    ],
    "history": [
        "A completed charging session is missing from my history list in the app.",
        "My charging history shows a duplicate entry for session {session} with the wrong amount.",
        "Transactions list in the app is incomplete — last week's sessions are missing from history.",
    ],
    "support": [
        "I raised a ticket about this a week ago and nobody responded. Customer support is ignoring my complaint.",
        "Called the helpline three times, opened a ticket, no response from customer care at all.",
    ],
    "telematics": [
        "My companion app shows the vehicle offline and the battery SOC is not updating after charging.",
        "The scooter telemetry in the app is stale — range shown is wrong and BMS error appears after a charge attempt.",
    ],
    "ambiguous": [
        "Something went wrong while charging at {station}; not sure what happened, it just did not work properly.",
        "Charging was problematic recently. Details unclear.",
        "It didn't work well the other day at the station.",
    ],
    "conflict_payment": [
        "I paid Rs {amt} for session {session} but the app says payment failed and the session shows completed.",
        "Money left my account but the app shows no payment record for that session at {station}.",
    ],
}

PII_SNIPPETS = [
    " My number is 9812345678, please call.",
    " Contact me at user.testcase@example.com.",
    " My car registration MH12AB1234 is parked there.",
    " Card ending 4111 1111 1111 1111 was used.",
    "",
]


def _fmt(tpl_key: str, rng: random.Random, ctx: Dict[str, str]) -> str:
    text = rng.choice(W[tpl_key]).format(**ctx)
    pii = rng.choice(PII_SNIPPETS) if rng.random() < 0.35 else ""
    return text + pii


# ---------------------------------------------------------------------------
# Templates: (id, key, primary, secondary_or_None, apps_pool, difficulty)
# ---------------------------------------------------------------------------
TEMPLATES: List[Tuple[str, str, str, Optional[str], str, str]] = [
    ("T01", "start_fail", "session_fails_to_start", None, "network", "normal"),
    ("T02", "start_fail", "session_fails_to_start", "vehicle_charger_communication_issue", "network", "multifactor"),
    ("T03", "stop_early", "session_stops_unexpectedly", "network_or_connectivity_issue", "network", "multifactor"),
    ("T04", "stop_early", "session_stops_unexpectedly", "connector_or_charger_hardware_fault", "network", "multifactor"),
    ("T05", "payment_deducted_no_session", "payment_or_billing_failure", "session_fails_to_start", "network", "payment"),
    ("T06", "double_charge", "payment_or_billing_failure", None, "network", "payment"),
    ("T07", "refund_pending", "payment_or_billing_failure", None, "network", "payment"),
    ("T08", "availability", "charger_availability_mismatch", None, "all", "normal"),
    ("T09", "status_stale", "incorrect_or_delayed_station_status", None, "all_except_bajaj", "normal"),
    ("T10", "app_crash", "app_crash_or_technical_error", None, "all", "normal"),
    ("T11", "connectivity", "network_or_connectivity_issue", None, "network", "normal"),
    ("T12", "hardware", "connector_or_charger_hardware_fault", None, "network", "normal"),
    ("T13", "handshake", "vehicle_charger_communication_issue", None, "network", "normal"),
    ("T14", "history", "incorrect_or_incomplete_charging_history", None, "all", "normal"),
    ("T15", "support", "customer_support_issue", None, "all", "normal"),
    ("T16", "telematics", "vehicle_specific_or_telematics_issue", None, "bajaj_only", "normal"),
    ("T17", "ambiguous", "inconclusive", None, "all", "underdetermined"),
    ("T18", "conflict_payment", "payment_or_billing_failure", None, "network", "conflicting"),
    ("T19", "availability", "charger_availability_mismatch", None, "plugshare_only", "trap_ok"),
    ("T20", "payment_deducted_no_session", "inconclusive", None, "plugshare_only", "trap_nonapplicable"),
    ("T21", "hardware", "inconclusive", None, "bajaj_only", "trap_partial"),
    ("T22", "start_fail", "session_fails_to_start", None, "network", "missing_logs"),
    ("T23", "stop_early", "session_stops_unexpectedly", None, "network", "noisy"),
    ("T24", "handshake", "vehicle_charger_communication_issue", "session_fails_to_start", "network", "multifactor"),
    ("T25", "ambiguous", "inconclusive", None, "all_except_bajaj", "underdetermined"),
]

ERROR_CODES = {
    "start_fail": "E101",
    "stop_early": "HW204",
    "hardware": "HW210",
    "handshake": "HS301",
    "app_crash": "APP500",
}


class SyntheticDataset:
    """In-memory representation; .write(conn) inserts everything."""

    def __init__(self, seed: int = SEED_DEFAULT, n_per_template: int = 10):
        self.seed = seed
        self.n_per_template = n_per_template
        self.sessions: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.payments: List[Dict[str, Any]] = []
        self.status_log: List[Dict[str, Any]] = []
        self.cases: List[Dict[str, Any]] = []
        self._build()

    # helpers ---------------------------------------------------------------
    @staticmethod
    def _apps_for(pool: str, rng: random.Random) -> List[str]:
        if pool == "network":
            return NETWORK_APPS
        if pool == "all":
            return ALL_APP_IDS
        if pool == "all_except_bajaj":
            return [a for a in ALL_APP_IDS if a != "bajaj_chetak"]
        if pool == "plugshare_only":
            return ["plugshare"]
        if pool == "bajaj_only":
            return ["bajaj_chetak"]
        return NETWORK_APPS

    def _ids(self, i: int):
        sc = f"SIM-{self.seed:03d}-{i:04d}"
        ss = f"SSN-{self.seed:03d}-{i:04d}"
        st = f"STN-{(i % 12) + 101:03d}"
        ch = f"CHR-{(i % 12) + 101:03d}-{(i % 3) + 1}"
        us = f"USR-{(i % 40) + 1000:04d}"
        base = datetime(2026, 9, 1, 8, 0, 0) + timedelta(hours=i * 7 % 900,
                                                         minutes=(i * 13) % 60)
        return sc, ss, st, ch, us, base

    # builder ---------------------------------------------------------------
    def _build(self) -> None:
        rng = random.Random(self.seed)
        idx = 0
        for tid, key, primary, secondary, pool, difficulty in TEMPLATES:
            apps = self._apps_for(pool, rng)
            for v in range(self.n_per_template):
                idx += 1
                sc, sid, station, charger, user, t0 = self._ids(idx)
                app = apps[(idx + v) % len(apps)]
                amt = round(rng.uniform(80, 600), 2)
                code = ERROR_CODES.get(key, "")
                ctx = {"station": station, "amt": int(amt), "code": code,
                       "session": sid}
                text = _fmt(key, rng, ctx)
                tss = t0.isoformat(sep=" ")

                case = dict(case_id=sc, app_id=app, complaint_text=text,
                            complaint_ts=tss, reported_error=code or None,
                            reported_payment_status=None,
                            reported_session_ref=None, station_id=station,
                            user_ref=user, ground_truth=primary,
                            secondary=secondary, scenario_id=sc)

                sess = dict(session_id=sid, app_id=app, user_ref=user,
                            station_id=station, charger_id=charger,
                            connector_id=f"{charger}-C1", start_ts=tss,
                            end_ts=(t0 + timedelta(minutes=3)).isoformat(sep=" "),
                            status="completed", energy_kwh=10.0,
                            error_code=None, stop_reason=None,
                            payment_status="success", payment_ref=f"PAY-{sid}",
                            amount=amt, scenario_id=sc)
                evs = [("authorize", "authorized ok")]
                pays = [dict(session_id=sid, app_id=app, user_ref=user,
                             status="captured", amount=amt, ts=tss,
                             reconciliation_status="settled")]
                stat = [(charger, tss, "available", "network")]

                # ---- per-template evidence construction -------------------
                if key in ("start_fail",):
                    sess.update(status="failed", energy_kwh=0.0,
                                error_code="E101", stop_reason="start_timeout",
                                payment_status="none", end_ts=None)
                    evs = [("authorize", "ok"), ("start_request", "sent")]
                    if difficulty == "multifactor":
                        evs.append(("handshake_failed", "HS301 negotiation timeout"))
                    if difficulty == "missing_logs":
                        evs = []          # deliberately NO event logs
                    if difficulty == "trap_nonapplicable":
                        # PlugShare billing trap: keep a captured payment so the
                        # conflict exists, but the CATEGORY is non-applicable.
                        pass
                    pays = ([pays[0]] if app == "plugshare" else [])
                elif key == "stop_early":
                    sess.update(status="stopped_early", energy_kwh=0.4,
                                stop_reason="unexpected_stop")
                    evs += [("started", "charging began"),
                            ("heartbeat_lost", "lost mid-session")
                            if difficulty == "multifactor" and secondary ==
                            "network_or_connectivity_issue" else
                            ("connector_fault", "fault mid-session")
                            if difficulty == "multifactor" else
                            ("unexpected_end", "ended early")]
                    if difficulty == "noisy":
                        evs += [("app_error", "unrelated toast error"),
                                ("heartbeat", "normal beat"),
                                ("heartbeat", "normal beat")]
                elif key == "payment_deducted_no_session":
                    sess = None  # no session row at all: money gone, nothing started
                    pays = [dict(session_id=None, app_id=app, user_ref=user,
                                 status="captured", amount=amt, ts=tss,
                                 reconciliation_status="pending")]
                    case["reported_payment_status"] = "deducted"
                    if difficulty == "trap_nonapplicable":
                        # PlugShare: no payment infra should exist -> no record
                        pays = []
                        case["reported_payment_status"] = "deducted"
                elif key == "double_charge":
                    sess.update(status="completed", energy_kwh=12.0)
                    pays = [pays[0], dict(pays[0], status="captured")]
                    case["reported_payment_status"] = "deducted"
                    case["reported_session_ref"] = sid
                elif key == "refund_pending":
                    sess.update(status="failed", energy_kwh=0.0,
                                error_code="E101")
                    pays = [dict(pays[0], status="refund_pending",
                                 reconciliation_status="pending",
                                 ts=(t0 - timedelta(days=8)).isoformat(sep=" "))]
                    case["reported_payment_status"] = "refund_pending"
                    case["reported_session_ref"] = sid
                elif key == "availability":
                    sess = None
                    stat = [(charger, tss, "offline", "network"),
                            (charger, (t0 - timedelta(minutes=30)).isoformat(sep=" "),
                             "available", "app_display")]
                elif key == "status_stale":
                    sess = None
                    old = (t0 - timedelta(days=2)).isoformat(sep=" ")
                    stat = [(charger, old, "offline", "network"),
                            (charger, old, "available", "app_display")]
                elif key == "app_crash":
                    sess = None
                    evs = []
                    self.events.append(dict(session_id=None,
                                            charger_id=f"app:{app}",
                                            event_ts=tss, event_type="app_crash",
                                            detail="force close loop observed"))
                elif key == "connectivity":
                    sess.update(status="aborted", energy_kwh=2.1,
                                stop_reason="connection_lost")
                    evs += [("started", "ok"), ("heartbeat_lost", "no beats 12 min"),
                            ("charger_offline", "charger unreachable")]
                elif key == "hardware":
                    sess.update(status="failed", energy_kwh=0.0,
                                error_code="HW210", stop_reason="hardware_fault")
                    evs += [("connector_fault", "latch failure, HW210"),
                            ("overtemperature", "gun temp 62C")]
                elif key == "handshake":
                    sess.update(status="failed", energy_kwh=0.0,
                                error_code="HS301", stop_reason="handshake_timeout")
                    evs = [("authorize", "ok"), ("start_request", "sent"),
                           ("handshake_failed", "HS301 protocol negotiation failed")]
                    if secondary == "session_fails_to_start":
                        evs.append(("start_timeout", "E101 after retries"))
                elif key == "history":
                    sess.update(status="completed", energy_kwh=15.0)
                    etype = "history_missing" if v % 2 == 0 else "history_duplicate"
                    evs += [("started", "ok"), ("completed", "ok"),
                            (etype, "backend vs app view differs")]
                    if etype == "history_duplicate":
                        # a second captured payment so history-vs-payment amount
                        # checks have real data to compare against
                        pays.append(dict(pays[0], status="captured"))
                elif key == "support":
                    sess = None
                    evs = []
                    self.events.append(dict(session_id=None,
                                            charger_id=f"app:{app}",
                                            event_ts=tss, event_type="app_error",
                                            detail=f"ticket-escalation-no-response {user}"))
                elif key == "telematics":
                    sess = None
                    self.events.append(dict(session_id=None,
                                            charger_id=f"app:{app}",
                                            event_ts=tss, event_type="soc_not_updating",
                                            detail=f"soc frozen {user}"))
                    self.events.append(dict(session_id=None,
                                            charger_id=f"app:{app}",
                                            event_ts=tss, event_type="bms_error",
                                            detail=f"BMS err 27 {user}"))
                elif key == "ambiguous":
                    # underdetermined: almost nothing in the backend
                    sess = None
                    evs = []
                elif key == "conflict_payment":
                    sess.update(status="completed", energy_kwh=9.0,
                                payment_status="failed")
                    pays = [dict(pays[0], status="failed",
                                 reconciliation_status="disputed")]
                    case["reported_payment_status"] = "deducted"
                    case["reported_session_ref"] = sid

                if sess is not None:
                    self.sessions.append(sess)
                for et, det in evs:
                    self.events.append(dict(session_id=sid if sess else None,
                                            charger_id=charger, event_ts=tss,
                                            event_type=et, detail=det))
                for p in pays:
                    self.payments.append(p)
                for c_, ts_, st_, src in stat:
                    self.status_log.append(dict(charger_id=c_, station_id=station,
                                                ts=ts_, reported_status=st_,
                                                source=src))
                self.cases.append(case)

    # writer ----------------------------------------------------------------
    def write(self, conn) -> Dict[str, int]:
        """Insert all rows idempotently (INSERT OR IGNORE on deterministic PKs).

        Order matters for FK integrity: sessions -> status log -> events ->
        payments -> cases. Events/payments referencing a session that was NOT
        generated (e.g. the 'no session created' payment scenarios) have their
        session_id nulled so the FK constraint stays satisfied while the
        diagnostic signal ('captured payment with no session') is preserved.
        """
        counts = {"sessions": 0, "events": 0, "payments": 0, "status": 0,
                  "cases": 0}
        for s in self.sessions:
            cur = conn.execute(
                "INSERT OR IGNORE INTO charging_sessions(session_id, app_id,"
                " user_ref, station_id, charger_id, connector_id, start_ts, end_ts,"
                " status, energy_kwh, error_code, stop_reason, payment_status,"
                " payment_ref, amount, data_origin, scenario_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'simulated',?)",
                (s["session_id"], s["app_id"], s["user_ref"], s["station_id"],
                 s["charger_id"], s["connector_id"], s["start_ts"], s["end_ts"],
                 s["status"], s["energy_kwh"], s["error_code"], s["stop_reason"],
                 s["payment_status"], s["payment_ref"], s["amount"],
                 s["scenario_id"]))
            counts["sessions"] += cur.rowcount
        existing_sessions = {r["session_id"] for r in conn.execute(
            "SELECT session_id FROM charging_sessions WHERE session_id LIKE 'SSN-%'"
        ).fetchall()}
        for stw in self.status_log:
            conn.execute(
                "INSERT INTO charger_status_log(charger_id, station_id, ts,"
                " reported_status, source, data_origin) SELECT ?,?,?,?,?, 'simulated'"
                " WHERE NOT EXISTS(SELECT 1 FROM charger_status_log"
                " WHERE charger_id=? AND ts=? AND reported_status=? AND source=?)",
                (stw["charger_id"], stw["station_id"], stw["ts"],
                 stw["reported_status"], stw["source"], stw["charger_id"],
                 stw["ts"], stw["reported_status"], stw["source"]))
            counts["status"] += 1
        for e in self.events:
            sid = e["session_id"] if e["session_id"] in existing_sessions else None
            conn.execute(
                "INSERT INTO session_events(session_id, charger_id, event_ts,"
                " event_type, detail, data_origin) SELECT ?,?,?,?,?, 'simulated'"
                " WHERE NOT EXISTS(SELECT 1 FROM session_events WHERE"
                " IFNULL(session_id,'')=? AND IFNULL(charger_id,'')=? AND"
                " event_ts=? AND event_type=?)",
                (sid, e["charger_id"], e["event_ts"], e["event_type"],
                 e["detail"], sid or "", e["charger_id"] or "",
                 e["event_ts"], e["event_type"]))
            counts["events"] += 1
        for p in self.payments:
            sid = p["session_id"] if p["session_id"] in existing_sessions else None
            conn.execute(
                "INSERT INTO payment_records(session_id, app_id, user_ref, status,"
                " amount, ts, reconciliation_status, data_origin)"
                " SELECT ?,?,?,?,?,?,?, 'simulated' WHERE NOT EXISTS(SELECT 1"
                " FROM payment_records WHERE IFNULL(session_id,'')=? AND"
                " user_ref=? AND status=? AND amount=? AND ts=?)",
                (sid, p["app_id"], p["user_ref"], p["status"],
                 p["amount"], p["ts"], p["reconciliation_status"],
                 sid or "", p["user_ref"], p["status"], p["amount"],
                 p["ts"]))
            counts["payments"] += 1
        for c in self.cases:
            cur = conn.execute(
                "INSERT OR IGNORE INTO cases(case_id, app_id,"
                " complaint_text_redacted, complaint_ts, reported_error,"
                " reported_payment_status, reported_session_ref,"
                " reported_station_id, user_ref, data_origin, status,"
                " ground_truth_category, scenario_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'diagnosed', ?, ?)",
                (c["case_id"], c["app_id"], c["complaint_text"],
                 c["complaint_ts"], c["reported_error"],
                 c["reported_payment_status"], c["reported_session_ref"],
                 c["station_id"], c["user_ref"], "simulated",
                 c["ground_truth"], c["scenario_id"]))
            counts["cases"] += cur.rowcount
        conn.commit()
        return counts

    def complaints(self) -> List[Dict[str, Any]]:
        """Complaint payloads for evaluation (ground truth kept separate)."""
        out = []
        for c in self.cases:
            out.append({
                "payload": {
                    "app_id": c["app_id"],
                    "complaint_text": c["complaint_text"],
                    "complaint_ts": c["complaint_ts"],
                    "reported_error": c["reported_error"],
                    "reported_payment_status": c["reported_payment_status"],
                    "session_ref": c["reported_session_ref"],
                    "station_id": c["station_id"],
                    "user_ref": c["user_ref"],
                    "data_origin": DATA_ORIGIN,
                },
                "ground_truth": c["ground_truth"],
                "secondary": c["secondary"],
                "scenario_id": c["scenario_id"],
            })
        return out


def reset_simulated_rows(conn) -> Dict[str, int]:
    """Delete previously seeded simulated rows (for safe re-seeding)."""
    removed = {}
    for table in ("cases", "charging_sessions", "session_events",
                  "payment_records", "charger_status_log"):
        col = "scenario_id" if table in ("charging_sessions", "cases") else None
        if col:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE data_origin='simulated' AND "
                f"{col} LIKE 'SIM-%'")
        else:
            cur = conn.execute(f"DELETE FROM {table} WHERE data_origin='simulated'")
        removed[table] = cur.rowcount
    conn.commit()
    return removed
