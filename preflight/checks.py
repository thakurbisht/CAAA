"""
Pre-flight data quality checks.

Every rule in this pipeline depends on assumptions about the feeds it reads.
On the synthetic estate those assumptions hold by construction, because the
generator was written to satisfy them. On a real estate they may not, and a
rule whose assumption has quietly failed does not error — it produces
confident, wrong findings.

The specific failure that motivated this: R1 treats an HCM termination date as
the date somebody left. In many HR systems the record is created weeks after
the fact with an effective date backdated. R1 would then report an account as
live 45 days after termination when the organisation only learned of it
yesterday. The finding is technically true and operationally useless, and a
remediation team shown a few of those stops reading the report.

So this runs first, on the raw extracts, before any rule. It scores nothing.
It states what the data looks like, names the assumptions that data does not
support, and says which rules are affected.

Three verdicts:
  SUPPORTED    the assumption holds well enough to rely on
  DEGRADED     it holds partially; findings need a caveat
  UNSUPPORTED  it does not hold; the affected rules should not be run
  UNKNOWN      the feed or column needed to test it is absent
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Check:
    id: str
    assumption: str
    verdict: str                    # SUPPORTED | DEGRADED | UNSUPPORTED | UNKNOWN
    detail: str
    affects: list[str]
    evidence: dict = field(default_factory=dict)
    advice: str = ""


VERDICT_ORDER = {"UNSUPPORTED": 0, "UNKNOWN": 1, "DEGRADED": 2, "SUPPORTED": 3}


class Extracts:
    """Raw feeds, loaded tolerantly.

    Real extracts will not match the synthetic column names exactly. Missing
    files and columns are recorded rather than raised, because "we could not
    test this" is a useful answer and a stack trace is not.
    """

    FEEDS = {
        "hcm": "hcm_workers.csv",
        "ad": "ad_accounts.csv",
        "entra": "entra_accounts.csv",
        "iga": "iga_identities.csv",
        "iga_ent": "iga_entitlements.csv",
        "app_ent": "app_entitlements.csv",
        "nhi": "nhi_register.csv",
    }

    def __init__(self, snapshot_dir: Path, previous: Path | None = None,
                 profile=None):
        self.dir = snapshot_dir
        self.previous = previous
        self.profile = profile

        from feeds import load_feeds, DEFAULT_FILES
        self.data = load_feeds(snapshot_dir, profile)
        files = (profile.files if profile else DEFAULT_FILES)
        self.missing = [files[k] for k, v in self.data.items() if not v]

    def rows(self, feed: str) -> list[dict]:
        return self.data.get(feed, [])

    def has(self, feed: str, col: str) -> bool:
        rows = self.rows(feed)
        return bool(rows) and col in rows[0]

    def values(self, feed: str, col: str) -> list[str]:
        return [r.get(col, "") for r in self.rows(feed)] if self.has(feed, col) else []


# ==========================================================================
# Checks
# ==========================================================================

def check_termination_recency(x: Extracts) -> Check:
    """R1 reads a termination date as the date somebody left.

    If HR records terminations well after the fact, that date is when the
    person left, not when anyone knew. Findings aged from it overstate how
    long access was open.

    The tell is a record-creation or last-modified column. Without one the
    lag cannot be measured at all, and that is itself the finding: R1's age
    figures are unverifiable.
    """
    affects = ["R1_LEAVER_NOT_DEPROVISIONED", "R4_ORPHANED_ADMIN_ACCOUNT"]

    stamp_cols = [c for c in ("record_created", "last_update_date",
                              "created_on", "effective_start_date")
                  if x.has("hcm", c)]

    if not x.has("hcm", "termination_date"):
        return Check("TERMINATION_DATE", "HCM carries a termination date",
                     "UNKNOWN", "No termination_date column in the HCM extract.",
                     affects,
                     advice="Without it no leaver check is possible at all.")

    terms = [t for t in x.values("hcm", "termination_date") if t]

    if not stamp_cols:
        return Check(
            "TERMINATION_RECENCY",
            "A termination date reflects when the organisation knew, not just "
            "when the person left",
            "UNKNOWN",
            f"{len(terms):,} terminations present, but the extract carries no "
            f"record-creation or last-modified column, so the lag between the "
            f"effective date and the date HR recorded it cannot be measured.",
            affects,
            evidence={"terminations": len(terms),
                      "timestamp_columns_found": []},
            advice=(
                "Ask HR whether termination records are commonly backdated. If "
                "they are, the 'days since termination' figure in every leaver "
                "finding is the age of the event, not the age of the exposure, "
                "and should be labelled as such. Adding a creation timestamp to "
                "the extract removes the ambiguity."),
        )

    col = stamp_cols[0]
    lags, backdated = [], 0
    for r in x.rows("hcm"):
        t, c = r.get("termination_date", ""), r.get(col, "")
        if not t or not c:
            continue
        try:
            lag = (date.fromisoformat(c[:10]) - date.fromisoformat(t[:10])).days
        except ValueError:
            continue
        lags.append(lag)
        if lag > 3:
            backdated += 1

    if not lags:
        return Check("TERMINATION_RECENCY",
                     "Termination dates can be aged against when they were recorded",
                     "UNKNOWN", f"Column {col} present but unparseable.", affects)

    lags.sort()
    median = lags[len(lags) // 2]
    share = backdated / len(lags)

    if share < 0.10:
        verdict, detail = "SUPPORTED", (
            f"{share:.0%} of terminations were recorded more than three days "
            f"after their effective date (median lag {median} days). Ageing a "
            f"leaver finding from the termination date is sound.")
        advice = ""
    elif share < 0.40:
        verdict, detail = "DEGRADED", (
            f"{share:.0%} of terminations were recorded late, median lag "
            f"{median} days. Some leaver findings will overstate exposure.")
        advice = ("Report age from the recorded date, not the effective date, "
                  "or state both.")
    else:
        verdict, detail = "UNSUPPORTED", (
            f"{share:.0%} of terminations were backdated, median lag {median} "
            f"days. A finding reading '45 days since termination' will often "
            f"mean the organisation learned of it this week.")
        advice = ("Age findings from the record-creation date. As written, R1's "
                  "age figures will not survive challenge by the remediation team.")

    return Check("TERMINATION_RECENCY",
                 "A termination date reflects when the organisation knew",
                 verdict, detail, affects,
                 evidence={"sampled": len(lags), "median_lag_days": median,
                           "backdated_share": round(share, 3),
                           "timestamp_column": col},
                 advice=advice)


def check_employee_id_semantics(x: Extracts) -> Check:
    """Tier 1 correlation assumes AD employee_id means the HR person number.

    After a merger that field routinely holds two different things — a
    contractor number from one directory, an HR number from the other. Tier 1
    then asserts a wrong link at its highest confidence, which is worse than
    having no identifier at all.
    """
    affects = ["correlation T1_EMPLOYEE_ID", "every rule keyed on cluster_id"]

    if not x.has("ad", "employee_id"):
        return Check("EMPLOYEE_ID", "AD carries an HR identifier", "UNKNOWN",
                     "No employee_id column in the AD extract.", affects,
                     advice="Correlation falls back to weaker name-based tiers.")

    ids = [v.strip() for v in x.values("ad", "employee_id") if v.strip()]
    total = len(x.rows("ad"))
    fill = len(ids) / total if total else 0

    def shape(v):
        return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "A", v))

    shapes = Counter(shape(v) for v in ids)
    dominant = shapes.most_common(1)[0] if shapes else ("", 0)
    dominant_share = dominant[1] / len(ids) if ids else 0

    pns = {r.get("person_number", "").strip()
           for r in x.rows("hcm") if r.get("person_number")}
    orphans = [i for i in ids if pns and i not in pns]
    orphan_share = len(orphans) / len(ids) if ids else 0

    dupes = [i for i, n in Counter(ids).items() if n > 1]

    ev = {
        "fill_rate": round(fill, 3),
        "distinct_formats": len(shapes),
        "dominant_format": dominant[0],
        "dominant_format_share": round(dominant_share, 3),
        "not_matching_any_hcm_person": len(orphans),
        "orphan_share": round(orphan_share, 3),
        "duplicated_across_accounts": len(dupes),
        "format_breakdown": dict(shapes.most_common(6)),
    }

    if len(shapes) > 1 and dominant_share < 0.90:
        return Check(
            "EMPLOYEE_ID", "AD employee_id consistently means the HR person number",
            "UNSUPPORTED",
            f"{len(shapes)} distinct identifier formats in the field; the most "
            f"common accounts for only {dominant_share:.0%}. This field is "
            f"holding more than one kind of identifier.",
            affects, ev,
            advice=(
                "Tier 1 will assert wrong links at 0.98 confidence, which is "
                "worse than no identifier. Establish which format belongs to "
                "which population, and either restrict Tier 1 to the format "
                "that is genuinely the HR number or drop the tier."),
        )

    if orphan_share > 0.15:
        return Check(
            "EMPLOYEE_ID", "AD employee_id consistently means the HR person number",
            "DEGRADED",
            f"{orphan_share:.0%} of populated identifiers match no worker in "
            f"HCM. Either the extract is scoped differently or the field holds "
            f"identifiers from another system.",
            affects, ev,
            advice=("Check whether the HCM extract covers the same population "
                    "as the directory before trusting Tier 1."),
        )

    if fill < 0.40:
        return Check(
            "EMPLOYEE_ID", "AD employee_id consistently means the HR person number",
            "DEGRADED",
            f"Only {fill:.0%} of accounts carry an identifier. Correlation will "
            f"lean on weaker name-based tiers for the rest.",
            affects, ev,
            advice="Expect a larger adjudication queue and calibrate accordingly.",
        )

    return Check("EMPLOYEE_ID", "AD employee_id consistently means the HR person number",
                 "SUPPORTED",
                 f"{fill:.0%} populated, {len(shapes)} format(s), "
                 f"{orphan_share:.0%} matching no HR record.",
                 affects, ev)


def check_naming_conventions(x: Extracts) -> Check:
    """Tiers 2 and 4 assume account names follow a discoverable convention.

    Several conventions in one estate is normal after a merger. The problem is
    not their existence but their number: fuzzy matching across three
    conventions produces more ambiguous candidates, and the engine refuses
    ambiguity rather than guessing, so the adjudication queue grows.
    """
    affects = ["correlation T2_UPN_LOCALPART", "correlation T4_NAME_FUZZY"]

    if not x.has("ad", "sam_account_name"):
        return Check("NAMING", "Account names follow a convention", "UNKNOWN",
                     "No sam_account_name column.", affects)

    sams = [s for s in x.values("ad", "sam_account_name") if s]
    patterns = Counter()
    for s in sams:
        low = s.lower()
        if re.match(r"^[a-z]+\.[a-z]+\d*$", low):
            patterns["first.last"] += 1
        elif re.match(r"^[a-z]\.[a-z]+$", low):
            patterns["f.last"] += 1
        elif re.match(r"^[a-z]{2,}\d*$", low) and "." not in low:
            patterns["flast or single token"] += 1
        elif "-" in low:
            patterns["prefixed (service or admin)"] += 1
        else:
            patterns["other"] += 1

    human = {k: v for k, v in patterns.items()
             if k != "prefixed (service or admin)"}
    total = sum(human.values()) or 1
    top = max(human.values()) / total if human else 0
    ev = {"patterns": dict(patterns), "dominant_share": round(top, 3)}

    if top >= 0.85:
        return Check("NAMING", "Account names follow a discoverable convention",
                     "SUPPORTED",
                     f"One convention covers {top:.0%} of human accounts.",
                     affects, ev)
    if top >= 0.55:
        return Check("NAMING", "Account names follow a discoverable convention",
                     "DEGRADED",
                     f"The commonest convention covers {top:.0%} of human "
                     f"accounts; {len(human)} are in use.",
                     affects, ev,
                     advice=("Expect a larger adjudication queue. Re-run "
                             "calibrate.py rather than carrying the 0.80 "
                             "threshold over from the synthetic estate."))
    return Check("NAMING", "Account names follow a discoverable convention",
                 "UNSUPPORTED",
                 f"No convention covers more than {top:.0%} of accounts; "
                 f"{len(human)} are in use.",
                 affects, ev,
                 advice=("Name-based tiers will resolve little and refuse much. "
                         "Correlation will depend almost entirely on the HR "
                         "identifier."))


def check_aggregation_scope(x: Extracts) -> Check:
    """R6 can only report a gap in an application it can see.

    This is the structural limit of the whole coverage check and it does not
    go away with better code. On the synthetic estate the generator writes
    both the platform's view and reality, so the difference is visible. In
    production, an application nobody aggregates produces no feed — so it
    cannot appear in a comparison of feeds.

    R6 is therefore an inventory comparison, not an entitlement analysis, and
    it needs a source of truth about which applications exist that does not
    come from the identity platform.
    """
    affects = ["R6_COVERAGE_GAP"]

    apps_seen = {r.get("application", "") for r in x.rows("app_ent")}
    apps_seen.discard("")
    iga_apps = {r.get("application", "") for r in x.rows("iga_ent")}
    iga_apps.discard("")

    ev = {"applications_in_holdings_feed": sorted(apps_seen),
          "applications_in_platform_feed": sorted(iga_apps),
          "in_holdings_not_in_platform": sorted(apps_seen - iga_apps)}

    return Check(
        "AGGREGATION_SCOPE",
        "The holdings feed covers applications the platform does not aggregate",
        "DEGRADED",
        f"The extract shows {len(apps_seen)} applications, of which "
        f"{len(apps_seen - iga_apps)} do not appear in the platform's own feed. "
        f"That comparison only finds gaps in applications already being "
        f"extracted from — an application nobody collects from produces no "
        f"feed and cannot appear here at all.",
        affects, ev,
        advice=(
            "Treat R6 as an inventory comparison rather than a data one. Take "
            "the list of applications holding privileged access from an "
            "architecture register or CMDB, take the list of connected sources "
            "from the platform, and compare those. The difference is the "
            "finding, and it needs no pipeline."),
    )


def check_usage_data(x: Extracts) -> Check:
    """D3 ages entitlements against a last-used date."""
    affects = ["D3_DORMANT_ENTITLEMENT"]

    if not x.has("app_ent", "last_used"):
        return Check("USAGE_DATA", "Entitlement usage is recorded", "UNSUPPORTED",
                     "No last_used column in the holdings extract.", affects,
                     advice=("Dormancy cannot be assessed. Either source usage "
                             "data or drop D3 rather than reporting grants as "
                             "dormant on the strength of their age alone."))

    vals = x.values("app_ent", "last_used")
    filled = sum(1 for v in vals if v.strip())
    share = filled / len(vals) if vals else 0
    ev = {"fill_rate": round(share, 3), "holdings": len(vals)}

    if share >= 0.80:
        return Check("USAGE_DATA", "Entitlement usage is recorded", "SUPPORTED",
                     f"{share:.0%} of holdings carry a usage date.", affects, ev)
    if share >= 0.40:
        return Check("USAGE_DATA", "Entitlement usage is recorded", "DEGRADED",
                     f"Only {share:.0%} of holdings carry a usage date.",
                     affects, ev,
                     advice=("Absence of a usage record is not evidence of "
                             "non-use. Report never-used and idle separately, "
                             "and caveat the former."))
    return Check("USAGE_DATA", "Entitlement usage is recorded", "UNSUPPORTED",
                 f"{share:.0%} of holdings carry a usage date.", affects, ev,
                 advice="D3 would report mostly missing data as dormancy.")


def check_time_series(x: Extracts) -> Check:
    """Drift needs the HR feed to actually change between snapshots."""
    affects = ["D1_RETAINED_ON_TRANSFER", "D4_STANDING_BREAKGLASS"]

    if x.previous is None:
        return Check("TIME_SERIES", "Consecutive extracts differ", "UNKNOWN",
                     "Only one snapshot supplied; drift cannot be assessed.",
                     affects,
                     advice=("Retain extracts on a schedule. Drift needs at "
                             "least 60 days of history before it reports "
                             "anything meaningful."))

    from feeds import load_feeds
    prev = {r["person_number"]: r.get("department", "")
            for r in load_feeds(x.previous, x.profile)["hcm"]
            if r.get("person_number")}
    cur = {r["person_number"]: r.get("department", "")
           for r in x.rows("hcm") if r.get("person_number")}

    common = set(prev) & set(cur)
    changed = [p for p in common if prev[p] != cur[p]]
    ev = {"workers_in_both": len(common), "department_changes": len(changed)}

    if not common:
        return Check("TIME_SERIES", "Consecutive extracts describe the same population",
                     "UNSUPPORTED",
                     "No worker appears in both extracts; they cannot be compared.",
                     affects, ev)
    if not changed:
        return Check(
            "TIME_SERIES", "Consecutive extracts differ", "UNSUPPORTED",
            f"No department changed across {len(common):,} workers present in "
            f"both extracts. Either no transfers occurred, or the extract "
            f"reports current state rather than state as at the snapshot date.",
            affects, ev,
            advice=(
                "Check how the extract is produced. A report that always "
                "returns today's department gives a flat history and makes "
                "transfer detection impossible — the same defect this project's "
                "own generator had until Phase 4 exposed it."),
        )

    return Check("TIME_SERIES", "Consecutive extracts differ", "SUPPORTED",
                 f"{len(changed)} department changes across {len(common):,} "
                 f"workers present in both extracts.", affects, ev)


def check_peer_groups(x: Extracts) -> Check:
    """D2 compares a worker against others in the same job and department."""
    affects = ["D2_PEER_GROUP_OUTLIER"]

    if not (x.has("hcm", "job_code") and x.has("hcm", "department")):
        return Check("PEER_GROUPS", "Job code and department form usable peer groups",
                     "UNKNOWN", "job_code or department column missing.", affects)

    groups = Counter((r.get("job_code", ""), r.get("department", ""))
                     for r in x.rows("hcm")
                     if r.get("assignment_status", "ACTIVE_ASSIGN") == "ACTIVE_ASSIGN")
    sizes = sorted(groups.values())
    usable = sum(n for n in sizes if n >= 8)
    total = sum(sizes)
    share = usable / total if total else 0
    ev = {"groups": len(groups), "median_group_size": sizes[len(sizes) // 2] if sizes else 0,
          "workers_in_groups_of_8_or_more": usable, "workers_total": total,
          "comparable_share": round(share, 3)}

    if share >= 0.70:
        return Check("PEER_GROUPS", "Job code and department form usable peer groups",
                     "SUPPORTED",
                     f"{share:.0%} of active workers sit in a group of eight or "
                     f"more.", affects, ev)
    if share >= 0.35:
        return Check("PEER_GROUPS", "Job code and department form usable peer groups",
                     "DEGRADED",
                     f"Only {share:.0%} of active workers sit in a group large "
                     f"enough to compare against.", affects, ev,
                     advice=("Consider grouping by job family rather than exact "
                             "job code, and state the reduced coverage."))
    return Check("PEER_GROUPS", "Job code and department form usable peer groups",
                 "UNSUPPORTED",
                 f"Only {share:.0%} of workers sit in a comparable group; the "
                 f"median group holds {ev['median_group_size']}.", affects, ev,
                 advice=("In groups this small every member is an outlier by "
                         "construction. D2 would produce noise."))


ALL_CHECKS = [
    check_termination_recency,
    check_employee_id_semantics,
    check_naming_conventions,
    check_aggregation_scope,
    check_usage_data,
    check_time_series,
    check_peer_groups,
]


def run_checks(x: Extracts) -> list[Check]:
    out = []
    for fn in ALL_CHECKS:
        try:
            out.append(fn(x))
        except Exception as e:
            out.append(Check(fn.__name__, "check could not run", "UNKNOWN",
                             f"{type(e).__name__}: {e}", []))
    return sorted(out, key=lambda c: VERDICT_ORDER.get(c.verdict, 9))
