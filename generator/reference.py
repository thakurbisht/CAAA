"""
Reference data for the synthetic healthcare identity estate.

This module is the domain model. Everything downstream — correlation,
reconciliation, drift detection — depends on the shapes defined here.

Deliberately modelled on a mid-size hospital group:
  - clinical staff rotating across departments (the main creep source)
  - Oracle ERP back-office with real SoD pairs
  - non-human identities (service, biomed device, interface engine)
  - a second facility with different naming conventions (merger legacy)
"""

# --------------------------------------------------------------------------
# Organisational structure
# --------------------------------------------------------------------------

FACILITIES = {
    "MAIN": {"name": "Central Hospital", "ad_ou": "OU=Users,OU=Central,DC=health,DC=local"},
    "NORTH": {"name": "North Medical Centre", "ad_ou": "OU=Staff,OU=NorthMC,DC=health,DC=local"},
}

DEPARTMENTS = {
    "ICU":      {"name": "Intensive Care",        "cost_centre": "CC-4100", "clinical": True},
    "ED":       {"name": "Emergency",             "cost_centre": "CC-4110", "clinical": True},
    "RAD":      {"name": "Radiology",             "cost_centre": "CC-4200", "clinical": True},
    "ONC":      {"name": "Oncology",              "cost_centre": "CC-4210", "clinical": True},
    "CARD":     {"name": "Cardiology",            "cost_centre": "CC-4220", "clinical": True},
    "LAB":      {"name": "Laboratory",            "cost_centre": "CC-4300", "clinical": True},
    "PHARM":    {"name": "Pharmacy",              "cost_centre": "CC-4310", "clinical": True},
    "SURG":     {"name": "Surgery",               "cost_centre": "CC-4400", "clinical": True},
    "MAT":      {"name": "Maternity",             "cost_centre": "CC-4410", "clinical": True},
    "FIN":      {"name": "Finance",               "cost_centre": "CC-7100", "clinical": False},
    "HR":       {"name": "Human Resources",       "cost_centre": "CC-7200", "clinical": False},
    "PROC":     {"name": "Procurement",           "cost_centre": "CC-7300", "clinical": False},
    "RCM":      {"name": "Revenue Cycle",         "cost_centre": "CC-7400", "clinical": False},
    "IT":       {"name": "Information Technology","cost_centre": "CC-8100", "clinical": False},
    "BIOMED":   {"name": "Biomedical Engineering","cost_centre": "CC-8200", "clinical": False},
}

# job_code -> (title, home departments, worker_type)
JOB_CODES = {
    "RN-01":   ("Registered Nurse",          ["ICU", "ED", "ONC", "CARD", "SURG", "MAT"], "EMPLOYEE"),
    "RN-02":   ("Senior Registered Nurse",   ["ICU", "ED", "ONC", "CARD", "SURG", "MAT"], "EMPLOYEE"),
    "RN-03":   ("Nurse Manager",             ["ICU", "ED", "ONC", "CARD", "SURG", "MAT"], "EMPLOYEE"),
    "PHY-01":  ("Resident Physician",        ["ICU", "ED", "ONC", "CARD", "SURG"],        "EMPLOYEE"),
    "PHY-02":  ("Attending Physician",       ["ICU", "ED", "ONC", "CARD", "SURG"],        "EMPLOYEE"),
    "PHY-03":  ("Consultant",                ["ONC", "CARD", "SURG", "RAD"],              "EMPLOYEE"),
    "RAD-01":  ("Radiographer",              ["RAD"],                                     "EMPLOYEE"),
    "RAD-02":  ("Senior Radiographer",       ["RAD"],                                     "EMPLOYEE"),
    "LAB-01":  ("Laboratory Technician",     ["LAB"],                                     "EMPLOYEE"),
    "LAB-02":  ("Laboratory Scientist",      ["LAB"],                                     "EMPLOYEE"),
    "PHA-01":  ("Pharmacist",                ["PHARM"],                                   "EMPLOYEE"),
    "PHA-02":  ("Pharmacy Technician",       ["PHARM"],                                   "EMPLOYEE"),
    "FIN-01":  ("Accounts Payable Clerk",    ["FIN"],                                     "EMPLOYEE"),
    "FIN-02":  ("Financial Accountant",      ["FIN"],                                     "EMPLOYEE"),
    "FIN-03":  ("Finance Manager",           ["FIN"],                                     "EMPLOYEE"),
    "PROC-01": ("Procurement Officer",       ["PROC"],                                    "EMPLOYEE"),
    "HR-01":   ("HR Officer",                ["HR"],                                      "EMPLOYEE"),
    "RCM-01":  ("Billing Specialist",        ["RCM"],                                     "EMPLOYEE"),
    "RCM-02":  ("Coding Specialist",         ["RCM"],                                     "EMPLOYEE"),
    "IT-01":   ("Service Desk Analyst",      ["IT"],                                      "EMPLOYEE"),
    "IT-02":   ("Systems Administrator",     ["IT"],                                      "EMPLOYEE"),
    "IT-03":   ("Application Support",       ["IT"],                                      "EMPLOYEE"),
    "BME-01":  ("Biomedical Engineer",       ["BIOMED"],                                  "EMPLOYEE"),
    "LOC-RN":  ("Locum Nurse",               ["ICU", "ED", "SURG", "MAT"],                "CONTINGENT"),
    "LOC-PHY": ("Locum Physician",           ["ED", "ICU"],                                "CONTINGENT"),
    "VND-01":  ("Vendor Support Engineer",   ["IT", "BIOMED", "RAD"],                     "VENDOR"),
}

# --------------------------------------------------------------------------
# Entitlement catalogue
#
# Two layers, deliberately:
#   DIRECTORY  - AD groups / Entra roles. This is what a directory-only
#                certification process can see.
#   APPLICATION- app-internal roles (Cerner security classes, Oracle ERP
#                responsibilities, PACS, pharmacy). This is where the real
#                privilege lives, and is the layer most often NOT aggregated.
#
# The `aggregated_by_iga` flag simulates coverage gaps. Detection engines
# should be able to report on what they cannot see.
# --------------------------------------------------------------------------

ENTITLEMENTS = {
    # ---- Directory layer -------------------------------------------------
    "GRP_ALL_STAFF":            {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_VPN_REMOTE":           {"layer": "DIRECTORY", "app": "AD",     "risk": "MEDIUM",   "aggregated_by_iga": True},
    "GRP_DEPT_ICU":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_ED":              {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_RAD":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_ONC":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_CARD":            {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_LAB":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_PHARM":           {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_SURG":            {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_MAT":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_FIN":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_HR":              {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_PROC":            {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_RCM":             {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_IT":              {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_DEPT_BIOMED":          {"layer": "DIRECTORY", "app": "AD",     "risk": "LOW",      "aggregated_by_iga": True},
    "GRP_SRV_ADMIN_TIER1":      {"layer": "DIRECTORY", "app": "AD",     "risk": "CRITICAL", "aggregated_by_iga": True},
    "GRP_DOMAIN_ADMINS":        {"layer": "DIRECTORY", "app": "AD",     "risk": "CRITICAL", "aggregated_by_iga": True},
    "ENT_ROLE_GLOBAL_READER":   {"layer": "DIRECTORY", "app": "ENTRA",  "risk": "MEDIUM",   "aggregated_by_iga": True},
    "ENT_ROLE_USER_ADMIN":      {"layer": "DIRECTORY", "app": "ENTRA",  "risk": "HIGH",     "aggregated_by_iga": True},

    # ---- Cerner EHR (aggregated) ----------------------------------------
    "CERN_SEC_RN_GENERAL":      {"layer": "APPLICATION", "app": "CERNER", "risk": "MEDIUM", "aggregated_by_iga": True},
    "CERN_SEC_RN_ICU":          {"layer": "APPLICATION", "app": "CERNER", "risk": "HIGH",   "aggregated_by_iga": True},
    "CERN_SEC_RN_ED":           {"layer": "APPLICATION", "app": "CERNER", "risk": "HIGH",   "aggregated_by_iga": True},
    "CERN_SEC_RN_ONC":          {"layer": "APPLICATION", "app": "CERNER", "risk": "HIGH",   "aggregated_by_iga": True},
    "CERN_SEC_RN_MAT":          {"layer": "APPLICATION", "app": "CERNER", "risk": "HIGH",   "aggregated_by_iga": True},
    "CERN_SEC_PHYSICIAN":       {"layer": "APPLICATION", "app": "CERNER", "risk": "HIGH",   "aggregated_by_iga": True},
    "CERN_SEC_ORDER_CPOE":      {"layer": "APPLICATION", "app": "CERNER", "risk": "HIGH",   "aggregated_by_iga": True},
    "CERN_SEC_CHART_CORRECT":   {"layer": "APPLICATION", "app": "CERNER", "risk": "CRITICAL","aggregated_by_iga": True},
    "CERN_SEC_BREAKGLASS":      {"layer": "APPLICATION", "app": "CERNER", "risk": "CRITICAL","aggregated_by_iga": True},

    # ---- Oracle ERP (aggregated) — the SoD-rich surface ------------------
    "ORA_AP_INVOICE_ENTRY":     {"layer": "APPLICATION", "app": "ORACLE", "risk": "MEDIUM", "aggregated_by_iga": True},
    "ORA_AP_PAYMENT_APPROVE":   {"layer": "APPLICATION", "app": "ORACLE", "risk": "HIGH",   "aggregated_by_iga": True},
    "ORA_AP_VENDOR_MAINTAIN":   {"layer": "APPLICATION", "app": "ORACLE", "risk": "HIGH",   "aggregated_by_iga": True},
    "ORA_GL_JOURNAL_ENTRY":     {"layer": "APPLICATION", "app": "ORACLE", "risk": "MEDIUM", "aggregated_by_iga": True},
    "ORA_GL_JOURNAL_APPROVE":   {"layer": "APPLICATION", "app": "ORACLE", "risk": "HIGH",   "aggregated_by_iga": True},
    "ORA_PO_REQUISITION":       {"layer": "APPLICATION", "app": "ORACLE", "risk": "LOW",    "aggregated_by_iga": True},
    "ORA_PO_APPROVE":           {"layer": "APPLICATION", "app": "ORACLE", "risk": "HIGH",   "aggregated_by_iga": True},
    "ORA_HCM_WORKER_MAINTAIN":  {"layer": "APPLICATION", "app": "ORACLE", "risk": "HIGH",   "aggregated_by_iga": True},
    "ORA_HCM_PAYROLL_RUN":      {"layer": "APPLICATION", "app": "ORACLE", "risk": "CRITICAL","aggregated_by_iga": True},

    # ---- NOT aggregated — the coverage gap the assessment should find ----
    "PACS_ROLE_VIEWER":         {"layer": "APPLICATION", "app": "PACS",   "risk": "MEDIUM", "aggregated_by_iga": False},
    "PACS_ROLE_REPORTING":      {"layer": "APPLICATION", "app": "PACS",   "risk": "HIGH",   "aggregated_by_iga": False},
    "PACS_ROLE_ADMIN":          {"layer": "APPLICATION", "app": "PACS",   "risk": "CRITICAL","aggregated_by_iga": False},
    "LIS_RESULT_AUTHORISE":     {"layer": "APPLICATION", "app": "LIS",    "risk": "CRITICAL","aggregated_by_iga": False},
    "LIS_RESULT_ENTRY":         {"layer": "APPLICATION", "app": "LIS",    "risk": "MEDIUM", "aggregated_by_iga": False},
    "PYX_DISPENSE_CTRL_SUB":    {"layer": "APPLICATION", "app": "PYXIS",  "risk": "CRITICAL","aggregated_by_iga": False},
    "PYX_OVERRIDE":             {"layer": "APPLICATION", "app": "PYXIS",  "risk": "CRITICAL","aggregated_by_iga": False},
    "PYX_INVENTORY_ADJUST":     {"layer": "APPLICATION", "app": "PYXIS",  "risk": "HIGH",   "aggregated_by_iga": False},
}

# --------------------------------------------------------------------------
# Role baselines: what a person in this job code, in this department,
# is *expected* to hold. Anything beyond this is a drift candidate.
#
# Note the design choice: baselines are intentionally INCOMPLETE for some
# job codes. Real role models always are. Peer-group analysis has to be able
# to derive a baseline empirically without relying on this table.
# --------------------------------------------------------------------------

BASE_ALL = ["GRP_ALL_STAFF"]

DEPT_GROUP = {d: f"GRP_DEPT_{d}" for d in DEPARTMENTS}

CERNER_BY_DEPT = {
    "ICU":  "CERN_SEC_RN_ICU",
    "ED":   "CERN_SEC_RN_ED",
    "ONC":  "CERN_SEC_RN_ONC",
    "MAT":  "CERN_SEC_RN_MAT",
}

ROLE_BASELINE = {
    "RN-01":   ["CERN_SEC_RN_GENERAL"],
    "RN-02":   ["CERN_SEC_RN_GENERAL", "CERN_SEC_ORDER_CPOE"],
    "RN-03":   ["CERN_SEC_RN_GENERAL", "CERN_SEC_ORDER_CPOE", "CERN_SEC_CHART_CORRECT"],
    "PHY-01":  ["CERN_SEC_PHYSICIAN", "CERN_SEC_ORDER_CPOE"],
    "PHY-02":  ["CERN_SEC_PHYSICIAN", "CERN_SEC_ORDER_CPOE", "CERN_SEC_CHART_CORRECT"],
    "PHY-03":  ["CERN_SEC_PHYSICIAN", "CERN_SEC_ORDER_CPOE", "CERN_SEC_CHART_CORRECT", "GRP_VPN_REMOTE"],
    "RAD-01":  ["PACS_ROLE_VIEWER"],
    "RAD-02":  ["PACS_ROLE_VIEWER", "PACS_ROLE_REPORTING"],
    "LAB-01":  ["LIS_RESULT_ENTRY"],
    "LAB-02":  ["LIS_RESULT_ENTRY", "LIS_RESULT_AUTHORISE"],
    "PHA-01":  ["PYX_DISPENSE_CTRL_SUB", "CERN_SEC_ORDER_CPOE"],
    "PHA-02":  ["PYX_DISPENSE_CTRL_SUB"],
    "FIN-01":  ["ORA_AP_INVOICE_ENTRY"],
    "FIN-02":  ["ORA_AP_INVOICE_ENTRY", "ORA_GL_JOURNAL_ENTRY"],
    "FIN-03":  ["ORA_GL_JOURNAL_ENTRY", "ORA_GL_JOURNAL_APPROVE", "ORA_AP_PAYMENT_APPROVE"],
    "PROC-01": ["ORA_PO_REQUISITION"],
    "HR-01":   ["ORA_HCM_WORKER_MAINTAIN"],
    "RCM-01":  ["CERN_SEC_RN_GENERAL"],
    "RCM-02":  ["CERN_SEC_RN_GENERAL"],
    "IT-01":   ["GRP_VPN_REMOTE", "ENT_ROLE_GLOBAL_READER"],
    "IT-02":   ["GRP_VPN_REMOTE", "GRP_SRV_ADMIN_TIER1", "ENT_ROLE_USER_ADMIN"],
    "IT-03":   ["GRP_VPN_REMOTE", "ENT_ROLE_GLOBAL_READER"],
    "BME-01":  ["GRP_VPN_REMOTE"],
    "LOC-RN":  ["CERN_SEC_RN_GENERAL"],
    "LOC-PHY": ["CERN_SEC_PHYSICIAN", "CERN_SEC_ORDER_CPOE"],
    "VND-01":  ["GRP_VPN_REMOTE"],
}

# --------------------------------------------------------------------------
# Segregation of Duties rules.
# Cross-application pairs are the ones an IGA platform usually misses,
# because most SoD engines are configured per-application.
# --------------------------------------------------------------------------

SOD_RULES = [
    {
        "id": "SOD-FIN-01",
        "name": "Vendor maintenance and payment approval",
        "left": "ORA_AP_VENDOR_MAINTAIN",
        "right": "ORA_AP_PAYMENT_APPROVE",
        "severity": "CRITICAL",
        "scope": "INTRA_APP",
        "rationale": "Enables creation of a fictitious vendor and self-approval of payment to it.",
    },
    {
        "id": "SOD-FIN-02",
        "name": "Journal entry and journal approval",
        "left": "ORA_GL_JOURNAL_ENTRY",
        "right": "ORA_GL_JOURNAL_APPROVE",
        "severity": "HIGH",
        "scope": "INTRA_APP",
        "rationale": "Permits unreviewed adjustment of the general ledger.",
    },
    {
        "id": "SOD-PROC-01",
        "name": "Requisition raising and purchase approval",
        "left": "ORA_PO_REQUISITION",
        "right": "ORA_PO_APPROVE",
        "severity": "HIGH",
        "scope": "INTRA_APP",
        "rationale": "Permits self-approved procurement.",
    },
    {
        "id": "SOD-HR-01",
        "name": "Worker maintenance and payroll execution",
        "left": "ORA_HCM_WORKER_MAINTAIN",
        "right": "ORA_HCM_PAYROLL_RUN",
        "severity": "CRITICAL",
        "scope": "INTRA_APP",
        "rationale": "Permits creation of a ghost worker and payment to them.",
    },
    {
        "id": "SOD-CLIN-01",
        "name": "Controlled substance ordering and dispensing",
        "left": "CERN_SEC_ORDER_CPOE",
        "right": "PYX_DISPENSE_CTRL_SUB",
        "severity": "CRITICAL",
        "scope": "CROSS_APP",
        "rationale": "Permits a single actor to order and dispense controlled substances without independent witness.",
    },
    {
        "id": "SOD-CLIN-02",
        "name": "Result entry and result authorisation",
        "left": "LIS_RESULT_ENTRY",
        "right": "LIS_RESULT_AUTHORISE",
        "severity": "HIGH",
        "scope": "INTRA_APP",
        "rationale": "Permits unverified release of diagnostic results.",
    },
    {
        "id": "SOD-IT-01",
        "name": "Directory administration and clinical charting correction",
        "left": "GRP_SRV_ADMIN_TIER1",
        "right": "CERN_SEC_CHART_CORRECT",
        "severity": "CRITICAL",
        "scope": "CROSS_APP",
        "rationale": "Permits an administrator to alter clinical records and suppress the identity trail.",
    },
]

# --------------------------------------------------------------------------
# Non-human identity classes.
# These carry standing privilege and are almost never certified.
# --------------------------------------------------------------------------

NHI_CLASSES = {
    "SERVICE": {
        "prefix": "svc-",
        "examples": ["backup", "monitoring", "sccm", "sqlagent", "adsync", "reporting"],
        "typical_entitlements": ["GRP_SRV_ADMIN_TIER1", "GRP_ALL_STAFF"],
    },
    "INTERFACE": {
        "prefix": "hl7-",
        "examples": ["cerner-lab", "cerner-rad", "pacs-bridge", "pharmacy-feed"],
        "typical_entitlements": ["LIS_RESULT_ENTRY", "PACS_ROLE_VIEWER"],
    },
    "DEVICE": {
        "prefix": "dev-",
        "examples": ["infusion-icu-01", "infusion-icu-02", "ct-scanner-01",
                     "mri-01", "ultrasound-mat-01", "pyxis-ed-01"],
        "typical_entitlements": ["PACS_ROLE_VIEWER", "PYX_INVENTORY_ADJUST"],
    },
    "SHARED": {
        "prefix": "ws-",
        "examples": ["icu-station-3", "ed-triage-1", "theatre-2", "ward-b-desk"],
        "typical_entitlements": ["CERN_SEC_RN_GENERAL", "GRP_ALL_STAFF"],
    },
}

# --------------------------------------------------------------------------
# Name pools — deliberately mixed to reflect a Gulf healthcare workforce
# --------------------------------------------------------------------------

FIRST_NAMES = [
    "Aisha", "Omar", "Fatima", "Yusuf", "Layla", "Karim", "Noor", "Hassan",
    "Mariam", "Tariq", "Zainab", "Rashid", "Huda", "Salim", "Amina", "Faisal",
    "Priya", "Rahul", "Anjali", "Vikram", "Meera", "Arjun", "Divya", "Sanjay",
    "Grace", "Michael", "Sarah", "David", "Emma", "James", "Claire", "Daniel",
    "Maria", "Jose", "Ana", "Carlos", "Rosa", "Miguel", "Elena", "Pedro",
    "Chen", "Wei", "Ling", "Jun", "Mei", "Hao", "Yan", "Feng",
    "Ngozi", "Chidi", "Amara", "Emeka", "Zara", "Kwame", "Adaeze", "Tunde",
]

LAST_NAMES = [
    "Al-Mansouri", "Al-Hashimi", "Khan", "Ahmed", "Rahman", "Hussain",
    "Sharma", "Patel", "Nair", "Reddy", "Menon", "Iyer", "Kapoor", "Desai",
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Wilson",
    "Garcia", "Rodriguez", "Martinez", "Lopez", "Gonzalez", "Perez",
    "Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Huang", "Zhao",
    "Okafor", "Adeyemi", "Mensah", "Osei", "Nwosu", "Balogun",
    "Fernandes", "Pereira", "Dias", "Costa", "Santos",
]
