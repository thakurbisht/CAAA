"""
Generation configuration.

Pathology rates are expressed as either an absolute count or a proportion of
the population. They are deliberately tunable: a clean estate (all rates zero)
is a valid configuration and is useful for measuring the false-positive rate
of a detection engine.
"""

from dataclasses import dataclass, field


@dataclass
class Config:
    # ---- Population -----------------------------------------------------
    population: int = 800
    nhi_count: int = 90
    seed: int = 20260824

    # ---- Timeline -------------------------------------------------------
    # Snapshots are taken daily. 180 days gives two full quarterly
    # certification cycles, which is the minimum useful window for
    # between-cycle drift analysis.
    days: int = 180
    start_date: str = "2026-02-01"

    # How long terminated workers remain in the HCM extract. If this is
    # shorter than your detection lag, leavers vanish from the feed before
    # they are reconciled and can only be found via prior snapshots — a
    # real and commonly missed design constraint.
    hcm_retention_days: int = 400

    # ---- Workforce churn (per day, as proportion of population) ---------
    joiner_rate: float = 0.0020
    leaver_rate: float = 0.0018
    mover_rate: float = 0.0025          # the main source of privilege creep

    # ---- Pathology: correlation difficulty ------------------------------
    # These make cross-system identity matching non-trivial, which is the
    # single largest hidden cost in any real assurance build.
    admin_account_rate: float = 0.06     # a second 'a-' privileged account
    no_hcm_record_rate: float = 0.09     # contingent staff absent from HCM
    name_change_count: int = 12          # UPN no longer matches HCM name
    rehire_count: int = 8                # new person number, old AD account
    naming_variant_rate: float = 0.10    # NORTH facility legacy convention

    # ---- Pathology: reconciliation failures -----------------------------
    missed_leaver_count: int = 22        # HCM terminated, still enabled in AD
    stale_connector_count: int = 15      # IGA says disabled, AD says enabled
    entra_only_count: int = 18           # cloud-only / guest, absent from IGA
    out_of_sync_scope_count: int = 11    # AD object outside Connect scope

    # ---- Pathology: drift and privilege creep ---------------------------
    # Proportion of movers whose prior-role entitlements are never revoked.
    # 0.55 is deliberately high — it is what an unmanaged estate looks like.
    retained_on_transfer_rate: float = 0.55
    peer_outlier_count: int = 30         # entitlement held by <5% of peers
    dormant_entitlement_rate: float = 0.14

    # ---- Pathology: SoD and privileged access ---------------------------
    sod_violation_count: int = 26
    orphaned_nhi_rate: float = 0.30      # NHI whose owner has left or is unset
    standing_breakglass_count: int = 9   # break-glass held permanently

    # ---- Output ---------------------------------------------------------
    out_dir: str = "estate"
    write_daily_snapshots: bool = True
    # Writing all 180 days is ~2M rows. For iteration, keep only the
    # snapshots that matter: first, last, and one per fortnight.
    sparse_snapshots: bool = True
    sparse_interval_days: int = 14

    def summary(self) -> dict:
        return {
            "population": self.population,
            "nhi_count": self.nhi_count,
            "days": self.days,
            "start_date": self.start_date,
            "seed": self.seed,
        }

    def make_clean(self) -> "Config":
        """Return a copy with every injected pathology disabled.

        Note what this does not do: it does not produce an estate with nothing
        to find. Joiners, movers and leavers still occur across the simulated
        window, so device and interface accounts belonging to departed staff
        still accumulate, and role-granted entitlement pairs still collide.
        Those are real conditions and a rule is correct to report them.

        The false-positive baseline is therefore not "zero findings" but
        "every finding confirmed by an independent oracle".
        """
        import dataclasses
        rates = ("admin_account_rate", "no_hcm_record_rate",
                 "retained_on_transfer_rate", "dormant_entitlement_rate",
                 "orphaned_nhi_rate")
        counts = ("name_change_count", "rehire_count", "missed_leaver_count",
                  "stale_connector_count", "entra_only_count",
                  "out_of_sync_scope_count", "peer_outlier_count",
                  "sod_violation_count", "standing_breakglass_count")
        c = dataclasses.replace(self)
        for k in rates:
            setattr(c, k, 0.0)
        for k in counts:
            setattr(c, k, 0)
        return c
