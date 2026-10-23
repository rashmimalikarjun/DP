#!/usr/bin/env python3
"""Run the adaptive-vs-baseline evaluation and write reports/.

Usage (from ev_backend/):
    python scripts/run_evaluation.py [--seed 42] [--per-template 10]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.evaluation.evaluation import evaluate, write_reports  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--per-template", type=int, default=10)
    args = ap.parse_args()

    print(f"Running evaluation (seed={args.seed}, "
          f"{args.per_template} variants/template) on SIMULATED data...")
    report = evaluate(seed=args.seed, n_per_template=args.per_template)
    paths = write_reports(report)
    m = report["metrics"]
    print(f"\nDataset size: {report['dataset_size']} simulated labelled cases\n")
    hdr = f"{'metric':28s} {'adaptive_agent':>16s} {'fixed_baseline':>16s}"
    print(hdr)
    print("-" * len(hdr))
    for k in ("accuracy_pct", "macro_f1", "abstention_correct_pct",
              "false_confident_pct", "avg_checks_per_case",
              "avg_unnecessary_checks", "traceability_pct",
              "conflict_detection_pct", "runtime_s"):
        print(f"{k:28s} {str(m['adaptive_agent'].get(k)):>16s} "
              f"{str(m['fixed_baseline'].get(k)):>16s}")
    print("\nReports written:")
    for p in paths:
        print(" -", p)
    print("\nREMINDER: these numbers come from synthetic data authored by the "
          "project developer; they are NOT real-world performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
