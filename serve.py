#!/usr/bin/env python3
"""
The local review console.

    python3 serve.py

Serves on http://127.0.0.1:8000. Nothing to install: the standard library's
HTTP server is enough, which matters on a managed VM where `pip install` may
not be available and every added dependency is a question somebody has to
answer.

It binds to the loopback address only. This process reads HR and directory
extracts for an entire workforce, and a review console that is reachable from
the network is a data exposure wearing a convenience's clothing. Remote access
belongs behind SSH port forwarding, where the existing access control applies.

What it does that the command line does not: record what a human decided.
Findings previously went out and nothing came back, so the same answered
question reappeared every month until people stopped reading the report.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import subprocess
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from decisions import (Decision, DecisionLog, VERDICTS, LINK_VERDICTS,
                       finding_id, cluster_item_id, now, write_run_manifest)

ROOT = Path(__file__).parent.resolve()
UI = ROOT / "ui"

# Where each artefact is read from. Overridden by --* flags at startup.
PATHS = {
    "estate": ROOT / "estate",
    "correlation": ROOT / "correlation_output",
    "reconciliation": ROOT / "reconciliation_output",
    "drift": ROOT / "drift_output",
    "work": ROOT / "review",
}

STATE = {"running": False, "log": [], "last_run": None}
LOCK = threading.Lock()


# ==========================================================================
# Reading the pipeline's output
# ==========================================================================

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_findings() -> list[dict]:
    """Both finding sets, with a content-derived id on each.

    The id is computed here rather than stored by the rules, so it stays
    correct even for output produced before the decision log existed.
    """
    out = []
    recon = read_csv(PATHS["reconciliation"] / "findings.csv")
    for r in recon:
        detail = ""
        try:
            ev = json.loads(r.get("evidence") or "{}")
            # A person breaching two segregation rules is two findings, not
            # one, so the rule breached is part of the identity.
            detail = ev.get("sod_rule_id") or ev.get("entitlement") or ""
        except json.JSONDecodeError:
            ev = {}
        out.append({
            "id": finding_id(r["rule_id"], r["subject"], detail),
            "source": "reconciliation",
            "rule_id": r["rule_id"],
            "severity": r["severity"],
            "subject": r["subject"],
            "person_number": r.get("person_number", ""),
            "summary": r.get("summary", ""),
            "basis": "DETERMINISTIC",
            "platform_visibility": r.get("visible_in_platform_data", "unknown"),
            "evidence": ev,
        })

    for r in read_csv(PATHS["drift"] / "drift_findings.csv"):
        try:
            ev = json.loads(r.get("evidence") or "{}")
        except json.JSONDecodeError:
            ev = {}
        out.append({
            "id": finding_id(r["rule_id"], r["subject"],
                             ev.get("entitlement", "")),
            "source": "drift",
            "rule_id": r["rule_id"],
            "severity": r["severity"],
            "subject": r["subject"],
            "person_number": r.get("person_number", ""),
            "summary": r.get("summary", ""),
            "basis": r.get("basis", ""),
            "platform_visibility": "unknown",
            "evidence": ev,
        })
    return out


def load_queue() -> list[dict]:
    rows = read_csv(PATHS["correlation"] / "adjudication_queue.csv")
    out = []
    for r in rows:
        out.append({
            "id": cluster_item_id(r.get("cluster_id", ""), r.get("ad_sam", "")),
            "cluster_id": r.get("cluster_id", ""),
            "account_type": r.get("account_type", ""),
            "status": r.get("status", ""),
            "confidence": float(r.get("confidence") or 0),
            "ad_sam": r.get("ad_sam", ""),
            "entra_upn": r.get("entra_upn", ""),
            "candidate": r.get("candidate_person_number", ""),
            "why": r.get("why_it_needs_a_human", ""),
        })
    return sorted(out, key=lambda x: -x["confidence"])


def load_coverage() -> list[dict]:
    out = []
    for rel in ("reconciliation/coverage_statement.csv", "drift/evidence_basis.csv"):
        key, name = rel.split("/")
        for r in read_csv(PATHS[key] / name):
            try:
                excl = json.loads(r.get("excluded") or "{}")
            except json.JSONDecodeError:
                excl = {}
            out.append({
                "rule_id": r["rule_id"],
                "name": r.get("name", r["rule_id"]),
                "basis": r.get("basis", "DETERMINISTIC"),
                "examined": int(r.get("population_examined") or 0),
                "total": int(r.get("population_total") or 0),
                "coverage": r.get("coverage_pct", ""),
                "findings": int(r.get("findings") or 0),
                "excluded": excl,
                "notes": r.get("notes", ""),
            })
    return out


def summary() -> dict:
    log = DecisionLog(PATHS["work"] / "decisions.jsonl")
    current = log.current()
    findings = load_findings()

    open_findings = [f for f in findings if f["id"] not in current]
    by_sev = {}
    for f in open_findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    queue = load_queue()
    return {
        "findings_total": len(findings),
        "findings_open": len(open_findings),
        "findings_decided": len(findings) - len(open_findings),
        "open_by_severity": by_sev,
        "queue_total": len(queue),
        "queue_open": len([q for q in queue if q["id"] not in current]),
        "expiring_soon": [
            {"item_id": d.item_id, "subject": d.subject,
             "expires_on": d.expires_on, "days": d.days_remaining}
            for d in log.expiring_soon(30)
        ],
        "estate": str(PATHS["estate"]),
        "scored": (PATHS["estate"] / "ground_truth" / "identity_map.json").exists(),
        "run": read_json(PATHS["work"] / "run.json"),
        "running": STATE["running"],
    }


# ==========================================================================
# Running the pipeline
# ==========================================================================

def pipeline_steps(profile: str | None) -> list[list[str]]:
    p = ["--profile", profile] if profile else []
    corr = str(PATHS["correlation"] / "correlation_map.csv")
    return [
        [sys.executable, "correlate.py", "--estate", str(PATHS["estate"]),
         "--out", str(PATHS["correlation"])] + p,
        [sys.executable, "reconcile.py", "--estate", str(PATHS["estate"]),
         "--correlation", corr, "--out", str(PATHS["reconciliation"])] + p,
        [sys.executable, "detect_drift.py", "--estate", str(PATHS["estate"]),
         "--correlation", corr, "--out", str(PATHS["drift"])] + p,
    ]


def run_pipeline(profile: str | None):
    with LOCK:
        if STATE["running"]:
            return
        STATE["running"] = True
        STATE["log"] = []

    def log(line: str):
        STATE["log"].append(line)

    try:
        snaps = sorted((PATHS["estate"] / "snapshots").iterdir())
        run_id = write_run_manifest(
            PATHS["work"] / "run.json",
            snapshot=snaps[-1].name if snaps else None,
            snapshots=len(snaps),
            estate=str(PATHS["estate"]),
            profile=profile,
            scored=(PATHS["estate"] / "ground_truth" / "identity_map.json").exists(),
        )
        log(f"run {run_id}")

        for cmd in pipeline_steps(profile):
            name = Path(cmd[1]).stem
            log(f"\n$ {name}")
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            for line in (r.stdout or "").splitlines():
                if line.strip():
                    log(line)
            if r.returncode:
                for line in (r.stderr or "").splitlines()[-6:]:
                    log(line)
                log(f"\n{name} failed. Later stages depend on it, so the run "
                    f"stopped here.")
                return
        log("\nDone.")
        STATE["last_run"] = now()
    except Exception as e:
        log(f"\n{type(e).__name__}: {e}")
    finally:
        with LOCK:
            STATE["running"] = False


# ==========================================================================
# HTTP
# ==========================================================================

class Handler(BaseHTTPRequestHandler):

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This serves an internal review console over loopback; no external
        # resources are referenced and none should be loadable.
        self.send_header("X-Content-Type-Options", "nosniff")
        # Scripts load from this origin only and never inline, so a name or
        # note that contains markup cannot execute. The stylesheet is static
        # and sits in the document head, which is why style-src is permitted
        # inline and script-src is not.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self' "
            "'unsafe-inline'; connect-src 'self'; img-src 'self'; "
            "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def log_message(self, *args):
        pass                      # the console has its own output

    # ---------------------------------------------------------------- GET

    def do_GET(self):
        route = urlparse(self.path)
        path, query = route.path, parse_qs(route.query)

        if path in ("/", "/index.html"):
            return self._file(UI / "index.html")
        if path.startswith("/static/"):
            return self._file(UI / path[len("/static/"):])

        if path == "/api/summary":
            return self._json(summary())
        if path == "/api/coverage":
            return self._json(load_coverage())
        if path == "/api/run-log":
            return self._json({"running": STATE["running"], "log": STATE["log"]})

        log = DecisionLog(PATHS["work"] / "decisions.jsonl")
        current = log.current()

        if path == "/api/findings":
            items = load_findings()
            for f in items:
                d = current.get(f["id"])
                f["decision"] = _decision_json(d) if d else None
            return self._json(items)

        if path == "/api/queue":
            items = load_queue()
            for q in items:
                d = current.get(q["id"])
                q["decision"] = _decision_json(d) if d else None
                if q["candidate"]:
                    q["candidate_detail"] = _person(q["candidate"])
            return self._json(items)

        if path == "/api/history":
            item = (query.get("id") or [""])[0]
            return self._json([_decision_json(d) for d in log.history(item)])

        if path == "/api/verdicts":
            return self._json({"finding": VERDICTS, "link": LINK_VERDICTS})

        return self._json({"error": "not found"}, 404)

    # --------------------------------------------------------------- POST

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/run":
            if STATE["running"]:
                return self._json({"error": "a run is already in progress"}, 409)
            # A one-element args must carry the trailing comma. Written as
            # args=(x) it is not a tuple, and Thread raises before the run
            # begins — with the failure landing on the request rather than in
            # the run log, where somebody would look for it.
            threading.Thread(target=run_pipeline,
                             args=(body.get("profile") or None,),
                             daemon=True).start()
            return self._json({"started": True})

        if path == "/api/decide":
            required = ("item_id", "item_kind", "verdict", "decided_by")
            missing = [k for k in required if not body.get(k)]
            if missing:
                return self._json({"error": f"missing: {', '.join(missing)}"}, 400)

            kind = body["item_kind"]
            allowed = VERDICTS if kind == "finding" else LINK_VERDICTS
            if body["verdict"] not in allowed:
                return self._json({"error": "unknown verdict"}, 400)

            # An accepted risk without an end date is a suppression nobody
            # will revisit, so the expiry is required rather than suggested.
            if body["verdict"] == "accepted_risk" and not body.get("expires_on"):
                return self._json(
                    {"error": "an accepted risk needs an expiry date, or the "
                              "finding is suppressed indefinitely"}, 400)

            run = read_json(PATHS["work"] / "run.json")
            d = Decision(
                item_id=body["item_id"],
                item_kind=kind,
                verdict=body["verdict"],
                note=(body.get("note") or "").strip(),
                decided_by=body["decided_by"].strip(),
                decided_at=now(),
                expires_on=body.get("expires_on") or None,
                run_id=run.get("run_id"),
                subject=body.get("subject", ""),
                rule_id=body.get("rule_id", ""),
            )
            DecisionLog(PATHS["work"] / "decisions.jsonl").append(d)
            return self._json(_decision_json(d))

        return self._json({"error": "not found"}, 404)

    # -------------------------------------------------------------- utils

    def _file(self, p: Path):
        if not p.exists() or not p.is_file():
            return self._json({"error": "not found"}, 404)
        # Refuse anything that escapes the UI directory.
        try:
            p.resolve().relative_to(UI.resolve())
        except ValueError:
            return self._json({"error": "forbidden"}, 403)
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        self._send(200, p.read_bytes(), ctype)


def _decision_json(d: Decision) -> dict:
    return {"verdict": d.verdict, "note": d.note, "by": d.decided_by,
            "at": d.decided_at, "expires_on": d.expires_on,
            "days_remaining": d.days_remaining, "run_id": d.run_id}


def _person(person_number: str) -> dict:
    """The HR record behind a candidate link, for the reviewer to judge."""
    snaps = sorted((PATHS["estate"] / "snapshots").iterdir())
    if not snaps:
        return {}
    for r in read_csv(snaps[-1] / "hcm_workers.csv"):
        if r.get("person_number") == person_number:
            return {k: r.get(k, "") for k in
                    ("person_number", "first_name", "last_name", "department",
                     "job_code", "assignment_status", "termination_date")}
    return {}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="CAAA review console")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--estate", default=None)
    ap.add_argument("--correlation", default=None)
    ap.add_argument("--reconciliation", default=None)
    ap.add_argument("--drift", default=None)
    ap.add_argument("--work", default=None,
                    help="where decisions and run manifests are kept")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    for key in ("estate", "correlation", "reconciliation", "drift", "work"):
        if getattr(a, key):
            PATHS[key] = Path(getattr(a, key)).resolve()
    PATHS["work"].mkdir(parents=True, exist_ok=True)

    url = f"http://127.0.0.1:{a.port}"
    print(f"""
  CAAA review console
  {url}

  estate     {PATHS['estate']}
  decisions  {PATHS['work'] / 'decisions.jsonl'}

  Bound to loopback only. This process reads workforce data; for access from
  another machine use SSH port forwarding rather than exposing the port.

  Ctrl-C to stop.
""")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
