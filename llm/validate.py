"""
Validation of model output.

Everything phases 1 to 4 produce can be re-derived by hand from the feeds.
This layer cannot: a paraphrase has no derivation, and asking the model the
same question twice is not verification. So the model is confined to
operations whose output can be checked mechanically against the deterministic
input that produced it, and anything that fails the check is discarded.

Discarded, not repaired. A clustering that has quietly dropped eleven findings
can be patched by appending them to a leftover group, and the result looks
complete while nobody knows which of the remaining groups are trustworthy.
Falling back to a deterministic grouping loses some readability and keeps the
property that matters, which is that the set of findings is intact.

The checks are deliberately blunt:

  Coverage — does the output account for every input, exactly once, and
  introduce nothing that was not in the input?
  Consistency — does the text contradict a fact already established, such as
  an entitlement's application or risk rating?
  Containment — does the output stay inside its remit, or has it started
  asserting findings of its own?
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str):
        self.ok = False
        self.failures.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


def extract_json(text: str):
    """Pull a JSON document out of a model response.

    Models wrap JSON in prose and fences with some regularity even when told
    not to. Recovering from that is reasonable; guessing at malformed JSON is
    not, so anything that will not parse is a failure rather than something to
    repair heuristically.
    """
    if not text or not text.strip():
        return None

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text

    try:
        return json.loads(candidate.strip())
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


# ==========================================================================
# Translation
# ==========================================================================

RISK_WORDS = {
    "CRITICAL": {"critical", "severe"},
    "HIGH": {"high"},
    "MEDIUM": {"medium", "moderate"},
    "LOW": {"low", "minor", "minimal"},
}

KNOWN_APPS = {"AD", "ENTRA", "CERNER", "ORACLE", "PACS", "LIS", "PYXIS"}


def validate_translation(code: str, meta: dict, text: str) -> ValidationResult:
    """Check a plain-language rendering of an entitlement.

    The failure that matters is not clumsy phrasing. It is a description that
    misstates what the access does, because a reviewer approving access on the
    strength of that sentence never sees the underlying code.
    """
    v = ValidationResult(True)

    if not text or not text.strip():
        v.fail("empty translation")
        return v

    words = text.split()
    if len(words) > 60:
        v.fail(f"translation is {len(words)} words; a reviewer will not read it")
    if len(words) < 3:
        v.fail("translation is too short to describe anything")

    if code.lower() in text.lower():
        v.fail("translation repeats the raw code rather than explaining it")

    # An application named in the text must be the one the catalogue records.
    named = {a for a in KNOWN_APPS if re.search(rf"\b{a}\b", text, re.IGNORECASE)}
    actual = meta["app"]
    wrong = named - {actual}
    if wrong:
        v.fail(f"names {sorted(wrong)} but this entitlement belongs to {actual}")

    # Risk language must not contradict the catalogue. A HIGH entitlement
    # described as low risk is how a reviewer is talked into approving it.
    actual_risk = meta["risk"]
    lowered = text.lower()
    for level, terms in RISK_WORDS.items():
        if level == actual_risk:
            continue
        for t in terms:
            if re.search(rf"\b{t}[- ]risk\b", lowered):
                v.fail(f"describes {actual_risk} access as {t} risk")

    # The model is describing an entitlement, not judging a person.
    for phrase in ("should be revoked", "must be removed", "is a violation",
                   "is non-compliant", "unauthorised"):
        if phrase in lowered:
            v.warn(f"contains a judgement ('{phrase}') rather than a description")

    return v


# ==========================================================================
# Clustering
# ==========================================================================

def validate_clustering(clusters: list[dict],
                        finding_ids: set[str]) -> ValidationResult:
    """Check that a thematic grouping is a partition of the findings.

    This is the check that earns the layer its place. A grouping that loses
    findings still reads as a complete report — the themes are plausible, the
    prose is fluent, and the eleven findings that fell out are invisible.
    Set arithmetic settles it in a way that reading the output never will.
    """
    v = ValidationResult(True)

    if not isinstance(clusters, list) or not clusters:
        v.fail("clustering is empty or not a list")
        return v

    seen: list[str] = []
    for i, c in enumerate(clusters):
        if not isinstance(c, dict):
            v.fail(f"cluster {i} is not an object")
            return v
        if not c.get("name"):
            v.fail(f"cluster {i} has no name")
        ids = c.get("finding_ids")
        if not isinstance(ids, list):
            v.fail(f"cluster {i} has no finding_ids list")
            return v
        seen.extend(ids)

    seen_set = set(seen)

    invented = seen_set - finding_ids
    if invented:
        v.fail(f"{len(invented)} finding ids do not exist "
               f"(e.g. {sorted(invented)[:3]})")

    dropped = finding_ids - seen_set
    if dropped:
        v.fail(f"{len(dropped)} findings were not placed in any cluster "
               f"(e.g. {sorted(dropped)[:3]})")

    if len(seen) != len(seen_set):
        dupes = [x for x in seen_set if seen.count(x) > 1]
        v.fail(f"{len(dupes)} findings appear in more than one cluster "
               f"(e.g. {sorted(dupes)[:3]})")

    if len(clusters) > 15:
        v.warn(f"{len(clusters)} clusters is too many to be a summary")

    singletons = sum(1 for c in clusters if len(c.get("finding_ids", [])) == 1)
    if singletons > len(clusters) / 2:
        v.warn(f"{singletons} of {len(clusters)} clusters hold a single finding; "
               f"this is close to no grouping at all")

    return v


# ==========================================================================
# Narrative
# ==========================================================================

# The percent sign has to be inside the match. Written as `%?\b` it never
# matched, because `%` is not a word character — so "86.2%" tokenised as
# "86.2" and failed against a permitted set holding "86.2%". The check was
# rejecting figures it had itself supplied.
NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b%?")


def validate_narrative(text: str, permitted: set[str]) -> ValidationResult:
    """Check prose that accompanies computed figures.

    Every number in the narrative must be one the deterministic layer
    produced. A model asked to summarise 267 findings will readily write "over
    300", and a reader has no way to tell that figure apart from the measured
    ones sitting beside it. Restricting the model to numbers it was given is
    cruder than checking each claim, and it is checkable.
    """
    v = ValidationResult(True)

    if not text or not text.strip():
        v.fail("empty narrative")
        return v

    used = {m.group().rstrip(".") for m in NUMBER.finditer(text)}
    invented = {n for n in used if n not in permitted}

    # Small integers are ordinary prose ("the two rules", "a third of"), not
    # claims about the estate.
    invented = {n for n in invented
                if not (n.isdigit() and int(n) <= 10 and "%" not in n)}

    if invented:
        v.fail(f"cites figures the pipeline did not produce: {sorted(invented)[:5]}")

    for phrase in ("i recommend", "you should", "it is likely that",
                   "probably", "i estimate", "in my opinion"):
        if phrase in text.lower():
            v.warn(f"contains speculation ('{phrase}')")

    return v
