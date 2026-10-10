"""
The four ITGC audit agents.
"""

import os
from typing import TypedDict

import pandas as pd
from pydantic import BaseModel, Field

from audit_trail import log_event
from control_tests import load_evidence, run_all_tests
from load_controls import get_control, load_controls

MODEL = os.getenv("AUDIT_MODEL", "claude-sonnet-5-5")
OFFLINE = False  # set True by run_audit.py --offline to run without API calls

SYSTEM_PROMPT = """You are a senior IT auditor assisting on an IT General Controls review.
Rules you must follow:
- Use ONLY the facts provided. Never invent numbers, names, dates, or systems.
- Never invent framework references; cite only those provided to you.
- Write in clear, professional audit language suitable for management.
- Your output is a DRAFT that a human auditor will review before it is final."""

RATING_ORDER = ["Low", "Medium", "High", "Critical"]


# ---------------------------------------------------------------------------
# Shared state passed between agents
# ---------------------------------------------------------------------------
class AuditState(TypedDict, total=False):
    run_id: str
    evidence_stats: dict          # Evidence Collector
    evidence_issues: list
    evidence_blocking: bool
    evidence_assessment: str
    test_summary: list            # Control Tester
    exceptions: list
    anomaly_review: dict
    findings: list                # Framework Mapper, then Reporter


# ---------------------------------------------------------------------------
# Structured outputs: the model must return exactly these fields
# ---------------------------------------------------------------------------
class EvidenceAssessment(BaseModel):
    assessment: str = Field(description="2-4 sentences assessing whether the evidence is "
                            "complete and reliable enough to test, referencing the checks performed.")


class AnomalyReview(BaseModel):
    patterns: str = Field(description="2-4 sentences on notable patterns across the exceptions, "
                          "such as concentration in one system or employees with repeat exceptions.")
    priority_items: str = Field(description="The specific exceptions the auditor should look at first, and why.")


class RiskRationale(BaseModel):
    rationale: str = Field(description="2-3 sentences explaining why this finding has its risk rating, "
                           "referring to the escalated exceptions where relevant.")


class DraftFinding(BaseModel):
    title: str = Field(description="Short finding title, under 12 words.")
    condition: str = Field(description="What was found: the facts, with counts and examples.")
    criteria: str = Field(description="What should have happened, citing the control and framework references provided.")
    cause: str = Field(description="The likely cause. Phrase it as a possible cause to be confirmed with management.")
    effect: str = Field(description="The risk or impact to the organization.")
    recommendation: str = Field(description="Specific actions management should take.")


def ask(run_id, agent, task, prompt, schema):
    """Send one request to the model and log it to the audit trail."""
    if OFFLINE:
        # Placeholder text so the whole workflow can be tested for free
        result = schema(**{f: f"[Offline: {agent} would write the {f.replace('_', ' ')} here]"
                           for f in schema.model_fields})
        model_used = "offline"
    else:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model=MODEL, max_tokens=2000)
        result = llm.with_structured_output(schema, method="json_schema").invoke(
            [("system", SYSTEM_PROMPT), ("human", prompt)])
        model_used = MODEL

    log_event(run_id, agent, task, inputs={"prompt": prompt},
              outputs=result.model_dump(), model=model_used)
    return result


# ---------------------------------------------------------------------------
# Agent 1: Evidence Collector
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    "hr": ["employee_id", "status", "termination_date"],
    "access": ["account_id", "employee_id", "system", "role",
               "account_active", "mfa_enabled", "last_login"],
    "tickets": ["ticket_id", "system", "requester_id", "approver_id", "approval_date"],
    "deployments": ["deployment_id", "ticket_id", "system", "deployer_id", "deployment_date"],
}
PRIMARY_KEYS = {"hr": "employee_id", "access": "account_id",
                "tickets": "ticket_id", "deployments": "deployment_id"}


def evidence_collector(state):
    """Load the evidence and check it is complete and accurate before testing."""
    run_id = state["run_id"]
    audit = load_controls()["audit"]
    start, end = pd.Timestamp(audit["period_start"]), pd.Timestamp(audit["period_end"])

    ev = load_evidence()
    issues, blocking = [], False

    for name, df in ev.items():
        missing = [c for c in REQUIRED_COLUMNS[name] if c not in df.columns]
        if missing:
            issues.append(f"{name}: missing required columns {missing}")
            blocking = True
        if df.empty:
            issues.append(f"{name}: file contains no records")
            blocking = True
        key = PRIMARY_KEYS[name]
        if key in df.columns:
            if df[key].isna().any():
                issues.append(f"{name}: {df[key].isna().sum()} records missing {key}")
            if df[key].duplicated().any():
                issues.append(f"{name}: {df[key].duplicated().sum()} duplicate {key} values")

    # Accuracy: do dates fall inside the audit period?
    for name, col in [("tickets", "approval_date"), ("deployments", "deployment_date")]:
        outside = ev[name][(ev[name][col] < start) | (ev[name][col] > end)]
        if len(outside):
            issues.append(f"{name}: {len(outside)} records dated outside the audit period")

    # Completeness: does every account belong to a known employee?
    unknown = set(ev["access"].employee_id) - set(ev["hr"].employee_id)
    if unknown:
        issues.append(f"access: {len(unknown)} accounts belong to employees not in the HR roster")

    stats = {name: len(df) for name, df in ev.items()}
    stats["terminated_employees"] = int((ev["hr"].status == "Terminated").sum())

    prompt = f"""Assess the evidence received for the audit period {start.date()} to {end.date()}.

Record counts: {stats}
Checks performed: required columns present, no empty files, no missing or duplicate
record IDs, dates within the audit period, all accounts linked to an HR record.
Issues found: {issues if issues else "None"}
Blocking issues (testing cannot proceed): {"Yes" if blocking else "No"}"""

    result = ask(run_id, "Evidence Collector", "Assess evidence completeness and accuracy",
                 prompt, EvidenceAssessment)

    return {"evidence_stats": stats, "evidence_issues": issues,
            "evidence_blocking": blocking, "evidence_assessment": result.assessment}


# ---------------------------------------------------------------------------
# Agent 2: Control Tester
# ---------------------------------------------------------------------------
def control_tester(state):
    """Run the deterministic control tests, then review the results for patterns."""
    run_id = state["run_id"]
    exceptions, summary = run_all_tests()

    log_event(run_id, "Control Tester", "Execute deterministic control tests",
              outputs={"exceptions": len(exceptions), "summary": summary.to_dict("records")})

    # Code does the counting; the model only interprets the counts
    by_system = exceptions.groupby(["control_id", "system"]).size().to_dict()
    repeat = exceptions.employee_id.value_counts()
    repeat = repeat[repeat > 1].to_dict()
    escalated = exceptions[exceptions.escalated][["control_id", "record_id", "condition"]]

    prompt = f"""Review these ITGC test results and identify patterns and priorities.

Summary by control:
{summary.to_string(index=False)}

Exceptions by control and system: {by_system}
Employees appearing in more than one exception: {repeat if repeat else "None"}
Escalated (higher-risk) exceptions:
{escalated.to_string(index=False)}"""

    result = ask(run_id, "Control Tester", "Review test results for patterns",
                 prompt, AnomalyReview)

    return {"test_summary": summary.to_dict("records"),
            "exceptions": exceptions.to_dict("records"),
            "anomaly_review": result.model_dump()}


# ---------------------------------------------------------------------------
# Agent 3: Framework Mapper
# ---------------------------------------------------------------------------
def framework_mapper(state):
    """Group exceptions into one finding per control and attach framework references."""
    run_id = state["run_id"]
    exceptions = pd.DataFrame(state["exceptions"])
    findings = []

    for cid, group in exceptions.groupby("control_id"):
        control = get_control(cid)
        # Finding rating = the highest rating among its exceptions
        rating = max(group.risk_rating, key=RATING_ORDER.index)

        # Framework references come from controls.yaml, never from the model,
        # so a citation can never be invented
        finding = {
            "finding_id": f"F-{cid}",
            "control_id": cid,
            "control_name": control["name"],
            "domain": control["domain"],
            "frameworks": control["frameworks"],
            "risk_rating": rating,
            "exception_count": len(group),
            "escalated_count": int(group.escalated.sum()),
            "systems": sorted(group.system.unique()),
        }

        prompt = f"""Explain why this finding is rated {rating}.

Control: {cid} {control['name']}
Default rating for this control: {control['risk_rating']}
Escalation rule: {control['escalation_rule'].strip()}
Exceptions: {len(group)} total, {finding['escalated_count']} escalated
Rating breakdown: {group.risk_rating.value_counts().to_dict()}
Systems affected: {finding['systems']}"""

        result = ask(run_id, "Framework Mapper", f"Risk rationale for {finding['finding_id']}",
                     prompt, RiskRationale)
        finding["risk_rationale"] = result.rationale
        findings.append(finding)

    return {"findings": findings}


# ---------------------------------------------------------------------------
# Agent 4: Reporter
# ---------------------------------------------------------------------------
def reporter(state):
    """Draft each finding in Condition / Criteria / Cause / Effect / Recommendation format."""
    run_id = state["run_id"]
    exceptions = pd.DataFrame(state["exceptions"])
    drafted = []

    for finding in state["findings"]:
        control = get_control(finding["control_id"])
        rows = exceptions[exceptions.control_id == finding["control_id"]]
        # Escalated exceptions first, so the most important examples are seen
        rows = rows.sort_values("escalated", ascending=False).head(25)

        prompt = f"""Draft an audit finding for this control failure.

Control: {control['id']} {control['name']}
Control description: {control['control_description'].strip()}
Framework references: {finding['frameworks']}
Risk rating: {finding['risk_rating']}
Risk rationale: {finding['risk_rationale']}
Total exceptions: {finding['exception_count']} ({finding['escalated_count']} escalated)
Recommendation guidance: {control['recommendation_guidance'].strip()}

Exception details (up to 25 shown):
{rows[['record_id', 'employee_id', 'system', 'condition', 'risk_rating']].to_string(index=False)}"""

        result = ask(run_id, "Reporter", f"Draft finding {finding['finding_id']}",
                     prompt, DraftFinding)
        drafted.append({**finding, **result.model_dump(),
                        "status": "Draft - pending auditor review"})

    return {"findings": drafted}