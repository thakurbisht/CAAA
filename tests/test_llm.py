"""
Phase 6 test suite: the interpretation layer.

This layer is the only part of the project whose output cannot be re-derived.
A paraphrase has no derivation, and asking the model the same question twice
is not verification. So what is tested is not the model — it is the machinery
that decides whether to trust the model on any given call.

The failure modes are constructed rather than waited for. A real model drops
findings from a clustering occasionally and unpredictably; a scripted one
drops them on demand, which is the only way to assert that the check catches
it. Testing against a live model would establish that it behaved on the day
the suite ran, and nothing more.
"""

import json

import pytest

from llm import (LLMClient, ScriptedClient, translate_one, template,
                 cluster_findings, by_rule, assess, grade_for,
                 validate_translation, validate_clustering,
                 validate_narrative, extract_json)


CAT = {
    "PYX_DISPENSE_CTRL_SUB": {"layer": "APPLICATION", "app": "PYXIS",
                              "risk": "CRITICAL", "aggregated_by_iga": False},
    "GRP_ALL_STAFF": {"layer": "DIRECTORY", "app": "AD",
                      "risk": "LOW", "aggregated_by_iga": True},
}


def findings(n=12):
    return [{"finding_id": f"R{i:04d}", "rule_id": f"RULE_{i % 3}",
             "severity": "HIGH", "summary": f"finding number {i}"}
            for i in range(1, n + 1)]


# ==========================================================================
# JSON recovery
# ==========================================================================

class TestExtraction:

    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_wrapped_in_prose(self):
        """Models preface JSON with commentary even when told not to."""
        assert extract_json('Sure! Here you go:\n{"a": 1}\nHope that helps.') == {"a": 1}

    def test_malformed_json_is_not_guessed_at(self):
        """Recovering from fences is reasonable; repairing broken JSON is not."""
        assert extract_json('{"a": 1,,, "b"') is None

    def test_empty(self):
        assert extract_json("") is None
        assert extract_json("   ") is None


# ==========================================================================
# Translation validation
# ==========================================================================

class TestTranslationValidation:

    def test_accepts_a_good_translation(self):
        v = validate_translation(
            "PYX_DISPENSE_CTRL_SUB", CAT["PYX_DISPENSE_CTRL_SUB"],
            "Allows dispensing of controlled drugs from automated cabinets on the ward.")
        assert v.ok, v.failures

    def test_rejects_the_wrong_application(self):
        """A description naming the wrong system sends a reviewer to the wrong
        owner to ask about it."""
        v = validate_translation(
            "PYX_DISPENSE_CTRL_SUB", CAT["PYX_DISPENSE_CTRL_SUB"],
            "Allows dispensing of controlled drugs through the CERNER system.")
        assert not v.ok
        assert any("CERNER" in f for f in v.failures)

    def test_rejects_understated_risk(self):
        """This is the failure that does damage: a reviewer approves critical
        access because the sentence in front of them called it low risk."""
        v = validate_translation(
            "PYX_DISPENSE_CTRL_SUB", CAT["PYX_DISPENSE_CTRL_SUB"],
            "A routine low-risk permission for dispensing medication.")
        assert not v.ok
        assert any("low risk" in f for f in v.failures)

    def test_rejects_restating_the_code(self):
        v = validate_translation(
            "PYX_DISPENSE_CTRL_SUB", CAT["PYX_DISPENSE_CTRL_SUB"],
            "This grants PYX_DISPENSE_CTRL_SUB to the user.")
        assert not v.ok

    def test_rejects_empty_and_overlong(self):
        meta = CAT["GRP_ALL_STAFF"]
        assert not validate_translation("GRP_ALL_STAFF", meta, "").ok
        assert not validate_translation("GRP_ALL_STAFF", meta, " ".join(["word"] * 80)).ok

    def test_warns_on_judgement(self):
        """Describing access is in remit. Deciding about it is not."""
        v = validate_translation(
            "GRP_ALL_STAFF", CAT["GRP_ALL_STAFF"],
            "Basic staff directory membership that should be revoked on exit.")
        assert v.ok
        assert v.warnings


class TestTranslationFallback:

    def test_no_backend_uses_the_template(self):
        c = LLMClient("none")
        t = translate_one(c, "GRP_ALL_STAFF", CAT["GRP_ALL_STAFF"])
        assert t.source == "template"
        assert t.description

    def test_bad_output_falls_back_rather_than_being_repaired(self):
        """A translation that misstates the system is discarded whole. There is
        no partially-correct version of it worth keeping."""
        c = ScriptedClient(['{"description": "Dispensing through CERNER, low-risk."}'])
        t = translate_one(c, "PYX_DISPENSE_CTRL_SUB", CAT["PYX_DISPENSE_CTRL_SUB"])
        assert t.source == "rejected"
        assert t.description == template("PYX_DISPENSE_CTRL_SUB",
                                         CAT["PYX_DISPENSE_CTRL_SUB"])
        assert t.failures

    def test_template_is_always_truthful_about_risk_and_app(self):
        for code, meta in CAT.items():
            text = template(code, meta)
            assert meta["app"] in text
            assert meta["risk"].lower() in text.lower()

    def test_good_output_is_kept(self):
        c = ScriptedClient(
            ['{"description": "Permits removal of controlled drugs from ward cabinets."}'])
        t = translate_one(c, "PYX_DISPENSE_CTRL_SUB", CAT["PYX_DISPENSE_CTRL_SUB"])
        assert t.source == "model"


# ==========================================================================
# Clustering — the check that earns the layer its place
# ==========================================================================

class TestClusteringValidation:

    def test_accepts_a_true_partition(self):
        ids = {f"R{i:04d}" for i in range(1, 7)}
        clusters = [
            {"name": "A", "finding_ids": ["R0001", "R0002", "R0003"]},
            {"name": "B", "finding_ids": ["R0004", "R0005", "R0006"]},
        ]
        assert validate_clustering(clusters, ids).ok

    def test_rejects_dropped_findings(self):
        """The failure this exists for. A grouping that loses findings still
        reads as a complete report; nothing in the prose reveals the gap."""
        ids = {f"R{i:04d}" for i in range(1, 7)}
        clusters = [{"name": "A", "finding_ids": ["R0001", "R0002"]}]
        v = validate_clustering(clusters, ids)
        assert not v.ok
        assert any("not placed" in f for f in v.failures)

    def test_rejects_invented_findings(self):
        ids = {"R0001", "R0002"}
        clusters = [{"name": "A", "finding_ids": ["R0001", "R0002", "R9999"]}]
        v = validate_clustering(clusters, ids)
        assert not v.ok
        assert any("do not exist" in f for f in v.failures)

    def test_rejects_double_counting(self):
        """A finding in two groups inflates every count downstream."""
        ids = {"R0001", "R0002"}
        clusters = [{"name": "A", "finding_ids": ["R0001", "R0002"]},
                    {"name": "B", "finding_ids": ["R0002"]}]
        v = validate_clustering(clusters, ids)
        assert not v.ok
        assert any("more than one cluster" in f for f in v.failures)

    def test_rejects_structural_nonsense(self):
        ids = {"R0001"}
        assert not validate_clustering([], ids).ok
        assert not validate_clustering([{"name": "A"}], ids).ok
        assert not validate_clustering([{"finding_ids": ["R0001"]}], ids).ok

    def test_warns_when_grouping_is_barely_grouping(self):
        ids = {f"R{i:04d}" for i in range(1, 5)}
        clusters = [{"name": f"C{i}", "finding_ids": [f"R{i:04d}"]}
                    for i in range(1, 5)]
        v = validate_clustering(clusters, ids)
        assert v.ok
        assert any("single finding" in w for w in v.warnings)


class TestClusteringFallback:

    def test_no_backend_groups_by_rule(self):
        f = findings()
        c = cluster_findings(LLMClient("none"), f)
        assert c.source == "by_rule"
        assert sum(len(x["finding_ids"]) for x in c.clusters) == len(f)

    def test_dropped_findings_reject_the_whole_clustering(self):
        """Not patched, not partially kept. A clustering that lost findings
        gives no basis for trusting the groups it did produce."""
        f = findings(10)
        bad = json.dumps([{"name": "Theme", "rationale": "x",
                           "finding_ids": [x["finding_id"] for x in f[:6]]}])
        c = cluster_findings(ScriptedClient([bad]), f)
        assert c.source == "by_rule"
        assert any("not placed" in x for x in c.failures)

    def test_fallback_never_loses_a_finding(self):
        f = findings(30)
        c = cluster_findings(ScriptedClient(["not json at all"]), f)
        placed = {i for x in c.clusters for i in x["finding_ids"]}
        assert placed == {x["finding_id"] for x in f}

    def test_valid_clustering_is_accepted(self):
        f = findings(9)
        good = json.dumps([
            {"name": "A", "rationale": "x",
             "finding_ids": [x["finding_id"] for x in f[:5]]},
            {"name": "B", "rationale": "y",
             "finding_ids": [x["finding_id"] for x in f[5:]]},
        ])
        c = cluster_findings(ScriptedClient([good]), f)
        assert c.source == "model"
        assert len(c.clusters) == 2

    def test_oversized_input_is_not_sent(self):
        """A request that will predictably fail the partition check is not
        worth making."""
        c = ScriptedClient(["should never be used"])
        r = cluster_findings(c, findings(400))
        assert r.source == "by_rule"
        assert c.calls == 0


# ==========================================================================
# Narrative
# ==========================================================================

class TestNarrativeValidation:

    def test_accepts_supplied_figures(self):
        v = validate_narrative(
            "Coverage was 86.2% across 267 findings.", {"86.2%", "267"})
        assert v.ok, v.failures

    def test_rejects_invented_figures(self):
        """A model summarising 267 findings will write 'over 300', and the
        reader cannot tell that number from the measured ones beside it."""
        v = validate_narrative(
            "Coverage was 91.4% across 312 findings.", {"86.2%", "267"})
        assert not v.ok

    def test_small_integers_are_ordinary_prose(self):
        v = validate_narrative(
            "Two rules could not be evaluated at all.", {"86.2%"})
        assert v.ok

    def test_warns_on_speculation(self):
        v = validate_narrative("Coverage was 86.2%. I recommend a full review.",
                               {"86.2%"})
        assert v.ok
        assert v.warnings


# ==========================================================================
# The score is never the model's
# ==========================================================================

class TestQualityIsComputed:

    def _metrics(self, coverages):
        return {"per_rule": [{"rule_id": f"R{i}", "coverage": c, "found": 10,
                              "excluded": {}}
                             for i, c in enumerate(coverages)]}

    def test_grade_comes_from_arithmetic_not_the_model(self):
        """A grade produced by a language model is an opinion in a metric's
        clothing, and would be the one number here nobody could re-derive."""
        m = self._metrics([0.99, 0.98, 0.97])
        a = assess(m, {"per_rule": []}, None)
        b = assess(m, {"per_rule": []},
                   ScriptedClient(["This certification is LIMITED and poor."]))
        assert a.grade == b.grade == "COMPREHENSIVE"

    def test_grade_bands(self):
        assert grade_for(0.99)[0] == "COMPREHENSIVE"
        assert grade_for(0.85)[0] == "ADEQUATE"
        assert grade_for(0.70)[0] == "PARTIAL"
        assert grade_for(0.20)[0] == "LIMITED"

    def test_a_weak_rule_is_not_averaged_away(self):
        """Weighting by population would let one near-complete check conceal
        another that examined almost nothing."""
        strong = assess(self._metrics([1.0, 1.0, 1.0]), {"per_rule": []}, None)
        mixed = assess(self._metrics([1.0, 1.0, 0.10]), {"per_rule": []}, None)
        assert mixed.weighted_coverage < strong.weighted_coverage
        assert mixed.grade != "COMPREHENSIVE"

    def test_narrative_with_invented_numbers_is_discarded(self):
        m = self._metrics([0.90, 0.90])
        a = assess(m, {"per_rule": []},
                   ScriptedClient(["We examined 99.9% of 5000 accounts."]))
        assert a.narrative_source == "rejected"
        assert "90.0%" in a.narrative or "90" in a.narrative

    def test_computed_narrative_needs_no_model(self):
        a = assess(self._metrics([0.80]), {"per_rule": []}, None)
        assert a.narrative_source == "computed"
        assert a.narrative

    def test_exclusions_are_reported_as_unexamined(self):
        m = {"per_rule": [{"rule_id": "R1", "coverage": 0.7, "found": 5,
                           "excluded": {"correlation_unconfirmed": 40}}]}
        a = assess(m, {"per_rule": []}, None)
        assert a.blind_spots
        assert "unexamined" in a.narrative


# ==========================================================================
# The layer cannot affect detection
# ==========================================================================

class TestContainment:

    def test_no_backend_still_produces_full_output(self):
        """Every number this project reports comes from phases 1-4. Losing the
        model must cost readability and nothing else."""
        c = LLMClient("none")
        assert not c.available
        f = findings(20)
        cl = cluster_findings(c, f)
        assert sum(len(x["finding_ids"]) for x in cl.clusters) == len(f)
        t = translate_one(c, "GRP_ALL_STAFF", CAT["GRP_ALL_STAFF"])
        assert t.description

    def test_a_hostile_model_cannot_change_the_finding_set(self):
        f = findings(15)
        hostile = ScriptedClient([json.dumps([
            {"name": "Everything is fine", "rationale": "no issues",
             "finding_ids": ["R0001"]}])])
        cl = cluster_findings(hostile, f)
        placed = {i for x in cl.clusters for i in x["finding_ids"]}
        assert placed == {x["finding_id"] for x in f}

    def test_client_failure_is_recorded_not_raised(self):
        c = ScriptedClient([])
        r = c.complete("anything")
        assert not r.ok


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
