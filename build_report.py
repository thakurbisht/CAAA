#!/usr/bin/env python3
"""
Build a single self-contained HTML report from the pipeline output.

No dependencies and no server: one file that opens in a browser, survives
being emailed, and can be published to GitHub Pages. Adding a dashboard
framework would have meant a runtime dependency and a running process for
something whose whole purpose is to be handed to somebody who will not run
anything.

The report is built around the coverage statement rather than the finding
count, because that is the argument the pipeline exists to make. A rule that
examined 79.5% of accounts and raised 22 findings is telling you two things,
and a report that shows only the second is the failure this project was
written against.
"""

import argparse
import csv
import html
import json
from datetime import date
from pathlib import Path

CSS = """
:root {
  --paper:      #fbfbf9;
  --ink:        #1a1d1a;
  --ink-soft:   #5c625b;
  --rule:       #dcded7;
  --examined:   #2f4f3e;
  --unexamined: #c3c7bd;
  --critical:   #8b2e28;
  --high:       #a8663a;
  --signal:     #7a6a4f;
}

* { box-sizing: border-box; }

html { -webkit-text-size-adjust: 100%; }

body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 400 17px/1.62 Georgia, 'Iowan Old Style', 'Palatino Linotype', serif;
  font-variant-numeric: tabular-nums;
}

.sheet { max-width: 62rem; margin: 0 auto; padding: 4.5rem 2rem 8rem; }

h1 {
  font-size: 1.55rem; font-weight: 400; letter-spacing: -0.01em;
  margin: 0 0 0.2rem;
}
.dateline { color: var(--ink-soft); font-size: 0.95rem; margin: 0 0 4rem; }

h2 {
  font-size: 1.1rem; font-weight: 700; margin: 4.5rem 0 0.4rem;
  padding-top: 1.6rem; border-top: 1px solid var(--rule);
}
h2 + .lede { color: var(--ink-soft); margin: 0 0 2.2rem; max-width: 44rem; }

/* ---- verdict ---------------------------------------------------------- */

.verdict { margin-bottom: 3.5rem; }
.grade {
  font-size: clamp(2.6rem, 7vw, 4rem); line-height: 1;
  font-weight: 400; letter-spacing: -0.02em; margin: 0;
}
.grade-note { color: var(--ink-soft); margin: 0.7rem 0 1.8rem; }
.narrative { max-width: 40rem; margin: 0; }
.narrative + .provenance {
  color: var(--ink-soft); font-size: 0.88rem; margin-top: 0.9rem;
}

/* ---- coverage bars: the signature ------------------------------------- */

.rule { margin-bottom: 2.4rem; max-width: 46rem; }
.rule-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: 1rem; margin-bottom: 0.45rem;
}
.rule-name { font-weight: 700; }
.rule-pct { color: var(--ink-soft); font-size: 0.92rem; white-space: nowrap; }

.bar { display: flex; height: 0.62rem; background: var(--unexamined); }
.bar .seg-examined { background: var(--examined); }

.rule-legend {
  margin: 0.55rem 0 0; font-size: 0.9rem; color: var(--ink-soft);
}
.rule-legend b { color: var(--ink); font-weight: 400; }
.excl { display: block; }
.excl::before {
  content: ""; display: inline-block; width: 0.55rem; height: 0.55rem;
  background: var(--unexamined); margin-right: 0.5rem;
  transform: translateY(-0.05rem);
}

/* ---- evidence grading ------------------------------------------------- */

.basis { margin-bottom: 2.6rem; max-width: 44rem; }
.basis-name { font-weight: 700; }
.basis-name .count { font-weight: 400; color: var(--ink-soft); }
.basis-what { margin: 0.3rem 0 0; color: var(--ink-soft); }
.basis.statistical { border-left: 3px solid var(--signal); padding-left: 1.1rem; }

/* ---- findings --------------------------------------------------------- */

.controls { margin-bottom: 1.4rem; display: flex; flex-wrap: wrap; gap: 0.5rem; }
.controls button {
  font: inherit; font-size: 0.88rem; background: transparent;
  border: 1px solid var(--rule); color: var(--ink-soft);
  padding: 0.28rem 0.75rem; cursor: pointer;
}
.controls button[aria-pressed="true"] {
  border-color: var(--ink); color: var(--ink);
}
.controls button:focus-visible { outline: 2px solid var(--examined); outline-offset: 2px; }

table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
th {
  text-align: left; font-weight: 700; padding: 0.5rem 0.9rem 0.5rem 0;
  border-bottom: 1px solid var(--ink);
}
td { padding: 0.55rem 0.9rem 0.55rem 0; border-bottom: 1px solid var(--rule);
     vertical-align: top; }
td:last-child, th:last-child { padding-right: 0; }
tbody tr.hide { display: none; }

.sev { white-space: nowrap; }
.sev-CRITICAL { color: var(--critical); font-weight: 700; }
.sev-HIGH { color: var(--high); }
.sev-MEDIUM, .sev-LOW { color: var(--ink-soft); }
.subject { font-family: ui-monospace, 'SF Mono', Menlo, monospace;
           font-size: 0.86rem; }
.blind { color: var(--ink-soft); font-size: 0.86rem; }

.count-note { color: var(--ink-soft); font-size: 0.9rem; margin: 1rem 0 0; }

/* ---- stat lines ------------------------------------------------------- */

.figure { margin: 0 0 2.2rem; max-width: 40rem; }
.figure-value {
  font-size: 2.4rem; line-height: 1.1; display: block; margin-bottom: 0.2rem;
}
.figure-label { color: var(--ink-soft); }

footer {
  margin-top: 6rem; padding-top: 1.6rem; border-top: 1px solid var(--rule);
  color: var(--ink-soft); font-size: 0.88rem; max-width: 44rem;
}

@media (max-width: 40rem) {
  .sheet { padding: 2.5rem 1.2rem 4rem; }
  table { font-size: 0.86rem; }
  td, th { padding-right: 0.5rem; }
}

@media print {
  body { background: #fff; }
  .controls { display: none; }
  tbody tr.hide { display: table-row; }
}
"""

JS = """
(function () {
  var buttons = document.querySelectorAll('[data-filter]');
  var rows = document.querySelectorAll('#findings tbody tr');
  var note = document.getElementById('shown-count');
  var total = rows.length;

  function apply(key) {
    var shown = 0;
    rows.forEach(function (r) {
      var match = key === 'all' || r.dataset.severity === key ||
                  r.dataset.basis === key;
      r.classList.toggle('hide', !match);
      if (match) shown++;
    });
    note.textContent = shown === total
      ? total + ' findings'
      : shown + ' of ' + total + ' findings';
    buttons.forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.filter === key));
    });
  }

  buttons.forEach(function (b) {
    b.addEventListener('click', function () { apply(b.dataset.filter); });
  });
  apply('all');
})();
"""


def esc(s):
    return html.escape(str(s or ""))


def read_csv(p: Path):
    return list(csv.DictReader(open(p, newline=""))) if p.exists() else []


def read_json(p: Path):
    return json.load(open(p)) if p.exists() else {}


PRETTY = {
    "no_confirmed_hr_link": "no HR record to test against",
    "correlation_unconfirmed": "account owner not confirmed",
    "sod_rules_unreachable_from_platform": "rules the platform cannot evaluate",
    "peer_group_too_small": "peer group too small to compare",
    "no_grant_or_usage_date": "no dates to age the grant",
}

BASIS_ORDER = ["OBSERVED_HISTORY", "USAGE_RECORD", "PEER_DISTRIBUTION"]

BASIS_COPY = {
    "OBSERVED_HISTORY": (
        "Observed history",
        "Re-derivable by hand from two HR extracts. The transfer is a fact and "
        "so is the entitlement still held after it. This will survive being "
        "challenged."),
    "USAGE_RECORD": (
        "Usage record",
        "Factual, but measured against a dormancy threshold that is a policy "
        "choice rather than a discovered value. As defensible as the threshold "
        "behind it, which every finding restates."),
    "PEER_DISTRIBUTION": (
        "Peer distribution",
        "Statistical. Rare is not the same as wrong — a legitimate specialist "
        "sits outside their peer group every time. This ranks what a human "
        "should look at; it does not establish that anything is wrong."),
}


def coverage_section(recon_cov, drift_ev, recon_m, drift_m):
    excl_by_rule = {}
    for s in recon_m.get("per_rule", []) + drift_m.get("per_rule", []):
        excl_by_rule[s["rule_id"]] = s.get("excluded") or {}

    out = []
    for r in recon_cov + drift_ev:
        rid = r["rule_id"]
        examined = int(r["population_examined"])
        total = int(r["population_total"])
        pct = examined / total if total else 0
        excluded = excl_by_rule.get(rid, {})

        legend = [f"<b>{examined:,}</b> examined of {total:,}"]
        for reason, n in excluded.items():
            if n:
                legend.append(
                    f'<span class="excl"><b>{int(n):,}</b> '
                    f'{esc(PRETTY.get(reason, reason.replace("_", " ")))}</span>')

        out.append(f"""
    <div class="rule">
      <div class="rule-head">
        <span class="rule-name">{esc(r['name'])}</span>
        <span class="rule-pct">{pct:.1%} &middot; {int(r['findings']):,} findings</span>
      </div>
      <div class="bar" role="img"
           aria-label="{pct:.0%} of the population examined">
        <div class="seg-examined" style="width:{pct * 100:.2f}%"></div>
      </div>
      <p class="rule-legend">{''.join(legend)}</p>
    </div>""")
    return "\n".join(out)


def findings_section(rows):
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows = sorted(rows, key=lambda r: (order.get(r["severity"], 9),
                                       r["rule_id"], r["subject"]))
    trs = []
    for r in rows:
        basis = r.get("basis", "")
        blind = r.get("visible_in_platform_data", "") == "no"
        trs.append(f"""
      <tr data-severity="{esc(r['severity'])}" data-basis="{esc(basis)}">
        <td class="sev sev-{esc(r['severity'])}">{esc(r['severity'].title())}</td>
        <td class="subject">{esc(r['subject'])}</td>
        <td>{esc(r['summary'])}
            {'<span class="blind"><br>not reachable from platform data</span>'
             if blind else ''}</td>
      </tr>""")
    return "\n".join(trs)


def build(out_dir: Path, recon: Path, drift: Path, corr: Path,
          interp: Path, snapshot: str) -> str:
    recon_m = read_json(recon / "rule_metrics.json")
    drift_m = read_json(drift / "drift_metrics.json")
    corr_m = read_json(corr / "metrics.json")
    quality = read_json(interp / "certification_quality.json")

    recon_cov = read_csv(recon / "coverage_statement.csv")
    drift_ev = read_csv(drift / "evidence_basis.csv")
    findings = read_csv(recon / "findings.csv") + read_csv(drift / "drift_findings.csv")

    grade = quality.get("grade", "UNGRADED")
    coverage = quality.get("mean_coverage", 0)
    narrative = quality.get("narrative", "")
    source = quality.get("narrative_source", "computed")

    invisible = recon_m.get("aggregate", {}).get("invisible_share", 0)
    n_invisible = recon_m.get("aggregate", {}).get(
        "findings_invisible_to_platform", 0)

    ls = corr_m.get("link_scoring", {})

    # Evidence bases, deterministic first
    by_basis = {}
    for r in drift_ev:
        by_basis.setdefault(r["basis"], []).append(r)
    basis_blocks = []
    for b in BASIS_ORDER:
        if b not in by_basis:
            continue
        name, what = BASIS_COPY[b]
        n = sum(int(r["findings"]) for r in by_basis[b])
        cls = "basis statistical" if b == "PEER_DISTRIBUTION" else "basis"
        basis_blocks.append(f"""
    <div class="{cls}">
      <div class="basis-name">{esc(name)}
        <span class="count">&mdash; {n:,} findings</span></div>
      <p class="basis-what">{esc(what)}</p>
    </div>""")

    prov = ("Written by a language model from the figures above, then checked "
            "against them." if source == "model" else
            "Computed from the coverage statements. No model was involved.")

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Access assurance &mdash; estate as at {esc(snapshot)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="sheet">

  <h1>Continuous access assurance</h1>
  <p class="dateline">Synthetic healthcare estate, as at {esc(snapshot)}.
     Generated {date.today().isoformat()}.</p>

  <div class="verdict">
    <p class="grade">{esc(grade.title())}</p>
    <p class="grade-note">Mean coverage across
       {len(recon_cov) + len(drift_ev)} rules, {coverage:.1%}.</p>
    <p class="narrative">{esc(narrative)}</p>
    <p class="provenance">{esc(prov)}</p>
  </div>

  <h2>What each rule examined</h2>
  <p class="lede">The filled portion of each bar is the population the rule
     could assess. The rest was not clean &mdash; it was unexamined, and the
     reason is stated.</p>
{coverage_section(recon_cov, drift_ev, recon_m, drift_m)}

  <h2>What the platform could not have found</h2>
  <p class="lede">Each finding records whether the identity platform's own data
     would have revealed it. An independent check that only sees what the
     platform sees is not adding anything.</p>
  <p class="figure">
    <span class="figure-value">{invisible:.0%}</span>
    <span class="figure-label">of reconciliation findings &mdash;
      {n_invisible:,} of them &mdash; are unreachable from the platform's own
      records. Every state-disagreement finding is, by construction: that rule
      compares the platform against an independent read of the directory.</span>
  </p>

  <h2>How much argument each finding takes to defend</h2>
  <p class="lede">Drift findings rest on three different kinds of evidence.
     Reporting them as one list would leave a reader unable to tell which ones
     hold up.</p>
{''.join(basis_blocks)}

  <h2>Identity resolution</h2>
  <p class="lede">Every rule is keyed on a resolved identity rather than on any
     single system's identifier. A wrong link attributes privilege to the wrong
     person, so the engine refuses rather than guesses.</p>
  <p class="figure">
    <span class="figure-value">{ls.get('false_links', 0)}</span>
    <span class="figure-label">accounts linked to the wrong person, out of
      {ls.get('true_links', 0):,} links asserted. Recall
      {ls.get('recall', 0):.1%}; the shortfall sits in an adjudication queue
      with a stated reason, not in the findings.</span>
  </p>

  <h2>Findings</h2>
  <p class="lede">Sorted by severity. Statistical findings are capped below
     deterministic ones, so sorting cannot promote a hint above a fact.</p>
  <div class="controls">
    <button data-filter="all" aria-pressed="true">All</button>
    <button data-filter="CRITICAL" aria-pressed="false">Critical</button>
    <button data-filter="HIGH" aria-pressed="false">High</button>
    <button data-filter="OBSERVED_HISTORY" aria-pressed="false">Observed history</button>
    <button data-filter="PEER_DISTRIBUTION" aria-pressed="false">Statistical only</button>
  </div>
  <table id="findings">
    <thead>
      <tr><th>Severity</th><th>Account</th><th>What was found</th></tr>
    </thead>
    <tbody>
{findings_section(findings)}
    </tbody>
  </table>
  <p class="count-note" id="shown-count"></p>

  <footer>
    <p>All data is synthetic. Every figure here is produced by deterministic
       code and scored against a generated answer key; nothing on this page is
       an estimate. The summary paragraph is the only text a language model may
       write, and only from figures already computed.</p>
  </footer>

</div>
<script>{JS}</script>
</body>
</html>"""

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(doc, encoding="utf-8")
    return str(path)


def main():
    p = argparse.ArgumentParser(description="Build the HTML report")
    p.add_argument("--estate", default="estate")
    p.add_argument("--reconciliation", default="reconciliation_output")
    p.add_argument("--drift", default="drift_output")
    p.add_argument("--correlation", default="correlation_output")
    p.add_argument("--interpretation", default="interpretation_output")
    p.add_argument("--out", default="report")
    a = p.parse_args()

    snaps = sorted((Path(a.estate) / "snapshots").iterdir())
    path = build(Path(a.out), Path(a.reconciliation), Path(a.drift),
                 Path(a.correlation), Path(a.interpretation), snaps[-1].name)

    size = Path(path).stat().st_size
    print(f"\n  {path}  ({size / 1024:.0f} KB, self-contained)\n"
          f"  Open it in a browser, or publish the folder to GitHub Pages.\n")


if __name__ == "__main__":
    main()
