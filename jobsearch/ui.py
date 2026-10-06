"""Streamlit helpers shared by every page."""

import re
import time
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic_core import from_json
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from . import db, jobs, reports, resume
from .comparison import error_message


def call_claude(fn, *args):
    """Run a Claude call, showing a readable error and stopping the page on failure."""
    try:
        return fn(*args)
    except Exception as e:
        if (message := error_message(e)) is None:
            raise
        st.error(message)
    st.stop()


# ---------- Resume ----------

def resume_section(key: str) -> str:
    """Show the saved resume with a way to replace it. Returns its text, or '' if none is saved."""
    # Bumping this counter gives the uploader a fresh key, which clears it after a save.
    upload_key = f"{key}_upload_{st.session_state.get(f'{key}_n', 0)}"
    text = resume.load()

    if text:
        st.success(f"Using saved resume · `inputs/resume.txt` · updated {resume.updated_at()}")
        with st.expander("Preview resume text"):
            st.text(text)
        work_authorization_picker(key, text)
        notes_editor(key, text)
        with st.expander("Replace resume"):
            uploaded = st.file_uploader("Upload a new resume (PDF or TXT)", type=["pdf", "txt"], key=upload_key)
    else:
        uploaded = st.file_uploader("Upload your resume (PDF or TXT)", type=["pdf", "txt"], key=upload_key)
        st.caption("It's saved as `inputs/resume.txt` and used on every page until you replace it.")

    if uploaded:
        try:
            resume.save_upload(uploaded.name, uploaded.getvalue())
        except ValueError as e:
            st.error(str(e))
            return text
        st.session_state[f"{key}_n"] = st.session_state.get(f"{key}_n", 0) + 1
        st.rerun()
    return text


def work_authorization_picker(key: str, text: str) -> None:
    """Pick the work authorization saved with the candidate notes. Saves as soon as it changes."""
    _, saved, _ = resume.split_notes(text)
    # Keep a hand-edited value from the file selectable.
    options = resume.WORK_AUTHORIZATIONS + ([saved] if saved and saved not in resume.WORK_AUTHORIZATIONS else [])
    widget_key = f"{key}_work_auth"
    st.selectbox(
        "Work authorization",
        options,
        index=options.index(saved) if saved else None,
        placeholder="Not set (assumed U.S. citizen)",
        key=widget_key,
        on_change=lambda: resume.save_notes(work_authorization=st.session_state[widget_key] or ""),
        help="Saved with your resume, so every comparison and work authorization check uses it. "
        "Add details like your graduation date or OPT start under Candidate notes.",
    )


def notes_editor(key: str, text: str) -> None:
    """Edit the candidate notes saved at the end of the resume file."""
    _, _, notes = resume.split_notes(text)
    label = "Candidate notes" + (" · added" if notes else "")
    with st.expander(label, expanded=False):
        with st.form(f"{key}_notes", border=False):
            new_notes = st.text_area(
                "Extra details for comparisons",
                value=notes,
                height=100,
                placeholder="e.g. Graduating June 2027 with a STEM degree, so I'm eligible for STEM OPT.",
                help="Saved at the end of your resume text, so every comparison sees them.",
            )
            if st.form_submit_button("Save notes"):
                resume.save_notes(notes=new_notes)
                st.rerun()


def resume_input(key: str) -> tuple[str, str]:
    """Like resume_section, but stops the page if there's no resume. Returns (text, hash)."""
    text = resume_section(key)
    if not text:
        st.info("Upload your resume to continue.")
        st.stop()
    return text, resume.text_hash(text)


# ---------- Job postings ----------

# A posting that hasn't been saved yet is kept here so it persists across refreshes.
# inputs/ is git-ignored.
INPUTS_DIR = resume.INPUTS_DIR
POSTING_DIR = INPUTS_DIR / "posting"
POSTING_TEXT = INPUTS_DIR / "posting.txt"

@st.cache_data(show_spinner=False)
def pdf_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def saved_pdf(folder: Path) -> Path | None:
    return next(folder.glob("*.pdf"), None) if folder.exists() else None


def save_pdf(folder: Path, uploaded) -> None:
    """Replace the saved PDF in folder, but only if the upload is different."""
    data = uploaded.getvalue()
    current = saved_pdf(folder)
    if current and current.name == uploaded.name and current.read_bytes() == data:
        return
    clear_pdf(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / uploaded.name).write_bytes(data)


def clear_pdf(folder: Path) -> None:
    for f in folder.glob("*.pdf") if folder.exists() else []:
        f.unlink()


def pdf_section(label: str, folder: Path, key: str) -> str:
    """Upload widget backed by a saved file. Returns the PDF's text."""
    uploaded = st.file_uploader(f"{label} (PDF)", type="pdf", key=key)
    if uploaded:
        save_pdf(folder, uploaded)

    current = saved_pdf(folder)
    if not current:
        return ""

    c1, c2 = st.columns([5, 1])
    c1.success(f"Saved: {current.name}")
    if c2.button("Remove", key=f"{key}_remove"):
        clear_pdf(folder)
        st.rerun()

    try:
        text = pdf_text(current.read_bytes())
    except PdfReadError:
        st.error("Could not read this PDF. It may be corrupted or password-protected.")
        return ""
    if not text:
        st.warning("No text found in this PDF. It may be a scanned image.")
    return text



def posting_labels(postings: pd.DataFrame) -> dict[int, str]:
    return {
        int(row["id"]): f"{row['title']}" + (f" at {row['company']}" if row["company"] else "")
        for _, row in postings.iterrows()
    }


def posting_label(posting_text: str) -> str:
    first_line = next((line.strip() for line in posting_text.splitlines() if line.strip()), "Job posting")
    return first_line[:60] + ("…" if len(first_line) > 60 else "")


# ---------- Job postings ----------

NEW_POSTING = 0  # picker value for a posting that hasn't been saved yet


def draft_posting() -> str:
    """PDF upload and/or pasted text for an unsaved posting. Returns the combined text."""
    posting_pdf_text = pdf_section("Job posting", POSTING_DIR, "posting_pdf")

    saved_paste = POSTING_TEXT.read_text() if POSTING_TEXT.exists() else ""
    posting_paste = st.text_area("And/or paste the posting text", value=saved_paste, height=200)
    if posting_paste != saved_paste:
        INPUTS_DIR.mkdir(exist_ok=True)
        POSTING_TEXT.write_text(posting_paste)

    return "\n\n".join(t for t in [posting_pdf_text, posting_paste.strip()] if t)


def clear_draft() -> None:
    clear_pdf(POSTING_DIR)
    POSTING_TEXT.unlink(missing_ok=True)


def pick_posting(posting_id: int) -> None:
    # The picker's value can't change after it's drawn, so it's applied on the next run.
    st.session_state["posting_pick_next"] = posting_id


def posting_section(resume_text: str) -> tuple[str, str]:
    """Choose a saved posting or enter a new one. Returns (posting_text, label)."""
    postings = db.list_postings()
    labels = {NEW_POSTING: "➕ New posting", **posting_labels(postings)}

    if "posting_pick_next" in st.session_state:
        st.session_state["posting_pick"] = st.session_state.pop("posting_pick_next")
    elif "posting_pick" not in st.session_state:
        # The choice is kept in the URL so it survives a page refresh.
        from_url = st.query_params.get("posting", "")
        st.session_state["posting_pick"] = int(from_url) if from_url.isdigit() else NEW_POSTING
    if st.session_state["posting_pick"] not in labels:
        st.session_state["posting_pick"] = NEW_POSTING

    pick = st.selectbox("Saved postings", list(labels), format_func=labels.get, key="posting_pick")
    st.query_params["posting"] = str(pick)

    if pick != NEW_POSTING:
        row = postings.set_index("id").loc[pick]
        c1, c2 = st.columns([5, 1])
        c1.success(f"Saved posting · added {row['created_at'][:10]}")
        if c2.button("Delete", key="delete_posting"):
            db.delete_posting(pick)
            pick_posting(NEW_POSTING)
            st.rerun()
        with st.expander("Posting text"):
            st.text(row["body"])
        return row["body"], labels[pick]

    posting_text = draft_posting()
    if posting_text:
        # Prefill from a report on this posting, if one exists.
        key = reports.inputs_hash(resume_text, posting_text)
        report = next((r for r in reports.list_reports() if r.inputs_hash == key), None)
        with st.form("save_posting", border=True):
            st.markdown("**Save this posting** to compare against it again later.")
            c1, c2 = st.columns(2)
            title = c1.text_input("Job title", value=report.job_title if report else "")
            company = c2.text_input("Company", value=report.company if report else "")
            if st.form_submit_button("Save posting"):
                if title.strip():
                    pick_posting(db.add_posting(title.strip(), company.strip(), posting_text))
                    clear_draft()
                    st.rerun()
                st.warning("Enter a job title to save the posting.")
    return posting_text, posting_label(posting_text)


# ---------- Background job progress ----------

BULLET = re.compile(r"^\s*([-*•·▪◦‣]|\d+[.)])\s+\S")


def _scored_requirements(answer: str) -> list[dict]:
    try:
        partial = from_json(answer, allow_partial="trailing-strings")
    except ValueError:
        return []
    reqs = [r for r in partial.get("requirements", []) if isinstance(r, dict) and r.get("requirement")]
    # A number can be cut off mid-stream, so only trust a score once the next field has started.
    if reqs and "explanation" not in reqs[-1]:
        reqs[-1] = {**reqs[-1], "match_percent": None}
    return reqs


def render_job(job: jobs.Job) -> None:
    """Show a running job's reasoning, and for a comparison each scored requirement, as they stream in."""
    elapsed = int(time.time() - job.started_at)
    if job.stage == "interview":
        _render_interview_stage(job, elapsed)
        return
    # Rough requirement count from the posting's bullet lines, to size the progress bar.
    expected = sum(1 for line in job.posting_text.splitlines() if BULLET.match(line))

    if not job.answer:
        with st.status(f"{job.label} · Claude is analyzing the match ({elapsed}s)", expanded=True):
            st.progress(0.05, text="Thinking...")
            if job.thinking:
                # Show the tail of the reasoning summary so the newest thought is visible.
                tail = job.thinking[-1200:]
                st.info(("…" if len(job.thinking) > 1200 else "") + tail, icon="💭")
        return

    reqs = _scored_requirements(job.answer)
    done = sum(1 for r in reqs if r.get("match_percent") is not None)
    total = max(expected, len(reqs) + 1)
    with st.status(f"{job.label} · {done} requirements scored ({elapsed}s)", expanded=True):
        st.progress(min(0.1 + 0.85 * done / total, 0.95), text=f"{done} of ~{total} requirements scored")
        if reqs:
            st.dataframe(
                pd.DataFrame({
                    "Requirement": [r["requirement"] for r in reqs],
                    "Category": [r.get("category", "") for r in reqs],
                    "Match": [r.get("match_percent") for r in reqs],
                }),
                hide_index=True,
                column_config={
                    "Requirement": st.column_config.TextColumn(width="large"),
                    "Match": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
                },
            )


INTERVIEW_SECTIONS = [
    ("prep", "Prep"),
    ("recommendations", "Recommendations"),
    ("tips", "Tips"),
    ("practice", "Practice"),
    ("questions", "Possible questions"),
]


def _render_interview_stage(job: jobs.Job, elapsed: int) -> None:
    try:
        partial = from_json(job.answer, allow_partial="trailing-strings") if job.answer else {}
    except ValueError:
        partial = {}
    started = [label for key, label in INTERVIEW_SECTIONS if key in partial]
    with st.status(f"{job.label} · writing your interview plan ({elapsed}s)", expanded=True):
        if not started:
            st.progress(0.05, text="Thinking...")
            if job.thinking:
                tail = job.thinking[-1200:]
                st.info(("…" if len(job.thinking) > 1200 else "") + tail, icon="💭")
            return
        st.progress(min(0.1 + 0.85 * len(started) / len(INTERVIEW_SECTIONS), 0.95), text=f"Writing: {started[-1]}")
        counts = [f"{label}: {len(partial[key])}" for key, label in INTERVIEW_SECTIONS if isinstance(partial.get(key), list)]
        st.caption(" · ".join(counts))


def watch_jobs(prefix: str) -> None:
    """Show live progress for running jobs with this key prefix.

    The whole page reruns whenever one finishes, so it can show the new result.
    """
    running = tuple(j.key for j in jobs.with_prefix(prefix) if j.status == "running")
    if running:
        _job_monitor(prefix, running)


@st.fragment(run_every=1.0)
def _job_monitor(prefix: str, started: tuple[str, ...]) -> None:
    running = [j for j in jobs.with_prefix(prefix) if j.status == "running"]
    if tuple(j.key for j in running) != started:
        st.rerun()
    st.caption("This runs in the background. You can refresh or leave this page and come back.")
    for job in running:
        render_job(job)


def show_job_errors(prefix: str) -> None:
    for job in jobs.with_prefix(prefix):
        if job.status == "error":
            c1, c2 = st.columns([6, 1])
            c1.error(f"{job.label}: {job.error}")
            if c2.button("Dismiss", key=f"dismiss_{job.key}"):
                jobs.clear(job.key)
                st.rerun()
