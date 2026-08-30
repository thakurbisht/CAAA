"""
Drift detection.

Phases 1 to 3 all examine a single snapshot: a condition is either present or
it is not. Drift has no meaning at a point in time. It only exists relative to
a baseline, and the choice of baseline is the whole of the design.

Three baselines are used here, and they do not carry equal weight. Reporting
them as one undifferentiated list of findings would be the central dishonesty
available to this phase, because the reader cannot tell which findings will
survive challenge:

  Observed history — what the worker held before a transfer that actually
  appears in the HR feed. Deterministic. The transfer is a fact, the retained
  entitlement is a fact, and the finding can be re-derived by hand. This is
  evidence.

  Peer distribution — what comparable workers hold. Statistical. An outlier
  is not a violation; rare is not the same as wrong, and a legitimate
  specialist will sit outside their peer group every time. This is a
  prioritisation signal, and it is labelled as one.

  Usage record — whether the entitlement has been exercised. Factual, but the
  dormancy threshold is a policy choice rather than a discovered truth, so the
  finding is only as defensible as the threshold behind it. The threshold is
  therefore reported alongside every finding rather than buried in config.

Each finding carries the basis it rests on, so a reviewer can sort by how much
argument the finding will take to defend.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


# How much argument a finding will take to defend.
EVIDENCE_BASIS = {
    "OBSERVED_HISTORY": "deterministic; re-derivable from two HR snapshots",
    "PEER_DISTRIBUTION": "statistical; a ranking signal, not a violation",
    "USAGE_RECORD": "factual, against a policy-chosen threshold",
}


@dataclass
class DriftFinding:
    rule_id: str
    basis: str                       # one of EVIDENCE_BASIS
    severity: str
    subject: str
    cluster_id: str | None
    person_number: str | None
    summary: str
    evidence: dict = field(default_factory=dict)

    def row(self) -> list:
        import json
        return [self.rule_id, self.basis, self.severity, self.subject,
                self.cluster_id or "", self.person_number or "",
                self.summary, json.dumps(self.evidence, sort_keys=True)]


@dataclass
class DriftResult:
    rule_id: str
    name: str
    basis: str
    findings: list[DriftFinding]
    population_examined: int
    population_total: int
    excluded: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return (self.population_examined / self.population_total
                if self.population_total else 0.0)


# ==========================================================================
# Time series across snapshots
# ==========================================================================

class EstateTimeline:
    """The estate as a sequence of snapshots rather than a single state.

    Loads only the fields drift needs, since holding every feed for every
    snapshot in memory is unnecessary and slow.
    """

    def __init__(self, estate_dir: Path, correlation_csv: Path):
        self.estate = estate_dir
        self.snapshots = sorted((estate_dir / "snapshots").iterdir())
        self.dates = [date.fromisoformat(s.name) for s in self.snapshots]

        self.clusters = list(csv.DictReader(open(correlation_csv, newline="")))
        self.cluster_by_sam = {c["ad_sam"]: c for c in self.clusters if c["ad_sam"]}

        # person_number -> department, per snapshot
        self.dept_series: list[dict[str, str]] = []
        self.job_series: list[dict[str, str]] = []
        for s in self.snapshots:
            d, j = {}, {}
            for r in csv.DictReader(open(s / "hcm_workers.csv", newline="")):
                d[r["person_number"]] = r["department"]
                j[r["person_number"]] = r["job_code"]
            self.dept_series.append(d)
            self.job_series.append(j)

        latest = self.snapshots[-1]
        self.as_of = self.dates[-1]
        self.hcm = {r["person_number"]: r
                    for r in csv.DictReader(open(latest / "hcm_workers.csv", newline=""))}

        self.holdings: dict[str, list[dict]] = {}
        for r in csv.DictReader(open(latest / "app_entitlements.csv", newline="")):
            self.holdings.setdefault(r["ad_sam"], []).append(r)

    # ------------------------------------------------------------------

    def observed_transfers(self) -> list[dict]:
        """Transfers visible as a department change between two snapshots.

        Derived by comparing consecutive HR extracts rather than read from any
        field asserting a move. A production HR feed rarely carries "this
        entitlement was granted for department X"; consecutive extracts are
        what an assurance function actually has.
        """
        out = []
        for idx in range(1, len(self.dept_series)):
            prev, cur = self.dept_series[idx - 1], self.dept_series[idx]
            for pn, dept in cur.items():
                if pn in prev and prev[pn] != dept:
                    out.append({
                        "person_number": pn,
                        "from_dept": prev[pn],
                        "to_dept": dept,
                        "observed_between": (self.dates[idx - 1].isoformat(),
                                             self.dates[idx].isoformat()),
                        "observed_on": self.dates[idx],
                        "days_since": (self.as_of - self.dates[idx]).days,
                    })
        return out


# ==========================================================================
# D1 — retained access after an observed transfer
# ==========================================================================

def learn_department_scoped(t: EstateTimeline, *,
                            concentration: float = 0.65,
                            min_holders: int = 5) -> dict[str, str]:
    """Work out which entitlements belong to a department, from the estate.

    A first implementation tested `entitlement.startswith("GRP_DEPT_")`, which
    is a naming convention rather than a fact about the estate. It missed
    every clinical role entitlement — `CERN_SEC_RN_ICU` and its siblings —
    which are department-specific in substance and carry far more privilege
    than a directory group. Roughly a sixth of retained access was invisible
    to the rule for no better reason than how it was named.

    Concentration is measured instead: if almost everyone holding an
    entitlement sits in one department, the entitlement belongs to that
    department, whatever it is called. Entitlements spread across departments
    are global and are correctly ignored.

    The default threshold comes from the separation in the data rather than
    from taste. Genuinely department-scoped entitlements sit between 0.71 and
    1.00; the one global clinical role sits at 0.39 across 189 holders. 0.65
    falls in the empty band between them.

    One limitation is worth stating, because it cuts against the method: drift
    itself depresses concentration. `GRP_DEPT_ED` measures 0.79 rather than
    1.00 precisely because people who left ED still hold it. The more
    retention there is, the weaker the signal that identifies what was
    retained — so a threshold tuned on a badly drifted estate will be lower
    than one tuned on a clean estate, and should be re-derived rather than
    carried over.
    """
    dept_of_pn = t.dept_series[-1]
    sam_to_pn = {c["ad_sam"]: c["hcm_person_number"]
                 for c in t.clusters
                 if c["ad_sam"] and c["hcm_person_number"]
                 and c["status"] == "RESOLVED"}

    tally: dict[str, dict[str, int]] = {}
    for sam, held in t.holdings.items():
        pn = sam_to_pn.get(sam)
        dept = dept_of_pn.get(pn) if pn else None
        if not dept:
            continue
        for h in held:
            tally.setdefault(h["entitlement"], {})
            tally[h["entitlement"]][dept] = tally[h["entitlement"]].get(dept, 0) + 1

    scoped = {}
    for ent, depts in tally.items():
        total = sum(depts.values())
        if total < min_holders:
            continue
        top_dept, top_n = max(depts.items(), key=lambda x: x[1])
        if top_n / total >= concentration:
            scoped[ent] = top_dept
    return scoped


def rule_retained_on_transfer(t: EstateTimeline, *,
                              grace_days: int = 30) -> DriftResult:
    """D1 — department-scoped access from a prior role, after a real transfer.

    A grace period is applied because same-week revocation is not a realistic
    expectation and flagging it produces noise that trains reviewers to ignore
    the report. Only access still held `grace_days` after the move is counted,
    and the grace period is stated in every finding.
    """
    findings = []
    transfers = t.observed_transfers()
    unconfirmed = 0
    scoped = learn_department_scoped(t)

    # Where somebody moved more than once, the earliest origin is what matters:
    # access from two roles ago is more stale, not less.
    by_person: dict[str, list[dict]] = {}
    for tr in transfers:
        by_person.setdefault(tr["person_number"], []).append(tr)

    for pn, moves in by_person.items():
        moves.sort(key=lambda m: m["observed_on"])
        latest = moves[-1]

        if latest["days_since"] < grace_days:
            continue

        cluster = next((c for c in t.clusters
                        if c["hcm_person_number"] == pn), None)
        if not cluster or not cluster["ad_sam"]:
            unconfirmed += 1
            continue
        if cluster["status"] != "RESOLVED":
            unconfirmed += 1
            continue

        sam = cluster["ad_sam"]
        current = t.dept_series[-1].get(pn)
        prior_depts = {m["from_dept"] for m in moves} - {current}

        stale = [h for h in t.holdings.get(sam, [])
                 if scoped.get(h["entitlement"]) in prior_depts
                 and scoped.get(h["entitlement"]) != current]

        if not stale:
            continue

        oldest = min(m["observed_on"] for m in moves
                     if m["from_dept"] in prior_depts)
        age = (t.as_of - oldest).days
        elevated = [h["entitlement"] for h in stale
                    if h["risk"] in ("CRITICAL", "HIGH")]

        findings.append(DriftFinding(
            rule_id="D1_RETAINED_ON_TRANSFER",
            basis="OBSERVED_HISTORY",
            severity="HIGH" if (elevated or age > 90) else "MEDIUM",
            subject=sam,
            cluster_id=cluster["cluster_id"],
            person_number=pn,
            summary=(
                f"moved {latest['from_dept']} to {latest['to_dept']} "
                f"{latest['days_since']} days ago, still holding "
                f"{len(stale)} entitlements scoped to a prior department"
            ),
            evidence={
                "current_department": current,
                "prior_departments": sorted(prior_depts),
                "transfers_observed": len(moves),
                "retained_entitlements": sorted(h["entitlement"] for h in stale),
                "elevated_risk_retained": sorted(elevated),
                "days_since_earliest_move": age,
                "grace_period_days": grace_days,
                "observed_between": latest["observed_between"],
            },
        ))

    r = DriftResult("D1_RETAINED_ON_TRANSFER",
                    "Department access retained after an observed transfer",
                    "OBSERVED_HISTORY", findings,
                    len(by_person) - unconfirmed, len(by_person))
    if unconfirmed:
        r.excluded["correlation_unconfirmed"] = unconfirmed
        r.notes.append(
            f"{unconfirmed} transferred workers could not be checked: their "
            f"account could not be confirmed, so there is no holdings record "
            f"to test. Unassessed, not clean."
        )
    r.notes.append(
        f"Transfers are derived by comparing consecutive HR extracts, not read "
        f"from any field asserting a move. {len(transfers)} transfers observed "
        f"across {len(t.snapshots)} snapshots."
    )
    r.notes.append(
        f"{len(scoped)} entitlements were identified as department-scoped by "
        f"measuring how concentrated their holders are, rather than by matching "
        f"a naming convention."
    )
    return r


# ==========================================================================
# D2 — peer group outliers
# ==========================================================================

def rule_peer_group_outlier(t: EstateTimeline, *,
                            min_peers: int = 8,
                            max_prevalence: float = 0.05) -> DriftResult:
    """D2 — access held by very few comparable workers.

    Explicitly a ranking signal rather than a violation. A rare entitlement
    may be entirely correct: someone has to be the one nurse trained on the
    equipment. What the rule establishes is that the grant is unusual for the
    role and therefore worth a human looking at, which is a much weaker claim
    than "this is wrong" and is labelled as such.

    Peer groups below `min_peers` are not evaluated at all. In a small group
    every member is an outlier by construction, and reporting that is noise
    dressed as analysis.
    """
    findings = []

    # peer group = (job_code, department) as of the latest extract
    groups: dict[tuple, list[str]] = {}
    for pn, row in t.hcm.items():
        if row["assignment_status"] != "ACTIVE_ASSIGN":
            continue
        groups.setdefault((row["job_code"], row["department"]), []).append(pn)

    sam_of_pn = {c["hcm_person_number"]: c["ad_sam"]
                 for c in t.clusters
                 if c["hcm_person_number"] and c["status"] == "RESOLVED"}

    examined, small_groups = 0, 0

    for (job, dept), members in groups.items():
        if len(members) < min_peers:
            small_groups += len(members)
            continue
        examined += len(members)

        held_by: dict[str, set[str]] = {}
        for pn in members:
            sam = sam_of_pn.get(pn)
            if not sam:
                continue
            for h in t.holdings.get(sam, []):
                held_by.setdefault(h["entitlement"], set()).add(pn)

        for ent, holders in held_by.items():
            prevalence = len(holders) / len(members)
            if prevalence > max_prevalence:
                continue

            for pn in holders:
                sam = sam_of_pn.get(pn)
                if not sam:
                    continue
                risk = next((h["risk"] for h in t.holdings.get(sam, [])
                             if h["entitlement"] == ent), "LOW")
                if risk not in ("CRITICAL", "HIGH"):
                    continue

                findings.append(DriftFinding(
                    rule_id="D2_PEER_GROUP_OUTLIER",
                    basis="PEER_DISTRIBUTION",
                    severity="MEDIUM" if risk == "CRITICAL" else "LOW",
                    subject=sam,
                    cluster_id=(t.cluster_by_sam.get(sam) or {}).get("cluster_id"),
                    person_number=pn,
                    summary=(
                        f"holds {ent}, which {len(holders)} of {len(members)} "
                        f"{job} staff in {dept} hold"
                    ),
                    evidence={
                        "entitlement": ent,
                        "entitlement_risk": risk,
                        "peer_group": f"{job}/{dept}",
                        "peer_group_size": len(members),
                        "holders_in_group": len(holders),
                        "prevalence": round(prevalence, 4),
                        "prevalence_threshold": max_prevalence,
                        "interpretation": (
                            "unusual for the role; requires review, not "
                            "automatic revocation"
                        ),
                    },
                ))

    r = DriftResult("D2_PEER_GROUP_OUTLIER",
                    "High-risk access rare within the peer group",
                    "PEER_DISTRIBUTION", findings,
                    examined, examined + small_groups)
    if small_groups:
        r.excluded["peer_group_too_small"] = small_groups
        r.notes.append(
            f"{small_groups} workers sit in peer groups below {min_peers} "
            f"members and were not evaluated. In a group that small every "
            f"member is an outlier by construction."
        )
    r.notes.append(
        "Severity is capped at MEDIUM for this rule. A statistical signal does "
        "not establish that access is wrong, only that it is unusual, and "
        "escalating it alongside deterministic findings would misrepresent how "
        "much argument it will take to defend."
    )
    return r


# ==========================================================================
# D3 — dormant entitlements
# ==========================================================================

def rule_dormant_entitlement(t: EstateTimeline, *,
                             dormant_days: int = 180) -> DriftResult:
    """D3 — access carried but not exercised.

    Never-used and long-unused are reported as distinct conditions. An
    entitlement with no usage record at all may simply predate usage logging,
    which is a data-quality finding about the estate rather than a privilege
    finding about the person, and conflating them inflates the count with
    cases nobody can act on.

    Only elevated-risk entitlements are reported. Dormant `GRP_ALL_STAFF`
    is true of most of the estate and revoking it is not the intent.
    """
    findings = []
    examined = never_used = 0
    no_usage_field = 0

    for c in t.clusters:
        sam = c["ad_sam"]
        if not sam or c["account_type"] == "NHI":
            continue
        held = t.holdings.get(sam)
        if not held:
            continue
        examined += 1

        for h in held:
            if h["risk"] not in ("CRITICAL", "HIGH"):
                continue

            last = h.get("last_used") or ""
            granted = h.get("granted_on") or ""

            if not last:
                never_used += 1
                if not granted:
                    no_usage_field += 1
                    continue
                age = (t.as_of - date.fromisoformat(granted)).days
                if age < dormant_days:
                    continue
                findings.append(DriftFinding(
                    rule_id="D3_DORMANT_ENTITLEMENT",
                    basis="USAGE_RECORD",
                    severity="MEDIUM" if h["risk"] == "CRITICAL" else "LOW",
                    subject=sam,
                    cluster_id=c["cluster_id"],
                    person_number=c["hcm_person_number"] or None,
                    summary=(
                        f"{h['entitlement']} granted {age} days ago with no "
                        f"recorded use"
                    ),
                    evidence={
                        "entitlement": h["entitlement"],
                        "entitlement_risk": h["risk"],
                        "condition": "NEVER_USED",
                        "granted_on": granted,
                        "days_held": age,
                        "dormancy_threshold_days": dormant_days,
                        "caveat": (
                            "absence of a usage record is not proof of "
                            "non-use; the grant may predate usage logging"
                        ),
                    },
                ))
                continue

            idle = (t.as_of - date.fromisoformat(last)).days
            if idle < dormant_days:
                continue

            findings.append(DriftFinding(
                rule_id="D3_DORMANT_ENTITLEMENT",
                basis="USAGE_RECORD",
                severity="MEDIUM" if h["risk"] == "CRITICAL" else "LOW",
                subject=sam,
                cluster_id=c["cluster_id"],
                person_number=c["hcm_person_number"] or None,
                summary=(
                    f"{h['entitlement']} last used {idle} days ago"
                ),
                evidence={
                    "entitlement": h["entitlement"],
                    "entitlement_risk": h["risk"],
                    "condition": "IDLE",
                    "last_used": last,
                    "days_idle": idle,
                    "dormancy_threshold_days": dormant_days,
                },
            ))

    r = DriftResult("D3_DORMANT_ENTITLEMENT",
                    "Elevated-risk access not exercised",
                    "USAGE_RECORD", findings, examined, examined)
    if no_usage_field:
        r.excluded["no_grant_or_usage_date"] = no_usage_field
        r.notes.append(
            f"{no_usage_field} holdings carry neither a usage record nor a "
            f"grant date and cannot be aged at all."
        )
    r.notes.append(
        f"Threshold is {dormant_days} days and is a policy choice, not a "
        f"discovered value. Every finding restates it so the reader can judge "
        f"the finding against their own standard."
    )
    return r


# ==========================================================================
# D4 — standing emergency access
# ==========================================================================

def rule_standing_breakglass(t: EstateTimeline,
                             breakglass: set[str]) -> DriftResult:
    """D4 — emergency access held continuously rather than checked out.

    Break-glass access is defensible when it is drawn for an incident and
    surrendered afterwards. Held permanently, it is ordinary standing
    privilege wearing an exception's label, and it will pass a certification
    because the entitlement's stated purpose sounds legitimate.

    Continuity is established across snapshots rather than asserted from the
    current holding: the finding is that the access has never been released,
    which a single snapshot cannot show.
    """
    findings = []
    examined = 0

    # Presence of each break-glass grant in every snapshot
    seen: dict[tuple[str, str], int] = {}
    for s in t.snapshots:
        for r in csv.DictReader(open(s / "app_entitlements.csv", newline="")):
            if r["entitlement"] in breakglass:
                seen[(r["ad_sam"], r["entitlement"])] = \
                    seen.get((r["ad_sam"], r["entitlement"]), 0) + 1

    total_snaps = len(t.snapshots)

    for (sam, ent), count in sorted(seen.items()):
        examined += 1
        if count < total_snaps:
            continue

        c = t.cluster_by_sam.get(sam, {})
        pn = c.get("hcm_person_number") or None
        row = t.hcm.get(pn) if pn else None
        held = next((h for h in t.holdings.get(sam, [])
                     if h["entitlement"] == ent), {})

        findings.append(DriftFinding(
            rule_id="D4_STANDING_BREAKGLASS",
            basis="OBSERVED_HISTORY",
            severity="HIGH",
            subject=sam,
            cluster_id=c.get("cluster_id"),
            person_number=pn,
            summary=(
                f"holds {ent} continuously across all {total_snaps} snapshots: "
                f"emergency access that is never surrendered"
            ),
            evidence={
                "entitlement": ent,
                "snapshots_present": count,
                "snapshots_total": total_snaps,
                "window_days": (t.dates[-1] - t.dates[0]).days,
                "job_code": row.get("job_code") if row else None,
                "department": row.get("department") if row else None,
                "last_used": held.get("last_used") or None,
            },
        ))

    r = DriftResult("D4_STANDING_BREAKGLASS",
                    "Emergency access held continuously",
                    "OBSERVED_HISTORY", findings, examined, examined)
    r.notes.append(
        f"Continuity is established over a {(t.dates[-1] - t.dates[0]).days}-day "
        f"window. A grant issued and surrendered between two snapshots is "
        f"invisible to this check; the snapshot interval is the resolution "
        f"limit."
    )
    return r


ALL_DRIFT_RULES = [
    "D1_RETAINED_ON_TRANSFER",
    "D2_PEER_GROUP_OUTLIER",
    "D3_DORMANT_ENTITLEMENT",
    "D4_STANDING_BREAKGLASS",
]


def run_all(t: EstateTimeline, breakglass: set[str], **kw) -> list[DriftResult]:
    return [
        rule_retained_on_transfer(t, grace_days=kw.get("grace_days", 30)),
        rule_peer_group_outlier(t,
                                min_peers=kw.get("min_peers", 8),
                                max_prevalence=kw.get("max_prevalence", 0.05)),
        rule_dormant_entitlement(t, dormant_days=kw.get("dormant_days", 180)),
        rule_standing_breakglass(t, breakglass),
    ]
