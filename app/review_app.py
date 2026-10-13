"""
Auditor review dashboard for the ITGC Audit Agent.

"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from audit_trail import LOG_FILE  # noqa: E402
from review import (EDITABLE_FIELDS, FINDINGS_FILE, RATINGS, RESULTS_DIR,  # noqa: E402
                    issue_final_report, load_decisions, load_findings,
                    record_decision, review_progress)

RATING_ICON = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
STATUS_ICON = {"Approved": "✅", "Approved with edits": "✏️", "Rejected": "❌"}

st.set_page_config(page_title="ITGC Audit Review", page_icon="🛡️", layout="wide")

if not FINDINGS_FILE.exists():
    st.error("No findings to review yet. Run `python src/run_audit.py` first.")
    st.stop()

findings = load_findings()
decisions = load_decisions()
done, total = review_progress(findings, decisions)

# ---------------------------------------------------------------------------
# Sidebar: reviewer identity, progress, and sign-off
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Reviewer")
    reviewer = st.text_input("Your name", key="reviewer",
                             help="Recorded against every decision in the audit trail.")

    st.header("Progress")
    st.progress(done / total if total else 0, text=f"{done} of {total} findings reviewed")

    st.header("Sign-off")
    if st.button("Issue final report", type="primary", disabled=done < total,
                 width="stretch"):
        try:
            path = issue_final_report(reviewer)
            st.success("Final report issued.")
            st.download_button("Download final report", path.read_text(encoding="utf-8"),
                               file_name=path.name, width="stretch")
        except ValueError as e:
            st.error(str(e))
    if done < total:
        st.caption("Available once every finding has been reviewed.")

# ---------------------------------------------------------------------------
# Header and summary metrics
# ---------------------------------------------------------------------------
st.title("🛡️ ITGC Audit Review")
st.caption("AI agents drafted these findings. Nothing is final until you approve it.")

cols = st.columns(4)
cols[0].metric("Draft findings", total)
cols[1].metric("Critical", sum(f["risk_rating"] == "Critical" for f in findings))
cols[2].metric("Exceptions", sum(f["exception_count"] for f in findings))
cols[3].metric("Reviewed", f"{done}/{total}")

review_tab, trail_tab = st.tabs(["Review findings", "Audit trail"])

# ---------------------------------------------------------------------------
# Review tab: one panel per finding
# ---------------------------------------------------------------------------
with review_tab:
    exceptions = pd.read_csv(RESULTS_DIR / "exceptions.csv")

    for f in findings:
        fid = f["finding_id"]
        decision = decisions.get(fid)
        status = decision["status"] if decision else "Pending review"
        label = (f"{RATING_ICON[f['risk_rating']]} {fid}: {f['title']}  ·  "
                 f"{STATUS_ICON.get(status, '⏳')} {status}")

        with st.expander(label, expanded=decision is None):
            # Context the reviewer needs to judge the draft
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Control:** {f['control_id']} {f['control_name']}")
            c2.markdown(f"**AI rating:** {f['risk_rating']}")
            c3.markdown(f"**Exceptions:** {f['exception_count']} ({f['escalated_count']} escalated)")
            fw = f["frameworks"]
            st.markdown(f"**Frameworks:** SOX ITGC: {fw['sox_itgc']} · COBIT 2019: "
                        f"{fw['cobit_2019']} · NIST CSF 2.0: {fw['nist_csf_2']}")
            st.info(f"**Rating rationale (AI):** {f['risk_rationale']}")

            with st.popover(f"View the {f['exception_count']} underlying exceptions"):
                st.dataframe(exceptions[exceptions.control_id == f["control_id"]],
                             hide_index=True, width="stretch")

            if decision:
                st.success(f"{status} by {decision['reviewer']} on "
                           f"{decision['reviewed_at'][:10]}"
                           + (f": {decision['comment']}" if decision["comment"] else ""))

            # The review form: starts with the reviewer's last version, or the AI draft
            source = decision["final_text"] if decision else f
            with st.form(f"form_{fid}"):
                st.markdown("**Draft finding.** Edit any field directly.")
                edited = {field: st.text_area(field.title(), value=source[field],
                                              key=f"{fid}_{field}",
                                              height=68 if field == "title" else 110)
                          for field in EDITABLE_FIELDS}

                r1, r2 = st.columns([1, 2])
                current_rating = decision["final_rating"] if decision else f["risk_rating"]
                new_rating = r1.selectbox("Risk rating", RATINGS,
                                          index=RATINGS.index(current_rating), key=f"{fid}_rating")
                choice = r2.radio("Decision", ["Approve", "Reject"], horizontal=True,
                                  key=f"{fid}_decision")
                comment = st.text_area("Justification (required to reject, edit, or re-rate)",
                                       key=f"{fid}_comment")

                if st.form_submit_button("Record decision", type="primary"):
                    try:
                        result = record_decision(f, choice, reviewer, comment, edited, new_rating)
                        st.toast(f"{fid}: {result['status']}")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

# ---------------------------------------------------------------------------
# Audit trail tab: every agent action and human decision
# ---------------------------------------------------------------------------
with trail_tab:
    if not LOG_FILE.exists():
        st.info("The audit trail is empty.")
    else:
        trail = pd.DataFrame([json.loads(line) for line in
                              LOG_FILE.read_text(encoding="utf-8").splitlines()])
        trail["type"] = trail.model.apply(lambda m: "AI agent" if isinstance(m, str) else
                                          "Human / system")

        actors = st.multiselect("Filter by actor", sorted(trail.actor.unique()))
        if actors:
            trail = trail[trail.actor.isin(actors)]

        st.dataframe(trail[["timestamp", "run_id", "actor", "action", "model"]].iloc[::-1],
                     hide_index=True, width="stretch")

        st.markdown("**Inspect an event**")
        pick = st.selectbox("Event", trail.index[::-1],
                            format_func=lambda i: f"{trail.at[i, 'timestamp']} · "
                                                  f"{trail.at[i, 'actor']} · {trail.at[i, 'action']}")
        if pick is not None:
            e1, e2 = st.columns(2)
            e1.markdown("Input")
            e1.json(trail.at[pick, "inputs"] or {}, expanded=True)
            e2.markdown("Output")
            e2.json(trail.at[pick, "outputs"] or {}, expanded=True)