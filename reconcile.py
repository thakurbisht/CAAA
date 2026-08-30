"""Run the reconciliation rules against a correlated estate."""

import argparse
import json
from pathlib import Path

from _preflight import require_estate, require_correlation
from reconciliation import EstateView, TruthOracle, run_all, score_all, write_outputs


def main():
    p = argparse.ArgumentParser(description="Phase 3 — reconciliation rules")
    p.add_argument("--estate", default="estate")
    p.add_argument("--snapshot", default=None)
    p.add_argument("--correlation", default="correlation_output/correlation_map.csv")
    p.add_argument("--out", default="reconciliation_output")
    a = p.parse_args()

    estate = Path(a.estate)
    require_estate(estate)
    require_correlation(Path(a.correlation))
    snap = ((estate / "snapshots" / a.snapshot) if a.snapshot
            else sorted((estate / "snapshots").iterdir())[-1])

    sod = json.load(open(estate / "ground_truth" / "sod_rules.json"))
    v = EstateView(snap, Path(a.correlation))
    results = run_all(v, sod)
    oracle = TruthOracle(estate, snap)
    metrics = score_all(results, oracle)

    write_outputs(Path(a.out), results, metrics)

    print(f"\n  Snapshot {snap.name}   {len(v.clusters):,} correlated accounts\n")
    print(f"  {'rule':<34}{'found':>7}{'prec':>8}{'recall':>8}{'cover':>8}{'blind':>7}")
    print("  " + "-" * 72)
    for s in metrics["per_rule"]:
        print(f"  {s['rule_id']:<34}{s['found']:>7}{s['precision']:>8.1%}"
              f"{s['recall']:>8.1%}{s['coverage']:>8.1%}"
              f"{s['invisible_to_platform']:>7}")

    ag = metrics["aggregate"]
    print("  " + "-" * 72)
    print(f"  {'TOTAL':<34}{ag['total_findings']:>7}"
          f"{'':>8}{'':>8}{'':>8}{ag['findings_invisible_to_platform']:>7}")
    print(f"\n  {ag['invisible_share']:.1%} of all findings are unreachable "
          f"from the platform's own data")

    for s in metrics["per_rule"]:
        if s.get("false_positives") or s.get("missed"):
            print(f"\n  {s['rule_id']}")
            if s.get("false_positives"):
                print(f"    false positives ({s['false_positives']}): "
                      f"{', '.join(s['false_positive_examples'][:5])}")
            if s.get("missed"):
                print(f"    missed ({s['missed']}): "
                      f"{', '.join(s['missed_examples'][:5])}")

    print(f"\n  Written to {Path(a.out).resolve()}\n")


if __name__ == "__main__":
    main()
