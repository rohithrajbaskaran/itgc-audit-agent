"""
Human-in-the-loop review logic.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from audit_trail import LOG_FILE, log_event
from load_controls import load_controls

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
FINDINGS_FILE = RESULTS_DIR / "findings.json"
DECISIONS_FILE = RESULTS_DIR / "review_decisions.json"
FINAL_REPORT = RESULTS_DIR / "final_audit_report.md"

EDITABLE_FIELDS = ["title", "condition", "criteria", "cause", "effect", "recommendation"]
RATINGS = ["Low", "Medium", "High", "Critical"]


def load_findings():
    with open(FINDINGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_decisions():
    if not DECISIONS_FILE.exists():
        return {}
    with open(DECISIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def latest_run_id():
    """The run that produced the current findings, read from the audit trail."""
    run_id = None
    if LOG_FILE.exists():
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry["action"] == "Audit run completed":
                run_id = entry["run_id"]
    return run_id or "UNKNOWN"


def record_decision(finding, decision, reviewer, comment, edited_fields, new_rating):
    """
    Validate and save one review decision.

    decision: "Approve" or "Reject"
    edited_fields: the reviewer's version of each editable field
    Returns the saved decision. Raises ValueError if a governance rule is broken.
    """
    reviewer = reviewer.strip()
    comment = comment.strip()
    if not reviewer:
        raise ValueError("Enter your name before recording a decision.")

    changes = {f: {"ai_draft": finding[f], "reviewer": edited_fields[f]}
               for f in EDITABLE_FIELDS if edited_fields[f].strip() != finding[f].strip()}
    rating_changed = new_rating != finding["risk_rating"]

    if decision == "Reject":
        status = "Rejected"
    elif changes or rating_changed:
        status = "Approved with edits"
    else:
        status = "Approved"

    if status != "Approved" and not comment:
        raise ValueError("A justification is required to reject, edit, or re-rate a finding.")

    saved = {
        "finding_id": finding["finding_id"],
        "status": status,
        "reviewer": reviewer,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "comment": comment,
        "original_rating": finding["risk_rating"],
        "final_rating": new_rating,
        "final_text": {f: edited_fields[f].strip() for f in EDITABLE_FIELDS},
        "fields_edited": list(changes),
    }

    decisions = load_decisions()
    decisions[finding["finding_id"]] = saved
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(DECISIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(decisions, f, indent=2)

    log_event(latest_run_id(), reviewer, f"Review decision: {status} {finding['finding_id']}",
              inputs={"ai_rating": finding["risk_rating"]},
              outputs={"status": status, "final_rating": new_rating, "comment": comment,
                       "changes": changes})
    return saved


def review_progress(findings, decisions):
    reviewed = [f for f in findings if f["finding_id"] in decisions]
    return len(reviewed), len(findings)


def issue_final_report(reviewer):
    """Build the final report from approved findings. Only allowed once all are reviewed."""
    findings, decisions = load_findings(), load_decisions()
    done, total = review_progress(findings, decisions)
    if done < total:
        raise ValueError(f"{total - done} findings still need review before the report can be issued.")
    if not reviewer.strip():
        raise ValueError("Enter your name to sign off the report.")

    audit = load_controls()["audit"]
    approved = [f for f in findings if decisions[f["finding_id"]]["status"] != "Rejected"]
    rejected = [f for f in findings if decisions[f["finding_id"]]["status"] == "Rejected"]
    issued = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {audit['name']}: Final Report",
        "",
        f"**Audit period:** {audit['period_start']} to {audit['period_end']}  ",
        f"**Systems in scope:** {', '.join(audit['in_scope_systems'])}  ",
        f"**Issued:** {issued}  ",
        f"**Signed off by:** {reviewer.strip()}  ",
        f"**Source run:** {latest_run_id()}",
        "",
        "## Summary of Findings",
        "",
        "| Finding | Title | Rating | Exceptions | Review |",
        "|---|---|---|---|---|",
    ]
    for f in approved:
        d = decisions[f["finding_id"]]
        lines.append(f"| {f['finding_id']} | {d['final_text']['title']} | {d['final_rating']} | "
                     f"{f['exception_count']} | {d['status']} by {d['reviewer']} |")

    for f in approved:
        d, fw, text = decisions[f["finding_id"]], f["frameworks"], decisions[f["finding_id"]]["final_text"]
        lines += [
            "", f"## {f['finding_id']}: {text['title']}", "",
            f"**Risk rating:** {d['final_rating']}  ",
            f"**Frameworks:** SOX ITGC: {fw['sox_itgc']}; COBIT 2019: {fw['cobit_2019']}; "
            f"NIST CSF 2.0: {fw['nist_csf_2']}",
            "", "| | |", "|---|---|",
            f"| **Condition** | {text['condition']} |",
            f"| **Criteria** | {text['criteria']} |",
            f"| **Cause** | {text['cause']} |",
            f"| **Effect** | {text['effect']} |",
            f"| **Recommendation** | {text['recommendation']} |",
            "",
            f"*Drafted by AI agents; {d['status'].lower()} by {d['reviewer']} on {d['reviewed_at'][:10]}.*",
        ]
        if d["original_rating"] != d["final_rating"]:
            lines.append(f"*Rating changed from {d['original_rating']} to {d['final_rating']}: {d['comment']}*")

    if rejected:
        lines += ["", "## Appendix: Draft Findings Not Issued", "",
                  "| Finding | Rejected by | Reason |", "|---|---|---|"]
        for f in rejected:
            d = decisions[f["finding_id"]]
            lines.append(f"| {f['finding_id']} | {d['reviewer']} | {d['comment']} |")

    lines += ["", "---", "*Every AI-drafted finding in this report was reviewed and approved by "
              "a human auditor. The full decision history is recorded in logs/audit_trail.jsonl.*"]
    FINAL_REPORT.write_text("\n".join(lines), encoding="utf-8")

    log_event(latest_run_id(), reviewer.strip(), "Final report issued",
              outputs={"approved": [f["finding_id"] for f in approved],
                       "rejected": [f["finding_id"] for f in rejected]})
    return FINAL_REPORT