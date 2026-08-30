"""
Prerequisite checks.

Each stage consumes the previous stage's output. Running them out of order is
the first thing anyone does with an unfamiliar repository, and a raw
FileNotFoundError makes that look like a broken project rather than a missing
step. These checks say what is missing and which command produces it.
"""

import sys
from pathlib import Path


def _die(what: str, path: Path, fix: str):
    sys.exit(
        f"\n  Missing: {what}\n"
        f"    expected at: {path}\n\n"
        f"  Run this first:\n"
        f"    {fix}\n"
    )


def require_estate(estate: Path):
    if not estate.exists():
        _die("the synthetic estate", estate,
             "python3 main.py --population 800 --days 180")
    snaps = estate / "snapshots"
    if not snaps.exists() or not any(snaps.iterdir()):
        _die("estate snapshots", snaps,
             "python3 main.py --population 800 --days 180")
    gt = estate / "ground_truth" / "identity_map.json"
    if not gt.exists():
        _die("the ground-truth identity map", gt,
             "python3 main.py --population 800 --days 180   # regenerate; "
             "this file is what makes scoring possible")


def require_correlation(csv_path: Path):
    if not csv_path.exists():
        _die("the correlation map", csv_path,
             "python3 correlate.py")


def require_snapshots(estate: Path, min_days: int = 60):
    """Drift needs a window, not just two files.

    Two snapshots a week apart satisfy a naive count check and produce a
    drift report that looks complete while measuring almost nothing: with a
    30-day grace period, no transfer is old enough to be judged, and the
    output would read as a clean estate.
    """
    from datetime import date
    snaps = sorted((estate / "snapshots").iterdir())
    if len(snaps) < 2:
        sys.exit(
            f"\n  Drift compares snapshots over time; found {len(snaps)}.\n\n"
            f"  Generate a longer window:\n"
            f"    python3 main.py --days 180\n"
        )
    span = (date.fromisoformat(snaps[-1].name)
            - date.fromisoformat(snaps[0].name)).days
    if span < min_days:
        sys.exit(
            f"\n  The estate spans {span} days across {len(snaps)} snapshots, "
            f"which is too short to measure drift.\n"
            f"  With a 30-day grace period no transfer would be old enough to "
            f"judge, and the report would look clean because nothing was "
            f"assessed rather than because nothing was wrong.\n\n"
            f"  Generate at least {min_days} days:\n"
            f"    python3 main.py --days 180\n"
        )
