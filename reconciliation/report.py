"""Output writers for reconciliation findings."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .rules import SEVERITY_ORDER


def write_outputs(out_dir: Path, results, metrics: dict):
    out_dir.mkdir(parents=True, exist_ok=True)

    all_findings = [f for r in results for f in r.findings]
    all_findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9),
                                     f.rule_id, f.subject))

    with open(out_dir / "findings.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rule_id", "severity", "subject", "cluster_id",
                    "person_number", "summary", "visible_in_platform_data",
                    "evidence"])
        for f in all_findings:
            w.writerow(f.row())

    # The subset an independent control adds over the platform's own checks.
    with open(out_dir / "platform_blind_findings.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rule_id", "severity", "subject", "cluster_id",
                    "person_number", "summary", "evidence"])
        for f in all_findings:
            if f.visible_in_iga_data is False:
                r = f.row()
                w.writerow(r[:6] + [r[7]])

    with open(out_dir / "coverage_statement.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rule_id", "name", "population_examined", "population_total",
                    "coverage_pct", "findings", "invisible_to_platform",
                    "platform_comparison_unavailable", "excluded", "notes"])
        for r in results:
            w.writerow([r.rule_id, r.name, r.population_examined,
                        r.population_total, f"{r.coverage:.1%}",
                        len(r.findings), r.invisible_to_platform,
                        r.platform_comparison_unavailable,
                        json.dumps(r.excluded), " | ".join(r.notes)])

    with open(out_dir / "rule_metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)
