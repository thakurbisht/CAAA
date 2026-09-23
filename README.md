# Continuous Access Assurance Agent

[![tests](https://github.com/thakurbisht/CAAA/actions/workflows/tests.yml/badge.svg)](https://github.com/thakurbisht/CAAA/actions/workflows/tests.yml)

An independent, measured assurance control over a hybrid healthcare identity
estate: Oracle HCM, on-premises Active Directory, Entra ID and an IGA platform.

It generates a synthetic estate with a ground-truth answer key, resolves
identities across all four systems, detects reconciliation failures and
segregation-of-duties violations, tracks privilege drift over time, and records
what humans decided about each finding. Every rule is scored against that
answer key, and every figure in this file is asserted in CI.

The point is not that it finds things. It is that the finding rate, the false
positive rate, and the population each rule could not examine are all measured
and stated.

**Six phases, 200 tests, zero runtime dependencies. Runs on your own CSV
extracts, with a local review console.**

All data in the generator is synthetic. No production data is used, required,
or supported.

---

## Three commitments

These are what the phases have in common, and what the tests actually enforce.

**Independence.** The control never reads the IGA platform's own correlation or
entitlement claims as evidence. Doing so would mean assuring a system with its
own output. It resolves identities independently and *then* compares; where the
two disagree, that disagreement is a finding. A test empties the platform's
entitlement feed and asserts the finding set is unchanged.

**Declared coverage.** Every rule reports the population it could not examine
alongside the findings it produced. A check that silently skips three clinical
applications and reports zero findings is worse than no check, because it
manufactures confidence. R1 examines 79.5% of accounts and says so; R5 names
the two segregation rules it cannot evaluate from platform data at all.

**Graded evidence.** A statistical outlier and an observed transfer both look
like a row in a CSV, but only one survives being challenged by the person
losing access. Findings carry the basis they rest on, and a statistical signal
is capped below a deterministic one so that sorting by severity cannot promote
a hint above a fact.

---

## Before running any of this on real data

```bash
python3 check_data.py --snapshot path/to/extract --previous path/to/earlier
```

Every rule here depends on assumptions about the feeds it reads. On the
synthetic estate those hold by construction, because the generator was written
to satisfy them. On a real estate they may not — and a rule whose assumption
has quietly failed does not error. It produces confident, wrong findings.

The case that prompted this: R1 treats an HCM termination date as the date
somebody left. In many HR systems the record is created weeks later with an
effective date backdated. R1 then reports an account as live 45 days after
termination when the organisation learned of it yesterday. The finding is
technically true and operationally useless, and a remediation team shown a few
of those stops reading the report.

So this runs first, on the raw extracts, before any rule. It scores nothing
and detects nothing. It reports what the data looks like, names the
assumptions the data does not support, and says which rules are affected:

| Verdict | Meaning |
|---|---|
| `SUPPORTED` | the assumption holds well enough to rely on |
| `DEGRADED` | it holds partly; findings need a caveat |
| `UNSUPPORTED` | it does not hold; the affected rules should not be run |
| `UNKNOWN` | the feed or column needed to test it is absent |

`UNKNOWN` is deliberately separate from `SUPPORTED`. Being unable to test an
assumption is a different answer from the assumption holding, and collapsing
them is how a control ends up trusted for a property nobody checked.

Seven assumptions are tested: termination-date recency, whether `employee_id`
carries one kind of identifier or several, how many naming conventions are in
use, whether usage data is populated, whether consecutive extracts actually
differ, whether peer groups are large enough to compare within, and what the
holdings feed can see.

Run against this project's own synthetic estate it returns two honest answers.
The generator writes no record-creation timestamp, so termination lag cannot be
measured and the check reports `UNKNOWN` rather than passing. And the
aggregation-scope check declares its own structural limit, below.

**A limit no code fixes.** R6 can only report a gap in an application it can
see. On the synthetic estate the generator writes both the platform's view and
reality, so the difference is visible. In production an application nobody
aggregates produces no feed, and cannot appear in a comparison of feeds. R6 is
therefore an inventory comparison rather than an entitlement analysis: take the
applications holding privileged access from an architecture register, take the
connected sources from the platform, and compare those. The difference is the
finding, and it needs no pipeline at all.


## Running it

No runtime dependencies — the pipeline uses only the Python standard library.
Python 3.10 or newer.

```bash
git clone <your-repo-url> caaa
cd caaa

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install pytest                 # tests only

python3 run_all.py                 # every stage, then the test suite
python3 serve.py                   # the review console, on localhost
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
python3 interpret.py                           # optional model layer
python3 build_report.py                        # one self-contained HTML page
python3 serve.py                               # the review console
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

---

## Phase 2 — Correlation engine

```bash
python3 correlate.py                 # correlate the latest snapshot
python3 calibrate.py                 # sweep the acceptance threshold
```

Resolves each account to a human across HCM, Local AD, Entra and the IGA
platform, using five tiers strongest-first: employee identifier, UPN local
part, name plus department, near-name with margin, and derivation of
administrative satellites from their base account.

| | |
|---|---|
| Precision | **100.0%** |
| Recall | 96.5% |
| False link rate | **0.00%** |
| Non-human identities wrongly linked to a person | **0 of 90** |

Threshold calibration shows a stable plateau from 0.78 to 0.86 at zero false
links; below 0.78 a false link appears, above 0.86 recall collapses. The
default of 0.80 sits in the middle of that plateau, so it is a documented
decision rather than an arbitrary default.

**Why precision is the metric, not F1.** The two error modes are not
symmetric. A missed link lands in the adjudication queue and gets worked. A
false link silently attributes privilege to the wrong person, and every
downstream finding built on it is wrong in a way nothing else will catch. The
engine is tuned to refuse rather than guess:

- **Ties are refused.** Where several HR records match a name equally well, the
  margin over the runner-up *is* the evidence. Without one, the engine asserts
  nothing rather than taking the first.
- **Contested records are demoted, not arbitrated.** Where two accounts claim
  one person, at most one can be right, so neither is asserted.
- **Derived links inherit their parent's confidence.** An administrative
  satellite cannot be more certain than the primary account it was derived
  from — otherwise an error in the primary is laundered into a confident
  assertion about the *privileged* account.

---

## Phase 3 — Reconciliation rules

```bash
python3 reconcile.py
```

Six deterministic checks over the correlated estate. Every finding is
re-derivable by hand from its recorded evidence.

| Rule | Found | Precision | Recall | Coverage |
|---|---|---|---|---|
| R1 Leaver not deprovisioned | 22 | **100%** | 88.0% | 79.5% |
| R2 Platform state disagreement | 13 | **100%** | 100% | 88.8% |
| R3 Orphaned non-human identity | 52 | **100%** | 100% | 100% |
| R4 Orphaned admin account | 17 | **100%** | 100% | 100% |
| R5 Segregation-of-duties violation | 112 | **100%** | 100% | 75.6% |
| R6 Coverage gap | 3 | **100%** | 100% | 57.1% |

Zero false positives on every rule, on both a fault-injected estate and a
fault-free one.

**66% of findings are unreachable from the platform's own data** — including
every R2 finding by construction, since that rule compares the platform's
record against an independent directory read.

**What the build corrected.** Three measurement errors, each found by checking
output against the data rather than accepting a plausible number:

- *The answer key was an injection log.* Scored against it, R4 showed 18.8%
  precision — and every sampled "false positive" was an administrative account
  whose owner really had been terminated. The log records deliberately planted
  faults; 180 days of attrition produces many more real ones.
- *The clean baseline tested the wrong property.* Asserting that a fault-free
  estate yields zero findings failed with 114 findings, all genuine — staff
  still leave, and their device accounts remain.
- *Unconfirmed correlations were consumed as facts.* R1 produced 11 findings
  resting on correlations below threshold, each naming a person the account may
  not belong to. Gating on confirmed correlation cost 12 points of R1 recall
  and removed every false accusation. That trade is worth stating plainly:
  three missed leavers that sit in the adjudication queue, against eleven
  accusations naming the wrong person.

---

## Phase 4 — Drift detection

```bash
python3 detect_drift.py
```

The first phase that needs time. A single snapshot can say whether a condition
is present; drift only exists relative to a baseline, and the choice of
baseline is the whole design.

| Rule | Basis | Found | Precision | Recall |
|---|---|---|---|---|
| D1 Retained on transfer | observed history | 18 | 100% | 94.7% |
| D2 Peer group outlier | peer distribution | 32 | *not scored* | — |
| D3 Dormant entitlement | usage record | 646 | 100% | 100% |
| D4 Standing break-glass | observed history | 11 | 100% | 100% |

Three bases, never flattened together: *observed history* is deterministic and
re-derivable from two HR extracts; *peer distribution* is statistical, capped
at MEDIUM severity and deliberately not scored on precision because "unusual"
has no truth set; *usage record* is factual but measured against a
policy-chosen threshold that travels in every finding.

**The volume result is the uncomfortable one.** The weakest basis produces
almost all the output: dormancy is 91% of findings, 646 across 376 people,
while the two deterministic rules produce 29 between them. Grading by basis is
what makes those 29 findable.

**What the build corrected.** Phase 4 exposed a defect in Phase 1: snapshots
were written after the simulation finished, so every extract carried the final
department and the HR feed was flat across 180 days. Zero transfers were
observable, and phases 1–3 all read a single snapshot so nothing had caught it.

Scoping by name prefix then missed the entitlements that matter — matching
`GRP_DEPT_*` ignored every clinical role entitlement, which are
department-specific in substance and carry far more privilege than a directory
group. Department scoping is now learned by measuring how concentrated an
entitlement's holders are.

And recall was measured against faults the rule had declined to judge. D1 first
scored 30%, then 60%. Both were wrong: of 70 planted retentions, 36 belonged to
workers since terminated, and nearly all the rest had transferred inside the
30-day grace period. Measured against what the rule claims to assess, recall is
94.7%.

One hypothesis was tested and discarded rather than written up: the low recall
looked like snapshot cadence, so the estate was regenerated with daily instead
of fortnightly snapshots. Recall moved from 30.5% to 32.2%. Cadence was not the
cause.

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


---

## The report

```bash
python3 build_report.py
```

Writes `report/report.html`: one self-contained page, no server and no
dependencies, that opens in a browser and survives being emailed. A dashboard
framework would have meant a runtime dependency and a running process for
something whose purpose is to be handed to someone who will not run anything.

It is built around the coverage statement rather than the finding count. Each
rule gets a bar showing the population it could assess against the population
it could not, with the reason for every exclusion named underneath. A rule
that examined 69.4% of accounts and raised 23 findings is telling you two
things, and a report showing only the second is the failure this project was
written against.

Drift findings are separated by evidentiary basis, with the statistical rule
visually subordinated, so a reader can tell at a glance which findings will
survive a challenge.


---

## Running it on your own data

The pipeline reads CSV extracts. It does not need the generator, and it does
not need an answer key.

```bash
# 1. Draft a column mapping from your extract's headers
python3 check_data.py --snapshot extracts/2026-05-31 \
    --suggest-profile profile.json

# 2. Review it, then check the data against the rules' assumptions
python3 check_data.py --snapshot extracts/2026-05-31 \
    --previous extracts/2026-05-17 --profile profile.json

# 3. Run
python3 correlate.py  --estate extracts --profile profile.json --out out/corr
python3 reconcile.py  --estate extracts --profile profile.json \
    --correlation out/corr/correlation_map.csv --out out/recon
python3 detect_drift.py --estate extracts --profile profile.json \
    --correlation out/corr/correlation_map.csv --out out/drift
```

Layout: `extracts/snapshots/<date>/*.csv`, one directory per extract date, and
your segregation-of-duties policy at `extracts/config/sod_rules.json`.

**Two files are enough to start.** An HR worker extract and a directory dump
produce 30 findings on a 597-account estate — terminated workers holding live
access, and privileged accounts with no live owner. Each further feed adds
rules rather than being required; see [MINIMAL_EXTRACT.md](MINIMAL_EXTRACT.md)
for what each one unlocks, measured rather than assumed.

Where no platform entitlement feed is supplied, the pipeline reports its
findings and explicitly declines to say whether the identity platform could
have produced them. Asserting that a finding is unreachable from records that
were never supplied would be a claim about a comparison nobody made.

**Without an answer key, precision is not reported.** Coverage is. Every rule
still states the population it examined, what it excluded and why, and whether
the platform's own data could have produced each finding. A precision figure
on real data would have to be invented, so it is omitted rather than guessed —
which is the same reason `UNKNOWN` is kept separate from `SUPPORTED` in the
pre-flight guard.

**Three couplings had to be broken to make this possible**, and all three were
invisible while the generator was the only source of input:

- *Ground truth was required to start.* `require_estate()` refused to run
  without `identity_map.json`. It was written to give a clear error when
  commands were run out of order, and it quietly locked the whole pipeline to
  synthetic data — every command died on its first line against a real
  extract.
- *Detection and scoring were wired together in the CLIs.* The modules were
  already separable; the command-line entry points were not.
- *Segregation rules lived under `ground_truth/`*, implying an organisation's
  own policy was part of a scoring artefact. They belong in config.

**Two bugs the first real-shaped run exposed**, both in the mapping layer,
both of the kind that produce a plausible wrong answer rather than an error:

- `employeeID` mapped to `person_number` in the directory feed. The same
  column name means the HR key in an HR extract and the directory's reference
  to it in a directory extract; a flat alias table resolved it by whichever
  canonical name was registered first. Aliases are now scoped per feed — which
  is the merger problem the guard checks for, appearing one layer earlier.
- `userAccountControl` mapped to `enabled`. It is a numeric bitmask, not a
  boolean, so every rule testing `enabled == "TRUE"` returned nothing and R2
  reported zero findings on an estate that had thirteen. A mapping that
  attaches a plausible column of the wrong type is worse than one that finds
  no column, because the second is visible.

Verified by running the full pipeline against an extract with source-system
column names and no ground truth: the findings match the scored run exactly —
159 reconciliation findings, 8 drift — with precision correctly absent.


---

## The review console

```bash
python3 serve.py
```

Opens on `http://127.0.0.1:8000`. Nothing to install — the standard library's
HTTP server is enough, which matters on a managed VM where `pip install` may
not be available and every dependency is a question somebody has to answer.

It binds to loopback only. This process reads HR and directory extracts for an
entire workforce; a console reachable from the network is a data exposure
wearing a convenience's clothing. For access from another machine, forward the
port over SSH so the existing access control applies.

Four screens: coverage per rule, the findings list, the adjudication queue, and
a button that runs the pipeline.

**The queue is built to be worked with the keyboard.** `j` and `k` move,
`1`/`2`/`3` decide, `Esc` closes. Fifty-five accounts reached by mouse is a
chore; the same fifty-five by keyboard is a few minutes, and that is the
difference between a queue that gets cleared and one that does not. Deciding by
keystroke still opens the drawer rather than writing silently — a name and a
reason are required either way.

The first version of this reused the printed report's styling: serif body,
hairline rules, no chrome. That is right for a document somebody reads once and
wrong for a tool somebody sits in front of for an hour. Rebuilt as an
application, with the coverage bar kept as the signature element.

**What it adds over the command line is that decisions come back.** Findings
previously went out and nothing returned — somebody in IAM would say "that one
is a contractor on extended notice" and the pipeline never heard about it, so
the same finding appeared the next month, and the month after. A report that
repeats what has already been answered stops being read, which is the ordinary
way an assurance control dies.

The decision log is append-only. A decision is never edited, only superseded,
because an audit asking why an account was left alone in March needs the March
decision and the name against it — not the current state of a row somebody has
since overwritten.

**Three rules the console enforces, each closing a way a suppression becomes
permanent:**

- An accepted risk needs an end date. Without one it hides a real finding
  forever and nobody notices the suppression outliving the reason for it.
  Expired decisions drop out on their own and the finding returns.
- A decision needs a name against it. A decision with nobody attached is not
  evidence.
- A correlation link can be left `unknown`. Forcing a yes or no on ambiguous
  evidence is how a wrong link gets recorded as a confirmed one.

**Items are identified by content, not position.** A finding used to be "the
Nth row of a CSV", so inserting one renumbered everything below it and a
decision recorded against `R0001` silently attached itself to a different
account on the next run. The id is now derived from the rule, the subject and
the specific condition, so the same problem produces the same id whenever it is
seen.

That identity is also what the roadmap has been waiting on. Delta detection,
suppression, ticket handoff, trend and audit trail all need a finding to be the
same finding across runs, and none of them could be built while it was not.

**Two bugs the build surfaced**, both in code I had just written:

- The Content-Security-Policy header blocked the console's own inline script,
  so every screen rendered empty while the API worked perfectly. Extracting the
  script to its own file fixed it without loosening the policy — the right
  direction, since the console renders account names and free-text notes.
- A thread started with `args=(x)` rather than `args=(x,)`, which is not a
  tuple. The run button raised before the pipeline began, and the failure
  landed on the HTTP request rather than in the run log where anyone would look
  for it.


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

Phase 5 is partly done: the console records decisions and expires suppressions.
Ticket handoff, aging reports and delta detection between runs are not built —
all three were waiting on findings having a stable identity across runs, which
they now have.

---

## What this is not

It does not remediate. Detection and remediation are separated deliberately: an
assurance function that fixes what it finds is no longer second-line, and loses
the independence that makes its findings worth anything.

It does not replace an IGA platform. It assumes one exists and checks whether
its coverage is what the organisation believes it to be.

It is not a machine learning system. Detection is deterministic and hand
re-derivable throughout. Phase 6 adds an optional language model, but it only
describes findings the deterministic rules already produced — it cannot raise
one, suppress one, or change a score. A finding that cannot be explained to the
person losing access is not usable, whatever its accuracy.

## Licence

MIT.
