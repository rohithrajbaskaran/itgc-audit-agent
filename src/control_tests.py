"""
Deterministic ITGC control tests.
"""

from pathlib import Path

import pandas as pd

from load_controls import get_control, load_controls

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"

# Every test returns exceptions in this same format
COLUMNS = ["control_id", "record_id", "employee_id", "system",
           "condition", "risk_rating", "escalated"]


# ---------------------------------------------------------------------------
# Evidence loading
# ---------------------------------------------------------------------------
def load_evidence():
    """Load the four evidence files with correct data types."""
    hr = pd.read_csv(DATA_DIR / "hr_roster.csv",
                     parse_dates=["hire_date", "termination_date"])
    access = pd.read_csv(DATA_DIR / "user_access.csv", parse_dates=["last_login"])
    tickets = pd.read_csv(DATA_DIR / "change_tickets.csv", parse_dates=["approval_date"])
    deployments = pd.read_csv(DATA_DIR / "deployments.csv", parse_dates=["deployment_date"])
    return {"hr": hr, "access": access, "tickets": tickets, "deployments": deployments}


def make_exception(control_id, record_id, employee_id, system, condition, escalate=False):
    """Build one exception row, applying the control's escalation rule."""
    base_rating = get_control(control_id)["risk_rating"]
    if escalate:
        rating = {"Medium": "High", "High": "Critical"}.get(base_rating, base_rating)
    else:
        rating = base_rating
    return {
        "control_id": control_id,
        "record_id": record_id,
        "employee_id": employee_id,
        "system": system,
        "condition": condition,
        "risk_rating": rating,
        "escalated": escalate,
    }


def to_table(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


# ---------------------------------------------------------------------------
# AC-01: Timely removal of terminated user access
# ---------------------------------------------------------------------------
def test_ac01_terminated_access(ev):
    terminated = ev["hr"][ev["hr"].status == "Terminated"]
    active_accounts = ev["access"][ev["access"].account_active]

    # Join: terminated employees who still have an active account
    matches = terminated.merge(active_accounts, on="employee_id")

    rows = []
    for m in matches.itertuples():
        used_after = m.last_login > m.termination_date
        condition = (f"Employee terminated {m.termination_date.date()} but "
                     f"{m.system} account remains active")
        if used_after:
            days = (m.last_login - m.termination_date).days
            condition += f"; account used {m.last_login.date()}, {days} days after termination"
        rows.append(make_exception("AC-01", m.account_id, m.employee_id,
                                   m.system, condition, escalate=used_after))
    return to_table(rows)


# ---------------------------------------------------------------------------
# AC-02: Multi-factor authentication enforced
# ---------------------------------------------------------------------------
def test_ac02_mfa(ev):
    a = ev["access"]
    no_mfa = a[a.account_active & ~a.mfa_enabled]

    rows = []
    for r in no_mfa.itertuples():
        is_admin = r.role == "Admin"
        condition = f"Active {r.role} account on {r.system} does not have MFA enabled"
        rows.append(make_exception("AC-02", r.account_id, r.employee_id,
                                   r.system, condition, escalate=is_admin))
    return to_table(rows)


# ---------------------------------------------------------------------------
# CM-01: Production changes are authorized
# ---------------------------------------------------------------------------
def test_cm01_unauthorized_deployments(ev):
    d = ev["deployments"]
    tickets = ev["tickets"].set_index("ticket_id")

    rows = []
    for r in d.itertuples():
        financial = r.system == "SAP ERP"
        if pd.isna(r.ticket_id):
            condition = f"Deployment to {r.system} on {r.deployment_date.date()} has no change ticket"
        elif r.ticket_id not in tickets.index:
            condition = f"Deployment references ticket {r.ticket_id}, which does not exist"
        elif r.deployment_date < tickets.at[r.ticket_id, "approval_date"]:
            condition = (f"Deployment on {r.deployment_date.date()} occurred before "
                         f"ticket {r.ticket_id} was approved")
        else:
            continue  # Deployment traced to an approved ticket: no exception
        rows.append(make_exception("CM-01", r.deployment_id, r.deployer_id,
                                   r.system, condition, escalate=financial))
    return to_table(rows)


# ---------------------------------------------------------------------------
# CM-02: Segregation of duties in change approval
# ---------------------------------------------------------------------------
def test_cm02_self_approval(ev):
    t = ev["tickets"]
    self_approved = t[t.requester_id == t.approver_id]

    rows = []
    for r in self_approved.itertuples():
        financial = r.system == "SAP ERP"
        condition = (f"Change '{r.description}' on {r.system} was requested and "
                     f"approved by the same person")
        rows.append(make_exception("CM-02", r.ticket_id, r.requester_id,
                                   r.system, condition, escalate=financial))
    return to_table(rows)


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------
TESTS = {
    "AC-01": test_ac01_terminated_access,
    "AC-02": test_ac02_mfa,
    "CM-01": test_cm01_unauthorized_deployments,
    "CM-02": test_cm02_self_approval,
}

POPULATION = {  # which evidence file each control tests in full
    "AC-01": "access", "AC-02": "access",
    "CM-01": "deployments", "CM-02": "tickets",
}


def run_all_tests():
    """Run every control test. Returns (all exceptions, summary table)."""
    ev = load_evidence()
    results, summary = [], []

    for control in load_controls()["controls"]:
        cid = control["id"]
        exceptions = TESTS[cid](ev)
        results.append(exceptions)
        summary.append({
            "control_id": cid,
            "control": control["name"],
            "population": len(ev[POPULATION[cid]]),
            "exceptions": len(exceptions),
            "escalated": int(exceptions.escalated.sum()),
            "result": "Effective" if exceptions.empty else "Exceptions noted",
        })

    return pd.concat(results, ignore_index=True), pd.DataFrame(summary)


if __name__ == "__main__":
    exceptions, summary = run_all_tests()

    RESULTS_DIR.mkdir(exist_ok=True)
    exceptions.to_csv(RESULTS_DIR / "exceptions.csv", index=False)
    summary.to_csv(RESULTS_DIR / "test_summary.csv", index=False)

    print("Control test results\n")
    print(summary.to_string(index=False))
    print(f"\nTotal exceptions: {len(exceptions)}")
    print(exceptions.risk_rating.value_counts().to_string())
    print(f"\nSaved to {RESULTS_DIR.name}/exceptions.csv and {RESULTS_DIR.name}/test_summary.csv")