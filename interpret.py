"""Phase 6 — optional interpretation layer over the deterministic findings."""

import argparse
import csv
import json
from pathlib import Path

from _preflight import require_estate
from llm import LLMClient, translate_catalogue, cluster_findings, assess


def load_findings(recon: Path, drift: Path) -> list[dict]:
    """Both finding sets, with a stable id assigned per source row."""
    out = []
    for tag, path in (("R", recon / "findings.csv"),
                      ("D", drift / "drift_findings.csv")):
        if not path.exists():
            continue
        for i, r in enumerate(csv.DictReader(open(path, newline="")), 1):
            r["finding_id"] = f"{tag}{i:04d}"
            out.append(r)
    return out


def main():
    p = argparse.ArgumentParser(description="Phase 6 — interpretation layer")
    p.add_argument("--estate", default="estate")
    p.add_argument("--reconciliation", default="reconciliation_output")
    p.add_argument("--drift", default="drift_output")
    p.add_argument("--out", default="interpretation_output")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "ollama", "anthropic", "none"])
    p.add_argument("--model", default=None)
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--all-entitlements", action="store_true",
                   help="translate low-risk entitlements too")
    a = p.parse_args()

    estate = Path(a.estate)
    require_estate(estate)
    recon, drift, out = Path(a.reconciliation), Path(a.drift), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    client = LLMClient(a.backend, model=a.model, host=a.host)
    print(f"\n  Backend: {client.backend}"
          + (f" ({client.model})" if client.available else ""))
    if not client.available:
        print("  No model reachable. Deterministic fallbacks will be used "
              "throughout;\n  no finding is lost, only its readability.")

    # ---- translation -------------------------------------------------
    cat = json.load(open(estate / "ground_truth" / "entitlement_catalogue.json"))
    tr = translate_catalogue(client, cat, only_risky=not a.all_entitlements)
    json.dump(tr, open(out / "entitlement_translations.json", "w"), indent=2)

    src = {}
    for v in tr.values():
        src[v["source"]] = src.get(v["source"], 0) + 1
    print(f"\n  Translations   {len(tr)} entitlements")
    for k in ("model", "rejected", "template"):
        if src.get(k):
            print(f"    {k:<12}{src[k]:>5}")

    # ---- clustering --------------------------------------------------
    findings = load_findings(recon, drift)
    cl = cluster_findings(client, findings)
    with open(out / "finding_clusters.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster_name", "rationale", "finding_count", "finding_ids"])
        for c in cl.clusters:
            w.writerow([c["name"], c.get("rationale", ""),
                        len(c["finding_ids"]), " ".join(c["finding_ids"])])

    placed = sum(len(c["finding_ids"]) for c in cl.clusters)
    print(f"\n  Clustering     {len(cl.clusters)} groups over {placed} findings "
          f"(source: {cl.source})")
    if cl.failures:
        print(f"    rejected: {cl.failures[0]}")
    for c in cl.clusters[:8]:
        print(f"    {len(c['finding_ids']):>4}  {c['name'][:58]}")

    # ---- quality -----------------------------------------------------
    rm = json.load(open(recon / "rule_metrics.json"))
    dm = json.load(open(drift / "drift_metrics.json"))
    q = assess(rm, dm, client)
    json.dump({
        "grade": q.grade,
        "mean_coverage": q.weighted_coverage,
        "narrative": q.narrative,
        "narrative_source": q.narrative_source,
        "notes": q.notes,
        "per_rule": q.per_rule,
        "blind_spots": q.blind_spots,
    }, open(out / "certification_quality.json", "w"), indent=2)

    print(f"\n  Certification  {q.grade}  (mean coverage {q.weighted_coverage:.1%})")
    print(f"    narrative source: {q.narrative_source}")
    if q.notes:
        print(f"    {q.notes[0]}")
    print()
    for line in _wrap(q.narrative, 72):
        print(f"    {line}")

    json.dump(client.summary(), open(out / "llm_usage.json", "w"), indent=2)
    print(f"\n  Written to {out.resolve()}\n")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    main()
