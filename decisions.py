"""
The decision log.

Until now nothing came back. Findings went out, somebody in IAM said "that one
is a contractor on extended notice", and the pipeline never heard about it —
so the same finding appeared again the next month, and the month after that.
A report that repeats what has already been answered stops being read, which
is the ordinary way an assurance control dies.

This is the record of what humans decided. It is append-only: a decision is
never edited or deleted, only superseded by a later one. An audit asking why
an account was left alone in March needs the March decision and the name
against it, not the current state of a row somebody has since overwritten.

Items are identified by content, not position. A finding was previously
"R0001", meaning the first row of a CSV — so inserting one finding renumbered
everything below it, and a decision recorded against R0001 silently attached
itself to a different account on the next run. The id is now derived from what
the finding is about, so the same condition produces the same id whenever it
is seen, and a different condition never collides with it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path


# What a reviewer can say about an item.
VERDICTS = {
    "confirmed": "a real finding; send it for remediation",
    "false_positive": "not a finding; the data or the rule is wrong here",
    "accepted_risk": "real, but accepted for a stated period",
    "resolved": "has since been remediated",
}

# Adjudication verdicts, for correlation candidates rather than findings.
LINK_VERDICTS = {
    "confirmed": "this account does belong to that person",
    "rejected": "it does not",
    "unknown": "cannot be determined from the available evidence",
}


def item_id(kind: str, *parts: str) -> str:
    """A stable identifier derived from what the item is about.

    Same condition, same id, every run. Different condition, different id.
    Twelve hex characters is short enough to read aloud in a meeting and long
    enough that collisions are not a practical concern at estate scale.
    """
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return f"{kind}-{digest[:12]}"


def finding_id(rule_id: str, subject: str, detail: str = "") -> str:
    return item_id("f", rule_id, subject, detail)


def cluster_item_id(cluster_id: str, ad_sam: str) -> str:
    # Keyed on the account rather than the cluster number, which is assigned
    # in iteration order and changes between runs.
    return item_id("c", ad_sam or cluster_id)


@dataclass
class Decision:
    item_id: str
    item_kind: str            # "finding" | "link"
    verdict: str
    note: str
    decided_by: str
    decided_at: str
    expires_on: str | None = None
    run_id: str | None = None
    subject: str = ""
    rule_id: str = ""

    @property
    def active(self) -> bool:
        """Whether this decision still stands today.

        An accepted risk with no expiry would suppress a finding forever, and
        nobody would notice the suppression outliving the reason for it. The
        UI requires a date for that verdict; this is where it is enforced.
        """
        if not self.expires_on:
            return True
        try:
            return date.fromisoformat(self.expires_on) >= date.today()
        except ValueError:
            return True

    @property
    def days_remaining(self) -> int | None:
        if not self.expires_on:
            return None
        try:
            return (date.fromisoformat(self.expires_on) - date.today()).days
        except ValueError:
            return None


class DecisionLog:
    """Append-only, one JSON object per line."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, d: Decision) -> Decision:
        # Written with a trailing newline and flushed, so a crash mid-write
        # loses at most the line being added rather than corrupting the file.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(d), sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return d

    def all(self) -> list[Decision]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Decision(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                # A malformed line is skipped rather than failing the load.
                # Losing one record is better than losing the whole history.
                continue
        return out

    def current(self) -> dict[str, Decision]:
        """The standing decision per item: the most recent one that still holds.

        Expired decisions are not treated as current, so a finding whose
        accepted-risk window has closed reappears on its own rather than
        needing somebody to remember it.
        """
        latest: dict[str, Decision] = {}
        for d in self.all():
            prev = latest.get(d.item_id)
            if prev is None or d.decided_at >= prev.decided_at:
                latest[d.item_id] = d
        return {k: v for k, v in latest.items() if v.active}

    def history(self, item_id: str) -> list[Decision]:
        return sorted((d for d in self.all() if d.item_id == item_id),
                      key=lambda d: d.decided_at, reverse=True)

    def expiring_soon(self, days: int = 30) -> list[Decision]:
        out = []
        for d in self.current().values():
            r = d.days_remaining
            if r is not None and 0 <= r <= days:
                out.append(d)
        return sorted(out, key=lambda d: d.days_remaining or 0)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_run_manifest(path: Path, **fields) -> str:
    """Record what produced a set of findings.

    An auditor asking how a finding was reached needs the inputs and settings
    that produced it, not just the finding. Without this the answer is "the
    tool said so", which is the position this whole project exists to move
    away from.
    """
    run_id = item_id("r", now(), str(fields.get("snapshot", "")))
    manifest = {"run_id": run_id, "started_at": now(), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                      encoding="utf-8")
    json.dump(manifest, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, path)
    return run_id
