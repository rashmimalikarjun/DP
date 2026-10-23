# Evaluation Report — SIMULATED DATA ONLY (NOT real-world performance)

Generated: 2026-10-23T18:01:19  
Dataset size: **200 labelled synthetic cases** (seed=42, 8 variants/template)  
Data origin: **simulated** — fabricated by design.

## Procedure
Both methods ran on the identical labelled synthetic case set inside a fresh temporary SQLite DB seeded only with the static rule set (no learned weights, no cross-run leakage). Ground truth was withheld from agent inputs.

## Metric definitions
- accuracy: primary category equals ground truth; for true-'inconclusive' cases, a correct abstention (no asserted category) counts as correct.
- macro_f1: mean per-category F1 over the 12 problem categories.
- abstention_correct_pct: truly-inconclusive cases returned as inconclusive/requires_further_investigation.
- false_confident_pct: truly-inconclusive cases answered confirmed/probable.
- avg_checks_per_case: executed diagnostic tools per case (not_applicable skips excluded).
- avg_unnecessary_checks: checks run after the evidence prefix already supported the final classification (see evaluation.py docstring).
- traceability_pct: diagnoses whose supporting-evidence ids all resolve to stored evidence items.
- conflict_detection_pct: planted-conflict cases where conflicting evidence was recorded.

## Results (computed from this run — nothing hardcoded)

| Metric | Adaptive agent | Fixed baseline |
|---|---|---|
| n_cases | 200 | 200 |
| accuracy_pct | 62.5 | 44.5 |
| macro_f1 | 0.6565 | 0.4041 |
| abstention_correct_pct | 25.0 | 6.25 |
| false_confident_pct | 43.75 | 43.75 |
| avg_checks_per_case | 4.6 | 7.71 |
| avg_unnecessary_checks | 0.14 | 1.2 |
| traceability_pct | 100.0 | 100.0 |
| conflict_detection_pct | None | None |
| runtime_s | 0.474 | 0.325 |

### Per-category accuracy (%)

| Category | Adaptive | Baseline |
|---|---|---|
| app_crash_or_technical_error | 100.0 | 62.5 |
| charger_availability_mismatch | 0.0 | 0.0 |
| connector_or_charger_hardware_fault | 100.0 | 100.0 |
| customer_support_issue | 100.0 | 0.0 |
| inconclusive | 31.2 | 31.2 |
| incorrect_or_delayed_station_status | 62.5 | 62.5 |
| incorrect_or_incomplete_charging_history | 100.0 | 12.5 |
| network_or_connectivity_issue | 100.0 | 100.0 |
| payment_or_billing_failure | 75.0 | 0.0 |
| session_fails_to_start | 100.0 | 100.0 |
| session_stops_unexpectedly | 33.3 | 83.3 |
| vehicle_charger_communication_issue | 100.0 | 100.0 |
| vehicle_specific_or_telematics_issue | 0.0 | 0.0 |

The run above shows the adaptive agent ahead on these synthetic cases.

## LIMITATIONS

- Dataset is synthetic data authored by the same developer as the rules; results are optimistic and NOT real-world performance.
- Small scale (~hundreds of cases); percentages carry wide uncertainty.
- Generator templates and diagnosis rules are correlated by design (signals were defined together).
- No real charging-network logs were used anywhere.
- PII redaction is best-effort, not a guarantee.
