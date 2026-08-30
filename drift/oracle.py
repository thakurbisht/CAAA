"""
Derived truth for drift rules.

Same principle as Phase 3: the injection log records what was planted, not
what is true at the snapshot being examined. For drift this matters more, not
less, because drift conditions decay. A transfer injected in month one is
healed if the worker transfers back in month four, and a retained entitlement
is no longer retained once somebody revokes it.

The oracle recomputes each condition from the estate with perfect knowledge of
identity, so the gap between oracle and rule measures what the rules cost in
detection rather than what the generator happened to log.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path


class DriftOracle:

    def __init__(self, estate_dir: Path, timeline):
        self.estate = estate_dir
        self.t = timeline

        imap = json.load(open(estate_dir / "ground_truth" / "identity_map.json"))
        self.by_pid = {r["pid"]: r for r in imap}
        self.sam_of_pn = {}
        for r in imap:
            if r["ad_sam"] and r["person_number"] and r["in_hcm"]:
                self.sam_of_pn[r["person_number"]] = r["ad_sam"]

    # ------------------------------------------------------------------

    def d1_retained_on_transfer(self, grace_days: int = 30) -> set[str]:
        """Accounts whose true owner transferred and still holds prior-department
        access, using the real account mapping rather than an inferred one."""
        from .rules import learn_department_scoped
        t = self.t
        out = set()
        scoped = learn_department_scoped(t)
        transfers = t.observed_transfers()

        by_person = {}
        for tr in transfers:
            by_person.setdefault(tr["person_number"], []).append(tr)

        for pn, moves in by_person.items():
            moves.sort(key=lambda m: m["observed_on"])
            if moves[-1]["days_since"] < grace_days:
                continue
            sam = self.sam_of_pn.get(pn)
            if not sam:
                continue
            current = t.dept_series[-1].get(pn)
            prior = {m["from_dept"] for m in moves} - {current}
            for h in t.holdings.get(sam, []):
                sc = scoped.get(h["entitlement"])
                if sc and sc in prior and sc != current:
                    out.add(sam)
                    break
        return out

    def d3_dormant_entitlement(self, dormant_days: int = 180) -> set[tuple[str, str]]:
        """(account, entitlement) pairs genuinely dormant at the snapshot."""
        t = self.t
        out = set()
        human_sams = {c["ad_sam"] for c in t.clusters
                      if c["ad_sam"] and c["account_type"] != "NHI"}
        for sam in human_sams:
            for h in t.holdings.get(sam, []):
                if h["risk"] not in ("CRITICAL", "HIGH"):
                    continue
                last, granted = h.get("last_used") or "", h.get("granted_on") or ""
                if last:
                    if (t.as_of - date.fromisoformat(last)).days >= dormant_days:
                        out.add((sam, h["entitlement"]))
                elif granted:
                    if (t.as_of - date.fromisoformat(granted)).days >= dormant_days:
                        out.add((sam, h["entitlement"]))
        return out

    def d4_standing_breakglass(self, breakglass: set[str]) -> set[str]:
        """Accounts holding a break-glass entitlement in every snapshot."""
        t = self.t
        seen = {}
        for s in t.snapshots:
            for r in csv.DictReader(open(s / "app_entitlements.csv", newline="")):
                if r["entitlement"] in breakglass:
                    seen[(r["ad_sam"], r["entitlement"])] = \
                        seen.get((r["ad_sam"], r["entitlement"]), 0) + 1
        return {sam for (sam, _), n in seen.items() if n == len(t.snapshots)}

    def live_injected(self, pathology: str, *, require_active: bool = True,
                      grace_days: int | None = None) -> set[str]:
        """Planted faults that the rule is actually claiming to have assessed.

        Two exclusions, both of which a naive recall figure gets wrong:

        A fault stops being drift once the worker leaves. The condition is
        then a leaver finding and belongs to R1; counting it here would
        double-count it and blame the drift rule for someone else's job. Of
        70 planted retentions, 36 belonged to workers since terminated.

        A fault inside the grace period has not been judged yet. Where
        `grace_days` is given, transfers observed more recently than that are
        removed, because the rule deliberately withholds judgement on them.
        Measuring against them reports the grace period as a failure: of 30
        apparently missed retentions, nearly all had transferred within days
        of the final snapshot.
        """
        d = json.load(open(self.estate / "ground_truth" / "pathologies.json"))
        hcm = self.t.hcm
        recent: set[str] = set()

        if grace_days is not None:
            by_pn = {}
            for tr in self.t.observed_transfers():
                cur = by_pn.get(tr["person_number"])
                if cur is None or tr["observed_on"] > cur["observed_on"]:
                    by_pn[tr["person_number"]] = tr
            for pn, tr in by_pn.items():
                if tr["days_since"] < grace_days:
                    sam = self.sam_of_pn.get(pn)
                    if sam:
                        recent.add(sam)

        out = set()
        for rec in d["pathologies"]:
            if rec["pathology"] != pathology or not rec.get("ad_sam"):
                continue
            if rec["ad_sam"] in recent:
                continue
            pn = rec.get("person_number")
            if require_active:
                row = hcm.get(pn) if pn else None
                if not row or row["assignment_status"] != "ACTIVE_ASSIGN":
                    continue
            out.add(rec["ad_sam"])
        return out

    def injected_subset(self) -> dict:
        d = json.load(open(self.estate / "ground_truth" / "pathologies.json"))
        out = {}
        for rec in d["pathologies"]:
            if rec.get("ad_sam"):
                out.setdefault(rec["pathology"], set()).add(rec["ad_sam"])
        return out
