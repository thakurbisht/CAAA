"""
Tests for feed loading, column mapping, and running without an answer key.

Two properties are asserted here, and both were broken until this layer
existed.

An extract whose columns are named by the source system must produce the same
findings as one using the canonical names. If mapping changes what is
detected, the mapping is doing more than renaming.

And detection must not require ground truth. The pipeline previously refused
to start without `identity_map.json`, which only a generated estate has —
so the tool could not be pointed at real data at all. Scoring needs an answer
key; finding things does not.
"""

import csv
import json
from pathlib import Path

import pytest

from feeds import (Profile, load_feeds, missing_columns, suggest_profile,
                   DEFAULT_FILES, REQUIRED)
from _preflight import has_ground_truth, require_estate


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def canonical(tmp_path):
    # Both fixtures write under the same tmp_path when a test requests them
    # together, so each owns a distinct subdirectory. Sharing one silently let
    # the second fixture overwrite the first.
    root = tmp_path / "canonical"
    snap = root / "snapshots" / "2026-01-01"
    write_csv(snap / "hcm_workers.csv", [
        {"person_number": "E1", "first_name": "Amina", "last_name": "Khan",
         "department": "ICU", "job_code": "RN-02",
         "assignment_status": "ACTIVE_ASSIGN"},
    ])
    write_csv(snap / "ad_accounts.csv", [
        {"sam_account_name": "amina.khan", "display_name": "Amina Khan",
         "employee_id": "E1", "enabled": "TRUE", "department": "ICU"},
    ])
    return root, snap


@pytest.fixture
def renamed(tmp_path):
    """The same data with source-system column names."""
    root = tmp_path / "renamed"
    snap = root / "snapshots" / "2026-01-01"
    write_csv(snap / "hcm_workers.csv", [
        {"EmployeeNumber": "E1", "GivenName": "Amina", "Surname": "Khan",
         "OrgUnit": "ICU", "PositionCode": "RN-02",
         "WorkerStatus": "ACTIVE_ASSIGN"},
    ])
    write_csv(snap / "ad_accounts.csv", [
        {"sAMAccountName": "amina.khan", "cn": "Amina Khan",
         "employeeID": "E1", "accountEnabled": "TRUE", "department": "ICU"},
    ])
    return root, snap


# ==========================================================================
# Mapping renames and nothing else
# ==========================================================================

class TestMapping:

    def test_canonical_extract_needs_no_profile(self, canonical):
        _, snap = canonical
        assert not missing_columns(load_feeds(snap))

    def test_renamed_extract_is_unreadable_without_a_profile(self, renamed):
        _, snap = renamed
        absent = missing_columns(load_feeds(snap))
        assert "hcm" in absent and "ad" in absent

    def test_a_profile_makes_it_readable(self, renamed):
        _, snap = renamed
        assert not missing_columns(load_feeds(snap, suggest_profile(snap)))

    def test_mapping_preserves_values(self, canonical, renamed):
        a = load_feeds(canonical[1])["hcm"][0]
        b = load_feeds(renamed[1], suggest_profile(renamed[1]))["hcm"][0]
        for col in REQUIRED["hcm"]:
            assert a[col] == b[col], f"{col} differs after mapping"

    def test_unmapped_columns_survive(self, tmp_path):
        snap = tmp_path / "s"
        write_csv(snap / "hcm_workers.csv",
                  [{"EmployeeNumber": "E1", "CostCentre": "CC-9"}])
        p = Profile(columns={"hcm": {"EmployeeNumber": "person_number"}})
        row = load_feeds(snap, p)["hcm"][0]
        assert row["person_number"] == "E1"
        assert row["CostCentre"] == "CC-9"

    def test_missing_feeds_load_as_empty(self, tmp_path):
        snap = tmp_path / "s"
        snap.mkdir()
        feeds = load_feeds(snap)
        assert set(feeds) == set(DEFAULT_FILES)
        assert all(v == [] for v in feeds.values())

    def test_profile_round_trips(self, tmp_path):
        p = Profile(name="acme", columns={"hcm": {"A": "person_number"}})
        p.save(tmp_path / "p.json")
        assert Profile.load(tmp_path / "p.json").columns == p.columns


# ==========================================================================
# The suggester must not guess wrongly
# ==========================================================================

class TestSuggestion:

    def test_employee_id_resolves_per_feed(self, renamed):
        """The same column name means different things in different feeds.

        `employeeID` is the HR key in an HR extract and the directory's
        reference to it in a directory extract. A flat alias table resolved it
        by whichever canonical name was registered first, and mapped the
        directory's column to `person_number`.
        """
        _, snap = renamed
        prof = suggest_profile(snap)
        assert prof.columns["ad"]["employeeID"] == "employee_id"
        assert prof.columns["hcm"]["EmployeeNumber"] == "person_number"

    def test_bitmask_columns_are_not_treated_as_booleans(self, tmp_path):
        """`userAccountControl` is a numeric flag field, not a boolean.

        Mapping it to `enabled` made every rule testing `enabled == "TRUE"`
        return nothing, so R2 silently reported zero findings on an estate
        that had thirteen. A plausible column of the wrong type is worse than
        no column, because the second is visible.
        """
        snap = tmp_path / "s"
        write_csv(snap / "ad_accounts.csv", [
            {"sAMAccountName": "a.b", "userAccountControl": "512",
             "accountEnabled": "TRUE"},
        ])
        prof = suggest_profile(snap)
        assert prof.columns["ad"].get("accountEnabled") == "enabled"
        assert "userAccountControl" not in prof.columns["ad"]

    def test_one_source_column_per_canonical_name(self, tmp_path):
        snap = tmp_path / "s"
        write_csv(snap / "ad_accounts.csv",
                  [{"sAMAccountName": "a", "accountName": "a", "cn": "A"}])
        prof = suggest_profile(snap)
        targets = list(prof.columns.get("ad", {}).values())
        assert len(targets) == len(set(targets))


# ==========================================================================
# Detection must not require an answer key
# ==========================================================================

class TestUnscoredPath:

    def test_estate_without_ground_truth_is_usable(self, canonical):
        estate, _ = canonical
        require_estate(estate)          # must not exit
        assert has_ground_truth(estate) is False

    def test_generated_estate_is_scorable(self, tmp_path):
        estate = tmp_path / "e"
        (estate / "snapshots" / "2026-01-01").mkdir(parents=True)
        (estate / "ground_truth").mkdir()
        (estate / "ground_truth" / "identity_map.json").write_text("[]")
        assert has_ground_truth(estate) is True

    def test_missing_estate_still_fails_clearly(self, tmp_path):
        with pytest.raises(SystemExit) as e:
            require_estate(tmp_path / "nope")
        assert "estate" in str(e.value).lower()


# ==========================================================================
# Policy is not ground truth
# ==========================================================================

class TestPolicyLocation:

    def test_sod_rules_are_read_from_config_first(self, tmp_path):
        """Segregation rules are an organisation's policy, not an answer key.

        They lived under ground_truth/ because the generator wrote them there,
        which implied a customer's own rules were part of a scoring artefact.
        """
        import reconcile
        estate = tmp_path / "e"
        (estate / "config").mkdir(parents=True)
        (estate / "config" / "sod_rules.json").write_text('[{"id": "SOD-1"}]')
        assert reconcile._load_sod(estate, None)[0]["id"] == "SOD-1"

    def test_ground_truth_location_still_works(self, tmp_path):
        import reconcile
        estate = tmp_path / "e"
        (estate / "ground_truth").mkdir(parents=True)
        (estate / "ground_truth" / "sod_rules.json").write_text('[{"id": "SOD-2"}]')
        assert reconcile._load_sod(estate, None)[0]["id"] == "SOD-2"

    def test_absent_policy_is_reported_not_fatal(self, tmp_path, capsys):
        import reconcile
        estate = tmp_path / "e"
        estate.mkdir()
        assert reconcile._load_sod(estate, None) == []
        assert "No segregation-of-duties policy" in capsys.readouterr().out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
