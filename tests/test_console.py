"""
Tests for the decision log and the review console.

Two properties matter here and neither existed before.

Item identity must survive a re-run. A finding was previously the Nth row of a
CSV, so inserting one renumbered everything below it and a decision recorded
against "R0001" silently attached itself to a different account next month.
Identity is now derived from what the finding is about.

And a suppression must expire. An accepted risk with no end date hides a real
finding permanently, and nobody notices the suppression outliving the reason
for it — which is how the answer to "why was this never fixed" becomes "nobody
knows".
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from decisions import (Decision, DecisionLog, finding_id, cluster_item_id,
                       item_id, now, write_run_manifest, VERDICTS,
                       LINK_VERDICTS)


def d(item, verdict="confirmed", by="Reviewer", at=None, expires=None,
      kind="finding", subject=""):
    return Decision(item_id=item, item_kind=kind, verdict=verdict, note="",
                    decided_by=by, decided_at=at or now(),
                    expires_on=expires, subject=subject)


# ==========================================================================
# Identity
# ==========================================================================

class TestItemIdentity:

    def test_same_condition_same_id(self):
        a = finding_id("R1_LEAVER", "a-sarah.khan")
        b = finding_id("R1_LEAVER", "a-sarah.khan")
        assert a == b

    def test_different_subject_different_id(self):
        assert finding_id("R1_LEAVER", "a") != finding_id("R1_LEAVER", "b")

    def test_different_rule_different_id(self):
        assert finding_id("R1", "x") != finding_id("R4", "x")

    def test_detail_separates_two_breaches_by_one_person(self):
        """One identity breaching two segregation rules is two findings.

        Collapsing them to the account would let a decision on one silently
        suppress the other.
        """
        a = finding_id("R5_SOD", "amina.khan", "SOD-CLIN-01")
        b = finding_id("R5_SOD", "amina.khan", "SOD-CLIN-02")
        assert a != b

    def test_id_does_not_depend_on_position(self):
        """The property the old positional ids lacked."""
        rows = [("R1", "a"), ("R1", "b"), ("R1", "c")]
        first = [finding_id(r, s) for r, s in rows]
        inserted = [finding_id(r, s) for r, s in [("R1", "z")] + rows]
        assert first == inserted[1:]

    def test_cluster_id_keys_on_the_account(self):
        """Cluster numbers are assigned in iteration order and change between
        runs; the account name does not."""
        assert (cluster_item_id("C000001", "amina.khan")
                == cluster_item_id("C000999", "amina.khan"))

    def test_ids_are_prefixed_by_kind(self):
        assert finding_id("R1", "x").startswith("f-")
        assert cluster_item_id("C1", "x").startswith("c-")

    def test_separator_prevents_field_collisions(self):
        """'ab' + 'c' must not collide with 'a' + 'bc'."""
        assert item_id("f", "ab", "c") != item_id("f", "a", "bc")


# ==========================================================================
# Append-only log
# ==========================================================================

class TestDecisionLog:

    def test_append_and_read(self, tmp_path):
        log = DecisionLog(tmp_path / "d.jsonl")
        log.append(d("f-1"))
        assert len(log.all()) == 1

    def test_nothing_is_overwritten(self, tmp_path):
        """An audit asking why an account was left alone in March needs the
        March decision, not the current state of a row somebody replaced."""
        log = DecisionLog(tmp_path / "d.jsonl")
        log.append(d("f-1", "confirmed", at="2026-03-01T00:00:00+00:00"))
        log.append(d("f-1", "false_positive", at="2026-04-01T00:00:00+00:00"))
        assert len(log.all()) == 2
        assert len(log.history("f-1")) == 2

    def test_latest_decision_stands(self, tmp_path):
        log = DecisionLog(tmp_path / "d.jsonl")
        log.append(d("f-1", "confirmed", at="2026-03-01T00:00:00+00:00"))
        log.append(d("f-1", "false_positive", at="2026-04-01T00:00:00+00:00"))
        assert log.current()["f-1"].verdict == "false_positive"

    def test_history_is_newest_first(self, tmp_path):
        log = DecisionLog(tmp_path / "d.jsonl")
        log.append(d("f-1", at="2026-01-01T00:00:00+00:00"))
        log.append(d("f-1", at="2026-06-01T00:00:00+00:00"))
        assert log.history("f-1")[0].decided_at.startswith("2026-06")

    def test_a_malformed_line_does_not_lose_the_history(self, tmp_path):
        p = tmp_path / "d.jsonl"
        log = DecisionLog(p)
        log.append(d("f-1"))
        with open(p, "a") as f:
            f.write("{ this is not json\n")
        log.append(d("f-2"))
        assert len(log.all()) == 2

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert DecisionLog(tmp_path / "absent.jsonl").all() == []


# ==========================================================================
# Expiry
# ==========================================================================

class TestExpiry:

    def test_a_decision_without_expiry_stands(self):
        assert d("f-1").active

    def test_a_future_expiry_stands(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        assert d("f-1", "accepted_risk", expires=future).active

    def test_a_past_expiry_does_not(self):
        """The finding comes back on its own rather than needing somebody to
        remember it."""
        past = (date.today() - timedelta(days=1)).isoformat()
        assert not d("f-1", "accepted_risk", expires=past).active

    def test_expired_decisions_leave_current(self, tmp_path):
        log = DecisionLog(tmp_path / "d.jsonl")
        past = (date.today() - timedelta(days=5)).isoformat()
        log.append(d("f-1", "accepted_risk", expires=past))
        assert "f-1" not in log.current()
        assert len(log.all()) == 1      # still in the record

    def test_expiring_soon_is_ordered_by_urgency(self, tmp_path):
        log = DecisionLog(tmp_path / "d.jsonl")
        for days, item in ((25, "f-c"), (3, "f-a"), (12, "f-b")):
            log.append(d(item, "accepted_risk",
                         expires=(date.today() + timedelta(days=days)).isoformat()))
        assert [x.item_id for x in log.expiring_soon(30)] == ["f-a", "f-b", "f-c"]

    def test_expiring_soon_ignores_distant_dates(self, tmp_path):
        log = DecisionLog(tmp_path / "d.jsonl")
        log.append(d("f-1", "accepted_risk",
                     expires=(date.today() + timedelta(days=200)).isoformat()))
        assert log.expiring_soon(30) == []

    def test_an_unparseable_date_does_not_silently_suppress(self):
        """A bad date must not be read as 'expired', which would resurface a
        finding, nor crash the load."""
        assert d("f-1", "accepted_risk", expires="not-a-date").active


# ==========================================================================
# Run manifest
# ==========================================================================

class TestRunManifest:

    def test_records_what_produced_the_findings(self, tmp_path):
        p = tmp_path / "run.json"
        rid = write_run_manifest(p, snapshot="2026-05-31", scored=False,
                                 estate="/data/extracts")
        m = json.loads(p.read_text())
        assert m["run_id"] == rid
        assert m["snapshot"] == "2026-05-31"
        assert m["scored"] is False
        assert m["started_at"]

    def test_each_run_is_distinguishable(self, tmp_path):
        a = write_run_manifest(tmp_path / "a.json", snapshot="2026-01-01")
        b = write_run_manifest(tmp_path / "b.json", snapshot="2026-02-01")
        assert a != b

    def test_write_is_atomic(self, tmp_path):
        """A half-written manifest would make an auditable record unreadable."""
        p = tmp_path / "run.json"
        write_run_manifest(p, snapshot="a")
        write_run_manifest(p, snapshot="b")
        assert json.loads(p.read_text())["snapshot"] == "b"
        assert not list(tmp_path.glob("tmp*"))


# ==========================================================================
# Vocabulary
# ==========================================================================

class TestVerdicts:

    def test_every_verdict_explains_itself(self):
        for vocab in (VERDICTS, LINK_VERDICTS):
            for name, meaning in vocab.items():
                assert meaning and len(meaning) > 10, name

    def test_accepted_risk_exists_only_for_findings(self):
        """A correlation link is right or wrong. There is nothing to accept."""
        assert "accepted_risk" in VERDICTS
        assert "accepted_risk" not in LINK_VERDICTS

    def test_links_can_be_left_undetermined(self):
        """Forcing a yes or no on ambiguous evidence is how a wrong link gets
        recorded as a confirmed one."""
        assert "unknown" in LINK_VERDICTS


# ==========================================================================
# The console's own contract
# ==========================================================================

class TestConsole:

    def test_server_binds_to_loopback_only(self):
        """This process reads workforce data. A console reachable from the
        network is a data exposure wearing a convenience's clothing."""
        src = Path("serve.py").read_text()
        assert 'ThreadingHTTPServer(("127.0.0.1"' in src
        assert '"0.0.0.0"' not in src

    def test_no_inline_script_in_the_ui(self):
        """The console renders account names and reviewer notes. Scripts load
        from this origin only, so markup in either cannot execute."""
        html = Path("ui/index.html").read_text()
        assert "<script src=" in html
        assert "<script>" not in html

    def test_content_security_policy_forbids_inline_script(self):
        src = Path("serve.py").read_text()
        assert "script-src 'self'" in src
        assert "script-src 'unsafe-inline'" not in src

    def test_ui_escapes_interpolated_values(self):
        js = Path("ui/app.js").read_text(encoding="utf-8")
        assert "esc = " in js
        for ch in ("&", "<", ">"):
            assert ch in js.split("const esc")[1][:200]

    def test_hidden_panels_are_hidden(self):
        """`display: flex` and `display: block` beat what [hidden] sets.

        Without an explicit rule the decision drawer was open on first paint,
        covering the page, and the expiry field showed on decisions that have
        no expiry. Both looked like logic bugs and were neither.
        """
        css = Path("ui/app.css").read_text(encoding="utf-8")
        assert ".drawer[hidden]" in css
        assert ".field[hidden]" in css

    def test_motion_respects_the_reduced_motion_preference(self):
        css = Path("ui/app.css").read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in css

    def test_keyboard_path_still_records_a_reviewer(self):
        """Deciding from the keyboard opens the drawer pre-set rather than
        writing silently. A decision with nobody against it is not evidence."""
        js = Path("ui/app.js").read_text(encoding="utf-8")
        assert "function quickDecide" in js
        assert "openDrawer(" in js.split("function quickDecide")[1][:300]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
