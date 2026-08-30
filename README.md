# Continuous Access Assurance Agent

An independent, measured assurance control over a hybrid healthcare identity
estate: Oracle HCM, on-premises Active Directory, Entra ID and an IGA platform.

It generates a synthetic estate with a ground-truth answer key, resolves
identities across all four systems, detects reconciliation failures and
segregation-of-duties violations, and tracks privilege drift over time. Every
rule is scored against that answer key, and every figure in this file is
asserted in CI.

The point is not that it finds things. It is that the finding rate, the false
positive rate, and the population each rule could not examine are all
measured and stated.

All data is synthetic. No production data is used, required, or supported.

**Four phases, 86 tests, zero runtime dependencies.**

---


## Sample output

`sample_output/` holds the metrics from a deliberately small run — 250 workers
over 120 days — so the numbers below can be checked without cloning and
running anything.

It is a smaller estate than the one the headline figures come from, and two
differences are worth knowing before reading it:

- **D2 reports zero findings.** At 250 workers most peer groups fall below the
  eight-member floor, and the rule declines to evaluate groups that small
  because in a group of three everybody is an outlier by construction. Its
  coverage statement says 62.1% for exactly that reason. Zero findings here
  means the rule refused to guess, not that the estate was clean.
- **The correlation map itself is not committed**, only its metrics. The full
  map is a few hundred kilobytes of little interest to a reader; the scoring in
  `correlation/metrics.json` is the part worth checking.

Regenerate it with:

```bash
python3 main.py --population 250 --days 120 --out sample_estate
python3 correlate.py --estate sample_estate --out sample_output/correlation
python3 reconcile.py --estate sample_estate \
    --correlation sample_output/correlation/correlation_map.csv \
    --out sample_output/reconciliation
python3 detect_drift.py --estate sample_estate \
    --correlation sample_output/correlation/correlation_map.csv \
    --out sample_output/drift
```

Everything is seeded, so the same command produces the same numbers.

---

## Running it

No runtime dependencies — the pipeline uses only the Python standard library.
Python 3.10 or newer.

```bash
git clone <your-repo-url> caaa
cd caaa

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install pytest                 # tests only

python3 run_all.py                 # all four phases, then the test suite
```

On WSL2, clone into the Linux filesystem (`~/`) rather than `/mnt/c/`. Writing
several hundred snapshot files across the Windows mount is slow enough to be
noticeable.

Stages can also be run individually, in this order:

```bash
python3 main.py --population 800 --days 180    # generate the estate
python3 correlate.py                           # resolve identities
python3 calibrate.py                           # sweep the correlation threshold
python3 reconcile.py                           # six reconciliation rules
python3 detect_drift.py                        # drift across snapshots
```

Each stage consumes the previous one's output and will tell you which command
to run first if something is missing.

`python3 scripts/check_metrics.py` asserts every figure quoted below against
the output just produced. It runs in CI on each push, so a change that quietly
degrades detection fails the build rather than making this file untrue.

---

## Why a generator rather than a demo dataset

An assurance control that has never been scored is a demonstration, not a control.
This generator emits an estate **together with a ground-truth answer key** recording
every injected pathology, so a detection engine can be measured for precision,
recall and false-positive rate rather than merely shown working.

Running with `--clean` produces a pathology-free estate. Any finding raised against
it is by definition a false positive. That is the baseline every detection rule
should be held to before it is trusted.

---

## The estate

Six independent source feeds per snapshot, mirroring what a second-line function
would read **directly** rather than through the IGA platform:

| Feed | Represents |
|---|---|
| `hcm_workers.csv` | Oracle HCM worker extract — the authoritative population |
| `ad_accounts.csv` | Local Active Directory, read over LDAP |
| `entra_accounts.csv` | Entra ID, read over Graph |
| `iga_identities.csv` | IGA platform identity register |
| `iga_entitlements.csv` | What the IGA platform **believes** is held |
| `app_entitlements.csv` | What is **actually** held, including unaggregated apps |
| `nhi_register.csv` | Non-human identity register — deliberately incomplete |

The gap between `iga_entitlements` and `app_entitlements` is the point of the
exercise. Reconciliation lives there.

Snapshots are taken across a configurable window (default 180 days — two full
quarterly certification cycles), giving the time-series needed to answer the
question a point-in-time review cannot: *when did this identity acquire this
entitlement, and has it been used since?*

---

## Injected pathologies

**Correlation obstacles** — the largest hidden cost in any real build

| Pathology | What it breaks |
|---|---|
| `SECOND_PRIVILEGED_ACCOUNT` | `a-` accounts with no HCM record of their own |
| `NO_HCM_RECORD` | Locums, agency and vendor staff absent from the authoritative source |
| `NAME_CHANGE_UNSYNCED` | HCM surname updated, directory not |
| `REHIRE_NEW_PERSON_NUMBER` | New person number against a pre-existing AD account |
| naming variants | Second facility using a merger-legacy convention |

Only ~54% of AD accounts carry a populated `employee_id`. Joining on it alone will
not work — which is the point.

**Reconciliation failures**

| Pathology | Signal |
|---|---|
| `MISSED_LEAVER` | HCM terminated, AD still enabled |
| `ORPHANED_ADMIN_ACCOUNT` | Leaver's privileged second account left active — invisible to any check keyed on person number |
| `STALE_CONNECTOR` | IGA reports DISABLED, AD reports enabled |
| `ENTRA_ONLY_IDENTITY` | Cloud-only and guest identities absent from IGA |
| `OUT_OF_SYNC_SCOPE` | AD objects outside Connect sync scope |

**Drift and privilege creep**

| Pathology | Signal |
|---|---|
| `RETAINED_ON_TRANSFER` | Prior-department entitlements never revoked after a move |
| `PEER_GROUP_OUTLIER` | Entitlement held by a small minority of the same job code and department |
| `DORMANT_ENTITLEMENT` | Held but never used |
| `STANDING_BREAKGLASS` | Emergency clinical access held permanently |

**Toxic combinations and non-human identity**

`SOD_VIOLATION` covers seven rules, including two **cross-application** pairs
(controlled-substance ordering with dispensing; directory administration with
clinical charting correction). Cross-application SoD is the category most IGA
deployments miss, because SoD engines are typically configured per application.

`ORPHANED_NHI` covers service, HL7 interface, biomed device and shared clinical
workstation accounts with no owner — carrying standing privilege and no review
cadence.

---

## The headline number

Because the entitlement catalogue marks which applications are aggregated, the
estate reproduces a **coverage gap**:

```
AD        93.9%          LIS         0.0%   <-- not aggregated
CERNER    96.6%          PACS        0.0%   <-- not aggregated
ENTRA     84.9%          PYXIS       0.0%   <-- not aggregated
ORACLE   100.0%
                         TOTAL      84.9%
```

97 holdings rated CRITICAL — controlled-substance dispensing, dispensing override
and laboratory result authorisation — sit entirely outside the certification
perimeter.

This is the number that reframes the conversation. Not *"certification is failing"*
but *"we can currently evidence 85% of privilege, and the 15% we cannot see is
where the critical clinical entitlements are."* That is a coverage statement, and
it is far harder to argue with than an opinion.

---

## Usage

```bash
python3 main.py                                  # defaults: 800 staff, 90 NHI, 180 days
python3 main.py --population 5000 --days 365     # larger estate
python3 main.py --dense                          # snapshot every day, not fortnightly
python3 main.py --clean --out estate_clean       # zero pathologies: FP baseline
python3 main.py --seed 42                        # reproducible variant
```

Output:

```
estate/
  snapshots/<YYYY-MM-DD>/   six CSV feeds per snapshot
  ground_truth/
    pathologies.json         answer key — every injection, with subject and detail
    sod_rules.json           the seven rules, with rationale
    entitlement_catalogue.json  risk ratings and aggregation coverage
```

---

## Design constraints carried forward

**Read-only.** Nothing in this project writes to an identity system. A second-line
function that remediates cannot then assure its own work. Detection raises an
exception; remediation belongs to identity operations.

**Deterministic detection, assisted interpretation.** Reconciliation and drift logic
must be reproducible SQL. If an auditor asks how a finding was derived, "a language
model judged it" invalidates the control. LLM assistance belongs to entitlement
translation, exception clustering and narrative drafting — never to the detection
itself.

**Coverage stated, not implied.** Every report must declare what is in scope and
what is not. Silent gaps are overstated assurance, which is itself a finding.

---

## Status

| Phase | | |
|---|---|---|
| 1 | Synthetic estate + ground truth | **complete** |
| 2 | Correlation engine — cross-system identity resolution with confidence scoring | next |
| 3 | Reconciliation and SoD rules, scored against ground truth | |
| 4 | Drift detection — transfer-triggered creep, peer-group baselines | |
| 5 | Exception lifecycle and suppression governance | |
| 6 | LLM layer — entitlement translation, clustering, certification quality scoring | |

Phase 2 is the hard one, and is deliberately next. Everything downstream depends on
resolving a human to their accounts across four systems when 46% of directory
records carry no employee identifier.

---

## What this is not

It does not remediate. Detection and remediation are separated deliberately:
an assurance function that fixes what it finds is no longer second-line, and
loses the independence that makes its findings worth anything.

It does not replace an IGA platform. It assumes one exists and checks whether
its coverage is what the organisation believes it to be.

It is not a machine learning system. Detection is deterministic and hand
re-derivable throughout. A finding that cannot be explained to the person
losing access is not usable, whatever its accuracy.

## Licence

MIT.
