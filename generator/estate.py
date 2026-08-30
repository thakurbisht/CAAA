"""
Synthetic hybrid healthcare identity estate.

Emits four independent source feeds, mirroring what an assurance function
would read directly rather than through the IGA platform:

    hcm_workers.csv          Oracle HCM worker extract
    ad_accounts.csv          Local Active Directory (LDAP read)
    entra_accounts.csv       Entra ID (Graph read)
    iga_identities.csv       IGA platform identity register
    iga_entitlements.csv     IGA platform entitlement holdings
    app_entitlements.csv     ground truth of actual application privilege

The distinction between `iga_entitlements` and `app_entitlements` is the
point of the exercise: the first is what the platform believes, the second
is what is actually held. Reconciliation lives in the gap between them.

Alongside the feeds, `ground_truth.json` records every injected pathology so
that a detection engine can be scored for precision and recall rather than
merely demonstrated.
"""

from __future__ import annotations

import csv
import json
import shutil
import random
import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .config import Config
from . import reference as ref


# ==========================================================================
# Model
# ==========================================================================

@dataclass
class Holding:
    """A single entitlement held by an identity."""
    entitlement: str
    granted_on: date
    source_dept: str            # the department that justified the grant
    last_used: Optional[date] = None
    revoked_on: Optional[date] = None

    @property
    def active(self) -> bool:
        return self.revoked_on is None


@dataclass
class Identity:
    pid: str
    kind: str                          # HUMAN | NHI
    first_name: str = ""
    last_name: str = ""

    # --- HCM -------------------------------------------------------------
    person_number: Optional[str] = None
    has_hcm_record: bool = True
    job_code: Optional[str] = None
    department: Optional[str] = None
    facility: str = "MAIN"
    worker_type: str = "EMPLOYEE"
    hire_date: Optional[date] = None
    termination_date: Optional[date] = None
    manager_person_number: Optional[str] = None
    hcm_status: str = "ACTIVE_ASSIGN"

    # --- Local AD --------------------------------------------------------
    ad_sam: Optional[str] = None
    ad_upn: Optional[str] = None
    ad_enabled: bool = True
    ad_in_sync_scope: bool = True
    ad_employee_id: Optional[str] = None      # often blank in reality
    ad_last_logon: Optional[date] = None
    has_ad: bool = True

    # --- Entra -----------------------------------------------------------
    entra_upn: Optional[str] = None
    entra_object_id: Optional[str] = None
    entra_immutable_id: Optional[str] = None
    entra_enabled: bool = True
    entra_user_type: str = "Member"
    has_entra: bool = True

    # --- IGA -------------------------------------------------------------
    iga_id: Optional[str] = None
    has_iga_identity: bool = True
    iga_lifecycle_state: str = "ACTIVE"

    # --- Privilege -------------------------------------------------------
    holdings: list[Holding] = field(default_factory=list)
    # Entitlements the IGA platform believes are held but which are not.
    # Populated by the stale-connector pathology.
    iga_phantom: set[str] = field(default_factory=set)
    # Entitlements actually held but invisible to IGA (connector lag).
    iga_blind: set[str] = field(default_factory=set)

    # --- NHI -------------------------------------------------------------
    nhi_class: Optional[str] = None
    nhi_owner_pid: Optional[str] = None

    # --- Satellite accounts ---------------------------------------------
    owner_pid: Optional[str] = None    # for admin accounts: the human behind it

    # --- History ---------------------------------------------------------
    transfers: list[dict] = field(default_factory=list)

    def active_holdings(self) -> list[Holding]:
        return [h for h in self.holdings if h.active]

    def holds(self, ent: str) -> bool:
        return any(h.entitlement == ent and h.active for h in self.holdings)

    def department_on(self, on: date) -> str:
        """The department this worker sat in on a given date.

        Snapshots are written after the simulation finishes, so reading
        `self.department` returns the final department for every snapshot and
        leaves the HR feed flat across the whole window. A control that needs
        to *observe* a transfer rather than be told about one then has nothing
        to work with, and drift detection degrades into reading the answer off
        a `source_dept` column that no real HR extract carries.

        The transfer log is replayed instead: start from the current
        department and walk back through every move that happened after the
        date being asked about.
        """
        dept = self.department
        for t in sorted(self.transfers, key=lambda x: x["on"], reverse=True):
            if date.fromisoformat(t["on"]) > on:
                dept = t["from"]
        return dept

    def job_title_on(self, on: date, job_codes) -> str:
        return job_codes[self.job_code][0] if self.job_code else ""


# ==========================================================================
# Generator
# ==========================================================================

class EstateGenerator:

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.identities: dict[str, Identity] = {}
        self.ground_truth: list[dict] = []
        self.start = date.fromisoformat(cfg.start_date)
        self.end = self.start + timedelta(days=cfg.days - 1)
        self._seq = 0
        self._used_sams: set[str] = set()

    # ---------------------------------------------------------------- util

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:06d}"

    def _oid(self, seed: str) -> str:
        h = hashlib.sha256(seed.encode()).hexdigest()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _truth(self, kind: str, subject: str, on: date, **detail):
        self.ground_truth.append({
            "pathology": kind,
            "subject": subject,
            "injected_on": on.isoformat(),
            **detail,
        })

    def _sam(self, first: str, last: str, facility: str) -> str:
        base = f"{first}.{last}".lower().replace("'", "").replace("-", "")
        if facility == "NORTH" and self.rng.random() < self.cfg.naming_variant_rate:
            # merger legacy: first-initial + surname, no separator
            base = f"{first[0]}{last}".lower().replace("'", "").replace("-", "")
        sam, n = base, 1
        while sam in self._used_sams:
            n += 1
            sam = f"{base}{n}"
        self._used_sams.add(sam)
        return sam

    # ------------------------------------------------------------ baseline

    def _baseline_entitlements(self, job_code: str, dept: str) -> list[str]:
        ents = list(ref.BASE_ALL)
        ents.append(ref.DEPT_GROUP[dept])
        ents.extend(ref.ROLE_BASELINE.get(job_code, []))
        # department-specific Cerner class for nursing roles
        if job_code.startswith("RN") and dept in ref.CERNER_BY_DEPT:
            ents.append(ref.CERNER_BY_DEPT[dept])
        return sorted(set(ents))

    def _make_human(self, on: date, *, joiner: bool = False) -> Identity:
        cfg, rng = self.cfg, self.rng
        first = rng.choice(ref.FIRST_NAMES)
        last = rng.choice(ref.LAST_NAMES)
        job_code = rng.choice(list(ref.JOB_CODES))
        title, depts, worker_type = ref.JOB_CODES[job_code]
        dept = rng.choice(depts)
        facility = rng.choices(["MAIN", "NORTH"], weights=[0.75, 0.25])[0]

        pid = self._next("P")
        person_number = f"EMP{rng.randint(100000, 999999)}"

        if joiner:
            hire = on
        else:
            # tenure between 1 month and 12 years, skewed toward recent
            hire = self.start - timedelta(days=int(rng.triangular(30, 4400, 600)))

        ident = Identity(
            pid=pid, kind="HUMAN",
            first_name=first, last_name=last,
            person_number=person_number,
            job_code=job_code, department=dept, facility=facility,
            worker_type=worker_type, hire_date=hire,
        )

        sam = self._sam(first, last, facility)
        domain = "health.local" if facility == "MAIN" else "northmc.health.local"
        ident.ad_sam = sam
        ident.ad_upn = f"{sam}@{domain}"
        ident.entra_upn = f"{sam}@healthgroup.ae"
        ident.entra_object_id = self._oid(pid)
        ident.entra_immutable_id = self._oid("imm" + pid)[:22]
        ident.iga_id = self._next("IGA")
        # employeeID populated only sometimes — a real correlation obstacle
        ident.ad_employee_id = person_number if rng.random() < 0.62 else None
        ident.ad_last_logon = on - timedelta(days=rng.randint(0, 21))

        for ent in self._baseline_entitlements(job_code, dept):
            ident.holdings.append(Holding(
                entitlement=ent,
                granted_on=max(hire, self.start - timedelta(days=3000)),
                source_dept=dept,
                last_used=on - timedelta(days=rng.randint(0, 60)),
            ))

        self.identities[pid] = ident
        return ident

    def _make_nhi(self, on: date) -> Identity:
        rng = self.rng
        cls = rng.choice(list(ref.NHI_CLASSES))
        spec = ref.NHI_CLASSES[cls]
        name = rng.choice(spec["examples"])
        pid = self._next("N")
        sam = f"{spec['prefix']}{name}"
        n = 1
        while sam in self._used_sams:
            n += 1
            sam = f"{spec['prefix']}{name}-{n}"
        self._used_sams.add(sam)

        ident = Identity(
            pid=pid, kind="NHI",
            first_name=sam, last_name="",
            has_hcm_record=False,
            person_number=None,
            department="IT" if cls in ("SERVICE", "INTERFACE") else "BIOMED",
            facility="MAIN",
            worker_type="NON_HUMAN",
            hire_date=self.start - timedelta(days=rng.randint(200, 3000)),
            nhi_class=cls,
        )
        ident.ad_sam = sam
        ident.ad_upn = f"{sam}@health.local"
        ident.entra_upn = f"{sam}@healthgroup.ae"
        ident.entra_object_id = self._oid(pid)
        ident.entra_immutable_id = self._oid("imm" + pid)[:22]
        ident.iga_id = self._next("IGA")
        ident.has_entra = cls in ("SERVICE", "INTERFACE")
        # NHI are frequently absent from the IGA register entirely
        ident.has_iga_identity = rng.random() < 0.45
        ident.ad_last_logon = on - timedelta(days=rng.randint(0, 5))

        for ent in spec["typical_entitlements"]:
            ident.holdings.append(Holding(
                entitlement=ent, granted_on=ident.hire_date,
                source_dept=ident.department,
                last_used=on - timedelta(days=rng.randint(0, 3)),
            ))

        self.identities[pid] = ident
        return ident

    # ----------------------------------------------------------- lifecycle

    def _transfer(self, ident: Identity, on: date):
        """Move a worker to a new department.

        This is the principal creep mechanism. The new role's entitlements
        are granted; whether the prior role's entitlements are revoked is
        governed by `retained_on_transfer_rate`.
        """
        rng = self.rng
        title, depts, _ = ref.JOB_CODES[ident.job_code]
        candidates = [d for d in depts if d != ident.department]
        if not candidates:
            return
        old_dept = ident.department
        new_dept = rng.choice(candidates)

        ident.transfers.append({
            "on": on.isoformat(), "from": old_dept, "to": new_dept,
        })
        ident.department = new_dept

        # grant the new role
        for ent in self._baseline_entitlements(ident.job_code, new_dept):
            if not ident.holds(ent):
                ident.holdings.append(Holding(
                    entitlement=ent, granted_on=on, source_dept=new_dept,
                    last_used=on,
                ))

        # revoke — or fail to revoke — the old role
        old_specific = set(self._baseline_entitlements(ident.job_code, old_dept)) - \
                       set(self._baseline_entitlements(ident.job_code, new_dept))
        retain = rng.random() < self.cfg.retained_on_transfer_rate

        for h in ident.active_holdings():
            if h.entitlement in old_specific:
                if retain:
                    continue
                h.revoked_on = on + timedelta(days=rng.randint(0, 5))

        if retain and old_specific:
            self._truth(
                "RETAINED_ON_TRANSFER", ident.pid, on,
                person_number=ident.person_number,
                ad_sam=ident.ad_sam,
                from_dept=old_dept, to_dept=new_dept,
                retained=sorted(old_specific),
            )

    def _terminate(self, ident: Identity, on: date, *, clean: bool = True):
        ident.termination_date = on
        ident.hcm_status = "TERMINATED"
        if clean:
            ident.ad_enabled = False
            ident.entra_enabled = False
            ident.iga_lifecycle_state = "TERMINATED"
            for h in ident.active_holdings():
                h.revoked_on = on
        else:
            # the missed leaver: HCM says gone, everything else says present
            ident.iga_lifecycle_state = self.rng.choice(["ACTIVE", "ACTIVE", "TERMINATED"])
            self._truth(
                "MISSED_LEAVER", ident.pid, on,
                person_number=ident.person_number,
                ad_sam=ident.ad_sam,
                ad_enabled=ident.ad_enabled,
                entra_enabled=ident.entra_enabled,
                iga_state=ident.iga_lifecycle_state,
                active_entitlements=len(ident.active_holdings()),
            )

    def _handle_linked_admin(self, owner: Identity, on: date, *, clean: bool):
        """A leaver's second privileged account is the classic missed asset.

        It has no HCM record of its own, so a leaver reconciliation keyed on
        person_number will never see it. It is only reachable by first
        resolving the admin account back to its human owner.
        """
        adm = next((i for i in self.identities.values()
                    if i.worker_type == "EMPLOYEE_ADMIN"
                    and i.ad_sam == f"a-{owner.ad_sam}"), None)
        if adm is None or adm.termination_date is not None:
            return
        if clean:
            adm.termination_date = on
            adm.ad_enabled = False
            adm.entra_enabled = False
            adm.iga_lifecycle_state = "TERMINATED"
            for h in adm.active_holdings():
                h.revoked_on = on
        else:
            self._truth(
                "ORPHANED_ADMIN_ACCOUNT", adm.pid, on,
                ad_sam=adm.ad_sam,
                owner_pid=owner.pid,
                owner_person_number=owner.person_number,
                owner_terminated_on=on.isoformat(),
                privileged_entitlements=[h.entitlement for h in adm.active_holdings()],
            )

    # ---------------------------------------------------------- pathology

    def _inject_static_pathologies(self):
        cfg, rng = self.cfg, self.rng
        humans = [i for i in self.identities.values() if i.kind == "HUMAN"]
        nhis = [i for i in self.identities.values() if i.kind == "NHI"]
        d0 = self.start

        # --- correlation: privileged second account ----------------------
        for ident in rng.sample(humans, int(len(humans) * cfg.admin_account_rate)):
            pid = self._next("P")
            adm = Identity(
                pid=pid, kind="HUMAN",
                first_name=ident.first_name, last_name=ident.last_name,
                has_hcm_record=False,
                person_number=None,
                job_code=ident.job_code, department=ident.department,
                facility=ident.facility, worker_type="EMPLOYEE_ADMIN",
                hire_date=ident.hire_date,
            )
            sam = f"a-{ident.ad_sam}"
            self._used_sams.add(sam)
            adm.ad_sam = sam
            adm.ad_upn = f"{sam}@health.local"
            adm.has_entra = False
            adm.entra_upn = None
            adm.has_iga_identity = rng.random() < 0.35
            adm.iga_id = self._next("IGA")
            adm.ad_last_logon = d0 - timedelta(days=rng.randint(0, 40))
            adm.owner_pid = ident.pid
            for ent in ["GRP_SRV_ADMIN_TIER1", "GRP_ALL_STAFF"]:
                adm.holdings.append(Holding(ent, ident.hire_date, ident.department,
                                            last_used=d0 - timedelta(days=rng.randint(0, 30))))
            self.identities[pid] = adm
            self._truth("SECOND_PRIVILEGED_ACCOUNT", pid, d0,
                        linked_to_pid=ident.pid,
                        linked_person_number=ident.person_number,
                        ad_sam=sam, primary_sam=ident.ad_sam)

        # --- correlation: no HCM record (contingent) ---------------------
        contingent = [i for i in humans if i.worker_type in ("CONTINGENT", "VENDOR")]
        n = min(len(contingent), int(len(humans) * cfg.no_hcm_record_rate))
        for ident in rng.sample(contingent, n) if n else []:
            ident.has_hcm_record = False
            self._truth("NO_HCM_RECORD", ident.pid, d0,
                        ad_sam=ident.ad_sam, worker_type=ident.worker_type,
                        active_entitlements=len(ident.active_holdings()))

        # --- correlation: name change ------------------------------------
        for ident in rng.sample(humans, min(cfg.name_change_count, len(humans))):
            old_last = ident.last_name
            ident.last_name = rng.choice(ref.LAST_NAMES)
            # HCM updated, directory not
            self._truth("NAME_CHANGE_UNSYNCED", ident.pid, d0,
                        person_number=ident.person_number,
                        hcm_last_name=ident.last_name,
                        ad_sam=ident.ad_sam, ad_reflects=old_last)

        # --- correlation: rehire -----------------------------------------
        for ident in rng.sample(humans, min(cfg.rehire_count, len(humans))):
            old_pn = ident.person_number
            ident.person_number = f"EMP{rng.randint(100000, 999999)}"
            self._truth("REHIRE_NEW_PERSON_NUMBER", ident.pid, d0,
                        old_person_number=old_pn,
                        new_person_number=ident.person_number,
                        ad_sam=ident.ad_sam,
                        ad_employee_id=ident.ad_employee_id)

        # --- reconciliation: stale connector -----------------------------
        for ident in rng.sample(humans, min(cfg.stale_connector_count, len(humans))):
            ident.iga_lifecycle_state = "DISABLED"
            ident.ad_enabled = True
            self._truth("STALE_CONNECTOR", ident.pid, d0,
                        person_number=ident.person_number, ad_sam=ident.ad_sam,
                        iga_state="DISABLED", ad_enabled=True)

        # --- reconciliation: Entra-only / guest --------------------------
        for k in range(cfg.entra_only_count):
            pid = self._next("P")
            first, last = rng.choice(ref.FIRST_NAMES), rng.choice(ref.LAST_NAMES)
            g = Identity(
                pid=pid, kind="HUMAN", first_name=first, last_name=last,
                has_hcm_record=False, person_number=None,
                job_code="VND-01", department="IT", facility="MAIN",
                worker_type="VENDOR", hire_date=d0 - timedelta(days=rng.randint(30, 900)),
                has_ad=False, ad_sam=None, ad_upn=None,
                has_iga_identity=False,
            )
            g.entra_upn = f"{first.lower()}.{last.lower()}_ext#EXT#@healthgroup.ae"
            g.entra_object_id = self._oid(pid)
            g.entra_user_type = "Guest"
            g.iga_id = self._next("IGA")
            for ent in ["ENT_ROLE_GLOBAL_READER", "GRP_VPN_REMOTE"]:
                g.holdings.append(Holding(ent, g.hire_date, "IT",
                                          last_used=d0 - timedelta(days=rng.randint(0, 200))))
            self.identities[pid] = g
            self._truth("ENTRA_ONLY_IDENTITY", pid, d0,
                        entra_upn=g.entra_upn, user_type="Guest",
                        in_iga=False, active_entitlements=len(g.active_holdings()))

        # --- reconciliation: out of Connect sync scope -------------------
        for ident in rng.sample(humans, min(cfg.out_of_sync_scope_count, len(humans))):
            ident.ad_in_sync_scope = False
            ident.has_entra = False
            self._truth("OUT_OF_SYNC_SCOPE", ident.pid, d0,
                        person_number=ident.person_number, ad_sam=ident.ad_sam)

        # --- drift: peer-group outlier -----------------------------------
        high_risk = [e for e, m in ref.ENTITLEMENTS.items()
                     if m["risk"] in ("HIGH", "CRITICAL")]
        for ident in rng.sample(humans, min(cfg.peer_outlier_count, len(humans))):
            ent = rng.choice(high_risk)
            if ident.holds(ent):
                continue
            ident.holdings.append(Holding(
                ent, d0 - timedelta(days=rng.randint(60, 1200)),
                source_dept="UNKNOWN",
                last_used=None if rng.random() < 0.6 else d0 - timedelta(days=rng.randint(0, 90)),
            ))
            self._truth("PEER_GROUP_OUTLIER", ident.pid, d0,
                        person_number=ident.person_number, ad_sam=ident.ad_sam,
                        job_code=ident.job_code, department=ident.department,
                        entitlement=ent, risk=ref.ENTITLEMENTS[ent]["risk"])

        # --- SoD violations ----------------------------------------------
        for k in range(cfg.sod_violation_count):
            rule = rng.choice(ref.SOD_RULES)
            pool = [i for i in humans if not (i.holds(rule["left"]) and i.holds(rule["right"]))]
            if not pool:
                continue
            ident = rng.choice(pool)
            for ent in (rule["left"], rule["right"]):
                if not ident.holds(ent):
                    ident.holdings.append(Holding(
                        ent, d0 - timedelta(days=rng.randint(10, 800)),
                        source_dept=ident.department,
                        last_used=d0 - timedelta(days=rng.randint(0, 120)),
                    ))
            self._truth("SOD_VIOLATION", ident.pid, d0,
                        person_number=ident.person_number, ad_sam=ident.ad_sam,
                        rule_id=rule["id"], rule_name=rule["name"],
                        severity=rule["severity"], scope=rule["scope"],
                        entitlements=[rule["left"], rule["right"]])

        # --- standing break-glass ----------------------------------------
        clinicians = [i for i in humans if i.job_code.startswith(("RN", "PHY", "LOC"))]
        for ident in rng.sample(clinicians, min(cfg.standing_breakglass_count, len(clinicians))):
            if not ident.holds("CERN_SEC_BREAKGLASS"):
                ident.holdings.append(Holding(
                    "CERN_SEC_BREAKGLASS",
                    d0 - timedelta(days=rng.randint(90, 1000)),
                    source_dept=ident.department,
                    last_used=None if rng.random() < 0.7 else d0 - timedelta(days=rng.randint(0, 300)),
                ))
                self._truth("STANDING_BREAKGLASS", ident.pid, d0,
                            person_number=ident.person_number, ad_sam=ident.ad_sam,
                            job_code=ident.job_code, department=ident.department)

        # --- orphaned NHI ------------------------------------------------
        for ident in nhis:
            if rng.random() < cfg.orphaned_nhi_rate:
                ident.nhi_owner_pid = None
                self._truth("ORPHANED_NHI", ident.pid, d0,
                            ad_sam=ident.ad_sam, nhi_class=ident.nhi_class,
                            active_entitlements=len(ident.active_holdings()))
            else:
                owner = rng.choice([i for i in humans if i.department == "IT"] or humans)
                ident.nhi_owner_pid = owner.pid

        # --- dormancy ----------------------------------------------------
        for ident in self.identities.values():
            for h in ident.active_holdings():
                if rng.random() < cfg.dormant_entitlement_rate:
                    h.last_used = None
                    self._truth("DORMANT_ENTITLEMENT", ident.pid, d0,
                                ad_sam=ident.ad_sam, entitlement=h.entitlement,
                                granted_on=h.granted_on.isoformat(),
                                risk=ref.ENTITLEMENTS[h.entitlement]["risk"])

        # --- IGA blind spots: unaggregated applications -------------------
        for ident in self.identities.values():
            for h in ident.active_holdings():
                if not ref.ENTITLEMENTS[h.entitlement]["aggregated_by_iga"]:
                    ident.iga_blind.add(h.entitlement)

    # --------------------------------------------------------------- build

    def build(self):
        cfg, rng = self.cfg, self.rng

        for _ in range(cfg.population):
            self._make_human(self.start)
        for _ in range(cfg.nhi_count):
            self._make_nhi(self.start)

        self._inject_static_pathologies()

        snapshots: list[date] = []
        missed_budget = cfg.missed_leaver_count

        for d in range(cfg.days):
            today = self.start + timedelta(days=d)
            humans = [i for i in self.identities.values()
                      if i.kind == "HUMAN" and i.termination_date is None]

            # joiners
            for _ in range(self._poisson(cfg.joiner_rate * cfg.population)):
                self._make_human(today, joiner=True)

            # movers
            for _ in range(self._poisson(cfg.mover_rate * cfg.population)):
                if humans:
                    self._transfer(rng.choice(humans), today)

            # leavers — only workers with an HCM record can be a "leaver";
            # linked admin accounts are handled as a consequence, not a peer
            leaver_pool = [i for i in humans
                           if i.has_hcm_record and i.worker_type != "EMPLOYEE_ADMIN"]
            for _ in range(self._poisson(cfg.leaver_rate * cfg.population)):
                if not leaver_pool:
                    break
                victim = rng.choice(leaver_pool)
                dirty = missed_budget > 0 and rng.random() < 0.35
                self._terminate(victim, today, clean=not dirty)
                if dirty:
                    missed_budget -= 1
                leaver_pool.remove(victim)
                self._handle_linked_admin(victim, today, clean=not dirty)

            if self._is_snapshot_day(d):
                snapshots.append(today)

        return snapshots

    def _poisson(self, lam: float) -> int:
        # small-lambda Knuth; adequate for our rates
        import math
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            k += 1
            p *= self.rng.random()
            if p <= L:
                return k - 1

    def _is_snapshot_day(self, d: int) -> bool:
        if not self.cfg.write_daily_snapshots:
            return d == self.cfg.days - 1
        if not self.cfg.sparse_snapshots:
            return True
        return (d == 0 or d == self.cfg.days - 1
                or d % self.cfg.sparse_interval_days == 0)

    # --------------------------------------------------------------- emit

    def write(self, snapshots: list[date]):
        out = Path(self.cfg.out_dir)
        snap_dir = out / "snapshots"

        # Clear any previous run before writing.
        #
        # Without this, a re-run with different parameters leaves the earlier
        # snapshots in place and overwrites only the ground truth. Downstream
        # phases then read the newest snapshot on disk — which may belong to
        # the previous estate — and score it against the current answer key.
        # The result is not an error but a plausible-looking set of metrics
        # that describes nothing: correlation dropped from 100% precision to
        # 90.7% with 34 false links at supposedly *easier* settings, which is
        # what exposed this.
        if snap_dir.exists():
            stale = sorted(p.name for p in snap_dir.iterdir() if p.is_dir())
            keep = {s.isoformat() for s in snapshots}
            removed = [s for s in stale if s not in keep]
            if removed:
                print(f"  Clearing {len(removed)} snapshots from a previous run "
                      f"({removed[0]} to {removed[-1]})")
            shutil.rmtree(snap_dir)

        snap_dir.mkdir(parents=True, exist_ok=True)

        for snap in snapshots:
            self._write_snapshot(snap_dir / snap.isoformat(), snap)

        (out / "ground_truth").mkdir(parents=True, exist_ok=True)
        with open(out / "ground_truth" / "pathologies.json", "w") as f:
            json.dump({
                "config": self.cfg.summary(),
                "generated_snapshots": [s.isoformat() for s in snapshots],
                "pathology_counts": self._counts(),
                "pathologies": self.ground_truth,
            }, f, indent=2)

        # The correlation answer key. Records which accounts across which
        # systems belong to the same underlying identity, and — for admin
        # accounts and other satellites — which human they belong to.
        with open(out / "ground_truth" / "identity_map.json", "w") as f:
            json.dump([
                {
                    "pid": i.pid,
                    "kind": i.kind,
                    "worker_type": i.worker_type,
                    "owner_pid": self._owner_pid(i),
                    "person_number": i.person_number,
                    "ad_sam": i.ad_sam,
                    "entra_upn": i.entra_upn,
                    "entra_object_id": i.entra_object_id,
                    "iga_id": i.iga_id if i.has_iga_identity else None,
                    "in_hcm": i.has_hcm_record,
                    "in_ad": i.has_ad,
                    "in_entra": i.has_entra,
                    "in_iga": i.has_iga_identity,
                }
                for i in self.identities.values()
            ], f, indent=2)

        with open(out / "ground_truth" / "sod_rules.json", "w") as f:
            json.dump(ref.SOD_RULES, f, indent=2)

        with open(out / "ground_truth" / "entitlement_catalogue.json", "w") as f:
            json.dump(ref.ENTITLEMENTS, f, indent=2)

        return out

    def _owner_pid(self, ident: Identity) -> Optional[str]:
        """The human an account ultimately belongs to.

        For a primary human account this is itself. For an admin satellite
        or an owned non-human identity it is the human behind it. This is
        the link a correlation engine has to rediscover.
        """
        if ident.kind == "NHI":
            return ident.nhi_owner_pid
        if ident.worker_type == "EMPLOYEE_ADMIN":
            return ident.owner_pid
        return ident.pid

    def _counts(self) -> dict:
        c: dict[str, int] = {}
        for g in self.ground_truth:
            c[g["pathology"]] = c.get(g["pathology"], 0) + 1
        return dict(sorted(c.items()))

    def _write_snapshot(self, path: Path, on: date):
        path.mkdir(parents=True, exist_ok=True)
        idents = [i for i in self.identities.values()
                  if i.hire_date is None or i.hire_date <= on]

        # ---- HCM --------------------------------------------------------
        with open(path / "hcm_workers.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["person_number", "first_name", "last_name", "job_code",
                        "job_title", "department", "cost_centre", "facility",
                        "worker_type", "assignment_status", "hire_date",
                        "termination_date", "manager_person_number"])
            for i in idents:
                if not i.has_hcm_record or i.kind == "NHI":
                    continue
                if (i.termination_date
                        and i.termination_date < on - timedelta(days=self.cfg.hcm_retention_days)):
                    continue
                title = ref.JOB_CODES[i.job_code][0] if i.job_code else ""
                dept_on = i.department_on(on)
                w.writerow([
                    i.person_number, i.first_name, i.last_name, i.job_code, title,
                    dept_on, ref.DEPARTMENTS[dept_on]["cost_centre"],
                    i.facility, i.worker_type,
                    "TERMINATED" if i.termination_date and i.termination_date <= on else "ACTIVE_ASSIGN",
                    i.hire_date.isoformat() if i.hire_date else "",
                    i.termination_date.isoformat() if i.termination_date and i.termination_date <= on else "",
                    i.manager_person_number or "",
                ])

        # ---- Local AD ---------------------------------------------------
        with open(path / "ad_accounts.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sam_account_name", "distinguished_name", "user_principal_name",
                        "display_name", "employee_id", "department", "user_account_control",
                        "enabled", "in_sync_scope", "when_created", "last_logon_timestamp"])
            for i in idents:
                if not i.has_ad:
                    continue
                enabled = i.ad_enabled and not (
                    i.termination_date and i.termination_date <= on and i.ad_enabled is False)
                uac = 512 if enabled else 514
                ou = ref.FACILITIES[i.facility]["ad_ou"]
                w.writerow([
                    i.ad_sam, f"CN={i.ad_sam},{ou}", i.ad_upn,
                    f"{i.first_name} {i.last_name}".strip(),
                    i.ad_employee_id or "", i.department_on(on), uac,
                    "TRUE" if enabled else "FALSE",
                    "TRUE" if i.ad_in_sync_scope else "FALSE",
                    i.hire_date.isoformat() if i.hire_date else "",
                    i.ad_last_logon.isoformat() if i.ad_last_logon else "",
                ])

        # ---- Entra ------------------------------------------------------
        with open(path / "entra_accounts.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["object_id", "user_principal_name", "display_name",
                        "on_premises_immutable_id", "on_premises_sync_enabled",
                        "user_type", "account_enabled", "department", "created_datetime"])
            for i in idents:
                if not i.has_entra:
                    continue
                w.writerow([
                    i.entra_object_id, i.entra_upn,
                    f"{i.first_name} {i.last_name}".strip(),
                    i.entra_immutable_id if i.ad_in_sync_scope else "",
                    "TRUE" if i.ad_in_sync_scope and i.has_ad else "FALSE",
                    i.entra_user_type,
                    "TRUE" if i.entra_enabled else "FALSE",
                    i.department_on(on),
                    i.hire_date.isoformat() if i.hire_date else "",
                ])

        # ---- IGA identities ---------------------------------------------
        with open(path / "iga_identities.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["iga_identity_id", "display_name", "correlated_person_number",
                        "correlated_ad_sam", "lifecycle_state", "identity_type",
                        "manager_iga_id", "last_aggregation"])
            for i in idents:
                if not i.has_iga_identity:
                    continue
                state = i.iga_lifecycle_state
                if i.termination_date and i.termination_date <= on and state == "ACTIVE":
                    state = "ACTIVE"  # the missed-leaver signal
                w.writerow([
                    i.iga_id, f"{i.first_name} {i.last_name}".strip(),
                    i.person_number or "", i.ad_sam or "", state,
                    "NON_HUMAN" if i.kind == "NHI" else "EMPLOYEE",
                    "", on.isoformat(),
                ])

        # ---- IGA entitlements (what the platform believes) --------------
        with open(path / "iga_entitlements.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["iga_identity_id", "entitlement", "application", "layer",
                        "risk", "granted_on", "source"])
            for i in idents:
                if not i.has_iga_identity:
                    continue
                for h in i.holdings:
                    if h.revoked_on and h.revoked_on <= on:
                        continue
                    if h.granted_on > on:
                        continue
                    if not ref.ENTITLEMENTS[h.entitlement]["aggregated_by_iga"]:
                        continue          # not aggregated: invisible to IGA
                    m = ref.ENTITLEMENTS[h.entitlement]
                    w.writerow([i.iga_id, h.entitlement, m["app"], m["layer"],
                                m["risk"], h.granted_on.isoformat(), "AGGREGATED"])

        # ---- Actual application privilege (ground truth) ----------------
        with open(path / "app_entitlements.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ad_sam", "entra_upn", "entitlement", "application", "layer",
                        "risk", "aggregated_by_iga", "granted_on", "source_dept",
                        "last_used"])
            for i in idents:
                for h in i.holdings:
                    if h.revoked_on and h.revoked_on <= on:
                        continue
                    if h.granted_on > on:
                        continue
                    m = ref.ENTITLEMENTS[h.entitlement]
                    w.writerow([
                        i.ad_sam or "", i.entra_upn or "", h.entitlement,
                        m["app"], m["layer"], m["risk"],
                        "TRUE" if m["aggregated_by_iga"] else "FALSE",
                        h.granted_on.isoformat(), h.source_dept,
                        h.last_used.isoformat() if h.last_used else "",
                    ])

        # ---- NHI register (deliberately partial) ------------------------
        with open(path / "nhi_register.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ad_sam", "nhi_class", "owner_person_number",
                        "owner_status", "purpose", "review_cadence"])
            for i in idents:
                if i.kind != "NHI":
                    continue
                owner = self.identities.get(i.nhi_owner_pid) if i.nhi_owner_pid else None
                w.writerow([
                    i.ad_sam, i.nhi_class,
                    owner.person_number if owner and owner.person_number else "",
                    ("TERMINATED" if owner and owner.termination_date and owner.termination_date <= on
                     else "ACTIVE" if owner else "UNKNOWN"),
                    "", "",
                ])
