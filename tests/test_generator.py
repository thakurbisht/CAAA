"""
Phase 1 test suite: Verify the synthetic estate generator is correct
and that injected pathologies are actually detectable.

Run: python -m pytest tests/test_generator.py -v
"""

import json
import csv
import tempfile
from pathlib import Path
from datetime import date, timedelta
from collections import Counter

import pytest

from generator import Config, EstateGenerator
from generator import reference as ref


class TestGeneratorDeterminism:
    """Same seed → same output"""

    def test_same_seed_same_output(self):
        """Two runs with same seed produce identical estates"""
        cfg = Config(population=200, nhi_count=20, days=30, seed=12345, out_dir="test_out_1")
        g1 = EstateGenerator(cfg)
        snap1 = g1.build()

        cfg = Config(population=200, nhi_count=20, days=30, seed=12345, out_dir="test_out_2")
        g2 = EstateGenerator(cfg)
        snap2 = g2.build()

        assert len(snap1) == len(snap2)
        assert len(g1.identities) == len(g2.identities)
        assert g1._counts() == g2._counts()

    def test_different_seed_different_output(self):
        """Different seeds produce different estates"""
        cfg = Config(population=200, nhi_count=20, days=30, seed=111, out_dir="test_out_3")
        g1 = EstateGenerator(cfg)
        snap1 = g1.build()

        cfg = Config(population=200, nhi_count=20, days=30, seed=222, out_dir="test_out_4")
        g2 = EstateGenerator(cfg)
        snap2 = g2.build()

        # Should differ in at least some pathology counts
        assert g1._counts() != g2._counts() or len(g1.identities) != len(g2.identities)


class TestPopulationInitialization:
    """Baseline population is correctly initialized"""

    def test_population_size(self):
        cfg = Config(population=500, nhi_count=50, days=10, seed=999)
        g = EstateGenerator(cfg)
        g.build()

        humans = [i for i in g.identities.values() if i.kind == "HUMAN"]
        nhis = [i for i in g.identities.values() if i.kind == "NHI"]

        # Over 10 days, joiners will increase population from 500
        assert len(humans) >= 500, f"Expected >=500 humans, got {len(humans)}"
        assert len(nhis) == 50, f"Expected 50 NHIs, got {len(nhis)}"

    def test_all_humans_have_required_attributes(self):
        cfg = Config(population=100, nhi_count=10, days=10, seed=111)
        g = EstateGenerator(cfg)
        g.build()

        for i in g.identities.values():
            if i.kind != "HUMAN":
                continue

            assert i.pid
            # Entra-only guests have no AD
            if i.has_ad:
                assert i.ad_sam
                assert i.ad_upn
            # Admin accounts and guests may not have Entra
            if i.worker_type != "EMPLOYEE_ADMIN" and i.entra_user_type != "Guest":
                assert i.entra_upn
                assert i.entra_object_id
            assert i.iga_id
            # Admin accounts and guests have no HCM record
            if i.worker_type not in ("EMPLOYEE_ADMIN", "VENDOR") or i.has_hcm_record:
                assert i.person_number
            assert i.job_code
            assert i.department
            assert i.hire_date
            # Terminated accounts may have empty holdings if clean-terminated,
            # or non-empty if dirty-terminated (missed leaver scenario).
            # Only check active employees.
            if i.termination_date is None:
                assert len(i.active_holdings()) > 0, f"{i.ad_sam or i.entra_upn} has no baseline entitlements"

    def test_nhi_diversity(self):
        """NHI span all classes: SERVICE, INTERFACE, DEVICE, SHARED"""
        cfg = Config(population=50, nhi_count=50, days=10, seed=222)
        g = EstateGenerator(cfg)
        g.build()

        nhis = [i for i in g.identities.values() if i.kind == "NHI"]
        classes = Counter(i.nhi_class for i in nhis)

        assert len(classes) == 4, f"Expected 4 NHI classes, got {len(classes)}"
        for cls in ["SERVICE", "INTERFACE", "DEVICE", "SHARED"]:
            assert cls in classes, f"Missing NHI class {cls}"


class TestBaselineEntitlements:
    """Correctly assigned per job code + department"""

    def test_nurses_get_department_specific_cerner_class(self):
        cfg = Config(population=200, days=10, seed=333)
        g = EstateGenerator(cfg)
        g.build()

        icu_nurses = [i for i in g.identities.values()
                      if i.kind == "HUMAN" and i.job_code.startswith("RN") and i.department == "ICU"]
        assert len(icu_nurses) > 0

        for nurse in icu_nurses:
            ents = {h.entitlement for h in nurse.active_holdings()}
            assert "CERN_SEC_RN_ICU" in ents, f"{nurse.ad_sam} in ICU but has no CERN_SEC_RN_ICU"
            assert "GRP_DEPT_ICU" in ents

    def test_finance_manager_has_approval_role(self):
        cfg = Config(population=500, days=10, seed=444)
        g = EstateGenerator(cfg)
        g.build()

        fin_mgrs = [i for i in g.identities.values()
                    if i.kind == "HUMAN" and i.job_code == "FIN-03"
                    and i.termination_date is None]
        assert len(fin_mgrs) > 0, "No active finance managers found"

        for mgr in fin_mgrs[:3]:  # check a sample
            ents = {h.entitlement for h in mgr.active_holdings()}
            # At minimum should have baseline roles (may have more from creep)
            baseline = set(ref.ROLE_BASELINE.get("FIN-03", []))
            assert baseline.issubset(ents), \
                f"{mgr.ad_sam} missing baseline roles. Has {ents}, baseline {baseline}"


class TestLifecycleEvents:
    """Joiners, movers, leavers operate correctly"""

    def test_joiners_added_daily(self):
        cfg = Config(population=100, nhi_count=10, days=30, seed=555,
                     joiner_rate=0.01)
        g = EstateGenerator(cfg)
        g.build()

        # Some identities should have hire_date > start_date
        late_hires = [i for i in g.identities.values()
                      if i.kind == "HUMAN" and i.hire_date > g.start + timedelta(days=5)]
        assert len(late_hires) > 0, "Expected some mid-stream joiners"

    def test_leavers_terminated(self):
        cfg = Config(population=100, nhi_count=10, days=30, seed=666,
                     leaver_rate=0.01)
        g = EstateGenerator(cfg)
        g.build()

        # Only check non-admin humans (admins are handled by _handle_linked_admin)
        terminated = [i for i in g.identities.values()
                      if i.kind == "HUMAN" and i.worker_type != "EMPLOYEE_ADMIN"
                      and i.termination_date is not None]
        assert len(terminated) > 0, "Expected some leavers"

        # All terminated should have hcm_status = TERMINATED
        for i in terminated:
            assert i.hcm_status == "TERMINATED", f"{i.ad_sam} not marked TERMINATED in HCM"

        # There should be some clean (all revoked) and some dirty (holdings retained)
        all_revoked = [i for i in terminated if len(i.active_holdings()) == 0]
        has_holdings = [i for i in terminated if len(i.active_holdings()) > 0]

        # Given our pathology rate, should have both
        assert len(all_revoked) > 0, "No fully-revoked terminations"
        assert len(has_holdings) > 0, "No dirty terminations (missed leavers)"

    def test_movers_create_creep(self):
        cfg = Config(population=100, nhi_count=10, days=60, seed=777,
                     mover_rate=0.05, retained_on_transfer_rate=0.8)
        g = EstateGenerator(cfg)
        g.build()

        transfers = [p for p in g.ground_truth if p["pathology"] == "RETAINED_ON_TRANSFER"]
        assert len(transfers) > 0, "Expected creep from transfers"


class TestPathologyDetectability:
    """Injected pathologies are actually detectable in emitted feeds"""

    def test_missed_leavers_detectable(self, tmp_path):
        """HCM terminated, AD still enabled"""
        cfg = Config(population=300, days=60, seed=888,
                     leaver_rate=0.01, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        # Read the latest snapshot
        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        # Load feeds
        def load_csv(name):
            with open(snap / name) as f:
                return list(csv.DictReader(f))

        hcm = load_csv("hcm_workers.csv")
        ad = load_csv("ad_accounts.csv")
        igai = load_csv("iga_identities.csv")

        # Find all HCM-terminated workers
        terminated = {r["person_number"] for r in hcm if r["assignment_status"] == "TERMINATED"}

        # Correlate to AD via IGA
        pn_to_sam = {r["correlated_person_number"]: r["correlated_ad_sam"]
                     for r in igai if r["correlated_person_number"]}
        ad_enabled = {r["sam_account_name"]: r["enabled"] for r in ad}

        # Find missed leavers
        missed = [pn for pn in terminated
                  if pn_to_sam.get(pn) and ad_enabled.get(pn_to_sam[pn]) == "TRUE"]

        # Ground truth
        gt_missed = [p["person_number"] for p in g.ground_truth
                     if p["pathology"] == "MISSED_LEAVER"]

        # Should match
        assert len(missed) == len(gt_missed), \
            f"Detected {len(missed)} missed leavers but ground truth has {len(gt_missed)}"

    def test_stale_connector_detectable(self, tmp_path):
        """IGA DISABLED, AD ENABLED"""
        cfg = Config(population=300, days=30, seed=999,
                     stale_connector_count=20, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        def load_csv(name):
            with open(snap / name) as f:
                return list(csv.DictReader(f))

        igai = load_csv("iga_identities.csv")
        ad = load_csv("ad_accounts.csv")

        iga_state = {r["correlated_ad_sam"]: r["lifecycle_state"]
                     for r in igai if r["correlated_ad_sam"]}
        ad_enabled = {r["sam_account_name"]: r["enabled"] for r in ad}

        stale = [sam for sam, state in iga_state.items()
                 if state == "DISABLED" and ad_enabled.get(sam) == "TRUE"]

        gt_stale = [p["ad_sam"] for p in g.ground_truth
                    if p["pathology"] == "STALE_CONNECTOR"]

        assert len(stale) >= len(gt_stale) - 3, \
            f"Detected {len(stale)} stale connectors, ground truth has {len(gt_stale)}"

    def test_nhi_without_owner_detectable(self, tmp_path):
        cfg = Config(population=100, nhi_count=50, days=30, seed=1001,
                     orphaned_nhi_rate=0.5, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        with open(snap / "nhi_register.csv") as f:
            nhi = list(csv.DictReader(f))

        orphaned = [r for r in nhi if r["owner_person_number"] == ""]

        gt_orphaned = [p for p in g.ground_truth
                       if p["pathology"] == "ORPHANED_NHI"]

        assert len(orphaned) == len(gt_orphaned), \
            f"Detected {len(orphaned)} orphaned NHI, ground truth has {len(gt_orphaned)}"

    def test_sod_violations_present(self, tmp_path):
        """SoD rule pairs actually appear in the data"""
        cfg = Config(population=500, days=30, seed=1002,
                     sod_violation_count=15, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        with open(snap / "app_entitlements.csv") as f:
            appe = list(csv.DictReader(f))

        # Map sam -> set of entitlements
        held = {}
        for r in appe:
            key = r["ad_sam"] or r["entra_upn"]
            held.setdefault(key, set()).add(r["entitlement"])

        # Load rules
        rules = json.load(open("estate/ground_truth/sod_rules.json"))

        # Check each rule
        detected = Counter()
        for sub, ents in held.items():
            for rule in rules:
                if rule["left"] in ents and rule["right"] in ents:
                    detected[rule["id"]] += 1

        # Ground truth
        gt_sod = [p for p in g.ground_truth if p["pathology"] == "SOD_VIOLATION"]

        assert len(detected) > 0, "No SoD pairs detected in data"
        assert sum(detected.values()) >= len(gt_sod) - 5, \
            f"Detected {sum(detected.values())} SoD instances, ground truth has {len(gt_sod)}"


class TestCoverageGap:
    """Unaggregated applications are actually invisible to IGA"""

    def test_unaggregated_entitlements_invisible_to_iga(self, tmp_path):
        cfg = Config(population=200, days=30, seed=1003, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        def load_csv(name):
            with open(snap / name) as f:
                return list(csv.DictReader(f))

        appe = load_csv("app_entitlements.csv")
        igae = load_csv("iga_entitlements.csv")

        # Ground truth unaggregated apps
        unagg = {e for e, m in ref.ENTITLEMENTS.items()
                 if not m["aggregated_by_iga"]}

        appe_unagg = set(r["entitlement"] for r in appe if r["entitlement"] in unagg)
        igae_unagg = set(r["entitlement"] for r in igae if r["entitlement"] in unagg)

        assert len(appe_unagg) > 0, "No unaggregated entitlements in app feed"
        assert len(igae_unagg) == 0, "Unaggregated entitlements leaked into IGA feed"

    def test_coverage_gap_measurable(self, tmp_path):
        """Coverage percentage can be calculated"""
        cfg = Config(population=200, days=30, seed=1004, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        def load_csv(name):
            with open(snap / name) as f:
                return list(csv.DictReader(f))

        appe = load_csv("app_entitlements.csv")
        igae = load_csv("iga_entitlements.csv")

        appe_count = Counter(r["application"] for r in appe)
        igae_count = Counter(r["application"] for r in igae)

        total_app = sum(appe_count.values())
        total_iga = sum(igae_count.values())
        coverage = total_iga / total_app if total_app > 0 else 0

        assert 0.75 < coverage < 1.0, \
            f"Coverage {coverage:.1%} outside expected 75-100%"


class TestCorrelationObstacles:
    """Obstacles to cross-system matching are present"""

    def test_admin_accounts_present(self, tmp_path):
        cfg = Config(population=200, days=30, seed=1005,
                     admin_account_rate=0.1, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        with open(snap / "ad_accounts.csv") as f:
            ad = list(csv.DictReader(f))

        admin_accts = [r for r in ad if r["sam_account_name"].startswith("a-")]
        assert len(admin_accts) > 0, "No admin accounts in AD feed"

    def test_employee_id_sparsity(self, tmp_path):
        """~54% of AD accounts carry employee_id"""
        cfg = Config(population=500, days=30, seed=1006, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        with open(snap / "ad_accounts.csv") as f:
            ad = list(csv.DictReader(f))

        with_empid = sum(1 for r in ad if r["employee_id"])
        coverage = with_empid / len(ad)

        assert 0.4 < coverage < 0.7, \
            f"Employee_id coverage {coverage:.1%}, expected 40-70%"

    def test_no_hcm_record_contingent(self, tmp_path):
        cfg = Config(population=300, days=30, seed=1007,
                     no_hcm_record_rate=0.1, out_dir=str(tmp_path))
        g = EstateGenerator(cfg)
        snaps = g.build()
        g.write(snaps)

        snap = sorted((tmp_path / "snapshots").iterdir())[-1]

        gt_no_hcm = [p for p in g.ground_truth
                     if p["pathology"] == "NO_HCM_RECORD"]
        assert len(gt_no_hcm) > 0


class TestGroundTruthAccuracy:
    """Ground truth answer key is accurate and reproducible"""

    def test_ground_truth_reproducible(self):
        cfg = Config(population=200, days=20, seed=1008)
        g = EstateGenerator(cfg)
        g.build()
        gt1 = sorted(g.ground_truth, key=lambda x: x["subject"])

        cfg = Config(population=200, days=20, seed=1008)
        g = EstateGenerator(cfg)
        g.build()
        gt2 = sorted(g.ground_truth, key=lambda x: x["subject"])

        assert gt1 == gt2, "Ground truth not reproducible with same seed"

    def test_ground_truth_pathology_counts_match_summary(self):
        cfg = Config(population=200, days=20, seed=1009)
        g = EstateGenerator(cfg)
        g.build()

        manual_count = Counter(p["pathology"] for p in g.ground_truth)
        summary_count = g._counts()

        assert dict(manual_count) == summary_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
