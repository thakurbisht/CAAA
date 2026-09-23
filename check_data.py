"""
Test an extract against the assumptions the rules depend on.

Run this before anything else, on the real feeds. It scores nothing and
detects nothing. It reports what the data looks like and names the rules whose
assumptions that data does not support, so a check that would produce
confident wrong findings is caught before it runs rather than after somebody
has acted on its output.
"""

import argparse
import json
from pathlib import Path

from feeds import Profile, suggest_profile, missing_columns
from preflight import Extracts, run_checks

MARK = {"SUPPORTED": "ok", "DEGRADED": "--", "UNSUPPORTED": "XX", "UNKNOWN": "??"}


def wrap(text, width, indent):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(indent + l for l in lines)


def main():
    p = argparse.ArgumentParser(
        description="Check an extract against the pipeline's assumptions")
    p.add_argument("--snapshot", required=True,
                   help="directory holding the extract CSVs")
    p.add_argument("--previous", default=None,
                   help="an earlier extract, to test whether the feed changes")
    p.add_argument("--profile", default=None,
                   help="column mapping for a non-standard extract")
    p.add_argument("--suggest-profile", default=None, metavar="PATH",
                   help="draft a mapping from the extract's headers and write "
                        "it here for review")
    p.add_argument("--out", default=None, help="write findings as JSON")
    a = p.parse_args()

    snap = Path(a.snapshot)
    if not snap.exists():
        raise SystemExit(f"\n  No such directory: {snap}\n")

    # Drafting a mapping comes before any check. A guard reading raw source
    # column names reports every assumption as untestable, which is true but
    # useless — the columns are there, under other names.
    if a.suggest_profile:
        prof = suggest_profile(snap)
        prof.save(Path(a.suggest_profile))
        print(f"\n  Drafted a mapping from the extract's headers:\n")
        for feed, m in prof.columns.items():
            print(f"    {feed}")
            for k, v in m.items():
                print(f"      {k:<28} -> {v}")
        print(f"\n  Written to {Path(a.suggest_profile).resolve()}")
        print("  These are guesses from column names alone. Review them before "
              "use:\n  a wrong mapping here becomes a wrong finding three "
              "phases later.\n")
        return

    profile = Profile.load(Path(a.profile)) if a.profile else None
    x = Extracts(snap, Path(a.previous) if a.previous else None,
                 profile=profile)

    absent = missing_columns(x.data)
    if absent:
        print(f"\n  Columns the rules need but this extract does not supply:")
        for feed, cols in absent.items():
            print(f"    {feed:<10}{', '.join(cols)}")
        print("\n  If they exist under other names, draft a mapping with:")
        print(f"    python3 check_data.py --snapshot {snap} "
              f"--suggest-profile profile.json")

    checks = run_checks(x)

    print(f"\n  Extract: {snap}")
    if x.missing:
        print(f"  Feeds not present: {', '.join(x.missing)}")
    for feed in ("hcm", "ad", "entra", "iga", "app_ent"):
        n = len(x.rows(feed))
        if n:
            print(f"    {feed:<10}{n:>8,} rows")

    print(f"\n  {'':<4}{'assumption':<58}{'verdict'}")
    print("  " + "-" * 76)
    for c in checks:
        print(f"  {MARK[c.verdict]:<4}{c.assumption[:56]:<58}{c.verdict}")

    blocked = [c for c in checks if c.verdict in ("UNSUPPORTED", "UNKNOWN")]
    degraded = [c for c in checks if c.verdict == "DEGRADED"]

    for group, heading in ((blocked, "Assumptions the data does not support"),
                           (degraded, "Assumptions that hold only partly")):
        if not group:
            continue
        print(f"\n\n  {heading}")
        print("  " + "=" * 76)
        for c in group:
            print(f"\n  {c.assumption}")
            print(wrap(c.detail, 72, "    "))
            if c.affects:
                print(f"\n    Affects: {', '.join(c.affects)}")
            if c.advice:
                print(wrap(c.advice, 72, "    "))

    n_bad = len(blocked)
    print(f"\n\n  {len(checks) - n_bad - len(degraded)} supported, "
          f"{len(degraded)} degraded, {n_bad} unsupported or untestable.")
    if n_bad:
        print("  Rules resting on an unsupported assumption should not be run "
              "until it is\n  resolved. They will not error; they will produce "
              "confident wrong findings.")

    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        json.dump([{
            "id": c.id, "assumption": c.assumption, "verdict": c.verdict,
            "detail": c.detail, "affects": c.affects, "evidence": c.evidence,
            "advice": c.advice,
        } for c in checks], open(a.out, "w"), indent=2)
        print(f"\n  Written to {Path(a.out).resolve()}")
    print()


if __name__ == "__main__":
    main()
