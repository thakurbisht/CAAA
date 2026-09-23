# The smallest useful extract

Two files produce findings. Everything else adds rules.

This was measured rather than assumed: the pipeline was run against
progressively smaller extracts to find where it stops working. It stops later
than expected.

---

## Start with two files

```
extracts/snapshots/2026-05-31/
    hcm_workers.csv
    ad_accounts.csv
```

That produced **30 findings** on a 597-account estate — 23 terminated workers
still holding live access, 7 privileged accounts with no live owner.

Neither file needs every column. These are the ones the two rules read:

**hcm_workers.csv**

| Column | Why |
|---|---|
| `person_number` | the key everything correlates to |
| `first_name`, `last_name` | name-based matching where the identifier is absent |
| `department` | corroborates a match, and scopes findings |
| `job_code` | peer grouping; optional for these two rules |
| `assignment_status` | `TERMINATED` is what R1 tests |
| `termination_date` | ages the finding |

**ad_accounts.csv**

| Column | Why |
|---|---|
| `sam_account_name` | the account identifier |
| `display_name` | name matching |
| `employee_id` | the strongest correlation tier when populated |
| `enabled` | whether access is live |
| `department` | corroboration |

Columns named differently by your source systems are handled by a profile —
`check_data.py --suggest-profile` drafts one from your headers.

---

## What each additional feed unlocks

Measured on the same estate, adding one feed at a time:

| Added | Rules that start working | Findings |
|---|---|---|
| *(hcm + ad)* | R1 leaver, R4 orphaned admin | 30 |
| `app_entitlements.csv` | R5 segregation of duties, R6 coverage gap | 100 |
| `iga_identities.csv` | R2 platform state disagreement | 113 |
| `iga_entitlements.csv` | — enables the platform-visibility comparison | 113 |
| `nhi_register.csv` | R3 orphaned non-human identity | 159 |

`iga_entitlements.csv` adds no rule of its own. What it adds is the ability to
say whether the identity platform's own data could have produced each finding
— which is the claim the whole control rests on. Without it the pipeline
reports the findings and explicitly declines to make that claim, rather than
assuming the platform is blind.

Drift detection needs snapshots over time: at least two extracts, spanning at
least 60 days. One extract produces no drift findings, and the pipeline says
so rather than reporting a clean result.

---

## Getting a first finding

The two-file start is deliberately chosen to be the least difficult ask. An HR
worker extract and a directory dump are the two exports most organisations
already produce for other purposes, and neither contains entitlement data.

R1 is also the easiest finding to defend, because it needs no interpretation:
HR says this person left, the directory says the account is live. Both
statements come from systems the reader already trusts, and the finding is the
disagreement between them.

Start with one department if a full extract is difficult. Fifty rows is enough
to produce a finding, and a real finding on fifty rows is a stronger argument
than a clean result on a synthetic estate.

---

## Before trusting anything

```bash
python3 check_data.py --snapshot extracts/2026-05-31 --suggest-profile profile.json
# review profile.json — the guesses are made from column names alone
python3 check_data.py --snapshot extracts/2026-05-31 --profile profile.json
```

The guard tests whether your data supports what the rules assume. Two of its
checks matter most for a two-file start:

**Is `employee_id` one kind of identifier or several?** After a merger that
field often holds a contractor number in some rows and an HR number in others.
Correlation would then assert wrong links at its highest confidence, which is
worse than having no identifier at all.

**Are termination dates recorded when the person left, or weeks later?** If HR
backdates, R1's age figures describe when the event happened rather than when
anyone knew, and a finding reading "45 days since termination" may mean the
organisation learned of it yesterday. The guard reports `UNKNOWN` when the
extract carries no record-creation timestamp to measure this against — being
unable to test an assumption is a different answer from the assumption
holding.
