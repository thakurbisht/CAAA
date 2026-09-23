"""Run the reconciliation rules against a correlated estate."""

import argparse
import json
from pathlib import Path

from _preflight import require_estate, require_correlation, has_ground_truth
from feeds import Profile
from reconciliation import EstateView, TruthOracle, run_all, score_all, write_outputs


SOD_SEARCH = ["config/sod_rules.json", "ground_truth/sod_rules.json"]


def _load_sod(estate: Path, explicit: str | None) -> list:
    if explicit:
        return json.load(open(explicit))
    for rel in SOD_SEARCH:
        p = estate / rel
        if p.exists():
            return json.load(open(p))
    print("  No segregation-of-duties policy found; R5 will report nothing.\n"
          "  Supply one with --sod-rules, or place it at config/sod_rules.json")
    return []


def _unscored(results) -> dict:
    """Metrics for an estate with no answer key.

    Everything except precision and recall survives: the findings, the
    population each rule examined, what it excluded and why. Reporting a
    precision figure here would mean inventing one.
    """
    return {
        "scored": False,
        "reason": ("no ground truth for this estate; precision and recall "
                   "cannot be measured, only coverage"),
        "per_rule": [{
            "rule_id": r.rule_id,
            "name": r.name,
            "found": len(r.findings),
            "coverage": round(r.coverage, 4),
            "population_examined": r.population_examined,
            "population_total": r.population_total,
            "invisible_to_platform": r.invisible_to_platform,
            "platform_comparison_unavailable":
                r.platform_comparison_unavailable,
            "excluded": r.excluded,
            "notes": r.notes,
        } for r in results],
        "aggregate": {
            "rules_run": len(results),
            "total_findings": sum(len(r.findings) for r in results),
            "findings_invisible_to_platform":
                sum(r.invisible_to_platform for r in results),
            "invisible_share": (
                round(sum(r.invisible_to_platform for r in results)
                      / max(1, sum(len(r.findings) for r in results)), 4)),
        },
    }


def main():
    p = argparse.ArgumentParser(description="Phase 3 — reconciliation rules")
    p.add_argument("--estate", default="estate")
    p.add_argument("--snapshot", default=None)
    p.add_argument("--correlation", default="correlation_output/correlation_map.csv")
    p.add_argument("--out", default="reconciliation_output")
    p.add_argument("--profile", default=None,
                   help="column mapping for a non-standard extract")
    p.add_argument("--sod-rules", default=None,
                   help="segregation-of-duties policy; defaults to the "
                        "estate's own rules if present")
    a = p.parse_args()

    estate = Path(a.estate)
    require_estate(estate)
    require_correlation(Path(a.correlation))
    snap = ((estate / "snapshots" / a.snapshot) if a.snapshot
            else sorted((estate / "snapshots").iterdir())[-1])

    profile = Profile.load(Path(a.profile)) if a.profile else None

    # Segregation rules are policy, not ground truth. They were filed under
    # ground_truth/ because the generator happened to write them there, which
    # implied an organisation's own rules were part of an answer key.
    sod = _load_sod(estate, a.sod_rules)

    v = EstateView(snap, Path(a.correlation), profile=profile)
    results = run_all(v, sod)

    # Scoring requires an answer key, which only a generated estate has. On
    # real extracts the rules still run; what changes is that precision cannot
    # be reported, and coverage carries the weight instead.
    scored = has_ground_truth(estate)
    metrics = (score_all(results, TruthOracle(estate, snap)) if scored
               else _unscored(results))

    write_outputs(Path(a.out), results, metrics)

    print(f"\n  Snapshot {snap.name}   {len(v.clusters):,} correlated accounts")
    if not scored:
        print("  No answer key for this estate: coverage is reported, "
              "precision is not.")
    print()

    if scored:
        print(f"  {'rule':<34}{'found':>7}{'prec':>8}{'recall':>8}"
              f"{'cover':>8}{'blind':>7}")
    else:
        print(f"  {'rule':<34}{'found':>7}{'examined':>10}"
              f"{'cover':>8}{'blind':>7}")
    print("  " + "-" * 72)
    for s in metrics["per_rule"]:
        if scored:
            print(f"  {s['rule_id']:<34}{s['found']:>7}{s['precision']:>8.1%}"
                  f"{s['recall']:>8.1%}{s['coverage']:>8.1%}"
                  f"{s['invisible_to_platform']:>7}")
        else:
            print(f"  {s['rule_id']:<34}{s['found']:>7}"
                  f"{s['population_examined']:>10,}{s['coverage']:>8.1%}"
                  f"{s['invisible_to_platform']:>7}")

    ag = metrics["aggregate"]
    print("  " + "-" * 72)
    pad = 24 if scored else 18
    print(f"  {'TOTAL':<34}{ag['total_findings']:>7}"
          f"{'':>{pad}}{ag['findings_invisible_to_platform']:>7}")
    unavailable = sum(r.platform_comparison_unavailable for r in results)
    if unavailable:
        print(f"\n  No platform entitlement feed was supplied, so none of the "
              f"{unavailable} findings\n  can be described as reachable or "
              f"unreachable from the platform's records.")
    else:
        print(f"\n  {ag['invisible_share']:.1%} of all findings are unreachable "
              f"from the platform's own data")

    for s in metrics["per_rule"] if scored else []:
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
