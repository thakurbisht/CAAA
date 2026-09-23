#!/usr/bin/env python3
"""
Run the whole pipeline end to end.

    python3 run_all.py

Each stage depends on the one before it, and running them out of order is the
most common way to get a confusing error. This checks prerequisites before
each stage and says what to do rather than failing on a missing file.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

STAGES = [
    ("Phase 1", "Generate synthetic estate", "main.py", []),
    ("Phase 2", "Correlate identities across systems", "correlate.py", []),
    ("Phase 3", "Run reconciliation rules", "reconcile.py", []),
    ("Phase 4", "Detect drift across snapshots", "detect_drift.py", []),
    # Phase 6 runs with --backend auto, which resolves to no model unless one
    # is configured. That is a supported outcome rather than a failure: the
    # deterministic fallbacks produce the full output either way.
    ("Phase 6", "Interpret findings (optional model layer)", "interpret.py", []),
    ("Report", "Build the HTML report", "build_report.py", []),
]


def check_python():
    if sys.version_info < (3, 10):
        sys.exit(
            f"Python 3.10 or newer is required (found {sys.version.split()[0]}).\n"
            f"The code uses match-free but modern typing syntax such as `str | None`."
        )


def run(script: str, args: list[str], root: Path) -> bool:
    print(f"\n{'=' * 74}")
    cmd = [sys.executable, script] + args
    print(f"  $ {' '.join(cmd)}")
    print("=" * 74)
    r = subprocess.run(cmd, cwd=root)
    return r.returncode == 0


def main():
    p = argparse.ArgumentParser(description="Run the full CAAA pipeline")
    p.add_argument("--population", type=int, default=800)
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--seed", type=int, default=20260824)
    p.add_argument("--fresh", action="store_true",
                   help="delete previously generated output first")
    p.add_argument("--skip-tests", action="store_true")
    a = p.parse_args()

    check_python()
    root = Path(__file__).parent.resolve()

    if a.fresh:
        for d in ("estate", "correlation_output", "reconciliation_output",
                  "drift_output"):
            shutil.rmtree(root / d, ignore_errors=True)
        print("  Removed previous output.")

    gen_args = ["--population", str(a.population),
                "--days", str(a.days), "--seed", str(a.seed)]

    for label, desc, script, extra in STAGES:
        args = gen_args if script == "main.py" else extra
        print(f"\n\n### {label}: {desc}")
        if not run(script, args, root):
            sys.exit(
                f"\n{label} failed. The stages depend on each other in order, "
                f"so fix this before continuing."
            )

    if not a.skip_tests:
        print("\n\n### Test suite")
        try:
            import pytest  # noqa: F401
        except ImportError:
            print("  pytest is not installed; skipping.")
            print("  Install it with:  pip install pytest")
        else:
            subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=root)

    print(f"""

{'=' * 74}
  Done. Output written to:

    estate/                    synthetic estate, 7 feeds per snapshot
    estate/ground_truth/       the answer key that makes scoring possible
    correlation_output/        cluster map, adjudication queue, calibration
    reconciliation_output/     findings, coverage statement, per-rule metrics
    drift_output/              drift findings graded by evidentiary basis
    interpretation_output/     translations, themes, certification quality
    report/report.html         one self-contained page; open it in a browser

  The two worth reading first:

    reconciliation_output/coverage_statement.csv
        what each rule could not examine, and why

    drift_output/evidence_basis.csv
        how much argument each class of finding will take to defend
{'=' * 74}
""")


if __name__ == "__main__":
    main()
