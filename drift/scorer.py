"""
Scoring drift rules.

D2 is deliberately not scored on precision. A peer outlier is a statistical
signal, and there is no truth set against which "unusual" can be right or
wrong — a legitimate specialist is a true outlier and a correct finding, and
counting them as errors would measure the wrong thing. What is reported for
D2 instead is its recall against deliberately planted outliers, plus the
volume it produces, since volume is what determines whether the signal is
usable by a human reviewer.
"""

from __future__ import annotations

DETERMINISTIC = {"D1_RETAINED_ON_TRANSFER", "D3_DORMANT_ENTITLEMENT",
                 "D4_STANDING_BREAKGLASS"}

INJECTION_CLASS = {
    "D1_RETAINED_ON_TRANSFER": "RETAINED_ON_TRANSFER",
    "D2_PEER_GROUP_OUTLIER": "PEER_GROUP_OUTLIER",
    "D3_DORMANT_ENTITLEMENT": "DORMANT_ENTITLEMENT",
    "D4_STANDING_BREAKGLASS": "STANDING_BREAKGLASS",
}


def _found(result):
    if result.rule_id == "D3_DORMANT_ENTITLEMENT":
        return {(f.subject, f.evidence["entitlement"]) for f in result.findings}
    return {f.subject for f in result.findings}


def score_all(results, oracle, breakglass, **kw) -> dict:
    truth = {
        "D1_RETAINED_ON_TRANSFER":
            oracle.d1_retained_on_transfer(kw.get("grace_days", 30)),
        "D3_DORMANT_ENTITLEMENT":
            oracle.d3_dormant_entitlement(kw.get("dormant_days", 180)),
        "D4_STANDING_BREAKGLASS":
            oracle.d4_standing_breakglass(breakglass),
    }
    injected = oracle.injected_subset()
    per_rule = []

    for r in results:
        found = _found(r)
        entry = {
            "rule_id": r.rule_id,
            "name": r.name,
            "basis": r.basis,
            # Always the number of findings written, so this column means the
            # same thing for every rule. Scoring keys differ — D3 scores per
            # (account, entitlement) pair and D2 per account — and reporting
            # the scoring key here put two numbers in the same report that
            # should have agreed and did not.
            "found": len(r.findings),
            "scoring_keys": len(found),
            "coverage": round(r.coverage, 4),
            # The ratio alone does not say whether 79.8% is of a thousand
            # accounts or of twelve, and those are different claims.
            "population_examined": r.population_examined,
            "population_total": r.population_total,
            "excluded": r.excluded,
            "notes": r.notes,
        }

        if r.rule_id in DETERMINISTIC:
            expected = truth[r.rule_id]
            tp, fp, fn = found & expected, found - expected, expected - found
            p = len(tp) / len(found) if found else 0.0
            rec = len(tp) / len(expected) if expected else 0.0
            entry.update({
                "scored": True,
                "expected": len(expected),
                "true_positives": len(tp),
                "false_positives": len(fp),
                "missed": len(fn),
                "precision": round(p, 4),
                "recall": round(rec, 4),
                "f1": round(2 * p * rec / (p + rec), 4) if (p + rec) else 0.0,
                "false_positive_examples": [str(x) for x in sorted(fp, key=str)[:8]],
                "missed_examples": [str(x) for x in sorted(fn, key=str)[:8]],
            })
        else:
            entry.update({
                "scored": False,
                "reason": (
                    "statistical signal; 'unusual' has no truth set, and a "
                    "legitimate specialist is a correct outlier"
                ),
                "subjects_flagged": len({f.subject for f in r.findings}),
            })

        cls = INJECTION_CLASS.get(r.rule_id)
        if cls:
            planted = injected.get(cls, set())
            live = oracle.live_injected(
                cls,
                grace_days=(kw.get("grace_days", 30)
                            if r.rule_id == "D1_RETAINED_ON_TRANSFER" else None))
            accounts = ({s for s, *_ in found}
                        if r.rule_id == "D3_DORMANT_ENTITLEMENT" else found)
            entry["injected"] = {
                "planted_total": len(planted),
                "planted_still_live": len(live),
                "caught": len(live & accounts),
                "recall_on_live": (round(len(live & accounts) / len(live), 4)
                                   if live else None),
                "note": (
                    "measured against planted faults the rule claims to have "
                    "assessed: excludes workers since terminated (a leaver "
                    "finding, not drift) and transfers inside the grace period "
                    "(deliberately not yet judged)"
                ),
            }
        per_rule.append(entry)

    det = [e for e in per_rule if e.get("scored")]
    return {
        "per_rule": per_rule,
        "aggregate": {
            "total_findings": sum(len(r.findings) for r in results),
            "deterministic_findings": sum(len(r.findings) for r in results
                                          if r.rule_id in DETERMINISTIC),
            "statistical_findings": sum(len(r.findings) for r in results
                                        if r.rule_id not in DETERMINISTIC),
            "worst_precision": min((e["precision"] for e in det), default=0.0),
            "worst_recall": min((e["recall"] for e in det), default=0.0),
        },
    }


def score_clean_run(results, oracle, breakglass, **kw) -> dict:
    truth = {
        "D1_RETAINED_ON_TRANSFER":
            oracle.d1_retained_on_transfer(kw.get("grace_days", 30)),
        "D3_DORMANT_ENTITLEMENT":
            oracle.d3_dormant_entitlement(kw.get("dormant_days", 180)),
        "D4_STANDING_BREAKGLASS":
            oracle.d4_standing_breakglass(breakglass),
    }
    out, total = {}, 0
    for r in results:
        if r.rule_id not in DETERMINISTIC:
            out[r.rule_id] = {"findings": len(r.findings), "counted": False,
                              "reason": "statistical rule; not a precision test"}
            continue
        found = _found(r)
        fp = found - truth[r.rule_id]
        out[r.rule_id] = {"findings": len(found), "false_positives": len(fp),
                          "examples": [str(x) for x in sorted(fp, key=str)[:8]]}
        total += len(fp)
    return {"per_rule": out, "total_false_positives": total, "clean": total == 0}
