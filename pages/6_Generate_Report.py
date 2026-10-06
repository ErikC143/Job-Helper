import re
from datetime import date

import streamlit as st
from dotenv import load_dotenv

from jobsearch import db, interview, pdf_report, plans, reports, resume

load_dotenv()
st.set_page_config(page_title="Generate Report", page_icon="🖨️", layout="wide")
st.title("🖨️ Generate Report")
st.caption(
    "Bundle what you've already generated into a printable PDF for interview prep. "
    "Nothing here calls Claude, so it's instant and free."
)

LEVELS = {
    "sparknotes": "⚡ Sparknotes",
    "simple": "📋 Simple",
    "complete": "📚 Complete",
}
LEVEL_HELP = {
    "sparknotes": "The essentials: match scores, top strengths and gaps, top priorities, and the hardest questions. "
                  "A page or two per posting, for a quick refresher right before the interview.",
    "simple": "The main points of every section: a requirements table with talking points, the full to-do lists, "
              "and every question with how to answer it.",
    "complete": "Everything: per-requirement explanations, evidence, strengths and weaknesses, plain-English "
                "definitions, practice exercises, H-1B data, and the original posting text.",
}
CONTENT = {
    "match": "📄 Job posting match",
    "plans": "📈 Improvement plans",
    "interviews": "🎤 Interview notes",
}


def posting_texts(selected: list[reports.Report]) -> dict[str, str]:
    """The original text of each selected posting, when it's saved and the resume hasn't changed since."""
    resume_text = resume.load()
    by_hash = {reports.inputs_hash(resume_text, body): body for body in db.list_postings()["body"]}
    return {r.id: by_hash[r.inputs_hash] for r in selected if r.inputs_hash in by_hash}


def file_name(selected: list[reports.Report], level: str) -> str:
    who = selected[0].company if len(selected) == 1 else f"{len(selected)}-postings"
    slug = re.sub(r"[^a-z0-9]+", "-", who.lower()).strip("-")[:40] or "postings"
    return f"prep-report_{slug}_{level}_{date.today()}.pdf"


# ---------- 1. Postings ----------

st.header("1. Job postings")
all_reports = reports.list_reports()
if not all_reports:
    st.info("No job postings compared yet. Run a comparison on the **Resume Match** page first.")
    st.stop()

by_id = {r.id: r for r in all_reports}
picked_ids = st.multiselect(
    "Which postings should the report cover?",
    list(by_id),
    default=[all_reports[0].id],
    format_func=lambda i: f"{by_id[i].job_title} at {by_id[i].company} · compared {by_id[i].created_at[:10]}",
)
if not picked_ids:
    st.info("Choose at least one posting.")
    st.stop()
selected = [by_id[i] for i in picked_ids]

# What's available for these postings.
matching_plans = [p for p in plans.list_plans() if {x.report_id for x in p.postings} & set(picked_ids)]
matching_interviews = [s for s in interview.list_interviews() if s.report_id in picked_ids]
available = {"match": len(selected), "plans": len(matching_plans), "interviews": len(matching_interviews)}

# ---------- 2. Contents ----------

st.header("2. Include")
include = st.pills(
    "Click to add or remove sections",
    list(CONTENT),
    selection_mode="multi",
    default=[k for k, n in available.items() if n],
    format_func=lambda k: f"{CONTENT[k]} ({available[k]})",
    key="include",
)
for k in include:
    if not available[k]:
        st.caption(f"No {CONTENT[k][2:].lower()} saved for these postings yet, so this section will be left out.")

chosen_plans, chosen_interviews = [], []
if "plans" in include and matching_plans:
    plan_ids = {p.id: p for p in matching_plans}
    if len(matching_plans) == 1:
        chosen_plans = matching_plans
    else:
        chosen = st.multiselect(
            "Improvement plans to include",
            list(plan_ids),
            default=[matching_plans[0].id],  # the newest; older plans usually overlap with it
            format_func=lambda i: f"{plan_ids[i].label} · {', '.join(x.label for x in plan_ids[i].postings)}",
        )
        chosen_plans = [plan_ids[i] for i in chosen]

if "interviews" in include and matching_interviews:
    newest = {}
    for s in matching_interviews:  # newest first, so the first per posting wins
        newest.setdefault(s.report_id, s.id)
    note_ids = {s.id: s for s in matching_interviews}
    if len(matching_interviews) == len(newest):
        chosen_interviews = matching_interviews
    else:
        chosen = st.multiselect(
            "Interview notes to include",
            list(note_ids),
            default=list(newest.values()),
            format_func=lambda i: note_ids[i].label,
        )
        chosen_interviews = [note_ids[i] for i in chosen]

# ---------- 3. Detail ----------

st.header("3. Detail level")
level = st.segmented_control(
    "How much detail?", list(LEVELS), default="simple", required=True,
    format_func=LEVELS.get, key="level", label_visibility="collapsed",
)
st.caption(LEVEL_HELP[level])

easiest_first = False
if chosen_plans:
    easiest_first = st.radio(
        "Rank improvement plan skills by", ["Most impactful", "Easiest to complete"], horizontal=True,
    ) == "Easiest to complete"

# ---------- Generate ----------

include_match = "match" in include
if not (include_match or chosen_plans or chosen_interviews):
    st.info("Pick at least one section that has saved content to include.")
    st.stop()

st.divider()
pdf = pdf_report.build_pdf(
    selected,
    level,
    include_match=include_match,
    interviews=chosen_interviews,
    plans=chosen_plans,
    easiest_first=easiest_first,
    posting_texts=posting_texts(selected) if level == "complete" else None,
)
parts = [f"{len(selected)} posting{'s' if len(selected) != 1 else ''}"]
if chosen_interviews:
    parts.append(f"{len(chosen_interviews)} interview note{'s' if len(chosen_interviews) != 1 else ''}")
if chosen_plans:
    parts.append(f"{len(chosen_plans)} improvement plan{'s' if len(chosen_plans) != 1 else ''}")
st.markdown(f"**Ready:** {LEVELS[level]} report covering {', '.join(parts)} · {len(pdf) // 1024 + 1} KB")
st.download_button(
    "⬇️ Download PDF", pdf, file_name=file_name(selected, level), mime="application/pdf", type="primary",
)
