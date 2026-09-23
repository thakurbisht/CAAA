"""
Reconciliation rules.

Six deterministic checks over a correlated estate. No inference, no scoring
model, no LLM — each rule is a comparison whose result can be re-derived by
hand from the feeds. That is the property that makes a finding survive an
audit challenge.

Two design commitments distinguish these from the equivalent checks inside an
IGA platform:

  Dual-source evaluation. Every rule is evaluated twice: once against what
  identities actually hold, and once against what the platform believes they
  hold. The difference between the two answers is itself a finding, and it is
  the one that matters most — a rule that returns "no violations" from
  platform data has not established that there are none. It has established
  that the platform cannot see any.

  Declared coverage. Every rule reports the population it could not examine
  alongside the findings it produced. A check that silently skips three
  clinical applications and reports zero findings is worse than no check,
  because it manufactures confidence where none is warranted.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _as_of_from(snapshot_dir: Path) -> date:
    """The date a snapshot describes.

    Taken from the directory name when it is a date, which is the generator's
    convention. A real extract is unlikely to follow it, so fall back to
    today rather than refusing to run — the caller can pass `as_of`
    explicitly when the extract date is known.
    """
    try:
        return date.fromisoformat(snapshot_dir.name)
    except ValueError:
        return date.today()


@dataclass
class Finding:
    rule_id: str
    severity: str
    subject: str                     # the ad_sam or nhi account the finding is about
    cluster_id: str | None
    person_number: str | None
    summary: str
    evidence: dict = field(default_factory=dict)

    # Would the platform's own data have revealed this? Three states, not two.
    # Asserting "no" when the platform's feed was never supplied is a claim
    # about a comparison that was not made — the same conflation of "we could
    # not test this" with "we tested it and it failed" that the pre-flight
    # guard keeps separate as UNKNOWN.
    visible_in_iga_data: bool | None = True

    @property
    def platform_visibility(self) -> str:
        if self.visible_in_iga_data is None:
            return "unknown"
        return "yes" if self.visible_in_iga_data else "no"

    def row(self) -> list:
        return [
            self.rule_id, self.severity, self.subject,
            self.cluster_id or "", self.person_number or "",
            self.summary,
            self.platform_visibility,
            json.dumps(self.evidence, sort_keys=True),
        ]


@dataclass
class RuleResult:
    rule_id: str
    name: str
    findings: list[Finding]
    population_examined: int
    population_total: int
    excluded: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return (self.population_examined / self.population_total
                if self.population_total else 0.0)

    @property
    def invisible_to_platform(self) -> int:
        """Findings the platform's data demonstrably could not produce.

        Counts only findings compared against a platform feed that was
        actually present. Where no comparison was possible the finding is
        neither visible nor invisible, and counting it either way overstates
        what is known.
        """
        return sum(1 for f in self.findings if f.visible_in_iga_data is False)

    @property
    def platform_comparison_unavailable(self) -> int:
        return sum(1 for f in self.findings if f.visible_in_iga_data is None)


# ==========================================================================
# Estate view — the correlated, joined data the rules run over
# ==========================================================================

class EstateView:
    """Joins the feeds onto the Phase 2 correlation map.

    Everything is keyed on cluster_id rather than on any single system's
    identifier. That is what allows a leaver check to reach an administrative
    satellite: those accounts have no HR record of their own, so a check keyed
    on person number cannot see them at all.
    """

    def __init__(self, snapshot_dir: Path, correlation_csv: Path,
                 as_of: date | None = None, profile=None):
        self.as_of = as_of or _as_of_from(snapshot_dir)

        from feeds import load_feeds
        f = load_feeds(snapshot_dir, profile)
        self.hcm = f["hcm"]
        self.ad = f["ad"]
        self.entra = f["entra"]
        self.iga_ident = f["iga"]
        self.iga_ent = f["iga_ent"]
        self.app_ent = f["app_ent"]
        self.nhi = f["nhi"]

        self.clusters = list(csv.DictReader(open(correlation_csv, newline="")))

        # Whether a platform entitlement feed was supplied at all. Without it,
        # no finding can be described as reachable or unreachable from the
        # platform's records, because there are no records to compare against.
        self.has_platform_holdings = bool(self.iga_ent)

        self._index()

    def _index(self):
        self.hcm_by_pn = {r["person_number"]: r for r in self.hcm}
        self.ad_by_sam = {r["sam_account_name"]: r for r in self.ad}
        self.iga_by_id = {r["iga_identity_id"]: r for r in self.iga_ident}

        self.cluster_by_sam = {c["ad_sam"]: c for c in self.clusters if c["ad_sam"]}
        self.cluster_by_id = {c["cluster_id"]: c for c in self.clusters}

        # Actual holdings, keyed by account
        self.holdings_by_sam: dict[str, list[dict]] = {}
        for r in self.app_ent:
            self.holdings_by_sam.setdefault(r["ad_sam"], []).append(r)

        # What the platform believes is held, mapped back to the account
        sam_by_iga = {r["iga_identity_id"]: r.get("correlated_ad_sam", "")
                      for r in self.iga_ident}
        self.iga_holdings_by_sam: dict[str, list[dict]] = {}
        for r in self.iga_ent:
            sam = sam_by_iga.get(r["iga_identity_id"], "")
            if sam:
                self.iga_holdings_by_sam.setdefault(sam, []).append(r)

        # Which applications the platform aggregates at all
        self.aggregated_apps, self.unaggregated_apps = set(), set()
        for r in self.app_ent:
            (self.aggregated_apps if r["aggregated_by_iga"] == "TRUE"
             else self.unaggregated_apps).add(r["application"])
        self.unaggregated_apps -= self.aggregated_apps

    # -- helpers ---------------------------------------------------------

    def cluster_for(self, sam: str) -> dict | None:
        return self.cluster_by_sam.get(sam)

    def person_for(self, sam: str) -> dict | None:
        c = self.cluster_by_sam.get(sam)
        if not c or not c["hcm_person_number"]:
            return None
        return self.hcm_by_pn.get(c["hcm_person_number"])

    def is_terminated(self, hcm_row: dict) -> bool:
        return hcm_row.get("assignment_status") == "TERMINATED"

    def entitlement_visible_to_iga(self, sam: str, entitlement: str) -> bool:
        return any(r["entitlement"] == entitlement
                   for r in self.iga_holdings_by_sam.get(sam, []))

    def visibility(self, seen_in_platform: bool) -> bool | None:
        """Resolve a visibility claim, or decline to make one."""
        return seen_in_platform if self.has_platform_holdings else None


# ==========================================================================
# Rules
# ==========================================================================

def rule_leaver_not_deprovisioned(v: EstateView) -> RuleResult:
    """R1 — a worker HR has terminated still holds live access.

    Evaluated per account rather than per person, so an administrative
    satellite is checked in its own right. Those accounts carry the highest
    privilege in the estate and have no HR record to be terminated, which is
    precisely why a person-keyed check misses them.
    """
    findings, examined = [], 0
    no_link = 0
    unconfirmed = 0

    for c in v.clusters:
        sam = c["ad_sam"]
        if not sam or c["account_type"] == "NHI":
            continue

        pn = c["hcm_person_number"]
        if not pn:
            no_link += 1
            continue

        # A candidate still in adjudication is not a confirmed owner. Testing
        # its termination status asserts a finding about a person this account
        # may not belong to — which is how a correlation error becomes a
        # wrongful revocation. These accounts are unassessed, not clean, and
        # are counted against coverage rather than silently passed.
        if c["status"] != "RESOLVED":
            unconfirmed += 1
            continue

        hcm = v.hcm_by_pn.get(pn)
        if not hcm:
            no_link += 1
            continue

        examined += 1
        if not v.is_terminated(hcm):
            continue

        ad = v.ad_by_sam.get(sam, {})
        held = v.holdings_by_sam.get(sam, [])
        ad_enabled = ad.get("enabled") == "TRUE"

        if not ad_enabled and not held:
            continue

        iga_visible = [h for h in held
                       if v.entitlement_visible_to_iga(sam, h["entitlement"])]
        critical = [h["entitlement"] for h in held if h["risk"] == "CRITICAL"]

        findings.append(Finding(
            rule_id="R1_LEAVER_NOT_DEPROVISIONED",
            severity="CRITICAL" if (critical or c["account_type"] == "HUMAN_ADMIN")
                     else "HIGH",
            subject=sam,
            cluster_id=c["cluster_id"],
            person_number=pn,
            summary=(
                f"terminated {hcm.get('termination_date') or 'date unknown'}, "
                f"account still {'enabled' if ad_enabled else 'disabled'} "
                f"holding {len(held)} entitlements"
            ),
            evidence={
                "account_type": c["account_type"],
                "termination_date": hcm.get("termination_date"),
                "ad_enabled": ad_enabled,
                "entitlements_held": len(held),
                "critical_entitlements": critical,
                "correlation_confidence": float(c["confidence"] or 0),
                "correlation_tier": c["tier"],
            },
            visible_in_iga_data=v.visibility(bool(iga_visible)),
        ))

    r = RuleResult("R1_LEAVER_NOT_DEPROVISIONED",
                   "Terminated worker retains live access",
                   findings, examined, len(v.clusters))
    r.excluded["no_confirmed_hr_link"] = no_link
    r.excluded["correlation_unconfirmed"] = unconfirmed
    if no_link:
        r.notes.append(
            f"{no_link} accounts could not be checked: no HR link at all, so "
            f"there is no authoritative termination date to test against."
        )
    if unconfirmed:
        r.notes.append(
            f"{unconfirmed} accounts have a candidate HR link that did not reach "
            f"the correlation acceptance threshold. Asserting a leaver finding "
            f"on an unconfirmed link risks naming the wrong person, so these are "
            f"reported as unassessed. They are not evidence of a clean estate."
        )
    return r


def rule_platform_state_disagreement(v: EstateView) -> RuleResult:
    """R2 — the platform's account state contradicts the directory.

    Where the platform records an account as disabled and the directory has it
    enabled, one of the two is wrong. Either access is live that governance
    believes is closed, or the register is stale. Both are reportable, and the
    platform cannot detect this on its own because it is one of the two
    disagreeing parties.
    """
    findings, examined = [], 0

    for c in v.clusters:
        sam, iga_id = c["ad_sam"], c["iga_identity_id"]
        if not sam or not iga_id:
            continue

        iga = v.iga_by_id.get(iga_id)
        ad = v.ad_by_sam.get(sam)
        if not iga or not ad:
            continue

        examined += 1
        iga_state = (iga.get("lifecycle_state") or "").upper()
        ad_enabled = ad.get("enabled") == "TRUE"

        if iga_state == "DISABLED" and ad_enabled:
            held = v.holdings_by_sam.get(sam, [])
            findings.append(Finding(
                rule_id="R2_PLATFORM_STATE_DISAGREEMENT",
                severity="HIGH" if held else "MEDIUM",
                subject=sam,
                cluster_id=c["cluster_id"],
                person_number=c["hcm_person_number"] or None,
                summary=(
                    f"platform records this identity as disabled while the "
                    f"directory has the account enabled with {len(held)} "
                    f"entitlements"
                ),
                evidence={
                    "platform_state": iga_state,
                    "directory_enabled": ad_enabled,
                    "entitlements_held": len(held),
                    "last_logon": ad.get("last_logon_timestamp"),
                },
                visible_in_iga_data=False,
            ))

    r = RuleResult("R2_PLATFORM_STATE_DISAGREEMENT",
                   "Platform account state contradicts the directory",
                   findings, examined, len(v.clusters))
    r.notes.append(
        "By construction this rule has no equivalent inside the platform: it "
        "compares the platform's own record against an independent read of the "
        "directory."
    )
    return r


def rule_orphaned_nhi(v: EstateView) -> RuleResult:
    """R3 — a non-human identity with no accountable owner.

    Service accounts, interfaces and shared workstations do not appear in any
    access review, because no manager is asked to attest to them. An owner who
    is unknown or has left means nobody is answerable for standing privilege.
    """
    findings = []

    for r in v.nhi:
        sam = r["ad_sam"]
        status = (r.get("owner_status") or "").upper()
        owner = r.get("owner_person_number") or ""

        if status not in ("UNKNOWN", "TERMINATED") and owner:
            continue

        held = v.holdings_by_sam.get(sam, [])
        critical = [h["entitlement"] for h in held if h["risk"] == "CRITICAL"]

        findings.append(Finding(
            rule_id="R3_ORPHANED_NHI",
            severity="CRITICAL" if critical else "HIGH",
            subject=sam,
            cluster_id=(v.cluster_for(sam) or {}).get("cluster_id"),
            person_number=owner or None,
            summary=(
                f"{r.get('nhi_class', 'non-human')} identity with "
                f"{'no recorded owner' if status == 'UNKNOWN' else 'a terminated owner'}, "
                f"holding {len(held)} entitlements"
            ),
            evidence={
                "nhi_class": r.get("nhi_class"),
                "owner_status": status,
                "owner_person_number": owner or None,
                "purpose": r.get("purpose"),
                "review_cadence": r.get("review_cadence"),
                "entitlements_held": len(held),
                "critical_entitlements": critical,
            },
            visible_in_iga_data=v.visibility(
                bool(v.iga_holdings_by_sam.get(sam))),
        ))

    return RuleResult("R3_ORPHANED_NHI",
                      "Non-human identity without an accountable owner",
                      findings, len(v.nhi), len(v.nhi))


def rule_orphaned_admin_account(v: EstateView) -> RuleResult:
    """R4 — a privileged satellite whose owner has left.

    An `a-` account is the highest-privilege object attached to a person and
    has no HR record of its own. When the person is terminated, the primary
    account is deprovisioned by an HR-triggered process and the satellite is
    not, because nothing in HR refers to it. Only a correlated view finds it.
    """
    findings, examined = [], 0

    for c in v.clusters:
        if c["account_type"] != "HUMAN_ADMIN":
            continue
        sam = c["ad_sam"]
        if not sam:
            continue
        examined += 1

        pn = c["hcm_person_number"]
        confirmed = c["status"] == "RESOLVED"
        hcm = v.hcm_by_pn.get(pn) if (pn and confirmed) else None

        # An unconfirmed link is not evidence that the owner is alive and
        # employed. Treating it as such is what causes a privileged account
        # with a departed owner to be passed over — the exact failure this
        # rule exists to catch. Unconfirmed resolves to unknown, and unknown
        # is reportable.
        owner_gone = hcm is not None and v.is_terminated(hcm)
        owner_unknown = hcm is None

        if not (owner_gone or owner_unknown):
            continue

        ad = v.ad_by_sam.get(sam, {})
        held = v.holdings_by_sam.get(sam, [])
        privileged = [h["entitlement"] for h in held
                      if h["risk"] in ("CRITICAL", "HIGH")]

        findings.append(Finding(
            rule_id="R4_ORPHANED_ADMIN_ACCOUNT",
            severity="CRITICAL",
            subject=sam,
            cluster_id=c["cluster_id"],
            person_number=pn or None,
            summary=(
                "privileged account whose owner has been terminated"
                if owner_gone else
                "privileged account with no confirmed owner"
            ),
            evidence={
                "owner_status": "TERMINATED" if owner_gone else "UNCONFIRMED",
                "owner_person_number": pn or None,
                "correlation_status": c["status"],
                "termination_date": hcm.get("termination_date") if hcm else None,
                "ad_enabled": ad.get("enabled") == "TRUE",
                "privileged_entitlements": privileged,
                "parent_cluster": c["parent_cluster_id"] or None,
                "correlation_confidence": float(c["confidence"] or 0),
            },
            visible_in_iga_data=v.visibility(
                bool(v.iga_holdings_by_sam.get(sam))),
        ))

    return RuleResult("R4_ORPHANED_ADMIN_ACCOUNT",
                      "Privileged satellite account without a live owner",
                      findings, examined, examined)


def rule_sod_violation(v: EstateView, sod_rules: list[dict]) -> RuleResult:
    """R5 — one identity holding both sides of a segregated duty.

    Evaluated twice: against holdings as they actually are, and against the
    platform's view of them. Several of these rules reference applications the
    platform does not aggregate, so the platform-only evaluation is structurally
    incapable of returning a complete answer — and would report a clean result
    rather than an incomplete one.
    """
    findings, examined = [], 0
    unreachable_rules = []

    # A rule is unreachable from platform data if either side lives in an
    # application the platform does not aggregate.
    app_of_entitlement = {r["entitlement"]: r["application"] for r in v.app_ent}
    for rule in sod_rules:
        apps = {app_of_entitlement.get(rule["left"]),
                app_of_entitlement.get(rule["right"])}
        if apps & v.unaggregated_apps:
            unreachable_rules.append(rule["id"])

    for c in v.clusters:
        sam = c["ad_sam"]
        if not sam:
            continue
        held = v.holdings_by_sam.get(sam)
        if not held:
            continue
        examined += 1

        codes = {h["entitlement"] for h in held}
        iga_codes = {h["entitlement"] for h in v.iga_holdings_by_sam.get(sam, [])}

        for rule in sod_rules:
            if not ({rule["left"], rule["right"]} <= codes):
                continue

            visible = {rule["left"], rule["right"]} <= iga_codes

            findings.append(Finding(
                rule_id="R5_SOD_VIOLATION",
                severity=rule["severity"],
                subject=sam,
                cluster_id=c["cluster_id"],
                person_number=c["hcm_person_number"] or None,
                summary=f"{rule['id']}: {rule['name'].lower()}",
                evidence={
                    "sod_rule_id": rule["id"],
                    "scope": rule["scope"],
                    "entitlements": [rule["left"], rule["right"]],
                    "rationale": rule["rationale"],
                    "reachable_from_platform_data": visible,
                },
                visible_in_iga_data=v.visibility(visible),
            ))

    r = RuleResult("R5_SOD_VIOLATION",
                   "Toxic combination of entitlements held by one identity",
                   findings, examined, len(v.clusters))
    if unreachable_rules:
        r.excluded["sod_rules_unreachable_from_platform"] = len(unreachable_rules)
        r.notes.append(
            f"{len(unreachable_rules)} of {len(sod_rules)} rules "
            f"({', '.join(unreachable_rules)}) reference applications the "
            f"platform does not aggregate. Evaluated against platform data alone, "
            f"these rules cannot return a violation regardless of what is held."
        )
    return r


def rule_coverage_gap(v: EstateView) -> RuleResult:
    """R6 — privilege held in applications the platform does not aggregate.

    Not a finding about any individual. It is a finding about the certification
    itself: these holdings cannot appear in an access review, cannot be revoked
    through it, and cannot be attested to. Reported per application because the
    remediation is per connector.
    """
    findings = []
    by_app: dict[str, list[dict]] = {}
    for r in v.app_ent:
        if r["aggregated_by_iga"] != "FALSE":
            continue
        by_app.setdefault(r["application"], []).append(r)

    for app, rows in sorted(by_app.items()):
        critical = [r for r in rows if r["risk"] == "CRITICAL"]
        holders = {r["ad_sam"] for r in rows}
        codes: dict[str, int] = {}
        for r in critical:
            codes[r["entitlement"]] = codes.get(r["entitlement"], 0) + 1

        findings.append(Finding(
            rule_id="R6_COVERAGE_GAP",
            severity="CRITICAL" if critical else "HIGH",
            subject=app,
            cluster_id=None,
            person_number=None,
            summary=(
                f"{len(rows)} entitlements held by {len(holders)} identities in "
                f"{app}, none of which the platform aggregates"
            ),
            evidence={
                "application": app,
                "holdings": len(rows),
                "distinct_holders": len(holders),
                "critical_holdings": len(critical),
                "critical_entitlements": dict(sorted(
                    codes.items(), key=lambda x: -x[1])),
            },
            visible_in_iga_data=False,
        ))

    total_apps = len(v.aggregated_apps | v.unaggregated_apps)
    r = RuleResult("R6_COVERAGE_GAP",
                   "Privilege outside the platform's aggregation scope",
                   findings, len(v.aggregated_apps), total_apps)
    r.notes.append(
        "Coverage here is measured over applications, not identities: an estate "
        "can be 100% enrolled by headcount and still leave whole applications "
        "ungoverned."
    )
    return r


# ==========================================================================
# Runner
# ==========================================================================

ALL_RULES = [
    "R1_LEAVER_NOT_DEPROVISIONED",
    "R2_PLATFORM_STATE_DISAGREEMENT",
    "R3_ORPHANED_NHI",
    "R4_ORPHANED_ADMIN_ACCOUNT",
    "R5_SOD_VIOLATION",
    "R6_COVERAGE_GAP",
]


def run_all(v: EstateView, sod_rules: list[dict]) -> list[RuleResult]:
    return [
        rule_leaver_not_deprovisioned(v),
        rule_platform_state_disagreement(v),
        rule_orphaned_nhi(v),
        rule_orphaned_admin_account(v),
        rule_sod_violation(v, sod_rules),
        rule_coverage_gap(v),
    ]
