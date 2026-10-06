
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from jobsearch import h1b, jobs, reports, resume
from jobsearch.comparison import (
    ComparisonResult,
    RequirementExplanation,
    RequirementMatch,
    WorkAuthorization,
    average_match,
    check_work_authorization,
    explain_requirements,
)
from jobsearch.ui import call_claude, posting_section, resume_section, show_job_errors, watch_jobs

load_dotenv()

CATEGORIES = [
    ("minimum", "Minimum qualifications"),
    ("preferred", "Preferred qualifications"),
    ("other", "Other requirements"),
]

STANCE_LABELS = {
    "accepts": ("Posting allows your status", "green"),
    "rejects": ("Posting rules out your status", "red"),
    "not_stated": ("Posting doesn't say", "gray"),
}

st.set_page_config(page_title="Resume Match", page_icon="📄", layout="wide")
st.title("📄 Resume Match")
st.caption("Upload your resume and a job posting to see how well they match.")


# ---------- Report rendering ----------

def bullets(items: list[str], empty: str) -> None:
    if items:
        st.markdown("\n".join(f"- {item}" for item in items))
    else:
        st.caption(empty)


def requirement_detail(req: RequirementMatch, meaning: RequirementExplanation | None) -> None:
    with st.container(border=True):
        st.markdown(f"**{req.requirement}**")
        st.progress(req.match_percent / 100, text=f"{req.match_percent}% match")
        overview, what, talking, strengths, weaknesses = st.tabs(
            ["Overview", "What it means", "Talking points", "Strengths", "Weaknesses"]
        )
        with overview:
            st.write(req.explanation)
            st.markdown(f"**Resume evidence:** {req.context}")
        with what:
            if meaning:
                st.markdown(f"**What it's asking for:** {meaning.plain_english}")
                st.markdown(f"**In this role:** {meaning.in_this_role}")
                if meaning.key_terms:
                    st.markdown("**Key terms**")
                    st.markdown("\n".join(f"- **{t.term}**: {t.definition}" for t in meaning.key_terms))
            else:
                st.caption("Not explained yet. Click **Explain requirements** above the results.")
        with talking:
            bullets(req.talking_points, "No talking points.")
        with strengths:
            bullets(req.strengths, "No strengths noted.")
        with weaknesses:
            bullets(req.weaknesses, "No weaknesses noted.")


def category_section(
    result: ComparisonResult,
    category: str,
    label: str,
    report_id: str,
    meanings: dict[int, RequirementExplanation],
) -> None:
    indexes = [i for i, r in enumerate(result.requirements) if r.category == category]
    reqs = [result.requirements[i] for i in indexes]
    if not reqs:
        return
    avg = average_match(result, category)

    with st.expander(f"{label} · {avg:.0f}% average · {len(reqs)} item{'s' if len(reqs) != 1 else ''}", expanded=category == "minimum"):
        st.progress(avg / 100, text=f"Total match: {avg:.0f}%")
        st.caption("Click a row to see its breakdown.")

        table = pd.DataFrame({
            "Requirement": [r.requirement for r in reqs],
            "Match": [r.match_percent for r in reqs],
        })
        event = st.dataframe(
            table,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            selection_default={"selection": {"rows": [0]}},
            key=f"table_{report_id}_{category}",
            column_config={
                "Requirement": st.column_config.TextColumn(width="large"),
                "Match": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%d%%", width="medium"
                ),
            },
        )
        rows = event.selection.rows
        if rows:
            requirement_detail(reqs[rows[0]], meanings.get(indexes[rows[0]]))


def explain_section(report: reports.Report, posting_text: str | None) -> None:
    """Button that explains every requirement in the report and saves the explanations with it."""
    c1, c2 = st.columns([4, 1], vertical_alignment="center")
    if report.explanations:
        c1.caption("Each requirement's **What it means** tab explains what it's asking for and defines its key terms.")
    else:
        c1.caption(
            "Explain what each requirement is asking for, define its technical terms, "
            "and how it likely applies to this job. Shown in each requirement's **What it means** tab."
        )
    if c2.button("Explain again" if report.explanations else "Explain requirements", key=f"explain_{report.id}"):
        with st.spinner("Explaining each requirement... this can take a minute."):
            explanations = call_claude(explain_requirements, report.result, posting_text)
        reports.save_explanations(report, explanations)
        st.rerun()


def confidence_label(confidence: int) -> tuple[str, str]:
    if confidence >= 70:
        return "Likely accepted", "green"
    if confidence >= 40:
        return "Uncertain", "orange"
    return "Unlikely to be accepted", "red"


def h1b_table(auth: WorkAuthorization) -> None:
    matched = set(auth.assessment.matched_employers if auth.assessment else [])
    rows = sorted(auth.h1b_matches, key=lambda m: m.employer not in matched)  # matches first
    st.dataframe(
        pd.DataFrame({
            "Employer": [m.employer for m in rows],
            "Same company": [m.employer in matched for m in rows],
            "Locations": [", ".join(m.locations[:3]) + (" …" if len(m.locations) > 3 else "") for m in rows],
            "Years": [", ".join(map(str, sorted(m.years))) for m in rows],
            "Initial approvals": [m.initial_approvals for m in rows],
            "Continuing approvals": [m.continuing_approvals for m in rows],
            "Denials": [m.initial_denials + m.continuing_denials for m in rows],
        }),
        hide_index=True,
        column_config={"Employer": st.column_config.TextColumn(width="large")},
    )
    st.caption(
        "**Initial** approvals are new H-1B jobs, such as a student moving from OPT to H-1B. "
        "**Continuing** approvals are extensions and transfers. **Same company** is Claude's judgment, "
        "since names are matched loosely."
    )


def render_work_authorization(auth: WorkAuthorization) -> None:
    posting = auth.posting
    assumed = "" if posting.status_stated else " _(assumed: pick your status under **Work authorization** below your resume if this is wrong)_"
    st.markdown(f"**Your status:** {posting.status}{assumed}")
    verdict, color = confidence_label(auth.confidence)
    st.progress(auth.confidence / 100, text=f"{auth.confidence}% confidence this job accepts your status")
    st.markdown(f":{color}-badge[{verdict}]")
    st.write(auth.summary)

    st.markdown("##### 1. What the posting says")
    stance, color = STANCE_LABELS[posting.posting_stance]
    st.markdown(f":{color}-badge[{stance}] :gray-badge[Posting-only confidence {posting.confidence}%]")
    if posting.posting_evidence:
        st.markdown("\n\n".join(f"> {quote}" for quote in posting.posting_evidence))
    else:
        st.caption("The posting doesn't mention citizenship, visas, sponsorship, or export control.")

    st.markdown(f"##### 2. Government data · {h1b.SOURCE}")
    if auth.database_error:
        st.warning(auth.database_error)
    elif not auth.database_searched:
        reason = "the posting explicitly allows it" if posting.needs_sponsorship else "it doesn't need visa sponsorship"
        st.caption(f"Not searched, since {reason}.")
    else:
        st.caption("Searched for: " + ", ".join(posting.employer_names))
        if auth.h1b_matches:
            h1b_table(auth)
        else:
            st.info("No H-1B petitions found under these names.")

    if auth.considerations:
        st.markdown("##### Things to know")
        bullets(auth.considerations, "")
    st.caption("Not legal advice. Confirm with the recruiter and, if you're an F-1 student, your school's DSO.")


def work_authorization_section(report: reports.Report, inputs: tuple[str, str] | None) -> None:
    auth = report.work_authorization
    title = "🛂 Work authorization" + (f" · {auth.confidence}% confidence" if auth else "")
    with st.expander(title, expanded=bool(auth and auth.posting.status_stated)):
        if auth:
            render_work_authorization(auth)
        else:
            st.caption("This report doesn't have a work authorization check yet.")
        if inputs:
            if st.button("Check again" if auth else "Check work authorization", key=f"auth_{report.id}"):
                with st.spinner("Checking the posting and USCIS H-1B data..."):
                    auth = call_claude(check_work_authorization, *inputs)
                reports.save_work_authorization(report, auth)
                st.rerun()
        elif not auth:
            st.caption("Load this report's resume and posting above, or run Compare again, to check it.")


def render_report(report: reports.Report, inputs: tuple[str, str] | None = None) -> None:
    """inputs is the (resume, posting) text the report was made from, if it's loaded on the page."""
    result = report.result
    st.header(f"{result.job_title} at {result.company}")
    st.caption(f"Report saved {report.label.split(' · ')[0]}")

    cols = st.columns(len(CATEGORIES))
    for col, (category, label) in zip(cols, CATEGORIES):
        avg = average_match(result, category)
        col.metric(label, f"{avg:.0f}%" if avg is not None else "—")

    with st.expander("Summary", expanded=True):
        st.write(result.overall_summary)
    with st.expander("Inferred skills"):
        st.write(", ".join(result.inferred_skills))
    work_authorization_section(report, inputs)

    explain_section(report, inputs and inputs[1])
    meanings = {e.number - 1: e for e in report.explanations}
    for category, label in CATEGORIES:
        category_section(result, category, label, report.id, meanings)


# ---------- Page ----------

left, right = st.columns(2)

with left:
    st.subheader("Resume")
    resume_text = resume_section("resume")

with right:
    st.subheader("Job posting")
    posting_text, posting_name = posting_section(resume_text)

with st.expander("Preview extracted text"):
    p1, p2 = st.columns(2)
    p1.text_area("Resume text", resume_text, height=300, disabled=True)
    p2.text_area("Posting text", posting_text, height=300, disabled=True)

ready = bool(resume_text and posting_text)
key = reports.inputs_hash(resume_text, posting_text) if ready else None
job_key = f"report:{key}"

# Comparisons that finished in the background: their reports are already saved.
for job in jobs.with_prefix("report:"):
    if job.status == "done":
        if job.key == job_key:
            st.session_state["report_pick"] = job.output.id
        jobs.clear(job.key)

all_reports = reports.list_reports()
matching = next((r for r in all_reports if r.inputs_hash == key), None)

if not ready:
    st.info("Add a resume and a job posting (PDF, text, or both) to continue.")
else:
    # Saved reports are reused, so the API is only called on request.
    running = (job := jobs.get(job_key)) is not None and job.status == "running"
    if st.button(
        "Comparing..." if running else "Compare again" if matching else "Compare",
        type="primary",
        disabled=running,
    ):
        jobs.submit(
            job_key,
            posting_name,
            resume_text,
            posting_text,
            on_success=lambda result, auth, key=key, rh=resume.text_hash(resume_text): reports.save(
                result, key, rh, auth
            ),
        )
        st.rerun()

show_job_errors("report:")
watch_jobs("report:")

# Sidebar: every saved report, defaulting to the newest one for the current inputs.
with st.sidebar:
    st.header("Saved reports")
    if not all_reports:
        st.caption("No reports yet. Run a comparison to create one.")
        picked = None
    else:
        ids = [r.id for r in all_reports]
        if st.session_state.get("report_pick") not in ids:
            st.session_state["report_pick"] = matching.id if matching else ids[0]
        by_id = {r.id: r for r in all_reports}
        picked = by_id[st.radio(
            "Open a report", ids, key="report_pick", format_func=lambda i: by_id[i].label,
        )]
        st.caption(f"Stored as inputs/reports/{picked.id}.json")
        if st.button("Delete this report"):
            reports.delete(picked.id)
            del st.session_state["report_pick"]
            st.rerun()

if picked:
    st.divider()
    if ready and picked.inputs_hash != key:
        st.caption("This report was made from different inputs than the ones loaded above.")
    # The inputs are only known if they're the ones loaded above.
    render_report(picked, (resume_text, posting_text) if ready and picked.inputs_hash == key else None)
