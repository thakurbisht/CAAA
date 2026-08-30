"""
Phase 3 test suite: reconciliation rules.

Organised around the ways a detection control misleads the people relying on
it, in order of severity:

  1. Wrong accusations   — a finding naming a person it does not belong to
  2. Laundered coverage  — silence reported as a clean result
  3. Inherited error     — an unconfirmed correlation consumed as a fact
  4. Lost independence   — a check that only sees what the platform sees
"""

import json
from pathlib import Path

import pytest

from generator import Config, EstateGenerator
from correlation import CorrelationEngine, load_snapshot, write_outputs as write_corr
from reconciliation import EstateView, TruthOracle, run_all, score_all, score_clean_run
from reconciliation.scorer import INJECTION_CLASS
from reconciliation.rules import (rule_leaver_not_deprovisioned,
                                  rule_orphaned_admin_account,
                                  rule_sod_violation, rule_coverage_gap)


def _build(tmp, clean: bool, seed: int = 20260824):
    """Generate an estate, correlate it, and return the pieces the rules need."""
    out = tmp / ("clean" if clean else "dirty")
    cfg = Config(population=600, nhi_count=70, days=120,
                 seed=seed, out_dir=str(out))
    if clean:
        cfg = cfg.make_clean()
    g = EstateGenerator(cfg)
    g.write(g.build())

    snap = sorted((out / "snapshots").iterdir())[-1]
    feeds = load_snapshot(snap)
    eng = CorrelationEngine(feeds["hcm"], feeds["ad"], feeds["entra"], feeds["iga"])
    clusters = eng.correlate()

    corr_dir = out / "_corr"
    write_corr(corr_dir, clusters, eng.disagreements, {})

    v = EstateView(snap, corr_dir / "correlation_map.csv")
    sod = json.load(open(out / "ground_truth" / "sod_rules.json"))
    return out, snap, v, sod, TruthOracle(out, snap)


@pytest.fixture(scope="module")
def dirty(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("estate"), clean=False)


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("estate"), clean=True)


@pytest.fixture(scope="module")
def scored(dirty):
    estate, snap, v, sod, oracle = dirty
    results = run_all(v, sod)
    return results, score_all(results, oracle), v, oracle


# ==========================================================================
# Precision — the finding must be about the right subject
# ==========================================================================

class TestPrecision:

    def test_no_false_positives_on_any_rule(self, scored):
        _, metrics, _, _ = scored
        bad = [(s["rule_id"], s["false_positives"], s["false_positive_examples"])
               for s in metrics["per_rule"] if s["false_positives"]]
        assert not bad, f"rules produced unconfirmed findings: {bad}"

    def test_clean_estate_produces_no_unconfirmed_findings(self, clean):
        """A fault-free estate still contains real conditions.

        Terminations continue over the simulated window and leave device and
        interface accounts behind. The baseline therefore asks whether every
        finding is confirmed by the oracle, not whether the count is zero.
        """
        _, _, v, sod, oracle = clean
        cs = score_clean_run(run_all(v, sod), oracle)
        assert cs["clean"], (
            f"{cs['total_false_positives']} unconfirmed findings on a "
            f"fault-free estate: {cs['per_rule']}"
        )

    def test_planted_faults_are_caught_healed_or_declared(self, scored):
        """Every planted fault must reach one of three honest outcomes.

        Detected is the obvious one. Healed is legitimate: a stale connector
        injected in month one stops being a disagreement once the worker is
        properly terminated in month three, so the condition no longer exists
        at the snapshot under examination.

        The third is the one that matters for an assurance control. Where the
        subject's identity could not be confirmed — a name matching two HR
        records equally well, or two accounts contesting one person — the rule
        declines to name anyone and reports the account as unassessed instead.
        That is a disclosed limit, not a miss.

        What the test forbids is the fourth outcome: a fault that is still
        present, was not reported, and was not declared. That is silence
        presented as a clean result.
        """
        results, _, v, oracle = scored
        truth = oracle.all_truth()
        injected = oracle.injected_subset()

        # Accounts the correlation engine could not confirm, which the rules
        # exclude by design and record in their coverage statement.
        unconfirmed = {c["ad_sam"] for c in v.clusters
                       if c["ad_sam"] and c["status"] != "RESOLVED"}

        for r in results:
            cls = INJECTION_CLASS.get(r.rule_id)
            if not cls:
                continue
            planted = injected.get(cls, set())
            if not planted:
                continue

            found = {f.subject for f in r.findings}
            still_true = ({s for s, *_ in truth[r.rule_id]}
                          if r.rule_id == "R5_SOD_VIOLATION" else truth[r.rule_id])

            healed = planted - still_true
            declared = planted & unconfirmed
            silent = planted - found - healed - declared

            assert not silent, (
                f"{r.rule_id} silently missed {len(silent)} planted faults that "
                f"are still present and were not declared unassessable: "
                f"{sorted(silent)[:5]}"
            )

    def test_declared_exclusions_are_counted_in_coverage(self, scored):
        """A rule that excludes accounts must say how many, so the reader can
        see the difference between a clean result and an unexamined one."""
        results, _, _, _ = scored
        for r in results:
            if r.excluded:
                assert sum(r.excluded.values()) > 0
                assert r.notes, f"{r.rule_id} excluded accounts without explanation"


# ==========================================================================
# Correlation confidence must not be laundered into certainty
# ==========================================================================

class TestCorrelationGating:

    def test_leaver_findings_rest_only_on_confirmed_links(self, scored):
        """Naming someone a leaver on an unconfirmed link risks revoking the
        wrong person's access."""
        results, _, v, _ = scored
        r1 = next(r for r in results if r.rule_id == "R1_LEAVER_NOT_DEPROVISIONED")
        by_sam = {c["ad_sam"]: c for c in v.clusters if c["ad_sam"]}
        for f in r1.findings:
            assert by_sam[f.subject]["status"] == "RESOLVED", (
                f"{f.subject} accused on a {by_sam[f.subject]['status']} correlation"
            )

    def test_unconfirmed_accounts_are_reported_not_dropped(self, scored):
        """Excluded population must appear in the coverage statement."""
        results, _, _, _ = scored
        r1 = next(r for r in results if r.rule_id == "R1_LEAVER_NOT_DEPROVISIONED")
        assert "correlation_unconfirmed" in r1.excluded
        if r1.excluded["correlation_unconfirmed"]:
            assert any("unconfirmed" in n for n in r1.notes)

    def test_admin_with_unconfirmed_owner_is_flagged_not_cleared(self, dirty):
        """An unconfirmed owner resolves to unknown, and unknown is reportable.

        Treating a tentative link as proof of a live owner is what lets a
        privileged account with a departed owner pass unnoticed.
        """
        _, _, v, _, _ = dirty
        r4 = rule_orphaned_admin_account(v)
        flagged = {f.subject for f in r4.findings}
        by_sam = {c["ad_sam"]: c for c in v.clusters if c["ad_sam"]}
        for sam, c in by_sam.items():
            if c["account_type"] == "HUMAN_ADMIN" and c["status"] != "RESOLVED":
                assert sam in flagged, f"{sam} has an unconfirmed owner but was cleared"


# ==========================================================================
# Coverage must be declared, not implied
# ==========================================================================

class TestCoverageHonesty:

    def test_every_rule_declares_a_population(self, scored):
        results, _, _, _ = scored
        for r in results:
            assert r.population_total > 0, f"{r.rule_id} declares no population"
            assert r.population_examined <= r.population_total

    def test_partial_coverage_carries_an_explanation(self, scored):
        """Anything under full coverage must say what was left out."""
        results, _, _, _ = scored
        for r in results:
            if r.coverage < 0.999:
                assert r.excluded or r.notes, (
                    f"{r.rule_id} examined {r.coverage:.1%} of its population "
                    f"without recording why"
                )

    def test_unaggregated_applications_are_surfaced(self, dirty):
        _, _, v, _, _ = dirty
        r6 = rule_coverage_gap(v)
        assert r6.findings, "estate has unaggregated applications but none reported"
        for f in r6.findings:
            assert f.evidence["holdings"] > 0
            assert not f.visible_in_iga_data

    def test_sod_rules_blocked_by_coverage_are_named(self, dirty):
        """Where a segregation rule depends on an application the platform does
        not aggregate, the report must say so rather than return a clean result.
        """
        estate, _, v, sod, _ = dirty
        r5 = rule_sod_violation(v, sod)
        blocked = r5.excluded.get("sod_rules_unreachable_from_platform", 0)
        if blocked:
            assert any("do not aggregate" in n or "does not aggregate" in n
                       for n in r5.notes)


# ==========================================================================
# Independence from the platform under assurance
# ==========================================================================

class TestIndependence:

    def test_findings_are_marked_by_platform_visibility(self, scored):
        results, _, _, _ = scored
        for r in results:
            for f in r.findings:
                assert isinstance(f.visible_in_iga_data, bool)

    def test_a_material_share_is_unreachable_from_platform_data(self, scored):
        """If everything found were visible in the platform's own data, an
        independent check would add nothing."""
        _, metrics, _, _ = scored
        assert metrics["aggregate"]["invisible_share"] > 0.10

    def test_state_disagreement_is_structurally_platform_blind(self, scored):
        """This rule compares the platform against the directory, so no finding
        it produces can be visible from platform data alone."""
        results, _, _, _ = scored
        r2 = next(r for r in results
                  if r.rule_id == "R2_PLATFORM_STATE_DISAGREEMENT")
        assert all(not f.visible_in_iga_data for f in r2.findings)

    def test_detection_is_unchanged_when_platform_data_is_removed(self, dirty):
        """Platform data may label a finding but must never produce one.

        Tested behaviourally rather than by inspecting the source: the rules
        are re-run against an estate view whose platform entitlement feed has
        been emptied. If any finding disappears, that finding depended on the
        platform's own claims — and a control that needs the platform's data
        to detect the platform's failures is not independent.
        """
        _, _, v, sod, _ = dirty
        before = {(f.rule_id, f.subject) for r in run_all(v, sod) for f in r.findings}

        v.iga_holdings_by_sam = {}
        v.iga_ent = []
        after = {(f.rule_id, f.subject) for r in run_all(v, sod) for f in r.findings}

        assert before == after, (
            f"{len(before - after)} findings vanished without platform data: "
            f"{sorted(before - after)[:5]}"
        )


# ==========================================================================
# Determinism
# ==========================================================================

class TestDeterminism:

    def test_same_estate_same_findings(self, tmp_path_factory):
        t1 = tmp_path_factory.mktemp("d1")
        t2 = tmp_path_factory.mktemp("d2")
        _, _, v1, sod1, _ = _build(t1, clean=False, seed=777)
        _, _, v2, sod2, _ = _build(t2, clean=False, seed=777)

        f1 = sorted((f.rule_id, f.subject) for r in run_all(v1, sod1) for f in r.findings)
        f2 = sorted((f.rule_id, f.subject) for r in run_all(v2, sod2) for f in r.findings)
        assert f1 == f2

    def test_findings_carry_reproducible_evidence(self, scored):
        """Every finding must be re-derivable by hand from its evidence."""
        results, _, _, _ = scored
        for r in results:
            for f in r.findings:
                assert f.evidence, f"{f.rule_id}/{f.subject} has no evidence"
                assert f.summary, f"{f.rule_id}/{f.subject} has no summary"
                assert f.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
