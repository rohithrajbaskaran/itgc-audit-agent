"""
Run the ITGC audit: connects the four agents into a LangGraph workflow.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

import agents
from agents import (AuditState, control_tester, evidence_collector,
                    framework_mapper, reporter)
from audit_trail import log_event
from load_controls import load_controls

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def route_after_evidence(state):
    """Auditors do not test evidence they cannot rely on."""
    return "stop" if state["evidence_blocking"] else "continue"


def build_graph():
    graph = StateGraph(AuditState)
    graph.add_node("evidence_collector", evidence_collector)
    graph.add_node("control_tester", control_tester)
    graph.add_node("framework_mapper", framework_mapper)
    graph.add_node("reporter", reporter)

    graph.add_edge(START, "evidence_collector")
    graph.add_conditional_edges("evidence_collector", route_after_evidence,
                                {"continue": "control_tester", "stop": END})
    graph.add_edge("control_tester", "framework_mapper")
    graph.add_edge("framework_mapper", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile()


def write_report(state, path):
    """Write the draft audit report as a Markdown file."""
    audit = load_controls()["audit"]
    lines = [
        f"# {audit['name']}: Draft Report",
        "",
        f"**Status:** DRAFT, pending auditor review  ",
        f"**Run ID:** {state['run_id']}  ",
        f"**Audit period:** {audit['period_start']} to {audit['period_end']}  ",
        f"**Systems in scope:** {', '.join(audit['in_scope_systems'])}",
        "",
        "## Evidence Assessment",
        "",
        state["evidence_assessment"],
        "",
        "## Testing Summary",
        "",
        "| Control | Population | Exceptions | Escalated | Result |",
        "|---|---|---|---|---|",
    ]
    for s in state["test_summary"]:
        lines.append(f"| {s['control_id']} {s['control']} | {s['population']:,} | "
                     f"{s['exceptions']} | {s['escalated']} | {s['result']} |")

    review = state["anomaly_review"]
    lines += ["", "## Key Observations", "", review["patterns"], "",
              f"**Priority items:** {review['priority_items']}", "", "## Findings"]

    for f in state["findings"]:
        fw = f["frameworks"]
        lines += [
            "",
            f"### {f['finding_id']}: {f['title']}",
            "",
            f"**Risk rating:** {f['risk_rating']}  ",
            f"**Exceptions:** {f['exception_count']} ({f['escalated_count']} escalated)  ",
            f"**Frameworks:** SOX ITGC: {fw['sox_itgc']}; COBIT 2019: {fw['cobit_2019']}; "
            f"NIST CSF 2.0: {fw['nist_csf_2']}",
            "",
            "| | |",
            "|---|---|",
            f"| **Condition** | {f['condition']} |",
            f"| **Criteria** | {f['criteria']} |",
            f"| **Cause** | {f['cause']} |",
            f"| **Effect** | {f['effect']} |",
            f"| **Recommendation** | {f['recommendation']} |",
            "",
            f"*Rating rationale: {f['risk_rationale']}*",
        ]

    lines += ["", "---", "*Drafted by the ITGC Audit Agent. All findings require "
              "auditor review and approval before issue. Full exception listing: "
              "results/exceptions.csv. Agent activity: logs/audit_trail.jsonl.*"]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Run the ITGC audit agents")
    parser.add_argument("--offline", action="store_true",
                        help="Run without calling the AI model (no API cost)")
    args = parser.parse_args()

    load_dotenv()
    agents.OFFLINE = args.offline
    run_id = datetime.now().strftime("RUN-%Y%m%d-%H%M%S")
    mode = "offline" if args.offline else f"model {agents.MODEL}"
    print(f"Starting audit {run_id} ({mode})\n")
    log_event(run_id, "System", "Audit run started", inputs={"mode": mode})

    state = {"run_id": run_id}
    for step in build_graph().stream(state):
        for agent_name, update in step.items():
            state.update(update)
            print(f"  [done] {agent_name.replace('_', ' ').title()}")

    RESULTS_DIR.mkdir(exist_ok=True)
    if state["evidence_blocking"]:
        print("\nAudit stopped: the evidence failed completeness checks.")
        for issue in state["evidence_issues"]:
            print(f"  - {issue}")
        log_event(run_id, "System", "Audit run stopped", outputs=state["evidence_issues"])
        return

    with open(RESULTS_DIR / "findings.json", "w", encoding="utf-8") as f:
        json.dump(state["findings"], f, indent=2, default=str)
    write_report(state, RESULTS_DIR / "draft_audit_report.md")
    log_event(run_id, "System", "Audit run completed",
              outputs={"findings": len(state["findings"])})

    print(f"\n{len(state['findings'])} draft findings:")
    for f in state["findings"]:
        print(f"  {f['finding_id']:<9} {f['risk_rating']:<9} {f['exception_count']:>3} exceptions")
    print("\nSaved: results/findings.json, results/draft_audit_report.md")
    print("Audit trail: logs/audit_trail.jsonl")


if __name__ == "__main__":
    main()