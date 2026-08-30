"""
Scoring reconciliation rules against derived truth.

Two things are measured, and they answer different questions:

  Against derived truth — the true condition set computed from the estate
  with perfect knowledge of who owns which account. This answers "does the
  rule detect the condition it claims to detect", and it is the headline
  number.

  Against the injection log — the faults deliberately planted by the
  generator. This answers only "did we catch the planted ones", and it is
  reported as a subset check. It is not used for precision, because the
  simulation produces genuine instances of every condition on its own and
  counting those as errors would penalise the rule for being right.

The gap between the two is itself informative. Where derived truth is much
larger than the injection log, the estate has generated the condition
naturally — which is what a real estate does, and what makes the rule worth
running continuously rather than once.
"""

from __future__ import annotations

import json
from pathlib import Path

INJECTION_CLASS = {
    "R1_LEAVER_NOT_DEPROVISIONED": "MISSED_LEAVER",
    "R2_PLATFORM_STATE_DISAGREEMENT": "STALE_CONNECTOR",
    "R3_ORPHANED_NHI": "ORPHANED_NHI",
    "R4_ORPHANED_ADMIN_ACCOUNT": "ORPHANED_ADMIN_ACCOUNT",
    "R5_SOD_VIOLATION": "SOD_VIOLATION",
}


def _found_set(result):
    """Findings as comparable keys. SoD is scored per (account, rule) pair."""
    if result.rule_id == "R5_SOD_VIOLATION":
        return {(f.subject, f.evidence["sod_rule_id"]) for f in result.findings}
    return {f.subject for f in result.findings}


def score_rule(result, truth: dict, injected: dict[str, set[str]]) -> dict:
    expected = truth.get(result.rule_id, set())
    found = _found_set(result)

    tp, fp, fn = found & expected, found - expected, expected - found
    precision = len(tp) / len(found) if found else 0.0
    recall = len(tp) / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)

    out = {
        "rule_id": result.rule_id,
        "expected": len(expected),
        "found": len(found),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "missed": len(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "coverage": round(result.coverage, 4),
        "population_examined": result.population_examined,
        "population_total": result.population_total,
        "invisible_to_platform": result.invisible_to_platform,
        "false_positive_examples": [str(x) for x in sorted(fp, key=str)[:8]],
        "missed_examples": [str(x) for x in sorted(fn, key=str)[:8]],
        "excluded": result.excluded,
        "notes": result.notes,
    }

    # Secondary: did we catch everything deliberately planted?
    cls = INJECTION_CLASS.get(result.rule_id)
    if cls:
        planted = injected.get(cls, set())
        found_accounts = ({s for s, _ in found} if result.rule_id == "R5_SOD_VIOLATION"
                          else found)
        caught = planted & found_accounts
        out["injected"] = {
            "planted": len(planted),
            "caught": len(caught),
            "recall_on_planted": (round(len(caught) / len(planted), 4)
                                  if planted else None),
            "missed_examples": sorted(planted - found_accounts)[:8],
        }
    return out


def score_all(results, oracle) -> dict:
    truth = oracle.all_truth()
    injected = oracle.injected_subset()
    per_rule = [score_rule(r, truth, injected) for r in results]

    total_findings = sum(len(r.findings) for r in results)
    total_invisible = sum(r.invisible_to_platform for r in results)

    return {
        "per_rule": per_rule,
        "aggregate": {
            "rules_run": len(results),
            "total_findings": total_findings,
            "findings_invisible_to_platform": total_invisible,
            "invisible_share": (round(total_invisible / total_findings, 4)
                                if total_findings else 0.0),
            "worst_precision": min((s["precision"] for s in per_rule), default=0.0),
            "worst_recall": min((s["recall"] for s in per_rule), default=0.0),
        },
    }


def score_clean_run(results, oracle) -> dict:
    """False-positive baseline against an estate with no injected faults.

    The naive version of this test asserts that a clean estate produces zero
    findings. That test is wrong, and running it was instructive: the clean
    estate still returned 114 findings, every sampled one of which was real.
    Disabling fault injection does not stop the simulation from terminating
    staff over 180 days, and their device and interface accounts are left
    behind exactly as they would be in production.

    The correct baseline compares the rules against the oracle on the clean
    estate. A false positive is a finding the oracle does not confirm — not
    merely a finding that exists.
    """
    truth = oracle.all_truth()
    out, total = {}, 0

    for r in results:
        expected = truth.get(r.rule_id, set())
        found = _found_set(r)
        fp = found - expected
        out[r.rule_id] = {
            "findings": len(found),
            "confirmed_by_oracle": len(found & expected),
            "false_positives": len(fp),
            "examples": [str(x) for x in sorted(fp, key=str)[:8]],
        }
        total += len(fp)

    return {"per_rule": out, "total_false_positives": total, "clean": total == 0}
