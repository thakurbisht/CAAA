"""Build the synthetic estate."""
import argparse
from generator import Config, EstateGenerator


def main():
    p = argparse.ArgumentParser(description="Synthetic healthcare identity estate generator")
    p.add_argument("--population", type=int, default=800)
    p.add_argument("--nhi", type=int, default=90)
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--seed", type=int, default=20260824)
    p.add_argument("--out", default="estate")
    p.add_argument("--dense", action="store_true", help="write every day, not fortnightly")
    p.add_argument("--clean", action="store_true", help="zero pathologies (false-positive baseline)")
    a = p.parse_args()

    cfg = Config(population=a.population, nhi_count=a.nhi, days=a.days,
                 seed=a.seed, out_dir=a.out, sparse_snapshots=not a.dense)

    if a.clean:
        cfg = cfg.make_clean()

    g = EstateGenerator(cfg)
    snaps = g.build()
    out = g.write(snaps)

    print(f"\n  Estate written to: {out.resolve()}")
    print(f"  Identities:        {len(g.identities):,}")
    print(f"  Snapshots:         {len(snaps)}  ({snaps[0]} -> {snaps[-1]})")
    print(f"\n  Injected pathologies")
    print(f"  {'-'*46}")
    for k, v in g._counts().items():
        print(f"  {k:<38} {v:>5}")
    print(f"  {'-'*46}")
    print(f"  {'TOTAL':<38} {len(g.ground_truth):>5}\n")


if __name__ == "__main__":
    main()
