"""
Acceptance threshold calibration.

A detection control that has not been calibrated is a control whose error
profile nobody knows. This sweeps the acceptance threshold and reports what
each setting costs, so the chosen value is a documented decision rather than
a default.

The asymmetry that drives the choice: a missed link is a visible gap that
lands in the adjudication queue and gets worked. A false link is an invisible
error that silently attributes privilege to the wrong person and corrupts
every finding built on top of it. The threshold should therefore be set where
false links reach zero, not where F1 peaks.
"""

import argparse
import json
from pathlib import Path

from _preflight import require_estate
from correlation import CorrelationEngine, load_snapshot, latest_snapshot, score


def main():
    p = argparse.ArgumentParser(description="Sweep the acceptance threshold")
    p.add_argument("--estate", default="estate")
    p.add_argument("--snapshot", default=None)
    p.add_argument("--start", type=float, default=0.55)
    p.add_argument("--stop", type=float, default=0.99)
    p.add_argument("--step", type=float, default=0.02)
    p.add_argument("--out", default="correlation_output/calibration.json")
    a = p.parse_args()

    estate = Path(a.estate)
    require_estate(estate)
    snap = (estate / "snapshots" / a.snapshot) if a.snapshot else latest_snapshot(estate)
    feeds = load_snapshot(snap)
    imap = json.load(open(estate / "ground_truth" / "identity_map.json"))

    print(f"\n  Snapshot {snap.name}   sweeping acceptance threshold\n")
    print(f"  {'accept':>8} {'precision':>10} {'recall':>9} {'F1':>8} "
          f"{'false':>7} {'missed':>7} {'queue':>7}")
    print("  " + "-" * 62)

    rows = []
    t = a.start
    while t <= a.stop + 1e-9:
        eng = CorrelationEngine(feeds["hcm"], feeds["ad"], feeds["entra"],
                                feeds["iga"], accept=t, review=min(t - 0.10, 0.55))
        clusters = eng.correlate()
        m = score(clusters, imap)
        ls = m["link_scoring"]
        queue = sum(v for k, v in m["cluster_status"].items()
                    if k in ("REVIEW", "UNRESOLVED"))

        rows.append({
            "accept_threshold": round(t, 3),
            "precision": ls["precision"],
            "recall": ls["recall"],
            "f1": ls["f1"],
            "false_links": ls["false_links"],
            "missed_links": ls["missed_links"],
            "adjudication_queue": queue,
        })

        flag = "  <-- first zero-false-link point" if (
            ls["false_links"] == 0 and
            all(r["false_links"] > 0 for r in rows[:-1])
        ) else ""

        print(f"  {t:>8.2f} {ls['precision']:>9.1%} {ls['recall']:>8.1%} "
              f"{ls['f1']:>7.1%} {ls['false_links']:>7} "
              f"{ls['missed_links']:>7} {queue:>7}{flag}")
        t += a.step

    zero_fp = [r for r in rows if r["false_links"] == 0]
    best_f1 = max(rows, key=lambda r: r["f1"])
    recommended = min(zero_fp, key=lambda r: r["missed_links"]) if zero_fp else best_f1

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({
            "snapshot": snap.name,
            "sweep": rows,
            "peak_f1": best_f1,
            "recommended": recommended,
            "basis": (
                "Lowest threshold at which false links reach zero. A missed "
                "link is queued and worked; a false link silently attributes "
                "privilege to the wrong person."
            ),
        }, f, indent=2)

    print("\n  Peak F1        "
          f"accept={best_f1['accept_threshold']:.2f}  F1={best_f1['f1']:.1%}  "
          f"false={best_f1['false_links']}")
    print("  Recommended    "
          f"accept={recommended['accept_threshold']:.2f}  "
          f"precision={recommended['precision']:.1%}  "
          f"recall={recommended['recall']:.1%}  "
          f"queue={recommended['adjudication_queue']}")
    print(f"\n  Written to {out.resolve()}\n")


if __name__ == "__main__":
    main()
