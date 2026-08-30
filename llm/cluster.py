"""
Thematic grouping of findings.

267 findings is more than anyone reads. Grouped into eight themes it becomes a
report somebody acts on, and the grouping is a presentational judgement rather
than a factual claim — which is what makes it safe to delegate.

Safe only under one condition: the grouping must account for every finding
exactly once. A model asked to organise 267 items will produce eight fluent,
plausible themes covering 250 of them, and nothing in the output reveals the
other 17. So the result is checked as a partition and rejected outright if it
is not one.

Rejection falls back to grouping by rule, which is what the findings CSV
already gives you. That is a real loss of readability and it is the right
trade: a summary that is complete and dull beats one that is elegant and
silently missing a critical finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .client import LLMClient
from .validate import validate_clustering, extract_json

SYSTEM = """You group access-control audit findings into themes for a report.

Rules:
- Every finding id you are given must appear in exactly one group.
- Do not invent finding ids. Do not omit any.
- 4 to 8 groups. Name each after the underlying problem, not the rule that
  found it.
- Do not judge severity or recommend action; both are supplied elsewhere.
- Return JSON only:
  [{"name": "...", "rationale": "...", "finding_ids": ["..."]}]"""


@dataclass
class Clustering:
    clusters: list
    source: str            # "model" | "by_rule"
    failures: list
    warnings: list


def by_rule(findings: list[dict]) -> list[dict]:
    """The deterministic fallback: group by the rule that produced each finding."""
    groups: dict[str, list[str]] = {}
    for f in findings:
        groups.setdefault(f["rule_id"], []).append(f["finding_id"])
    return [
        {"name": rule, "rationale": "grouped by originating rule",
         "finding_ids": ids}
        for rule, ids in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    ]


def _digest(findings: list[dict], limit: int = 220) -> str:
    """A compact rendering for the prompt.

    Only the fields a grouping decision needs. Sending full evidence blobs
    would fill the context with detail that cannot change which theme a
    finding belongs to.
    """
    lines = []
    for f in findings[:limit]:
        lines.append(
            f"{f['finding_id']} | {f['rule_id']} | {f['severity']} | "
            f"{f.get('summary', '')[:90]}"
        )
    return "\n".join(lines)


def cluster_findings(client: LLMClient, findings: list[dict]) -> Clustering:
    ids = {f["finding_id"] for f in findings}

    if not client.available:
        return Clustering(by_rule(findings), "by_rule",
                          ["no model backend configured"], [])

    if len(findings) > 220:
        # Beyond this the prompt gets long enough that omissions become
        # frequent, and the partition check simply rejects everything. Sending
        # a request that will predictably fail wastes a call.
        return Clustering(by_rule(findings), "by_rule",
                          [f"{len(findings)} findings exceeds the "
                           f"single-request limit of 220"], [])

    prompt = (
        f"Group these {len(findings)} findings into 4-8 themes.\n"
        f"Every id below must appear in exactly one group.\n\n"
        f"{_digest(findings)}"
    )
    r = client.complete(prompt, system=SYSTEM, max_tokens=4000)
    if not r.ok:
        return Clustering(by_rule(findings), "by_rule",
                          [r.error or "model call failed"], [])

    data = extract_json(r.text)
    if data is None:
        return Clustering(by_rule(findings), "by_rule",
                          ["response was not parseable as JSON"], [])
    if isinstance(data, dict) and "clusters" in data:
        data = data["clusters"]

    v = validate_clustering(data if isinstance(data, list) else [], ids)
    if not v.ok:
        return Clustering(by_rule(findings), "by_rule", v.failures, v.warnings)

    return Clustering(data, "model", [], v.warnings)
