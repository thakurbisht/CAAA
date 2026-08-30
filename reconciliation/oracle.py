"""
Derived ground truth.

The generator's `pathologies.json` is an injection log, not a truth set. It
records the faults that were deliberately planted; it does not record the
faults the simulation produced on its own. Over 180 days of terminations, a
great many device identities and administrative satellites are left behind by
ordinary attrition, and every one of them is a real finding.

Scoring against the injection log therefore penalises a rule for detecting
genuine problems. Measured that way, R4 scored 18.8% precision while every
sampled "false positive" was an administrative account whose owner really had
been terminated.

This module derives the true condition set from the estate itself. It is
written as an independent second implementation and deliberately does not
share code with the rules it scores — a shared helper would make the two
agree by construction and measure nothing.

The one asymmetry that matters: the oracle resolves identities using the
generator's own `identity_map.json`, which records the true account-to-person
mapping. The rules resolve identities using the Phase 2 correlation engine,
which infers that mapping and gets it right 96.5% of the time. The gap
between oracle and rule is therefore a direct measurement of what imperfect
correlation costs downstream — the number that connects Phase 2's accuracy to
Phase 3's.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


class TruthOracle:

    def __init__(self, estate_dir: Path, snapshot_dir: Path):
        self.estate = estate_dir
        self.snap = snapshot_dir

        def rd(name):
            p = snapshot_dir / name
            return list(csv.DictReader(open(p, newline=""))) if p.exists() else []

        self.hcm = {r["person_number"]: r for r in rd("hcm_workers.csv")}
        self.ad = {r["sam_account_name"]: r for r in rd("ad_accounts.csv")}
        self.nhi = {r["ad_sam"]: r for r in rd("nhi_register.csv")}
        self.iga_ident = {r["iga_identity_id"]: r for r in rd("iga_identities.csv")}
        self.app_ent = rd("app_entitlements.csv")
        self.iga_ent = rd("iga_entitlements.csv")

        imap = json.load(open(estate_dir / "ground_truth" / "identity_map.json"))
        self.by_pid = {r["pid"]: r for r in imap}
        self.imap = imap

        # Perfect account -> owning person mapping
        self.owner_pn_of_sam: dict[str, str | None] = {}
        self.kind_of_sam: dict[str, str] = {}
        for r in imap:
            if not r["ad_sam"]:
                continue
            owner = self.by_pid.get(r["owner_pid"]) if r["owner_pid"] else None
            self.owner_pn_of_sam[r["ad_sam"]] = (
                owner["person_number"] if (owner and owner.get("in_hcm")) else None)
            self.kind_of_sam[r["ad_sam"]] = (
                "NHI" if r["kind"] == "NHI"
                else "HUMAN_ADMIN" if r["worker_type"] == "EMPLOYEE_ADMIN"
                else "HUMAN_PRIMARY")

        self.holdings: dict[str, list[dict]] = {}
        for r in self.app_ent:
            self.holdings.setdefault(r["ad_sam"], []).append(r)

        sam_of_iga = {r["iga_identity_id"]: r.get("correlated_ad_sam", "")
                      for r in self.iga_ident.values()}
        self.iga_holdings: dict[str, list[dict]] = {}
        for r in self.iga_ent:
            sam = sam_of_iga.get(r["iga_identity_id"], "")
            if sam:
                self.iga_holdings.setdefault(sam, []).append(r)

    # ------------------------------------------------------------------

    def _terminated(self, pn: str | None) -> bool:
        if not pn:
            return False
        h = self.hcm.get(pn)
        return bool(h and h["assignment_status"] == "TERMINATED")

    # -- per-rule truth sets -------------------------------------------

    def r1_leaver_not_deprovisioned(self) -> set[str]:
        """Accounts whose true owner is terminated but which remain live."""
        out = set()
        for sam, kind in self.kind_of_sam.items():
            if kind == "NHI" or sam not in self.ad:
                continue
            pn = self.owner_pn_of_sam.get(sam)
            if not self._terminated(pn):
                continue
            enabled = self.ad[sam].get("enabled") == "TRUE"
            if enabled or self.holdings.get(sam):
                out.add(sam)
        return out

    def r2_platform_state_disagreement(self) -> set[str]:
        """Accounts the platform calls disabled that the directory has enabled."""
        out = set()
        for iga in self.iga_ident.values():
            sam = iga.get("correlated_ad_sam", "")
            if not sam or sam not in self.ad:
                continue
            if (iga.get("lifecycle_state", "").upper() == "DISABLED"
                    and self.ad[sam].get("enabled") == "TRUE"):
                out.add(sam)
        return out

    def r3_orphaned_nhi(self) -> set[str]:
        """Non-human identities with no owner, or an owner who has left."""
        out = set()
        for sam, r in self.nhi.items():
            status = (r.get("owner_status") or "").upper()
            owner = r.get("owner_person_number") or ""
            if status in ("UNKNOWN", "TERMINATED") or not owner:
                out.add(sam)
        return out

    def r4_orphaned_admin_account(self) -> set[str]:
        """Administrative satellites with no live owner.

        Truth here is defined on the real owner, not the inferred one. An
        account whose true owner has no HR record is genuinely orphaned even
        if a correlation engine has tentatively attached it to somebody.
        """
        out = set()
        for sam, kind in self.kind_of_sam.items():
            if kind != "HUMAN_ADMIN" or sam not in self.ad:
                continue
            pn = self.owner_pn_of_sam.get(sam)
            if pn is None or self._terminated(pn):
                out.add(sam)
        return out

    def r5_sod_violation(self) -> set[tuple[str, str]]:
        """(account, sod_rule_id) pairs genuinely held.

        Scored as pairs rather than accounts: one identity can breach several
        rules, and collapsing to the account would hide the difference between
        catching one breach and catching all of them.
        """
        sod = json.load(open(self.estate / "ground_truth" / "sod_rules.json"))
        out = set()
        for sam, held in self.holdings.items():
            codes = {h["entitlement"] for h in held}
            for rule in sod:
                if {rule["left"], rule["right"]} <= codes:
                    out.add((sam, rule["id"]))
        return out

    def r6_coverage_gap(self) -> set[str]:
        """Applications holding privilege the platform does not aggregate."""
        agg, unagg = set(), set()
        for r in self.app_ent:
            (agg if r["aggregated_by_iga"] == "TRUE" else unagg).add(r["application"])
        return unagg - agg

    # ------------------------------------------------------------------

    def all_truth(self) -> dict:
        return {
            "R1_LEAVER_NOT_DEPROVISIONED": self.r1_leaver_not_deprovisioned(),
            "R2_PLATFORM_STATE_DISAGREEMENT": self.r2_platform_state_disagreement(),
            "R3_ORPHANED_NHI": self.r3_orphaned_nhi(),
            "R4_ORPHANED_ADMIN_ACCOUNT": self.r4_orphaned_admin_account(),
            "R5_SOD_VIOLATION": self.r5_sod_violation(),
            "R6_COVERAGE_GAP": self.r6_coverage_gap(),
        }

    def injected_subset(self) -> dict[str, set[str]]:
        """The deliberately planted faults, as a secondary check.

        A rule should detect at least these. Detecting more is correct, not a
        false positive — which is the error the injection log invites.
        """
        d = json.load(open(self.estate / "ground_truth" / "pathologies.json"))
        out: dict[str, set[str]] = {}
        for rec in d["pathologies"]:
            if rec.get("ad_sam"):
                out.setdefault(rec["pathology"], set()).add(rec["ad_sam"])
        return out
