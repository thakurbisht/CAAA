"""
Certification quality.

The question this answers is not "what did we find" but "how much of the
estate did we actually look at". A certification that examined 62% of accounts
and found nothing is not a clean result, and an unqualified summary reads
exactly like one.

The score is computed arithmetically from the coverage statements phases 3 and
4 already emit. The model is given the finished numbers and asked only to
write them up. It never computes, weights or decides anything — a quality
grade produced by a language model is an opinion wearing a metric's clothes,
and it would be the one number in this project that nobody could re-derive.

Every figure in the narrative is then checked against the set the pipeline
produced, so a fluent summary cannot smuggle in a number nobody measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .client import LLMClient
from .validate import validate_narrative

SYSTEM = """You write the summary paragraph of an access certification report \
for a hospital's audit committee.

Rules:
- Use only the figures given to you. Do not calculate, round, or estimate.
- Do not introduce any number that is not in the input.
- State what was not examined as plainly as what was.
- Do not recommend actions or assign blame.
- 3 to 5 sentences of plain prose. No headings, no bullets."""


@dataclass
class QualityAssessment:
    grade: str
    weighted_coverage: float
    per_rule: list
    blind_spots: list
    narrative: str
    narrative_source: str      # "model" | "computed" | "rejected"
    notes: list


# Coverage bands. These are a policy choice and are stated as one rather than
# presented as a discovered threshold.
BANDS = [
    (0.95, "COMPREHENSIVE", "nearly the whole estate was examined"),
    (0.80, "ADEQUATE", "most of the estate was examined, with stated gaps"),
    (0.60, "PARTIAL", "substantial parts of the estate were not examined"),
    (0.00, "LIMITED", "the majority of the estate was not examined"),
]


def grade_for(coverage: float) -> tuple[str, str]:
    for floor, grade, meaning in BANDS:
        if coverage >= floor:
            return grade, meaning
    return "LIMITED", BANDS[-1][2]


def assess(recon_metrics: dict, drift_metrics: dict,
           client: LLMClient | None = None) -> QualityAssessment:
    per_rule, blind = [], []

    for s in recon_metrics.get("per_rule", []):
        per_rule.append({
            "rule_id": s["rule_id"],
            "coverage": s["coverage"],
            "findings": s["found"],
            "excluded": s.get("excluded", {}),
        })
        for reason, n in (s.get("excluded") or {}).items():
            if n:
                blind.append({"rule_id": s["rule_id"], "reason": reason,
                              "population": n})

    for s in drift_metrics.get("per_rule", []):
        per_rule.append({
            "rule_id": s["rule_id"],
            "coverage": s["coverage"],
            "findings": s["found"],
            "excluded": s.get("excluded", {}),
        })
        for reason, n in (s.get("excluded") or {}).items():
            if n:
                blind.append({"rule_id": s["rule_id"], "reason": reason,
                              "population": n})

    # Unweighted mean across rules. Weighting by population size was
    # considered and rejected: it lets one rule with near-total coverage
    # conceal another that examined almost nothing, which is the specific
    # failure this measure exists to expose.
    # Rounded once, here, and used everywhere downstream. Formatting the raw
    # value in one place and the rounded value in another produced a report
    # that gave two different coverage figures three lines apart.
    coverage = round(sum(r["coverage"] for r in per_rule) / len(per_rule), 4) \
        if per_rule else 0.0
    grade, meaning = grade_for(coverage)

    weakest = min(per_rule, key=lambda r: r["coverage"]) if per_rule else None
    total_findings = sum(r["findings"] for r in per_rule)

    computed = (
        f"This certification is graded {grade}: {meaning}. "
        f"Across {len(per_rule)} rules, mean coverage was "
        f"{coverage:.1%} and {total_findings} findings were raised. "
        + (f"The least complete check was {weakest['rule_id']} at "
           f"{weakest['coverage']:.1%}. " if weakest else "")
        + (f"{len(blind)} populations were excluded from at least one rule "
           f"and are recorded as unexamined rather than clean."
           if blind else "No populations were excluded.")
    )

    narrative, source, notes = computed, "computed", []

    if client and client.available:
        permitted = _permitted_figures(coverage, per_rule, total_findings, blind)
        prompt = (
            f"Grade: {grade}\n"
            f"Mean coverage across rules: {coverage:.1%}\n"
            f"Rules run: {len(per_rule)}\n"
            f"Total findings: {total_findings}\n\n"
            f"Per rule:\n" +
            "\n".join(f"  {r['rule_id']}: coverage {r['coverage']:.1%}, "
                      f"{r['findings']} findings" for r in per_rule) +
            f"\n\nExcluded populations:\n" +
            ("\n".join(f"  {b['rule_id']}: {b['population']} accounts "
                       f"({b['reason']})" for b in blind) or "  none") +
            "\n\nWrite the summary paragraph."
        )
        r = client.complete(prompt, system=SYSTEM, max_tokens=600)
        if r.ok and r.text.strip():
            v = validate_narrative(r.text.strip(), permitted)
            if v.ok:
                narrative, source = r.text.strip(), "model"
                notes = v.warnings
            else:
                source, notes = "rejected", v.failures
        else:
            notes = [r.error or "model call failed"]

    return QualityAssessment(grade, coverage, per_rule, blind,
                             narrative, source, notes)


def _permitted_figures(coverage, per_rule, total_findings, blind) -> set:
    """Numbers the narrative is allowed to contain."""
    out = {str(total_findings), str(len(per_rule)), str(len(blind))}
    for pct in [coverage] + [r["coverage"] for r in per_rule]:
        out.add(f"{pct:.1%}")
        out.add(f"{pct * 100:.1f}")
        out.add(str(round(pct * 100)))
        out.add(f"{round(pct * 100)}%")
    for r in per_rule:
        out.add(str(r["findings"]))
    for b in blind:
        out.add(str(b["population"]))
    return out
