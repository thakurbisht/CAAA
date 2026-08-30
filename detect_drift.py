"""Run drift detection across the snapshot history."""

import argparse
import csv
import json
from pathlib import Path

from _preflight import require_estate, require_correlation, require_snapshots
from drift import (EstateTimeline, DriftOracle, run_all, score_all,
                   DETERMINISTIC, EVIDENCE_BASIS)


def write_outputs(out: Path, results, metrics):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "drift_findings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "basis", "severity", "subject", "cluster_id",
                    "person_number", "summary", "evidence"])
        for r in results:
            for fi in r.findings:
                w.writerow(fi.row())

    with open(out / "evidence_basis.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "name", "basis", "what_it_establishes",
                    "findings", "coverage_pct", "excluded", "notes"])
        for r in results:
            w.writerow([r.rule_id, r.name, r.basis, EVIDENCE_BASIS[r.basis],
                        len(r.findings), f"{r.coverage:.1%}",
                        json.dumps(r.excluded), " | ".join(r.notes)])

    with open(out / "drift_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)


def main():
    p = argparse.ArgumentParser(description="Phase 4 — drift detection")
    p.add_argument("--estate", default="estate")
    p.add_argument("--correlation", default="correlation_output/correlation_map.csv")
    p.add_argument("--out", default="drift_output")
    p.add_argument("--grace-days", type=int, default=30)
    p.add_argument("--dormant-days", type=int, default=180)
    p.add_argument("--min-peers", type=int, default=8)
    p.add_argument("--max-prevalence", type=float, default=0.05)
    a = p.parse_args()

    estate = Path(a.estate)
    require_estate(estate)
    require_correlation(Path(a.correlation))
    require_snapshots(estate)
    t = EstateTimeline(estate, Path(a.correlation))
    cat = json.load(open(estate / "ground_truth" / "entitlement_catalogue.json"))
    breakglass = {k for k in cat if "BREAKGLASS" in k.upper()}

    kw = dict(grace_days=a.grace_days, dormant_days=a.dormant_days,
              min_peers=a.min_peers, max_prevalence=a.max_prevalence)
    results = run_all(t, breakglass, **kw)
    metrics = score_all(results, DriftOracle(estate, t), breakglass, **kw)
    write_outputs(Path(a.out), results, metrics)

    span = (t.dates[-1] - t.dates[0]).days
    print(f"\n  {len(t.snapshots)} snapshots over {span} days "
          f"({t.dates[0]} to {t.dates[-1]})")
    print(f"  {len(t.observed_transfers())} transfers observed in the HR feed\n")

    print(f"  {'rule':<28}{'basis':<20}{'found':>7}{'prec':>8}{'recall':>8}{'cover':>8}")
    print("  " + "-" * 80)
    for s in metrics["per_rule"]:
        if s.get("scored"):
            print(f"  {s['rule_id']:<28}{s['basis']:<20}{s['found']:>7}"
                  f"{s['precision']:>8.1%}{s['recall']:>8.1%}{s['coverage']:>8.1%}")
        else:
            print(f"  {s['rule_id']:<28}{s['basis']:<20}{s['found']:>7}"
                  f"{'n/a':>8}{'n/a':>8}{s['coverage']:>8.1%}")

    ag = metrics["aggregate"]
    print("  " + "-" * 80)
    print(f"  deterministic findings (evidence)   {ag['deterministic_findings']:>6}")
    print(f"  statistical findings (signal only)  {ag['statistical_findings']:>6}")

    for s in metrics["per_rule"]:
        if s.get("false_positives") or s.get("missed"):
            print(f"\n  {s['rule_id']}")
            if s.get("false_positives"):
                print(f"    false positives ({s['false_positives']}): "
                      f"{', '.join(s['false_positive_examples'][:4])}")
            if s.get("missed"):
                print(f"    missed ({s['missed']}): "
                      f"{', '.join(s['missed_examples'][:4])}")

    print(f"\n  Written to {Path(a.out).resolve()}\n")


if __name__ == "__main__":
    main()
