"""
Feed loading and column mapping.

Six modules each opened the feed files themselves, with the synthetic
generator's filenames and column names written directly into the code. That
was invisible while the only input was the generator's own output, and it
meant a real extract could not be used at all without editing six files.

This owns both. A real extract names its columns whatever the source system
names them — Oracle HCM does not emit `person_number`, and an LDAP dump does
not emit `sam_account_name` unless somebody asked it to. The mapping lives in
a profile so that adapting to an estate is a config change rather than a code
change.

The canonical names are the ones the rules use. Everything else is an alias
that gets renamed on load, once, at the boundary.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


# Canonical feed names and the file each is expected in. A profile may
# override any of these.
DEFAULT_FILES = {
    "hcm": "hcm_workers.csv",
    "ad": "ad_accounts.csv",
    "entra": "entra_accounts.csv",
    "iga": "iga_identities.csv",
    "iga_ent": "iga_entitlements.csv",
    "app_ent": "app_entitlements.csv",
    "nhi": "nhi_register.csv",
}

# The columns the rules actually read, per feed. Anything not listed here is
# carried through untouched — extra columns are harmless, missing ones are
# what the pre-flight guard reports on.
REQUIRED = {
    "hcm": ["person_number", "first_name", "last_name", "department",
            "job_code", "assignment_status"],
    "ad": ["sam_account_name", "display_name", "employee_id", "enabled",
           "department"],
    "entra": ["user_principal_name", "user_type"],
    "iga": ["iga_identity_id", "correlated_ad_sam", "lifecycle_state"],
    "app_ent": ["ad_sam", "entitlement", "application", "risk"],
    "nhi": ["ad_sam", "owner_status"],
}


@dataclass
class Profile:
    """How one organisation's extract maps onto the canonical names.

    A profile is data, not code. Adapting the pipeline to an estate whose
    HR system calls the key `EmployeeNumber` and whose directory calls the
    login `sAMAccountName` should not require touching a rule.
    """
    name: str = "default"
    files: dict = field(default_factory=lambda: dict(DEFAULT_FILES))
    columns: dict = field(default_factory=dict)   # feed -> {source: canonical}
    values: dict = field(default_factory=dict)    # feed -> col -> {source: canonical}

    @classmethod
    def load(cls, path: Path) -> "Profile":
        d = json.load(open(path))
        return cls(
            name=d.get("name", path.stem),
            files={**DEFAULT_FILES, **d.get("files", {})},
            columns=d.get("columns", {}),
            values=d.get("values", {}),
        )

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"name": self.name, "files": self.files,
                   "columns": self.columns, "values": self.values},
                  open(path, "w"), indent=2)


def _apply(rows: list[dict], feed: str, profile: Profile) -> list[dict]:
    colmap = profile.columns.get(feed, {})
    valmap = profile.values.get(feed, {})
    if not colmap and not valmap:
        return rows

    out = []
    for r in rows:
        n = {colmap.get(k, k): v for k, v in r.items()}
        for col, mapping in valmap.items():
            if col in n and n[col] in mapping:
                n[col] = mapping[n[col]]
        out.append(n)
    return out


def load_feeds(snapshot_dir: Path, profile: Profile | None = None) -> dict:
    """Read every feed present, renaming columns and values to canonical form.

    Missing files return an empty list rather than raising. A real extract
    will not carry all seven feeds, and "this feed is absent" is something the
    guard reports and the rules declare as reduced coverage — it is not a
    crash.
    """
    p = profile or Profile()
    out = {}
    for feed, filename in p.files.items():
        path = snapshot_dir / filename
        if not path.exists():
            out[feed] = []
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            out[feed] = _apply(list(csv.DictReader(f)), feed, p)
    return out


def missing_columns(feeds: dict) -> dict[str, list[str]]:
    """Canonical columns the rules need that the extract does not supply."""
    out = {}
    for feed, cols in REQUIRED.items():
        rows = feeds.get(feed) or []
        if not rows:
            continue
        absent = [c for c in cols if c not in rows[0]]
        if absent:
            out[feed] = absent
    return out


def suggest_profile(snapshot_dir: Path) -> Profile:
    """Draft a profile by guessing which source column maps to which canonical.

    Deliberately a starting point rather than an answer. The guesses are made
    on normalised names only, and every one is written into the profile for a
    human to confirm or correct — an inferred mapping that silently attaches
    the wrong column would be the same class of error as a wrong correlation,
    and harder to notice because it happens once at load.
    """
    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())

    # Aliases are scoped per feed, not global. `employeeID` means the HR key
    # in an HR extract and the directory's reference to it in a directory
    # extract — the same string, two canonical names. A flat table resolved it
    # by whichever was registered first, and mapped the directory's column to
    # `person_number`, which correlation would then have failed on in a way
    # that looked like a data problem rather than a mapping one.
    #
    # This is the merger problem from the pre-flight guard appearing one layer
    # earlier: one field name, more than one meaning, depending on where it
    # came from.
    aliases = {
        "hcm": {
            "person_number": ["personnumber", "employeenumber", "empid",
                              "employeeid", "workerid", "personid"],
            "first_name": ["firstname", "givenname", "forename"],
            "last_name": ["lastname", "surname", "familyname", "sn"],
            "department": ["department", "dept", "orgunit", "costcentrename"],
            "job_code": ["jobcode", "positioncode", "roleid"],
            "assignment_status": ["assignmentstatus", "status",
                                  "employeestatus", "workerstatus"],
            "termination_date": ["terminationdate", "enddate", "leavingdate",
                                 "actualterminationdate"],
            "hire_date": ["hiredate", "startdate", "joiningdate"],
        },
        "ad": {
            "sam_account_name": ["samaccountname", "sam", "logonname",
                                 "accountname"],
            "display_name": ["displayname", "cn", "fullname"],
            "employee_id": ["employeeid", "employeenumber", "empid",
                            "extensionattribute1"],
            # `useraccountcontrol` is deliberately absent. It is a numeric
            # bitmask, not a boolean, and mapping it here made every rule that
            # tests `enabled == "TRUE"` return nothing — R2 silently reported
            # zero findings on an estate that had thirteen. A mapping that
            # attaches a plausible column of the wrong type is worse than one
            # that finds no column at all, because the second is visible.
            "enabled": ["enabled", "accountenabled", "isactive"],
            "department": ["department", "dept", "orgunit"],
            "user_principal_name": ["userprincipalname", "upn", "mail"],
        },
        "entra": {
            "user_principal_name": ["userprincipalname", "upn", "mail",
                                    "email"],
            "user_type": ["usertype", "type"],
            "display_name": ["displayname", "cn"],
        },
        "iga": {
            "iga_identity_id": ["identityid", "igaidentityid", "id"],
            "correlated_ad_sam": ["correlatedadsam", "accountname",
                                  "nativeidentity"],
            "lifecycle_state": ["lifecyclestate", "identitystate", "state"],
        },
        "app_ent": {
            "ad_sam": ["adsam", "accountname", "samaccountname",
                       "nativeidentity"],
            "entitlement": ["entitlement", "permission", "privilege",
                            "rolename", "groupname"],
            "application": ["application", "app", "system", "source"],
            "last_used": ["lastused", "lastaccessed", "lastlogon"],
            "risk": ["risk", "risklevel", "criticality"],
        },
        "nhi": {
            "ad_sam": ["adsam", "accountname", "samaccountname"],
            "owner_status": ["ownerstatus", "status"],
            "owner_person_number": ["ownerpersonnumber", "owner", "ownerid"],
        },
    }

    prof = Profile(name=f"suggested-{snapshot_dir.name}")
    for feed, filename in DEFAULT_FILES.items():
        path = snapshot_dir / filename
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            header = next(csv.reader(f), [])

        lookup = {}
        for canonical, names in aliases.get(feed, {}).items():
            for n in names:
                lookup.setdefault(n, canonical)

        mapping, claimed = {}, set()
        for col in header:
            canonical = lookup.get(norm(col))
            # One source column per canonical name. Two columns claiming the
            # same target is ambiguous, and guessing between them is how a
            # mapping error becomes a correlation error three phases later.
            if canonical and canonical != col and canonical not in claimed:
                mapping[col] = canonical
                claimed.add(canonical)
        if mapping:
            prof.columns[feed] = mapping
    return prof
