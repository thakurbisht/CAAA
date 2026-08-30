"""
Phase 2 test suite: correlation engine.

The tests are organised around the failure modes that matter in an assurance
context, in order of severity:

  1. False links      — privilege attributed to the wrong human
  2. Laundered errors — a weak link inherited as a strong one
  3. Silent contests  — two accounts claiming one human, resolved arbitrarily
  4. Lost independence— the engine trusting the platform it is assuring
"""

import json
from pathlib import Path

import pytest

from generator import Config, EstateGenerator
from correlation import (CorrelationEngine, classify_ad_account,
                         classify_entra_account, load_snapshot, score)


@pytest.fixture(scope="module")
def estate(tmp_path_factory):
    """One generated estate shared across the module."""
    out = tmp_path_factory.mktemp("estate")
    cfg = Config(population=600, nhi_count=70, days=90,
                 seed=20260824, out_dir=str(out))
    g = EstateGenerator(cfg)
    snaps = g.build()
    g.write(snaps)
    return out


@pytest.fixture(scope="module")
def correlated(estate):
    snap = sorted((estate / "snapshots").iterdir())[-1]
    feeds = load_snapshot(snap)
    eng = CorrelationEngine(feeds["hcm"], feeds["ad"], feeds["entra"], feeds["iga"])
    clusters = eng.correlate()
    imap = json.load(open(estate / "ground_truth" / "identity_map.json"))
    return eng, clusters, score(clusters, imap), imap


# ==========================================================================
# Account classification
# ==========================================================================

class TestClassification:

    @pytest.mark.parametrize("sam,expected", [
        ("svc-backup", "NHI"),
        ("hl7-cerner-lab", "NHI"),
        ("dev-infusion-icu-01", "NHI"),
        ("ws-icu-station-3", "NHI"),
        ("a-omar.khan", "HUMAN_ADMIN"),
        ("adm-fatima.ahmed", "HUMAN_ADMIN"),
        ("omar.khan", "HUMAN_PRIMARY"),
        ("nkhan", "HUMAN_PRIMARY"),
    ])
    def test_ad_account_types(self, sam, expected):
        assert classify_ad_account(sam).account_type == expected

    def test_admin_prefix_yields_base_account(self):
        c = classify_ad_account("a-omar.khan")
        assert c.derived_owner_sam == "omar.khan"

    def test_empty_sam_is_unknown_not_guessed(self):
        c = classify_ad_account("")
        assert c.account_type == "UNKNOWN"
        assert c.confidence == 0.0

    def test_entra_guest_detected_by_either_signal(self):
        by_type = classify_entra_account("a.b@x.com", "Guest", "FALSE")
        by_upn = classify_entra_account("a.b_ext#EXT#@x.com", "Member", "FALSE")
        assert by_type.account_type == "GUEST"
        assert by_upn.account_type == "GUEST"

    def test_cloud_only_distinguished_from_synced(self):
        c = classify_entra_account("a.b@x.com", "Member", "FALSE")
        assert c.account_type == "CLOUD_ONLY"


# ==========================================================================
# The primary failure mode: false links
# ==========================================================================

class TestFalseLinks:

    def test_no_false_links_at_default_threshold(self, correlated):
        _, _, metrics, _ = correlated
        ls = metrics["link_scoring"]
        assert ls["false_links"] == 0, (
            f"{ls['false_links']} accounts linked to the wrong human. "
            f"Examples: {metrics['false_link_examples'][:3]}"
        )

    def test_precision_is_total(self, correlated):
        _, _, metrics, _ = correlated
        assert metrics["link_scoring"]["precision"] == 1.0

    def test_recall_is_acceptable(self, correlated):
        _, _, metrics, _ = correlated
        assert metrics["link_scoring"]["recall"] > 0.90

    def test_nhi_never_linked_to_a_human(self, correlated):
        """A service account is not its owner's account.

        Linking one to a human would attribute standing privilege to a
        person who does not personally hold it.
        """
        _, _, metrics, _ = correlated
        assert metrics["nhi_handling"]["wrongly_linked_to_a_human"] == 0

    def test_account_typing_is_exact(self, correlated):
        _, _, metrics, _ = correlated
        assert metrics["account_typing"]["accuracy"] == 1.0


# ==========================================================================
# Laundered confidence
# ==========================================================================

class TestConfidenceInheritance:

    def test_admin_never_exceeds_its_parent(self, correlated):
        """A derived link cannot be more certain than its derivation."""
        _, clusters, _, _ = correlated
        by_id = {c.cluster_id: c for c in clusters}

        checked = 0
        for c in clusters:
            if c.account_type != "HUMAN_ADMIN" or not c.parent_cluster_id:
                continue
            parent = by_id.get(c.parent_cluster_id)
            if parent and "HCM" in c.links:
                assert c.confidence <= parent.confidence + 1e-9, (
                    f"{c.key('AD')} at {c.confidence:.3f} exceeds parent "
                    f"{parent.key('AD')} at {parent.confidence:.3f}"
                )
                checked += 1
        assert checked > 0, "no admin satellites were exercised"

    def test_admin_without_primary_is_not_resolved(self, correlated):
        """A privileged account whose base account does not exist is a
        finding, not something to resolve on the strength of its prefix."""
        _, clusters, _, _ = correlated
        for c in clusters:
            if c.account_type == "HUMAN_ADMIN" and c.parent_cluster_id is None:
                assert c.status != "RESOLVED"

    def test_tier_confidences_are_ordered(self):
        from correlation.matcher import TIER_BASE
        assert (TIER_BASE["T1_EMPLOYEE_ID"]
                > TIER_BASE["T2_UPN_LOCALPART"]
                > TIER_BASE["T3_NAME_DEPT"]
                > TIER_BASE["T4_NAME_FUZZY"])


# ==========================================================================
# Contested records
# ==========================================================================

class TestCollisionDetection:

    def test_contested_records_are_demoted_not_arbitrated(self, correlated):
        """Where two primary accounts claim one human, neither is asserted."""
        _, clusters, _, _ = correlated
        claims = {}
        for c in clusters:
            if c.account_type != "HUMAN_PRIMARY":
                continue
            pn = c.key("HCM")
            if pn:
                claims.setdefault(pn, []).append(c)

        for pn, cs in claims.items():
            if len(cs) > 1:
                assert all(c.status != "RESOLVED" for c in cs), (
                    f"person '{pn}' is claimed by {len(cs)} accounts and at "
                    f"least one was asserted anyway"
                )

    def test_contests_are_reported(self, correlated):
        eng, _, _, _ = correlated
        contests = [d for d in eng.disagreements
                    if d["type"] == "CONTESTED_HCM_RECORD"]
        assert len(contests) > 0, "no contests surfaced in a colliding estate"

    def test_every_contest_is_genuine(self, correlated):
        """Contests should not fire on accounts that truly share an owner."""
        eng, _, _, imap = correlated
        by_pid = {r["pid"]: r for r in imap}
        truth = {}
        for r in imap:
            if not r["ad_sam"] or r["kind"] == "NHI":
                continue
            o = by_pid.get(r["owner_pid"]) if r["owner_pid"] else None
            truth[r["ad_sam"]] = o["person_number"] if (o and o.get("in_hcm")) else None

        for d in eng.disagreements:
            if d["type"] != "CONTESTED_HCM_RECORD":
                continue
            sams = d["ad_sam"].split(" + ")
            owners = {truth.get(s) for s in sams}
            assert len(owners) > 1, (
                f"contested {sams} but they share owner {owners}"
            )


# ==========================================================================
# Independence from the platform under assurance
# ==========================================================================

class TestIndependence:

    def test_engine_does_not_consume_iga_correlation(self):
        """The platform's own correlation must not be a matching input.

        Reading `correlated_person_number` as evidence would mean assuring a
        system with its own output.
        """
        src = Path("correlation/matcher.py").read_text()
        tier_section = src[src.index("def _t1_employee_id"):src.index("def _corroborate")]
        assert "correlated_person_number" not in tier_section
        assert "iga_by_pn" not in tier_section

    def test_disagreements_only_cite_asserted_links(self, correlated):
        """A candidate in adjudication is not an assertion, so it must not be
        raised as a contradiction of the platform."""
        eng, clusters, _, _ = correlated
        by_sam = {c.key("AD"): c for c in clusters if c.key("AD")}

        for d in eng.disagreements:
            if d["type"] != "CORRELATION_CONFLICT":
                continue
            c = by_sam.get(d["ad_sam"])
            assert c and c.status == "RESOLVED", (
                f"conflict raised for {d['ad_sam']} which the engine has not "
                f"asserted (status {c.status if c else 'missing'})"
            )

    def test_platform_gaps_are_surfaced(self, correlated):
        eng, _, _, _ = correlated
        types = {d["type"] for d in eng.disagreements}
        assert "IGA_UNCORRELATED_BUT_RESOLVABLE" in types
        assert "IGA_ASSERTS_UNVERIFIABLE_LINK" in types

    def test_platform_findings_are_verifiably_correct(self, correlated):
        """Where the engine says the platform has missed a correlation, the
        engine's answer must actually be right."""
        eng, _, _, imap = correlated
        by_pid = {r["pid"]: r for r in imap}
        truth = {}
        for r in imap:
            if not r["ad_sam"] or r["kind"] == "NHI":
                continue
            o = by_pid.get(r["owner_pid"]) if r["owner_pid"] else None
            truth[r["ad_sam"]] = o["person_number"] if (o and o.get("in_hcm")) else None

        rows = [d for d in eng.disagreements
                if d["type"] == "IGA_UNCORRELATED_BUT_RESOLVABLE"]
        assert rows, "no such findings to verify"
        wrong = [d for d in rows if d["independent_result"] != truth.get(d["ad_sam"])]
        assert not wrong, f"{len(wrong)} of {len(rows)} findings are incorrect"


# ==========================================================================
# Threshold behaviour
# ==========================================================================

class TestThresholds:

    def test_raising_threshold_never_adds_false_links(self, estate):
        snap = sorted((estate / "snapshots").iterdir())[-1]
        feeds = load_snapshot(snap)
        imap = json.load(open(estate / "ground_truth" / "identity_map.json"))

        prev = None
        for t in (0.70, 0.80, 0.90):
            eng = CorrelationEngine(feeds["hcm"], feeds["ad"], feeds["entra"],
                                    feeds["iga"], accept=t)
            m = score(eng.correlate(), imap)
            fp = m["link_scoring"]["false_links"]
            if prev is not None:
                assert fp <= prev, f"false links rose from {prev} to {fp} at {t}"
            prev = fp

    def test_unresolved_accounts_carry_a_reason(self, correlated):
        """Everything routed to a human must say why, or the queue is
        unworkable."""
        _, clusters, _, _ = correlated
        for c in clusters:
            if c.status in ("REVIEW", "UNRESOLVED"):
                assert c.notes, f"{c.key('AD') or c.key('ENTRA')} queued without a reason"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
