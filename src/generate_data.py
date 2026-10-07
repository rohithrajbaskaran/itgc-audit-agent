import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
SEED = 42
NUM_EMPLOYEES = 500
NUM_TICKETS = 200
AUDIT_PERIOD_START = date(2025, 10, 1)
AUDIT_PERIOD_END = date(2026, 9, 30)

# Planted exceptions (these become the answer key)
NUM_TERMINATED_WITH_ACCESS = 15   # AC-01
NUM_LOGIN_AFTER_TERMINATION = 5   # subset of the 15 above (higher risk)
NUM_MFA_GAPS = 20                 # AC-02
NUM_DEPLOYMENTS_NO_TICKET = 10    # CM-01
NUM_SELF_APPROVED = 5             # CM-02 (segregation of duties)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
KEY_DIR = DATA_DIR / "answer_key"

DEPARTMENTS = ["Finance", "Sales", "Marketing", "HR", "Operations", "IT", "Engineering"]
SYSTEMS = ["Active Directory", "SAP ERP", "Salesforce", "AWS Console"]
CHANGE_DESCRIPTIONS = [
    "Patch security vulnerability",
    "Update user role permissions",
    "Deploy new reporting feature",
    "Modify database configuration",
    "Upgrade application version",
    "Fix bug in invoice processing",
    "Change firewall rule",
    "Update password policy settings",
]

fake = Faker()
Faker.seed(SEED)
random.seed(SEED)


def random_date(start, end):
    """Return a random date between start and end (inclusive)."""
    return start + timedelta(days=random.randint(0, (end - start).days))


# ---------------------------------------------------------------------------
# Clean evidence 
# ---------------------------------------------------------------------------
def build_hr_roster():
    rows = []
    for i in range(1, NUM_EMPLOYEES + 1):
        terminated = random.random() < 0.15
        rows.append({
            "employee_id": f"E{i:04d}",
            "name": fake.name(),
            "department": random.choice(DEPARTMENTS),
            "job_title": fake.job(),
            "status": "Terminated" if terminated else "Active",
            "hire_date": random_date(date(2015, 1, 1), AUDIT_PERIOD_START - timedelta(days=30)),
            "termination_date": (
                random_date(AUDIT_PERIOD_START, AUDIT_PERIOD_END - timedelta(days=14))
                if terminated else None
            ),
        })
    return pd.DataFrame(rows)


def build_user_access(hr):
    rows = []
    account_num = 1
    for emp in hr.itertuples():
        for system in random.sample(SYSTEMS, k=random.randint(1, 3)):
            is_tech = emp.department in ("IT", "Engineering")
            if is_tech and random.random() < 0.3:
                role = "Admin"
            else:
                role = random.choices(["Standard User", "Power User"], weights=[85, 15])[0]

            if emp.status == "Terminated":
                # Correct behaviour: access removed, last login before termination
                active = False
                last_login = random_date(emp.termination_date - timedelta(days=30),
                                         emp.termination_date)
            else:
                active = True
                last_login = random_date(AUDIT_PERIOD_END - timedelta(days=60), AUDIT_PERIOD_END)

            rows.append({
                "account_id": f"ACC{account_num:05d}",
                "employee_id": emp.employee_id,
                "system": system,
                "role": role,
                "account_active": active,
                "mfa_enabled": True,
                "last_login": last_login,
            })
            account_num += 1
    return pd.DataFrame(rows)


def build_change_tickets(hr):
    tech_staff = hr[
        hr.department.isin(["IT", "Engineering"]) & (hr.status == "Active")
    ].employee_id.tolist()

    rows = []
    for i in range(1, NUM_TICKETS + 1):
        requester, approver = random.sample(tech_staff, 2)  # always different people
        rows.append({
            "ticket_id": f"CHG-{i:04d}",
            "description": random.choice(CHANGE_DESCRIPTIONS),
            "system": random.choice(SYSTEMS),
            "requester_id": requester,
            "approver_id": approver,
            "approval_date": random_date(AUDIT_PERIOD_START, AUDIT_PERIOD_END - timedelta(days=7)),
        })
    return pd.DataFrame(rows), tech_staff


# ---------------------------------------------------------------------------
# Planted exceptions
# ---------------------------------------------------------------------------
def plant_terminated_access(hr, access, answer_key):
    terminated_ids = hr.loc[hr.status == "Terminated", "employee_id"].tolist()
    chosen = random.sample(terminated_ids, NUM_TERMINATED_WITH_ACCESS)

    for n, emp_id in enumerate(chosen):
        acct_idx = access.index[access.employee_id == emp_id][0]
        term_date = hr.loc[hr.employee_id == emp_id, "termination_date"].iloc[0]
        access.at[acct_idx, "account_active"] = True
        details = f"Terminated {term_date}; account still active"

        if n < NUM_LOGIN_AFTER_TERMINATION:
            login = random_date(term_date + timedelta(days=1), AUDIT_PERIOD_END)
            access.at[acct_idx, "last_login"] = login
            details += f"; HIGH RISK: logged in {login}, after termination"

        answer_key.append({
            "control_id": "AC-01",
            "exception_type": "Terminated user with active access",
            "record_id": access.at[acct_idx, "account_id"],
            "employee_id": emp_id,
            "details": details,
        })


def plant_mfa_gaps(hr, access, answer_key):
    active_emps = set(hr.loc[hr.status == "Active", "employee_id"])
    candidates = access[access.employee_id.isin(active_emps)].index.tolist()

    for idx in random.sample(candidates, NUM_MFA_GAPS):
        access.at[idx, "mfa_enabled"] = False
        answer_key.append({
            "control_id": "AC-02",
            "exception_type": "Active account without MFA",
            "record_id": access.at[idx, "account_id"],
            "employee_id": access.at[idx, "employee_id"],
            "details": f"{access.at[idx, 'role']} on {access.at[idx, 'system']}",
        })


def plant_self_approvals(tickets, answer_key):
    for idx in random.sample(tickets.index.tolist(), NUM_SELF_APPROVED):
        tickets.at[idx, "approver_id"] = tickets.at[idx, "requester_id"]
        answer_key.append({
            "control_id": "CM-02",
            "exception_type": "Change approved by its own requester",
            "record_id": tickets.at[idx, "ticket_id"],
            "employee_id": tickets.at[idx, "requester_id"],
            "details": "Requester and approver are the same person",
        })


def build_deployments(tickets, tech_staff, answer_key):
    rows = []
    # Every ticket gets a deployment shortly after approval
    for t in tickets.itertuples():
        rows.append({
            "ticket_id": t.ticket_id,
            "system": t.system,
            "deployer_id": t.requester_id,
            "deployment_date": t.approval_date + timedelta(days=random.randint(0, 5)),
        })
    # Planted: deployments with no change ticket
    for _ in range(NUM_DEPLOYMENTS_NO_TICKET):
        rows.append({
            "ticket_id": None,
            "system": random.choice(SYSTEMS),
            "deployer_id": random.choice(tech_staff),
            "deployment_date": random_date(AUDIT_PERIOD_START, AUDIT_PERIOD_END),
        })

    # Sort by date so planted rows are mixed in, then assign IDs
    deployments = pd.DataFrame(rows).sort_values("deployment_date").reset_index(drop=True)
    deployments.insert(0, "deployment_id", [f"DEP-{i:04d}" for i in range(1, len(deployments) + 1)])

    for d in deployments[deployments.ticket_id.isna()].itertuples():
        answer_key.append({
            "control_id": "CM-01",
            "exception_type": "Deployment without change ticket",
            "record_id": d.deployment_id,
            "employee_id": d.deployer_id,
            "details": f"Deployed to {d.system} on {d.deployment_date}",
        })
    return deployments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    DATA_DIR.mkdir(exist_ok=True)
    KEY_DIR.mkdir(exist_ok=True)
    answer_key = []

    hr = build_hr_roster()
    access = build_user_access(hr)
    tickets, tech_staff = build_change_tickets(hr)

    plant_terminated_access(hr, access, answer_key)
    plant_mfa_gaps(hr, access, answer_key)
    plant_self_approvals(tickets, answer_key)
    deployments = build_deployments(tickets, tech_staff, answer_key)

    hr.to_csv(DATA_DIR / "hr_roster.csv", index=False)
    access.to_csv(DATA_DIR / "user_access.csv", index=False)
    tickets.to_csv(DATA_DIR / "change_tickets.csv", index=False)
    deployments.to_csv(DATA_DIR / "deployments.csv", index=False)
    key = pd.DataFrame(answer_key)
    key.to_csv(KEY_DIR / "answer_key.csv", index=False)

    print("Evidence files created in data/:")
    print(f"  hr_roster.csv       {len(hr):>5} employees ({(hr.status == 'Terminated').sum()} terminated)")
    print(f"  user_access.csv     {len(access):>5} accounts")
    print(f"  change_tickets.csv  {len(tickets):>5} tickets")
    print(f"  deployments.csv     {len(deployments):>5} deployments")
    print(f"\nAnswer key: {len(key)} planted exceptions")
    print(key.groupby(["control_id", "exception_type"]).size().to_string())


if __name__ == "__main__":
    main()