"""
Account classification.

Correlation cannot begin until accounts are typed. A service account matched
against a human worker is not a near miss — it is a false link, and a false
link in an assurance context is worse than an unresolved one, because it
attributes privilege to the wrong person.

Classification is deliberately conservative and rule-based. Anything that
cannot be typed with confidence is marked UNKNOWN and routed to adjudication
rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Prefix conventions. In a real estate these are discovered by inspection
# and confirmed with the directory team, never assumed.
NHI_PREFIXES = {
    "svc-": "SERVICE",
    "sa-": "SERVICE",
    "hl7-": "INTERFACE",
    "int-": "INTERFACE",
    "dev-": "DEVICE",
    "bio-": "DEVICE",
    "ws-": "SHARED_WORKSTATION",
    "kiosk-": "SHARED_WORKSTATION",
}

ADMIN_PREFIXES = ("a-", "adm-", "admin-")

# Tokens that suggest a non-human account even without a prefix convention
NHI_TOKENS = re.compile(
    r"\b(service|svc|backup|monitor|scanner|interface|integration|"
    r"pacs|pyxis|infusion|kiosk|station|scheduler|batch|daemon)\b",
    re.IGNORECASE,
)


@dataclass
class AccountClass:
    account_type: str        # HUMAN_PRIMARY | HUMAN_ADMIN | NHI | UNKNOWN
    subtype: str | None      # SERVICE | INTERFACE | DEVICE | SHARED_WORKSTATION
    derived_owner_sam: str | None   # for admin accounts, the stripped base
    confidence: float
    reason: str


def classify_ad_account(sam: str, display_name: str = "",
                        employee_id: str = "") -> AccountClass:
    """Type a Local AD account from its name and attributes."""
    s = (sam or "").lower().strip()

    if not s:
        return AccountClass("UNKNOWN", None, None, 0.0, "empty sAMAccountName")

    # --- non-human by prefix convention ---------------------------------
    for pfx, subtype in NHI_PREFIXES.items():
        if s.startswith(pfx):
            return AccountClass(
                "NHI", subtype, None, 0.97,
                f"prefix '{pfx}' matches non-human convention",
            )

    # --- administrative satellite ---------------------------------------
    for pfx in ADMIN_PREFIXES:
        if s.startswith(pfx):
            base = s[len(pfx):]
            # An admin account with a populated employee_id is unusual and
            # worth surfacing — it means the directory itself asserts a
            # human owner, which is stronger evidence than the prefix.
            conf = 0.95 if not employee_id else 0.99
            return AccountClass(
                "HUMAN_ADMIN", None, base, conf,
                f"prefix '{pfx}' with derivable base account '{base}'",
            )

    # --- non-human by token, without a prefix ----------------------------
    if NHI_TOKENS.search(s) or NHI_TOKENS.search(display_name or ""):
        return AccountClass(
            "NHI", None, None, 0.60,
            "name contains a non-human token but no prefix convention",
        )

    # --- default --------------------------------------------------------
    return AccountClass(
        "HUMAN_PRIMARY", None, None, 0.90,
        "no non-human or administrative marker",
    )


def classify_entra_account(upn: str, user_type: str,
                           sync_enabled: str, display_name: str = "") -> AccountClass:
    """Type an Entra account.

    Guests are treated as a distinct population. They are legitimate, but
    they have no HCM record by construction, so a correlation engine that
    treats them as unmatched humans will generate permanent noise.
    """
    u = (upn or "").lower()

    if (user_type or "").lower() == "guest" or "#ext#" in u:
        return AccountClass(
            "GUEST", None, None, 0.99,
            "Entra userType is Guest or UPN carries an #EXT# marker",
        )

    local = u.split("@")[0]
    for pfx, subtype in NHI_PREFIXES.items():
        if local.startswith(pfx):
            return AccountClass("NHI", subtype, None, 0.97,
                                f"prefix '{pfx}' matches non-human convention")

    for pfx in ADMIN_PREFIXES:
        if local.startswith(pfx):
            return AccountClass("HUMAN_ADMIN", None, local[len(pfx):], 0.95,
                                f"prefix '{pfx}'")

    if (sync_enabled or "").upper() == "FALSE":
        return AccountClass(
            "CLOUD_ONLY", None, None, 0.85,
            "no on-premises sync anchor: cloud-only identity",
        )

    return AccountClass("HUMAN_PRIMARY", None, None, 0.90, "synced member account")
