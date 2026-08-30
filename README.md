# Continuous Access Assurance — Synthetic Estate Generator

A test harness for building and **measuring** identity assurance controls against a
hybrid healthcare identity estate.

All data is synthetic. No production data is used, required, or supported.

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

## Phase 6 — Interpretation layer

```bash
python3 interpret.py                    # uses a local model if one is running
python3 interpret.py --backend ollama --model mistral-nemo:12b
python3 interpret.py --backend none     # deterministic fallbacks throughout
```

Everything in phases 1 to 4 can be re-derived by hand from the feeds. This
layer cannot: a paraphrase has no derivation, and asking a model the same
question twice is not verification. So the model is confined to operations
whose output can be checked mechanically against the deterministic input, and
anything failing the check is discarded rather than repaired.

It does three things, and it decides nothing.

**Translation.** `PYX_DISPENSE_CTRL_SUB` means nothing to the ward manager
asked to attest to it, and a reviewer who cannot read an entitlement approves
it. Each rendering is checked against the catalogue: naming the wrong
application, or calling CRITICAL access low-risk, rejects it. Rejected
translations fall back to a template that is duller and cannot be wrong.

**Clustering.** 267 findings is more than anyone reads; eight themes is a
report someone acts on. Grouping is a presentational judgement rather than a
factual claim, which is what makes it safe to delegate — but only under one
condition. A model asked to organise 267 items will produce eight fluent,
plausible themes covering 250 of them, and nothing in the output reveals the
other 17. The result is therefore checked as a partition and rejected outright
if it is not one, falling back to grouping by rule.

**Certification quality.** The grade is arithmetic over the coverage
statements phases 3 and 4 already emit. The model is handed the finished
numbers and asked only to write them up; every figure in its prose is then
checked against the set the pipeline produced. A grade produced by a language
model would be an opinion in a metric's clothing, and the one number here
nobody could re-derive.

**Rejected, not repaired.** A clustering that has quietly dropped eleven
findings can be patched by appending them to a leftover group. The result
looks complete, and nobody knows which of the remaining groups to trust.
Falling back loses readability and keeps the property that matters.

**The layer is optional.** With no model reachable the pipeline produces its
full output using deterministic fallbacks. A missing narrative is a cosmetic
loss; a missing finding is not.

**On testing this.** The failure modes are constructed rather than waited for.
A real model drops findings from a clustering occasionally and unpredictably;
a scripted client drops them on demand, which is the only way to assert the
check catches it. Testing against a live model would establish that it behaved
on the day the suite ran. The tests cover dropped findings, invented
identifiers, double-counted findings, wrong applications, understated risk,
and invented figures in prose — each one constructed, each one caught.

Two bugs surfaced here, both from output disagreeing with itself. The drift
scorer reported distinct accounts rather than findings for one rule, putting
two numbers three lines apart in the same report that should have agreed. And
the narrative validator's own regex never matched a percent sign, so it was
rejecting figures it had itself supplied.

cd /home/claude && cat > readme_addition.md << 'EOF'

**Run against a real model.** Everything above was built against a scripted
client. Run for the first time against `mistral-nemo:latest` on a local
Ollama, three things came out of it.

All 23 translations passed validation, and most were good — `PACS_ADMIN`
came back as full administration of the Picture Archiving and Communication
System, acronym expanded. One was wrong in a way that matters:
`LIS_RESULT_AUTHORISE` was rendered as authorising access to *view*
laboratory results. Authorising a result means releasing it into the clinical
record; viewing it is passive. The model turned an active capability into a
passive one, and that entitlement is one half of `SOD-CLIN-02` — a reviewer
reading "view test results" approves it without pausing, which is the exact
failure the segregation rule exists to prevent.

The validator did not catch it. It checks that the application is right and
that the risk language does not contradict the catalogue. Both were fine. **The
checks establish where a claim came from, not whether it means the right
thing** — and the second is the harder problem, still open. Until it is
solved, a translation is a reading aid rather than a substitute for the code.

The narrative passed validation and was worse than the fallback it replaced.
Handed the permitted figures, the model returned all of them as a list and
opened by describing 88.1% mean coverage across rules as covering "88.1% of
its rules adequately", which is a different and false claim. Every number was
one the pipeline produced, so the check passed. The computed fallback — four
plain sentences — is the better output, and on a 12B local model the
narration step currently earns nothing.

The third finding was accidental and the most reassuring. The first run used a
model name that did not exist, so every call returned 404. Translations fell
back to templates, clustering fell back to grouping by rule, the narrative
fell back to computed, and the pipeline produced its complete output. Total
model failure cost readability and not one finding — tested for with a
scripted client, then confirmed by a real outage.

**A gap this exposed.** Clustering refuses inputs over 220 findings because
beyond that omissions become frequent enough that the partition check rejects
everything. A full-size estate produces 930. So on any realistic estate the
clustering never reaches the model at all, and the limit that was meant to
avoid a wasted call instead disables the feature. Batching by rule and
clustering within each batch would fix it; that is not built.
EOF
cat readme_addition.md
Output


**Run against a real model.** Everything above was built against a scripted
client. Run for the first time against `mistral-nemo:latest` on a local
Ollama, three things came out of it.

All 23 translations passed validation, and most were good — `PACS_ADMIN`
came back as full administration of the Picture Archiving and Communication
System, acronym expanded. One was wrong in a way that matters:
`LIS_RESULT_AUTHORISE` was rendered as authorising access to *view*
laboratory results. Authorising a result means releasing it into the clinical
record; viewing it is passive. The model turned an active capability into a
passive one, and that entitlement is one half of `SOD-CLIN-02` — a reviewer
reading "view test results" approves it without pausing, which is the exact
failure the segregation rule exists to prevent.

The validator did not catch it. It checks that the application is right and
that the risk language does not contradict the catalogue. Both were fine. **The
checks establish where a claim came from, not whether it means the right
thing** — and the second is the harder problem, still open. Until it is
solved, a translation is a reading aid rather than a substitute for the code.

The narrative passed validation and was worse than the fallback it replaced.
Handed the permitted figures, the model returned all of them as a list and
opened by describing 88.1% mean coverage across rules as covering "88.1% of
its rules adequately", which is a different and false claim. Every number was
one the pipeline produced, so the check passed. The computed fallback — four
plain sentences — is the better output, and on a 12B local model the
narration step currently earns nothing.

The third finding was accidental and the most reassuring. The first run used a
model name that did not exist, so every call returned 404. Translations fell
back to templates, clustering fell back to grouping by rule, the narrative
fell back to computed, and the pipeline produced its complete output. Total
model failure cost readability and not one finding — tested for with a
scripted client, then confirmed by a real outage.

**A gap this exposed.** Clustering refuses inputs over 220 findings because
beyond that omissions become frequent enough that the partition check rejects
everything. A full-size estate produces 930. So on any realistic estate the
clustering never reaches the model at all, and the limit that was meant to
avoid a wasted call instead disables the feature. Batching by rule and
clustering within each batch would fix it; that is not built.


---

## Status

| Phase | | |
|---|---|---|
| 1 | Synthetic estate + ground truth | **complete** |
| 2 | Correlation engine — cross-system identity resolution with confidence scoring | next |
| 3 | Reconciliation and SoD rules, scored against ground truth | |
| 4 | Drift detection — transfer-triggered creep, peer-group baselines | |
| 5 | Exception lifecycle and suppression governance | |
| 6 | Interpretation layer — translation, clustering, certification quality | **complete** |

Phase 2 is the hard one, and is deliberately next. Everything downstream depends on
resolving a human to their accounts across four systems when 46% of directory
records carry no employee identifier.
