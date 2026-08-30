"""Run the correlation engine against a generated estate."""

import argparse
import json
from collections import Counter
from pathlib import Path

from _preflight import require_estate
from correlation import (CorrelationEngine, load_snapshot,
                         latest_snapshot, score, write_outputs)


def main():
    p = argparse.ArgumentParser(description="Phase 2 — identity correlation engine")
    p.add_argument("--estate", default="estate")
    p.add_argument("--snapshot", default=None, help="YYYY-MM-DD; defaults to latest")
    p.add_argument("--out", default="correlation_output")
    p.add_argument("--accept", type=float, default=0.80)
    p.add_argument("--review", type=float, default=0.55)
    a = p.parse_args()

    estate = Path(a.estate)
    require_estate(estate)
    snap = (estate / "snapshots" / a.snapshot) if a.snapshot else latest_snapshot(estate)
    feeds = load_snapshot(snap)

    print(f"\n  Snapshot   {snap.name}")
    print(f"  HCM {len(feeds['hcm']):>6,}   AD {len(feeds['ad']):>6,}   "
          f"Entra {len(feeds['entra']):>6,}   IGA {len(feeds['iga']):>6,}\n")

    eng = CorrelationEngine(feeds["hcm"], feeds["ad"], feeds["entra"], feeds["iga"],
                            accept=a.accept, review=a.review)
    clusters = eng.correlate()

    imap = json.load(open(estate / "ground_truth" / "identity_map.json"))
    metrics = score(clusters, imap)

    out = Path(a.out)
    write_outputs(out, clusters, eng.disagreements, metrics)

    ls = metrics["link_scoring"]
    print("  Link scoring vs ground truth")
    print("  " + "-" * 46)
    print(f"  {'true links':<28}{ls['true_links']:>8,}")
    print(f"  {'false links':<28}{ls['false_links']:>8,}")
    print(f"  {'missed links':<28}{ls['missed_links']:>8,}")
    print(f"  {'correctly unlinked':<28}{ls['correctly_unlinked']:>8,}")
    print("  " + "-" * 46)
    print(f"  {'precision':<28}{ls['precision']:>8.1%}")
    print(f"  {'recall':<28}{ls['recall']:>8.1%}")
    print(f"  {'F1':<28}{ls['f1']:>8.1%}")
    print(f"  {'FALSE LINK RATE':<28}{ls['false_link_rate']:>8.2%}")

    at = metrics["account_typing"]
    nh = metrics["nhi_handling"]
    print(f"\n  {'account typing accuracy':<28}{at['accuracy']:>8.1%}"
          f"   ({at['incorrect']} wrong)")
    print(f"  {'NHI correctly typed':<28}{nh['correctly_typed']:>8,} / {nh['total']:,}")
    print(f"  {'NHI wrongly linked to human':<28}{nh['wrongly_linked_to_a_human']:>8,}")

    print(f"\n  Cluster status")
    print("  " + "-" * 46)
    for k, v in metrics["cluster_status"].items():
        print(f"  {k:<28}{v:>8,}")

    print(f"\n  Matching tier used")
    print("  " + "-" * 46)
    for k, v in metrics["tier_distribution"].items():
        print(f"  {k:<28}{v:>8,}")

    dis = Counter(d["type"] for d in eng.disagreements)
    print(f"\n  Disagreements with the IGA platform")
    print("  " + "-" * 46)
    for k, v in sorted(dis.items()):
        print(f"  {k:<28}{v:>8,}")
    print(f"  {'TOTAL':<28}{len(eng.disagreements):>8,}")

    print(f"\n  Written to {out.resolve()}\n")


if __name__ == "__main__":
    main()
