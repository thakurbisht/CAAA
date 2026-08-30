"""
Phase 4 test suite: drift detection.

Drift is the first phase where findings rest on different kinds of evidence,
and the failure mode that matters most is presenting them as if they were
equivalent. A statistical outlier and an observed transfer both look like a
row in a CSV; only one of them will survive being challenged by the person
whose access is being revoked.

The suite is organised around that, then around the measurement errors this
phase invites: scoring a rule against conditions it deliberately declined to
judge, and against an oracle that merely restates the rule.
"""

import json
from pathlib import Path

import pytest

from generator import Config, EstateGenerator
from correlation import CorrelationEngine, load_snapshot, write_outputs as write_corr
from drift import (EstateTimeline, DriftOracle, run_all, score_all,
                   score_clean_run, DETERMINISTIC, EVIDENCE_BASIS)
from drift.rules import (learn_department_scoped, rule_retained_on_transfer,
                         rule_peer_group_outlier, rule_dormant_entitlement,
                         rule_standing_breakglass)


def _build(tmp, clean: bool, seed: int = 20260824, days: int = 150):
    out = tmp / ("clean" if clean else "dirty")
    cfg = Config(population=600, nhi_count=70, days=days,
                 seed=seed, out_dir=str(out))
    if clean:
        cfg = cfg.make_clean()
    g = EstateGenerator(cfg)
    g.write(g.build())

    snap = sorted((out / "snapshots").iterdir())[-1]
    feeds = load_snapshot(snap)
    eng = CorrelationEngine(feeds["hcm"], feeds["ad"], feeds["entra"], feeds["iga"])
    write_corr(out / "_corr", eng.correlate(), eng.disagreements, {})

    t = EstateTimeline(out, out / "_corr" / "correlation_map.csv")
    cat = json.load(open(out / "ground_truth" / "entitlement_catalogue.json"))
    bg = {k for k in cat if "BREAKGLASS" in k.upper()}
    return out, t, bg, DriftOracle(out, t)


@pytest.fixture(scope="module")
def dirty(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("drift"), clean=False)


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("drift"), clean=True)


@pytest.fixture(scope="module")
def scored(dirty):
    _, t, bg, oracle = dirty
    results = run_all(t, bg)
    return results, score_all(results, oracle, bg), t, oracle, bg


# ==========================================================================
# Evidence is graded, not flattened
# ==========================================================================

class TestEvidentiaryHonesty:

    def test_every_finding_declares_its_basis(self, scored):
        results, _, _, _, _ = scored
        for r in results:
            assert r.basis in EVIDENCE_BASIS
            for f in r.findings:
                assert f.basis in EVIDENCE_BASIS

    def test_statistical_findings_never_outrank_deterministic_ones(self, scored):
        """A peer outlier must not be raised to the severity of an observed
        fact. Sorting a report by severity would otherwise put a statistical
        hint above a documented transfer."""
        results, _, _, _, _ = scored
        for r in results:
            if r.rule_id in DETERMINISTIC:
                continue
            for f in r.findings:
                assert f.severity in ("MEDIUM", "LOW"), (
                    f"{f.rule_id} raised a {f.severity} finding on a "
                    f"{f.basis} basis"
                )

    def test_statistical_rule_is_not_scored_on_precision(self, scored):
        """There is no truth set for 'unusual'. A legitimate specialist is a
        correct outlier, so counting them as an error measures nothing."""
        _, metrics, _, _, _ = scored
        d2 = next(s for s in metrics["per_rule"]
                  if s["rule_id"] == "D2_PEER_GROUP_OUTLIER")
        assert d2["scored"] is False
        assert "reason" in d2

    def test_policy_thresholds_travel_with_the_finding(self, scored):
        """A dormancy finding is only as defensible as its threshold, so the
        threshold belongs in the evidence rather than in a config file the
        reader never sees."""
        results, _, _, _, _ = scored
        d3 = next(r for r in results if r.rule_id == "D3_DORMANT_ENTITLEMENT")
        for f in d3.findings:
            assert "dormancy_threshold_days" in f.evidence

    def test_never_used_is_distinguished_from_idle(self, scored):
        """Absence of a usage record is not proof of non-use."""
        results, _, _, _, _ = scored
        d3 = next(r for r in results if r.rule_id == "D3_DORMANT_ENTITLEMENT")
        conditions = {f.evidence["condition"] for f in d3.findings}
        assert conditions <= {"NEVER_USED", "IDLE"}
        for f in d3.findings:
            if f.evidence["condition"] == "NEVER_USED":
                assert "caveat" in f.evidence


# ==========================================================================
# Drift must be observed, not read off a convenience field
# ==========================================================================

class TestObservation:

    def test_transfers_come_from_consecutive_extracts(self, dirty):
        """A production HR feed does not carry a 'this was granted for
        department X' column. Transfers must be derived by comparing
        snapshots, or the rule is reading the answer."""
        _, t, _, _ = dirty
        observed = t.observed_transfers()
        assert observed, "no transfers observed across the window"
        for tr in observed:
            a, b = tr["observed_between"]
            assert a < b
            assert tr["from_dept"] != tr["to_dept"]

    def test_department_scoping_is_learned_not_pattern_matched(self, dirty):
        """Scoping by name prefix missed every clinical role entitlement.

        The learned version must pick up entitlements whose names carry no
        department marker, and must reject the one that is genuinely global.
        """
        _, t, _, _ = dirty
        scoped = learn_department_scoped(t)
        assert scoped, "no department-scoped entitlements identified"

        clinical = {e for e in scoped if e.startswith("CERN_SEC_RN_")}
        assert clinical, (
            "no clinical role entitlements classed as department-scoped; "
            "the rule has regressed to prefix matching"
        )
        assert "CERN_SEC_RN_GENERAL" not in scoped, (
            "a globally-held entitlement was classed as department-scoped"
        )

    def test_continuity_findings_span_the_whole_window(self, scored):
        """Standing access is a claim about history, not about now."""
        results, _, t, _, _ = scored
        d4 = next(r for r in results if r.rule_id == "D4_STANDING_BREAKGLASS")
        for f in d4.findings:
            assert f.evidence["snapshots_present"] == f.evidence["snapshots_total"]
            assert f.evidence["snapshots_total"] == len(t.snapshots)


# ==========================================================================
# Measuring the right population
# ==========================================================================

class TestMeasurement:

    def test_recall_excludes_faults_inside_the_grace_period(self, scored):
        """The rule withholds judgement inside the grace window, so scoring
        against those faults reports the grace period as a failure.

        Measured naively this rule looked like 60% recall; nearly every
        apparent miss had transferred within days of the final snapshot.
        """
        _, _, t, oracle, _ = scored
        all_live = oracle.live_injected("RETAINED_ON_TRANSFER")
        assessable = oracle.live_injected("RETAINED_ON_TRANSFER", grace_days=30)
        assert assessable <= all_live
        assert len(assessable) <= len(all_live)

    def test_recall_excludes_workers_who_have_since_left(self, scored):
        """A retention fault belonging to a departed worker is a leaver
        finding, and belongs to Phase 3."""
        _, _, t, oracle, _ = scored
        live = oracle.live_injected("RETAINED_ON_TRANSFER")
        for sam in live:
            c = t.cluster_by_sam.get(sam, {})
            pn = c.get("hcm_person_number")
            if pn and pn in t.hcm:
                assert t.hcm[pn]["assignment_status"] == "ACTIVE_ASSIGN"

    def test_assessable_recall_is_high(self, scored):
        _, metrics, _, _, _ = scored
        d1 = next(s for s in metrics["per_rule"]
                  if s["rule_id"] == "D1_RETAINED_ON_TRANSFER")
        assert d1["injected"]["recall_on_live"] >= 0.85, (
            f"caught {d1['injected']['caught']}/"
            f"{d1['injected']['planted_still_live']} assessable retentions"
        )

    def test_no_false_positives_on_deterministic_rules(self, scored):
        _, metrics, _, _, _ = scored
        bad = [(s["rule_id"], s["false_positives"])
               for s in metrics["per_rule"]
               if s.get("scored") and s["false_positives"]]
        assert not bad, f"unconfirmed findings: {bad}"

    def test_clean_estate_yields_no_unconfirmed_findings(self, clean):
        _, t, bg, oracle = clean
        cs = score_clean_run(run_all(t, bg), oracle, bg)
        assert cs["clean"], cs["per_rule"]


# ==========================================================================
# Correlation confidence, again
# ==========================================================================

class TestCorrelationGating:

    def test_transfer_findings_rest_on_confirmed_correlations(self, scored):
        results, _, t, _, _ = scored
        d1 = next(r for r in results if r.rule_id == "D1_RETAINED_ON_TRANSFER")
        for f in d1.findings:
            assert t.cluster_by_sam[f.subject]["status"] == "RESOLVED"

    def test_excluded_transfers_are_counted(self, scored):
        results, _, _, _, _ = scored
        d1 = next(r for r in results if r.rule_id == "D1_RETAINED_ON_TRANSFER")
        if d1.excluded.get("correlation_unconfirmed"):
            assert any("unassessed" in n.lower() for n in d1.notes)


# ==========================================================================
# Coverage and volume
# ==========================================================================

class TestCoverage:

    def test_small_peer_groups_are_excluded_and_declared(self, dirty):
        """In a group of three everyone is an outlier by construction."""
        _, t, _, _ = dirty
        r = rule_peer_group_outlier(t, min_peers=8)
        if r.excluded.get("peer_group_too_small"):
            assert any("too small" in n or "outlier by construction" in n
                       for n in r.notes)
        assert r.coverage <= 1.0

    def test_every_rule_states_its_limits(self, scored):
        results, _, _, _, _ = scored
        for r in results:
            assert r.notes, f"{r.rule_id} declares no limitations"

    def test_snapshot_resolution_limit_is_disclosed(self, scored):
        """A break-glass grant taken and surrendered between two snapshots is
        invisible, and the report must say so."""
        results, _, _, _, _ = scored
        d4 = next(r for r in results if r.rule_id == "D4_STANDING_BREAKGLASS")
        assert any("resolution limit" in n or "invisible" in n for n in d4.notes)


class TestDeterminism:

    def test_same_estate_same_drift(self, tmp_path_factory):
        _, t1, bg1, _ = _build(tmp_path_factory.mktemp("a"), clean=False, seed=4242)
        _, t2, bg2, _ = _build(tmp_path_factory.mktemp("b"), clean=False, seed=4242)
        f1 = sorted((f.rule_id, f.subject) for r in run_all(t1, bg1) for f in r.findings)
        f2 = sorted((f.rule_id, f.subject) for r in run_all(t2, bg2) for f in r.findings)
        assert f1 == f2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
