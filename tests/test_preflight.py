"""
Tests for the pre-flight data quality guard.

The guard exists to catch data that breaks a rule's assumptions before the
rule runs. So the tests feed it deliberately broken extracts: an
`employee_id` field holding two different kinds of identifier, an HR feed that
never changes between snapshots, terminations recorded weeks after the fact.

What is asserted is not just the verdict but that the guard says which rules
are affected and what to do. A check reporting UNSUPPORTED with no advice
leaves the reader knowing something is wrong and not what.
"""

import csv
from pathlib import Path

import pytest

from preflight import Extracts, run_checks
from preflight.checks import (check_termination_recency,
                              check_employee_id_semantics,
                              check_naming_conventions,
                              check_usage_data,
                              check_time_series,
                              check_peer_groups)


def write(d: Path, name: str, rows: list[dict]):
    d.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with open(d / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def hcm_row(pn, **kw):
    base = {"person_number": pn, "job_code": "RN-02", "department": "ICU",
            "assignment_status": "ACTIVE_ASSIGN", "termination_date": "",
            "hire_date": "2024-01-01"}
    base.update(kw)
    return base


def ad_row(sam, **kw):
    base = {"sam_account_name": sam, "employee_id": "", "enabled": "TRUE",
            "department": "ICU", "user_principal_name": f"{sam}@x.ae"}
    base.update(kw)
    return base


# ==========================================================================
# Termination recency — the check that motivated the guard
# ==========================================================================

class TestTerminationRecency:

    def test_no_timestamp_column_is_unknown_not_supported(self, tmp_path):
        """Not being able to test an assumption is a different answer from the
        assumption holding, and the guard must not conflate them."""
        write(tmp_path, "hcm_workers.csv",
              [hcm_row("E1", termination_date="2026-01-01",
                       assignment_status="TERMINATED")])
        c = check_termination_recency(Extracts(tmp_path))
        assert c.verdict == "UNKNOWN"
        assert c.advice

    def test_backdated_terminations_are_unsupported(self, tmp_path):
        rows = [hcm_row(f"E{i}", termination_date="2026-01-01",
                        record_created="2026-02-15",
                        assignment_status="TERMINATED") for i in range(20)]
        write(tmp_path, "hcm_workers.csv", rows)
        c = check_termination_recency(Extracts(tmp_path))
        assert c.verdict == "UNSUPPORTED"
        assert c.evidence["median_lag_days"] > 30
        assert "R1_LEAVER_NOT_DEPROVISIONED" in c.affects

    def test_prompt_terminations_are_supported(self, tmp_path):
        rows = [hcm_row(f"E{i}", termination_date="2026-01-01",
                        record_created="2026-01-01",
                        assignment_status="TERMINATED") for i in range(20)]
        write(tmp_path, "hcm_workers.csv", rows)
        assert check_termination_recency(Extracts(tmp_path)).verdict == "SUPPORTED"

    def test_partial_lag_is_degraded(self, tmp_path):
        rows = ([hcm_row(f"A{i}", termination_date="2026-01-01",
                         record_created="2026-01-01") for i in range(15)]
                + [hcm_row(f"B{i}", termination_date="2026-01-01",
                           record_created="2026-01-20") for i in range(5)])
        write(tmp_path, "hcm_workers.csv", rows)
        assert check_termination_recency(Extracts(tmp_path)).verdict == "DEGRADED"


# ==========================================================================
# Employee id semantics — the merger case
# ==========================================================================

class TestEmployeeIdSemantics:

    def test_mixed_identifier_formats_are_unsupported(self, tmp_path):
        """Two directories merged, two meanings in one field. Tier 1 would
        assert wrong links at its highest confidence."""
        rows = ([ad_row(f"a{i}", employee_id=f"EMP{i:06d}") for i in range(20)]
                + [ad_row(f"b{i}", employee_id=f"CTR-{i}-X") for i in range(18)])
        write(tmp_path, "ad_accounts.csv", rows)
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"EMP{i:06d}") for i in range(20)])
        c = check_employee_id_semantics(Extracts(tmp_path))
        assert c.verdict == "UNSUPPORTED"
        assert c.evidence["distinct_formats"] > 1
        assert "0.98" in c.advice or "wrong links" in c.advice

    def test_identifiers_matching_no_worker_are_degraded(self, tmp_path):
        rows = [ad_row(f"a{i}", employee_id=f"EMP{i:06d}") for i in range(20)]
        write(tmp_path, "ad_accounts.csv", rows)
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"EMP{i:06d}") for i in range(5)])
        c = check_employee_id_semantics(Extracts(tmp_path))
        assert c.verdict == "DEGRADED"
        assert c.evidence["orphan_share"] > 0.15

    def test_sparse_population_is_degraded_not_fatal(self, tmp_path):
        rows = ([ad_row(f"a{i}", employee_id=f"EMP{i:06d}") for i in range(5)]
                + [ad_row(f"b{i}") for i in range(20)])
        write(tmp_path, "ad_accounts.csv", rows)
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"EMP{i:06d}") for i in range(5)])
        assert check_employee_id_semantics(Extracts(tmp_path)).verdict == "DEGRADED"

    def test_clean_field_is_supported(self, tmp_path):
        rows = [ad_row(f"a{i}", employee_id=f"EMP{i:06d}") for i in range(30)]
        write(tmp_path, "ad_accounts.csv", rows)
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"EMP{i:06d}") for i in range(30)])
        assert check_employee_id_semantics(Extracts(tmp_path)).verdict == "SUPPORTED"

    def test_missing_column_is_unknown(self, tmp_path):
        write(tmp_path, "ad_accounts.csv",
              [{"sam_account_name": "a1", "enabled": "TRUE"}])
        assert check_employee_id_semantics(Extracts(tmp_path)).verdict == "UNKNOWN"


# ==========================================================================
# Naming conventions
# ==========================================================================

class TestNamingConventions:

    def test_single_convention_is_supported(self, tmp_path):
        write(tmp_path, "ad_accounts.csv",
              [ad_row(f"first{i}.last{i}") for i in range(30)])
        assert check_naming_conventions(Extracts(tmp_path)).verdict == "SUPPORTED"

    def test_three_conventions_are_unsupported(self, tmp_path):
        rows = ([ad_row(f"first{i}.last{i}") for i in range(10)]
                + [ad_row(f"flast{i}") for i in range(10)]
                + [ad_row(f"x.surname{i}") for i in range(10)])
        write(tmp_path, "ad_accounts.csv", rows)
        c = check_naming_conventions(Extracts(tmp_path))
        assert c.verdict in ("DEGRADED", "UNSUPPORTED")
        assert c.advice

    def test_service_accounts_do_not_count_as_a_human_convention(self, tmp_path):
        """Prefixed accounts are a separate population and should not make the
        human naming look inconsistent."""
        rows = ([ad_row(f"first{i}.last{i}") for i in range(20)]
                + [ad_row(f"svc-thing{i}") for i in range(20)])
        write(tmp_path, "ad_accounts.csv", rows)
        assert check_naming_conventions(Extracts(tmp_path)).verdict == "SUPPORTED"


# ==========================================================================
# Usage data
# ==========================================================================

class TestUsageData:

    def test_missing_column_blocks_dormancy(self, tmp_path):
        write(tmp_path, "app_entitlements.csv",
              [{"ad_sam": "a1", "entitlement": "X", "risk": "HIGH"}])
        c = check_usage_data(Extracts(tmp_path))
        assert c.verdict == "UNSUPPORTED"
        assert "D3_DORMANT_ENTITLEMENT" in c.affects

    def test_sparse_usage_is_degraded(self, tmp_path):
        rows = ([{"ad_sam": "a", "entitlement": "X", "risk": "HIGH",
                  "last_used": "2026-01-01"} for _ in range(5)]
                + [{"ad_sam": "b", "entitlement": "Y", "risk": "HIGH",
                    "last_used": ""} for _ in range(5)])
        write(tmp_path, "app_entitlements.csv", rows)
        c = check_usage_data(Extracts(tmp_path))
        assert c.verdict == "DEGRADED"
        assert "not evidence of non-use" in c.advice


# ==========================================================================
# Time series — the defect this project's own generator had
# ==========================================================================

class TestTimeSeries:

    def test_flat_history_is_unsupported(self, tmp_path):
        """An extract that always returns today's department gives a flat
        history, and transfer detection becomes impossible."""
        prev, cur = tmp_path / "p", tmp_path / "c"
        rows = [hcm_row(f"E{i}", department="ICU") for i in range(20)]
        write(prev, "hcm_workers.csv", rows)
        write(cur, "hcm_workers.csv", rows)
        c = check_time_series(Extracts(cur, prev))
        assert c.verdict == "UNSUPPORTED"
        assert "flat history" in c.advice or "current state" in c.detail

    def test_observed_changes_are_supported(self, tmp_path):
        prev, cur = tmp_path / "p", tmp_path / "c"
        write(prev, "hcm_workers.csv",
              [hcm_row(f"E{i}", department="ICU") for i in range(20)])
        write(cur, "hcm_workers.csv",
              [hcm_row(f"E{i}", department="RAD" if i < 3 else "ICU")
               for i in range(20)])
        c = check_time_series(Extracts(cur, prev))
        assert c.verdict == "SUPPORTED"
        assert c.evidence["department_changes"] == 3

    def test_single_snapshot_is_unknown(self, tmp_path):
        write(tmp_path, "hcm_workers.csv", [hcm_row("E1")])
        c = check_time_series(Extracts(tmp_path))
        assert c.verdict == "UNKNOWN"
        assert "60 days" in c.advice

    def test_disjoint_populations_are_unsupported(self, tmp_path):
        prev, cur = tmp_path / "p", tmp_path / "c"
        write(prev, "hcm_workers.csv", [hcm_row(f"A{i}") for i in range(10)])
        write(cur, "hcm_workers.csv", [hcm_row(f"B{i}") for i in range(10)])
        assert check_time_series(Extracts(cur, prev)).verdict == "UNSUPPORTED"


# ==========================================================================
# Peer groups
# ==========================================================================

class TestPeerGroups:

    def test_small_groups_are_unsupported(self, tmp_path):
        rows = [hcm_row(f"E{i}", job_code=f"J{i}", department=f"D{i}")
                for i in range(20)]
        write(tmp_path, "hcm_workers.csv", rows)
        c = check_peer_groups(Extracts(tmp_path))
        assert c.verdict == "UNSUPPORTED"
        assert "outlier by construction" in c.advice

    def test_large_groups_are_supported(self, tmp_path):
        rows = [hcm_row(f"E{i}", job_code="RN-02", department="ICU")
                for i in range(40)]
        write(tmp_path, "hcm_workers.csv", rows)
        assert check_peer_groups(Extracts(tmp_path)).verdict == "SUPPORTED"


# ==========================================================================
# The guard's contract
# ==========================================================================

class TestGuardContract:

    def test_missing_feeds_do_not_raise(self, tmp_path):
        """A real extract will not match the synthetic column names. 'We could
        not test this' is a useful answer; a stack trace is not."""
        tmp_path.mkdir(exist_ok=True)
        checks = run_checks(Extracts(tmp_path))
        assert checks
        assert all(c.verdict in ("SUPPORTED", "DEGRADED", "UNSUPPORTED", "UNKNOWN")
                   for c in checks)

    def test_every_problem_names_affected_rules(self, tmp_path):
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"E{i}", job_code=f"J{i}") for i in range(10)])
        for c in run_checks(Extracts(tmp_path)):
            if c.verdict in ("UNSUPPORTED", "DEGRADED"):
                assert c.affects, f"{c.id} reports a problem without naming a rule"

    def test_every_problem_says_what_to_do(self, tmp_path):
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"E{i}", job_code=f"J{i}") for i in range(10)])
        for c in run_checks(Extracts(tmp_path)):
            if c.verdict == "UNSUPPORTED":
                assert c.advice, f"{c.id} says something is wrong but not what to do"

    def test_worst_verdicts_are_reported_first(self, tmp_path):
        write(tmp_path, "hcm_workers.csv",
              [hcm_row(f"E{i}", job_code=f"J{i}") for i in range(10)])
        from preflight.checks import VERDICT_ORDER
        checks = run_checks(Extracts(tmp_path))
        order = [VERDICT_ORDER[c.verdict] for c in checks]
        assert order == sorted(order)

    def test_guard_scores_nothing(self):
        """The guard reports data shape. Producing a finding would make it a
        detection rule, and one that has not been scored against anything."""
        src = Path("preflight/checks.py").read_text()
        for word in ("precision", "recall", "true_positive", "finding_id"):
            assert word not in src, f"guard has started scoring: {word}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
