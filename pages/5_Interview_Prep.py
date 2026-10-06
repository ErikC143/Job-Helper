import hashlib

import streamlit as st
from dotenv import load_dotenv

from jobsearch import comparison, interview, jobs, reports, resume
from jobsearch.ui import INTERVIEW_SECTIONS, posting_section, resume_section, show_job_errors, watch_jobs

load_dotenv()
st.set_page_config(page_title="Interview Prep", page_icon="🎤", layout="wide")
st.title("🎤 Interview Prep")
st.caption(
    "Choose a saved job posting or add a new one. Its resume comparison is used to build an "
    "interview plan tailored to you and the role."
)

QUESTION_TYPES = {
    "behavioral": "Behavioral",
    "technical": "Technical",
    "situational": "Situational",
    "role_specific": "Role-specific",
    "resume": "About your resume",
    "culture_fit": "Culture fit",
}
DIFFICULTY_COLOR = {"easy": "green", "medium": "orange", "hard": "red"}


# ---------- Rendering ----------

def item_id(*parts: str) -> str:
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()[:12]


def toggle(saved: interview.SavedInterview, iid: str) -> None:
    interview.set_done(saved, iid, st.session_state[f"done_{saved.id}_{iid}"])


def checkbox(saved: interview.SavedInterview, iid: str, label: str) -> None:
    # Keys include the plan id so each saved plan keeps its own checkboxes.
    key = f"done_{saved.id}_{iid}"
    st.session_state.setdefault(key, iid in saved.done)
    st.checkbox(label, key=key, on_change=toggle, args=(saved, iid))


def prep_tab(saved: interview.SavedInterview) -> None:
    st.caption("Work through these before the interview. Your progress is saved.")
    for task in saved.plan.prep:
        with st.container(border=True):
            checkbox(saved, item_id("prep", task.task), f"**{task.task}**")
            st.markdown(f":gray-badge[⏱ {task.time_estimate}]")
            st.caption(task.details)


def recommendations_tab(saved: interview.SavedInterview) -> None:
    for rec in saved.plan.recommendations:
        with st.container(border=True):
            st.markdown(f"**{rec.title}**")
            st.write(rec.details)


def tips_tab(saved: interview.SavedInterview) -> None:
    for tip in saved.plan.tips:
        with st.container(border=True):
            st.markdown(f"💡 **{tip.tip}**")
            st.caption(tip.why)


def practice_tab(saved: interview.SavedInterview) -> None:
    st.caption("Rehearse these out loud or hands-on. Check them off as you go.")
    for ex in saved.plan.practice:
        with st.container(border=True):
            checkbox(saved, item_id("practice", ex.exercise), f"**{ex.exercise}**")
            st.markdown(f":blue-badge[{ex.related_requirement}]")
            st.write(ex.how_to)


def questions_tab(saved: interview.SavedInterview) -> None:
    questions = saved.plan.questions
    present = [t for t in QUESTION_TYPES if any(q.type == t for q in questions)]
    shown = st.pills(
        "Question types",
        present,
        selection_mode="multi",
        default=present,
        format_func=QUESTION_TYPES.get,
        key=f"qtypes_{saved.id}",
    )
    for i, q in enumerate((q for q in questions if q.type in shown), 1):
        with st.expander(f"**Q{i}.** {q.question}"):
            st.markdown(
                f":violet-badge[{QUESTION_TYPES[q.type]}] "
                f":{DIFFICULTY_COLOR[q.difficulty]}-badge[{q.difficulty.title()}]"
            )
            st.markdown("**Why they ask it**")
            st.write(q.why_they_ask)
            st.markdown("**How to answer**")
            st.write(q.answer_outline)
            st.markdown("**Draw on**")
            if q.resume_evidence:
                st.markdown("\n".join(f"- {e}" for e in q.resume_evidence))
            else:
                st.caption("Nothing on your resume covers this directly. Answer honestly using the outline above.")


def render_interview(saved: interview.SavedInterview) -> None:
    plan = saved.plan
    st.header(f"{saved.job_title} at {saved.company}")
    st.caption(f"Interview plan saved {saved.created_at[:16].replace('T', ' ')}")
    with st.container(border=True):
        st.write(plan.summary)

    prep_ids = [item_id("prep", t.task) for t in plan.prep]
    practice_ids = [item_id("practice", p.exercise) for p in plan.practice]
    c1, c2, c3 = st.columns(3)
    c1.metric("Prep done", f"{sum(i in saved.done for i in prep_ids)}/{len(prep_ids)}")
    c2.metric("Practice done", f"{sum(i in saved.done for i in practice_ids)}/{len(practice_ids)}")
    c3.metric("Possible questions", len(plan.questions))

    tabs = st.tabs([label for _, label in INTERVIEW_SECTIONS])
    for tab, show in zip(tabs, [prep_tab, recommendations_tab, tips_tab, practice_tab, questions_tab]):
        with tab:
            show(saved)


# ---------- Page ----------

left, right = st.columns(2)

with left:
    st.subheader("Resume")
    resume_text = resume_section("resume")

with right:
    st.subheader("Job posting")
    posting_text, posting_name = posting_section(resume_text)

ready = bool(resume_text and posting_text)
key = reports.inputs_hash(resume_text, posting_text) if ready else None
job_key = f"interview:{key}"

# Plans that finished in the background are already saved.
for job in jobs.with_prefix("interview:"):
    if job.status == "done":
        if job.key == job_key:
            st.session_state["interview_pick"] = job.output.id
        jobs.clear(job.key)

if not ready:
    st.info("Add a resume and a job posting (PDF, text, or both) to continue.")
else:
    report = next((r for r in reports.list_reports() if r.inputs_hash == key), None)
    if report:
        st.caption(f"Uses your comparison report from {report.label.split(' · ')[0]}.")
    else:
        st.caption("This posting hasn't been compared to your resume yet. That runs first and is saved to your reports.")

    running = (job := jobs.get(job_key)) is not None and job.status == "running"
    if st.button("Building..." if running else "Build interview plan", type="primary", disabled=running):

        def build(job: jobs.Job, report=report, resume_text=resume_text, posting_text=posting_text, key=key):
            resume_hash = resume.text_hash(resume_text)
            if report is None:
                job.start_stage("compare")
                result = comparison.compare(resume_text, posting_text, on_event=job.on_event)
                report = reports.save(result, key, resume_hash)
            job.start_stage("interview")
            plan = interview.generate(resume_text, posting_text, report.result, on_event=job.on_event)
            return interview.save(plan, report, resume_hash)

        jobs.submit_task(job_key, posting_name, posting_text, build)
        st.rerun()

show_job_errors("interview:")
watch_jobs("interview:")

# Sidebar: every saved interview plan, newest first.
with st.sidebar:
    st.header("Saved interview plans")
    saved_plans = interview.list_interviews()
    if not saved_plans:
        st.caption("No interview plans yet. Build one to see it here.")
        st.stop()
    by_id = {s.id: s for s in saved_plans}
    if st.session_state.get("interview_pick") not in by_id:
        st.session_state["interview_pick"] = saved_plans[0].id
    picked = by_id[st.radio(
        "Open a plan", list(by_id), key="interview_pick", format_func=lambda i: by_id[i].label,
    )]
    st.caption(f"Stored as inputs/interviews/{picked.id}.json")
    st.download_button(
        "Download JSON", interview.to_json(picked), file_name=f"{picked.id}.json", mime="application/json"
    )
    if st.button("Delete this plan"):
        interview.delete(picked.id)
        del st.session_state["interview_pick"]
        st.rerun()

st.divider()
render_interview(picked)
