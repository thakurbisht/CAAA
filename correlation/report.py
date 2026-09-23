"""
Loading, scoring and output.

Scoring is against the generator's ground-truth identity map. The metric that
matters most is not resolution rate but **false link rate**: the proportion of
asserted links that point at the wrong human. An unresolved account is a known
gap and can be worked. A wrongly resolved account is an unknown gap that
silently corrupts every finding built on top of it.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_snapshot(snapshot_dir: Path, profile=None) -> dict[str, list[dict]]:
    """Read a snapshot through the shared loader.

    Filenames and column names live in feeds.py rather than here, so an
    extract whose columns are named by the source system can be mapped in a
    profile instead of by editing this module and five others like it.
    """
    from feeds import load_feeds
    return load_feeds(snapshot_dir, profile)


def latest_snapshot(estate_dir: Path) -> Path:
    return sorted((estate_dir / "snapshots").iterdir())[-1]


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score(clusters, identity_map: list[dict]) -> dict:
    """Score asserted links against the ground-truth identity map."""

    # Ground truth: for each AD account, the HCM person_number it should
    # correlate to.
    #
    # Two distinctions matter here, and conflating either of them produces
    # misleading metrics:
    #
    #   Ownership is not correlation. A service account owned by an engineer
    #   is not that engineer's account. The engine is right to decline to
    #   link it, so NHI are excluded from link scoring and scored separately
    #   on whether they were correctly typed and routed.
    #
    #   An account whose worker is absent from the HCM extract has no correct
    #   link to find. Contingent and vendor staff frequently sit outside the
    #   authoritative source; the correct behaviour is to leave them
    #   unresolved and report them, not to invent a link.
    by_pid = {r["pid"]: r for r in identity_map}
    truth: dict[str, str | None] = {}
    truth_type: dict[str, str] = {}
    nhi_sams: set[str] = set()

    for r in identity_map:
        if not r["ad_sam"]:
            continue
        sam = r["ad_sam"]

        if r["kind"] == "NHI":
            truth_type[sam] = "NHI"
            nhi_sams.add(sam)
            continue

        truth_type[sam] = ("HUMAN_ADMIN" if r["worker_type"] == "EMPLOYEE_ADMIN"
                           else "HUMAN_PRIMARY")

        owner = by_pid.get(r["owner_pid"]) if r["owner_pid"] else None
        if owner and owner.get("in_hcm") and owner.get("person_number"):
            truth[sam] = owner["person_number"]
        else:
            truth[sam] = None      # nothing correct to link to

    tp = fp = fn = tn = 0            # link-level outcomes
    type_ok = type_wrong = 0
    unresolved_but_linkable = 0
    resolved_detail = []
    false_links = []

    nhi_typed_ok = nhi_wrongly_linked = 0

    for c in clusters:
        sam = c.key("AD")
        if not sam or sam not in truth_type:
            continue

        # account typing is scored for every account, including NHI
        if truth_type.get(sam) == c.account_type:
            type_ok += 1
        else:
            type_wrong += 1

        # NHI are scored on typing and on not being linked to a human
        if sam in nhi_sams:
            if c.account_type == "NHI":
                nhi_typed_ok += 1
            if c.key("HCM"):
                nhi_wrongly_linked += 1
            continue

        expected = truth[sam]
        got = c.key("HCM")
        asserted = c.status == "RESOLVED" and got is not None

        if asserted and expected is not None:
            if got == expected:
                tp += 1
            else:
                fp += 1
                false_links.append({
                    "ad_sam": sam, "asserted": got, "actual": expected,
                    "confidence": round(c.confidence, 3),
                    "tier": c.links["HCM"].tier,
                })
        elif asserted and expected is None:
            fp += 1
            false_links.append({
                "ad_sam": sam, "asserted": got, "actual": None,
                "confidence": round(c.confidence, 3),
                "tier": c.links["HCM"].tier,
                "note": "account has no human owner in HR",
            })
        elif not asserted and expected is not None:
            fn += 1
            unresolved_but_linkable += 1
        else:
            tn += 1

        resolved_detail.append({
            "ad_sam": sam, "status": c.status,
            "confidence": round(c.confidence, 3),
            "asserted": got, "expected": expected,
        })

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    total_typed = type_ok + type_wrong

    status_counts: dict[str, int] = {}
    for c in clusters:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1

    tier_counts: dict[str, int] = {}
    for c in clusters:
        if "HCM" in c.links:
            t = c.links["HCM"].tier
            tier_counts[t] = tier_counts.get(t, 0) + 1

    return {
        "link_scoring": {
            "true_links": tp,
            "false_links": fp,
            "missed_links": fn,
            "correctly_unlinked": tn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "false_link_rate": round(fp / (tp + fp), 4) if (tp + fp) else 0.0,
        },
        "account_typing": {
            "correct": type_ok,
            "incorrect": type_wrong,
            "accuracy": round(type_ok / total_typed, 4) if total_typed else 0.0,
        },
        "nhi_handling": {
            "total": len(nhi_sams),
            "correctly_typed": nhi_typed_ok,
            "wrongly_linked_to_a_human": nhi_wrongly_linked,
        },
        "cluster_status": dict(sorted(status_counts.items())),
        "tier_distribution": dict(sorted(tier_counts.items())),
        "false_link_examples": false_links[:20],
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_outputs(out_dir: Path, clusters, disagreements, metrics):
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "correlation_map.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_id", "account_type", "status", "confidence",
                    "parent_cluster_id", "hcm_person_number", "ad_sam",
                    "entra_upn", "iga_identity_id", "tier", "notes"])
        for c in clusters:
            hcm_link = c.links.get("HCM")
            w.writerow([
                c.cluster_id, c.account_type, c.status, round(c.confidence, 3),
                c.parent_cluster_id or "",
                c.key("HCM") or "", c.key("AD") or "",
                c.key("ENTRA") or "", c.key("IGA") or "",
                hcm_link.tier if hcm_link else "",
                " | ".join(c.notes),
            ])

    review = [c for c in clusters if c.status in ("REVIEW", "UNRESOLVED")]
    with open(out_dir / "adjudication_queue.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_id", "account_type", "status", "confidence",
                    "ad_sam", "entra_upn", "iga_identity_id",
                    "candidate_person_number", "why_it_needs_a_human"])
        for c in sorted(review, key=lambda x: -x.confidence):
            w.writerow([
                c.cluster_id, c.account_type, c.status, round(c.confidence, 3),
                c.key("AD") or "", c.key("ENTRA") or "", c.key("IGA") or "",
                c.key("HCM") or "",
                " | ".join(c.notes) or "confidence below acceptance threshold",
            ])

    with open(out_dir / "iga_correlation_disagreements.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "ad_sam", "iga_asserts", "independent_result",
                    "our_confidence", "tier"])
        for d in disagreements:
            w.writerow([d["type"], d["ad_sam"], d.get("iga_asserts") or "",
                        d.get("independent_result") or "",
                        d.get("our_confidence"), d.get("tier") or ""])

    with open(out_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
