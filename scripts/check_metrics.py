#!/usr/bin/env python3
"""
Verify the numbers the README claims.

Written because a README is a promise about behaviour and nothing normally
checks it. Every figure quoted as a result is asserted here, so a change that
quietly degrades detection fails the build rather than silently making the
documentation false.

Thresholds are set slightly below the observed values: the estate is
randomised by population size, so exact equality would be brittle, while a
floor still catches a real regression.
"""

import json
import sys
from pathlib import Path

FAILURES = []


def check(name, actual, op, expected):
    ok = {">=": actual >= expected, "==": actual == expected,
          "<=": actual <= expected}[op]
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark}  {name:<52}{actual!s:>8}  {op} {expected}")
    if not ok:
        FAILURES.append(f"{name}: {actual} not {op} {expected}")


def main():
    root = Path(__file__).parent.parent

    print("\nPhase 2 — correlation")
    m = json.load(open(root / "correlation_output" / "metrics.json"))
    ls = m["link_scoring"]
    check("no false links", ls["false_links"], "==", 0)
    check("precision", ls["precision"], ">=", 1.0)
    check("recall", ls["recall"], ">=", 0.90)
    check("account typing accuracy", m["account_typing"]["accuracy"], ">=", 1.0)
    check("no NHI linked to a human",
          m["nhi_handling"]["wrongly_linked_to_a_human"], "==", 0)

    print("\nPhase 3 — reconciliation")
    m = json.load(open(root / "reconciliation_output" / "rule_metrics.json"))
    for s in m["per_rule"]:
        check(f"{s['rule_id']} precision", s["precision"], ">=", 1.0)
    check("share of findings invisible to the platform",
          m["aggregate"]["invisible_share"], ">=", 0.10)

    print("\nPhase 4 — drift")
    m = json.load(open(root / "drift_output" / "drift_metrics.json"))
    for s in m["per_rule"]:
        if s.get("scored"):
            check(f"{s['rule_id']} precision", s["precision"], ">=", 1.0)
        else:
            check(f"{s['rule_id']} not scored on precision",
                  s["scored"], "==", False)
    d1 = next(s for s in m["per_rule"] if s["rule_id"] == "D1_RETAINED_ON_TRANSFER")
    check("D1 recall on assessable planted faults",
          d1["injected"]["recall_on_live"], ">=", 0.85)

    if FAILURES:
        print(f"\n{len(FAILURES)} claim(s) in the README no longer hold:\n")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\nAll documented metrics hold.\n")


if __name__ == "__main__":
    main()
