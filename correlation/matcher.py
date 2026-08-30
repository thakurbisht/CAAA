"""
Tiered identity correlation.

Design principle: the assurance engine correlates **independently** and only
then compares its result to the IGA platform's own assertion. Using the
platform's correlation as an input would mean assuring a system with its own
output — the precise failure this control exists to avoid. Disagreements
between the two are therefore not noise; they are findings.

Tiers run strongest-first. A match at a strong tier is not re-litigated by a
weaker one. Corroborating attributes adjust confidence but never create a
link on their own.

Confidence is calibrated against one asymmetry: in an assurance context a
false link is worse than an unresolved account. A wrong correlation
attributes privilege to the wrong human, and any finding built on it is
wrong in a way that is difficult to detect downstream. Accounts below
threshold therefore go to adjudication rather than to a best guess.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from datetime import date

# --------------------------------------------------------------------------
# Tier definitions
# --------------------------------------------------------------------------

TIER_BASE = {
    "T1_EMPLOYEE_ID":     0.98,   # directory asserts the HR key directly
    "T2_UPN_LOCALPART":   0.90,   # deterministic naming convention holds
    "T3_NAME_DEPT":       0.78,   # normalised full name plus department
    "T4_NAME_FUZZY":      0.62,   # near-name, requires corroboration
    "T5_ADMIN_DERIVED":   0.88,   # satellite resolved to its base account
}

ACCEPT_THRESHOLD = 0.80          # at or above: assert the link
REVIEW_THRESHOLD = 0.55          # between: adjudication queue
                                 # below: leave unresolved


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def norm_name(s: str) -> str:
    """Fold a personal name to a comparable form."""
    s = strip_accents((s or "").lower())
    s = re.sub(r"[^a-z\s]", " ", s)
    return " ".join(s.split())


def name_tokens(first: str, last: str) -> frozenset[str]:
    return frozenset(norm_name(f"{first} {last}").split())


def local_part(upn: str) -> str:
    return (upn or "").split("@")[0].lower().strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Link:
    system: str              # HCM | AD | ENTRA | IGA
    key: str                 # person_number | sam | upn | iga_id
    tier: str
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class Cluster:
    """A set of accounts believed to belong to one human."""
    cluster_id: str
    account_type: str                       # HUMAN_PRIMARY | HUMAN_ADMIN | NHI | GUEST
    links: dict[str, Link] = field(default_factory=dict)
    confidence: float = 0.0
    status: str = "UNRESOLVED"              # RESOLVED | REVIEW | UNRESOLVED
    notes: list[str] = field(default_factory=list)
    parent_cluster_id: str | None = None    # admin satellite -> primary

    def key(self, system: str) -> str | None:
        l = self.links.get(system)
        return l.key if l else None


# --------------------------------------------------------------------------
# Matcher
# --------------------------------------------------------------------------

class CorrelationEngine:

    def __init__(self, hcm, ad, entra, iga, *,
                 accept=ACCEPT_THRESHOLD, review=REVIEW_THRESHOLD):
        self.hcm = hcm
        self.ad = ad
        self.entra = entra
        self.iga = iga
        self.accept = accept
        self.review = review

        self.clusters: list[Cluster] = []
        self.disagreements: list[dict] = []
        self._seq = 0

        self._index()

    # ------------------------------------------------------------- indices

    def _index(self):
        self.hcm_by_pn = {r["person_number"]: r for r in self.hcm}

        self.hcm_by_empid = {}
        for r in self.hcm:
            self.hcm_by_empid.setdefault(r["person_number"], []).append(r)

        # name+dept index, allowing for collisions
        self.hcm_by_namedept: dict[tuple, list] = {}
        self.hcm_by_name: dict[frozenset, list] = {}
        for r in self.hcm:
            toks = name_tokens(r["first_name"], r["last_name"])
            self.hcm_by_namedept.setdefault((toks, r["department"]), []).append(r)
            self.hcm_by_name.setdefault(toks, []).append(r)

        self.ad_by_sam = {r["sam_account_name"]: r for r in self.ad}
        self.entra_by_upn = {r["user_principal_name"].lower(): r for r in self.entra}
        self.entra_by_immutable = {
            r["on_premises_immutable_id"]: r for r in self.entra
            if r.get("on_premises_immutable_id")
        }
        self.iga_by_sam = {r["correlated_ad_sam"]: r for r in self.iga
                           if r.get("correlated_ad_sam")}
        self.iga_by_pn = {r["correlated_person_number"]: r for r in self.iga
                          if r.get("correlated_person_number")}

    def _cid(self) -> str:
        self._seq += 1
        return f"C{self._seq:06d}"

    # -------------------------------------------------------------- tiers

    def _t1_employee_id(self, ad_row) -> tuple[str, float, list[str]] | None:
        emp = (ad_row.get("employee_id") or "").strip()
        if not emp:
            return None
        hit = self.hcm_by_pn.get(emp)
        if not hit:
            # The directory asserts an HR key that HR does not recognise.
            # This is itself worth reporting.
            return ("__ORPHAN_EMPID__", 0.0,
                    [f"AD employee_id '{emp}' has no matching HCM person_number"])
        return (emp, TIER_BASE["T1_EMPLOYEE_ID"],
                [f"AD employee_id matches HCM person_number '{emp}'"])

    def _t2_upn_localpart(self, ad_row) -> tuple[str, float, list[str]] | None:
        lp = local_part(ad_row.get("user_principal_name", ""))
        if not lp or "." not in lp:
            return None
        first, _, last = lp.partition(".")
        last = re.sub(r"\d+$", "", last)           # disambiguating suffixes
        toks = frozenset([first, last]) - {""}
        cands = self.hcm_by_name.get(toks, [])
        if len(cands) != 1:
            return None
        return (cands[0]["person_number"], TIER_BASE["T2_UPN_LOCALPART"],
                [f"UPN local part '{lp}' resolves to a unique HCM name"])

    def _t3_name_dept(self, ad_row) -> tuple[str, float, list[str]] | None:
        dn = ad_row.get("display_name", "")
        parts = norm_name(dn).split()
        if len(parts) < 2:
            return None
        toks = frozenset(parts)
        dept = ad_row.get("department", "")
        cands = self.hcm_by_namedept.get((toks, dept), [])
        if len(cands) != 1:
            return None
        return (cands[0]["person_number"], TIER_BASE["T3_NAME_DEPT"],
                [f"display name and department '{dept}' resolve uniquely in HCM"])

    def _t4_name_fuzzy(self, ad_row) -> tuple[str, float, list[str]] | None:
        """Near-name matching for name changes and merger naming variants.

        Deliberately weak. On its own it lands in the review band; it only
        reaches acceptance when corroborated by department or hire date.
        """
        dn = norm_name(ad_row.get("display_name", ""))
        if not dn:
            return None

        scored = []
        for r in self.hcm:
            cand = norm_name(f"{r['first_name']} {r['last_name']}")
            sc = similarity(dn, cand)
            if sc >= 0.82:
                scored.append((sc, r))
        if not scored:
            return None

        scored.sort(key=lambda x: -x[0])
        best_score, best = scored[0]

        # Refuse on a tie. Where several HR records are equally similar,
        # picking the first is not a weak match — it is a coin flip that
        # produces a confidently-wrong link. The margin over the runner-up
        # is the actual evidence, so without one there is nothing to assert.
        if len(scored) > 1:
            runner_up = scored[1][0]
            margin = best_score - runner_up
            if margin < 0.02:
                tied = [r["person_number"] for sc, r in scored if best_score - sc < 0.02]
                return ("__AMBIGUOUS__", 0.0, [
                    f"name matches {len(tied)} HCM records equally well "
                    f"({', '.join(tied[:4])}): no basis to choose between them"
                ])
            conf = TIER_BASE["T4_NAME_FUZZY"] * (0.85 + 0.15 * best_score)
            return (best["person_number"], conf, [
                f"name similarity {best_score:.2f}, "
                f"{margin:.2f} clear of the next candidate"
            ])

        conf = TIER_BASE["T4_NAME_FUZZY"] * (0.85 + 0.15 * best_score)
        return (best["person_number"], conf,
                [f"name similarity {best_score:.2f}, sole candidate above threshold"])

    # ------------------------------------------------------- corroboration

    def _corroborate(self, ad_row, hcm_row) -> tuple[float, list[str]]:
        """Independent signals that raise or lower confidence in a link."""
        delta, ev = 0.0, []

        if ad_row.get("department") and hcm_row.get("department"):
            if ad_row["department"] == hcm_row["department"]:
                delta += 0.06
                ev.append("department agrees between AD and HCM")
            else:
                delta -= 0.10
                ev.append(
                    f"department disagrees: AD '{ad_row['department']}' "
                    f"vs HCM '{hcm_row['department']}'"
                )

        wc, hd = ad_row.get("when_created"), hcm_row.get("hire_date")
        if wc and hd:
            try:
                gap = abs((date.fromisoformat(wc) - date.fromisoformat(hd)).days)
                if gap <= 7:
                    delta += 0.05
                    ev.append("AD creation date within a week of hire date")
                elif gap > 365:
                    delta -= 0.04
                    ev.append(f"AD creation date {gap} days from hire date")
            except ValueError:
                pass

        return delta, ev

    # --------------------------------------------------------------- build

    def correlate(self) -> list[Cluster]:
        from .classify import classify_ad_account, classify_entra_account

        primary_by_sam: dict[str, Cluster] = {}
        admin_rows = []

        # ---- pass 1: AD accounts ----------------------------------------
        for ad_row in self.ad:
            sam = ad_row["sam_account_name"]
            cls = classify_ad_account(
                sam, ad_row.get("display_name", ""), ad_row.get("employee_id", ""))

            if cls.account_type == "NHI":
                c = Cluster(self._cid(), "NHI", confidence=cls.confidence,
                            status="RESOLVED", notes=[cls.reason])
                c.links["AD"] = Link("AD", sam, "CLASSIFIED", cls.confidence,
                                     [cls.reason])
                self._attach_iga(c, sam)
                self.clusters.append(c)
                continue

            if cls.account_type == "HUMAN_ADMIN":
                admin_rows.append((ad_row, cls))
                continue

            c = self._resolve_human(ad_row)
            primary_by_sam[sam] = c
            self.clusters.append(c)

        # ---- pass 2: admin satellites -----------------------------------
        # Resolved after primaries, because a satellite's identity is
        # derived from its base account rather than from HR data. This is
        # the link a person-number-keyed reconciliation can never make.
        for ad_row, cls in admin_rows:
            sam = ad_row["sam_account_name"]
            c = Cluster(self._cid(), "HUMAN_ADMIN", notes=[cls.reason])
            c.links["AD"] = Link("AD", sam, "T5_ADMIN_DERIVED",
                                 TIER_BASE["T5_ADMIN_DERIVED"], [cls.reason])

            base = primary_by_sam.get(cls.derived_owner_sam)
            if base:
                c.parent_cluster_id = base.cluster_id
                c.notes.append(
                    f"resolved to primary account '{cls.derived_owner_sam}'")

                if "HCM" in base.links:
                    # A derived link cannot be more certain than the link it
                    # is derived from. Inheriting the parent's confidence
                    # stops an error in the primary from being laundered into
                    # a high-confidence assertion about the privileged
                    # satellite — which is the account that matters most.
                    inherited = min(TIER_BASE["T5_ADMIN_DERIVED"], base.confidence)
                    c.confidence = inherited
                    c.links["HCM"] = Link(
                        "HCM", base.links["HCM"].key, "T5_ADMIN_DERIVED",
                        inherited,
                        [f"inherited from primary account, which resolved at "
                         f"{base.confidence:.2f} via {base.links['HCM'].tier}"])
                    c.status = ("RESOLVED" if inherited >= self.accept
                                else "REVIEW" if inherited >= self.review
                                else "UNRESOLVED")
                    if base.status != "RESOLVED":
                        c.notes.append(
                            "primary account is itself unresolved: this "
                            "privileged account inherits that uncertainty")
                else:
                    c.confidence = 0.40
                    c.status = "REVIEW"
                    c.notes.append(
                        "primary account exists but has no HCM link: "
                        "privileged account with an unidentified owner")
            else:
                c.confidence = 0.45
                c.status = "REVIEW"
                c.notes.append(
                    f"no primary account '{cls.derived_owner_sam}' in directory: "
                    "privileged account with no derivable owner")

            self._attach_iga(c, sam)
            self.clusters.append(c)

        # ---- pass 3: Entra-only populations -----------------------------
        linked_upns = {
            (c.key("ENTRA") or "").lower() for c in self.clusters if c.key("ENTRA")
        }
        for e_row in self.entra:
            upn = e_row["user_principal_name"].lower()
            if upn in linked_upns:
                continue
            ecls = classify_entra_account(
                upn, e_row.get("user_type", ""),
                e_row.get("on_premises_sync_enabled", ""),
                e_row.get("display_name", ""))
            c = Cluster(self._cid(), ecls.account_type,
                        confidence=ecls.confidence,
                        status="RESOLVED" if ecls.account_type in ("GUEST", "NHI") else "REVIEW",
                        notes=[ecls.reason, "no corresponding Local AD account"])
            c.links["ENTRA"] = Link("ENTRA", upn, "CLASSIFIED",
                                    ecls.confidence, [ecls.reason])
            self.clusters.append(c)

        self._detect_collisions()
        self._compare_with_iga()
        return self.clusters

    # ---------------------------------------------------------- collisions

    def _detect_collisions(self):
        """Two primary accounts claiming one human is a contradiction.

        At most one of the links can be right, so both are demoted to review
        rather than one being picked arbitrarily. Silently keeping the higher
        score would convert a visible contradiction into an invisible error.

        A rehire is the benign case — the same person legitimately holding an
        old and a new account — and is annotated as such rather than
        suppressed, because a rehire with an unretired prior account is
        exactly what a leaver reconciliation needs to see.
        """
        claims: dict[str, list[Cluster]] = {}
        for c in self.clusters:
            if c.account_type != "HUMAN_PRIMARY":
                continue
            pn = c.key("HCM")
            if pn:
                claims.setdefault(pn, []).append(c)

        for pn, cs in claims.items():
            if len(cs) < 2:
                continue
            sams = [c.key("AD") for c in cs]
            for c in cs:
                c.status = "REVIEW"
                c.confidence = min(c.confidence, self.accept - 0.01)
                others = [s for s in sams if s != c.key("AD")]
                c.notes.append(
                    f"contested: HCM person '{pn}' is also claimed by "
                    f"{', '.join(others)}. At most one link can be correct")

            self.disagreements.append({
                "type": "CONTESTED_HCM_RECORD",
                "ad_sam": " + ".join(sams),
                "iga_asserts": None,
                "independent_result": pn,
                "our_confidence": round(max(c.confidence for c in cs), 3),
                "tier": None,
            })

    # ---------------------------------------------------------- resolution

    def _resolve_human(self, ad_row) -> Cluster:
        sam = ad_row["sam_account_name"]
        c = Cluster(self._cid(), "HUMAN_PRIMARY")
        c.links["AD"] = Link("AD", sam, "SOURCE", 1.0, ["Local AD account"])

        for tier, fn in (
            ("T1_EMPLOYEE_ID", self._t1_employee_id),
            ("T2_UPN_LOCALPART", self._t2_upn_localpart),
            ("T3_NAME_DEPT", self._t3_name_dept),
            ("T4_NAME_FUZZY", self._t4_name_fuzzy),
        ):
            res = fn(ad_row)
            if not res:
                continue
            pn, base_conf, ev = res

            if pn in ("__ORPHAN_EMPID__", "__AMBIGUOUS__"):
                c.notes.extend(ev)
                continue

            hcm_row = self.hcm_by_pn.get(pn)
            delta, corr_ev = self._corroborate(ad_row, hcm_row) if hcm_row else (0.0, [])
            conf = min(0.99, base_conf + delta)

            c.links["HCM"] = Link("HCM", pn, tier, conf, ev + corr_ev)
            c.confidence = conf
            break

        if "HCM" not in c.links:
            c.confidence = 0.0
            c.status = "UNRESOLVED"
            c.notes.append(
                "no HCM record matched by any tier: contingent worker, "
                "vendor, or an identity absent from the authoritative source")
        else:
            c.status = ("RESOLVED" if c.confidence >= self.accept
                        else "REVIEW" if c.confidence >= self.review
                        else "UNRESOLVED")
            if c.status != "RESOLVED":
                # An adjudication queue without reasons cannot be worked.
                # State the candidate, the tier that produced it, and the
                # shortfall, so the reviewer knows what to check rather than
                # having to redo the match by hand.
                link = c.links["HCM"]
                c.notes.append(
                    f"candidate '{link.key}' matched at {c.confidence:.2f} via "
                    f"{link.tier}, short of the {self.accept:.2f} acceptance "
                    f"threshold. Evidence: {'; '.join(link.evidence)}"
                )

        self._attach_entra(c, ad_row)
        self._attach_iga(c, sam)
        return c

    def _attach_entra(self, c: Cluster, ad_row):
        """Entra is joined on the sync anchor, falling back to UPN local part."""
        imm = None
        sam = ad_row["sam_account_name"]

        e = self.entra_by_upn.get(f"{sam}@healthgroup.ae")
        if e:
            c.links["ENTRA"] = Link(
                "ENTRA", e["user_principal_name"].lower(), "T2_UPN_LOCALPART",
                0.92, ["Entra UPN local part matches sAMAccountName"])
            return

        for upn, row in self.entra_by_upn.items():
            if local_part(upn) == sam:
                c.links["ENTRA"] = Link("ENTRA", upn, "T2_UPN_LOCALPART", 0.90,
                                        ["Entra UPN local part matches sAMAccountName"])
                return

        if ad_row.get("in_sync_scope") == "FALSE":
            c.notes.append(
                "AD object is outside directory sync scope: no Entra counterpart "
                "expected, and none governed")

    def _attach_iga(self, c: Cluster, sam: str):
        row = self.iga_by_sam.get(sam)
        if row:
            c.links["IGA"] = Link("IGA", row["iga_identity_id"], "PLATFORM_ASSERTED",
                                  0.99, ["IGA register carries this account"])
        else:
            c.notes.append("no IGA identity carries this account")

    # ------------------------------------------------- independent compare

    def _compare_with_iga(self):
        """Compare our independent correlation against the platform's own.

        The platform's `correlated_person_number` is its assertion about who
        an account belongs to. Where our correlation differs, one of the two
        is wrong — and either way it is a finding, because certification
        decisions are routed on the platform's answer.
        """
        for c in self.clusters:
            sam = c.key("AD")
            if not sam:
                continue
            iga_row = self.iga_by_sam.get(sam)
            if not iga_row:
                continue

            iga_pn = (iga_row.get("correlated_person_number") or "").strip()

            # Only links the engine actually stands behind may contradict the
            # platform. A candidate still in adjudication is not an assertion,
            # and raising it as a conflict would manufacture a finding the
            # engine cannot defend — the same overstatement this control
            # exists to prevent.
            our_pn = c.key("HCM") if c.status == "RESOLVED" else None
            tentative = c.key("HCM") if c.status != "RESOLVED" else None

            if iga_pn and our_pn and iga_pn != our_pn:
                self.disagreements.append({
                    "type": "CORRELATION_CONFLICT",
                    "ad_sam": sam,
                    "iga_asserts": iga_pn,
                    "independent_result": our_pn,
                    "our_confidence": round(c.confidence, 3),
                    "tier": c.links["HCM"].tier,
                })
            elif iga_pn and not our_pn:
                # The platform asserts an owner the engine could not confirm.
                # Distinguished by whether a candidate exists at all: a
                # candidate that merely fell short of threshold is a weaker
                # finding than no supporting evidence whatsoever.
                self.disagreements.append({
                    "type": ("IGA_ASSERTS_LINK_BELOW_OUR_THRESHOLD" if tentative
                             else "IGA_ASSERTS_UNVERIFIABLE_LINK"),
                    "ad_sam": sam,
                    "iga_asserts": iga_pn,
                    "independent_result": tentative,
                    "our_confidence": round(c.confidence, 3),
                    "tier": c.links["HCM"].tier if tentative else None,
                })
            elif our_pn and not iga_pn:
                self.disagreements.append({
                    "type": "IGA_UNCORRELATED_BUT_RESOLVABLE",
                    "ad_sam": sam,
                    "iga_asserts": None,
                    "independent_result": our_pn,
                    "our_confidence": round(c.confidence, 3),
                    "tier": c.links["HCM"].tier,
                })
