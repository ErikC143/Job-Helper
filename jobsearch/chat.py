"""A chat assistant that can see everything the user has given the app.

Each request sends the user's resume, job postings, and summaries of what the app has generated
as context, then the conversation. The conversation is saved to inputs/chat.json so it's shared by
every page and survives refreshes.
"""

import json
from collections.abc import Iterator

import anthropic

from jobsearch import db, interview, llm, plans, reports, resume
from jobsearch.comparison import average_match

MODEL = "claude-sonnet-5"
EFFORT = "medium"
CHAT_FILE = resume.INPUTS_DIR / "chat.json"
DRAFT_TEXT = resume.INPUTS_DIR / "posting.txt"
DRAFT_PDF_DIR = resume.INPUTS_DIR / "posting"
# Keeps each message's cost bounded once the user has a lot of saved material. Roughly 100k
# tokens; the newest items are kept and the UI says how many older ones were left out.
MAX_CONTEXT_CHARS = 400_000

SYSTEM_PROMPT = """\
You are a job search assistant built into the user's Job Search app, which runs on their own \
computer. The app compares their resume to job postings, builds improvement plans, and builds \
interview plans.

Below the conversation's start you'll find the user's materials: their resume (which may end with \
candidate notes, such as work authorization), the job postings they've saved or are drafting, and \
summaries of the comparison reports, improvement plans, and interview plans the app generated. \
Use them whenever they're relevant, and say which posting or report you're drawing on. The user can \
also ask about anything else: career advice, rewriting resume bullets, cover letters, salary \
negotiation, or topics unrelated to the job search.

Be direct and specific. If the answer depends on something that isn't in the materials, say so \
instead of guessing. You can't change the app's saved data or run its analyses; when the user wants \
that, point them to the page that does it: Resume Match (comparisons), Improvement Plan, Interview \
Prep, or Generate Report (PDFs)."""


# ---------- Conversation ----------

def load_history() -> list[dict]:
    """The saved conversation as [{"role": "user" | "assistant", "content": str}, ...]."""
    if not CHAT_FILE.exists():
        return []
    try:
        return json.loads(CHAT_FILE.read_text())
    except ValueError:
        return []


def save_history(messages: list[dict]) -> None:
    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_FILE.write_text(json.dumps(messages, indent=2))


def clear_history() -> None:
    CHAT_FILE.unlink(missing_ok=True)


def stream_reply(messages: list[dict], context: str, client: anthropic.Anthropic | None = None) -> Iterator[str]:
    """Stream Claude's reply to the conversation as text chunks."""
    return llm.stream_text([SYSTEM_PROMPT, context], messages, model=MODEL, effort=EFFORT, client=client)


# ---------- Context ----------

def build_context() -> tuple[str, str]:
    """Everything the user has given the app, as text for Claude, plus a short description of it."""
    sections: list[str] = []
    described: list[str] = []
    budget = MAX_CONTEXT_CHARS
    left_out = 0

    def add(text: str) -> bool:
        nonlocal budget, left_out
        if len(text) > budget:
            left_out += 1
            return False
        sections.append(text)
        budget -= len(text)
        return True

    resume_text = resume.load()
    if resume_text:
        add(f"<resume>\n{resume_text}\n</resume>")
        described.append("your resume")

    draft = _draft_posting()
    if draft and add(f"<draft_job_posting note=\"not saved yet\">\n{draft}\n</draft_job_posting>"):
        described.append("your draft posting")

    groups = [
        ("saved posting", _postings()),
        ("report", [_report_digest(r) for r in reports.list_reports()]),
        ("improvement plan", [_plan_digest(p) for p in plans.list_plans()]),
        ("interview plan", [_interview_digest(i) for i in interview.list_interviews()]),
    ]
    for name, items in groups:  # each list is newest first
        added = sum(add(item) for item in items)
        if added:
            described.append(f"{added} {name}{'s' if added != 1 else ''}")

    if not sections:
        return "The user hasn't added a resume or any job postings yet.", "nothing yet"
    summary = ", ".join(described)
    if left_out:
        summary += f" ({left_out} older item{'s' if left_out != 1 else ''} left out to keep messages affordable)"
    return "<user_materials>\n" + "\n\n".join(sections) + "\n</user_materials>", summary


def _draft_posting() -> str:
    parts = []
    pdf = next(DRAFT_PDF_DIR.glob("*.pdf"), None) if DRAFT_PDF_DIR.exists() else None
    if pdf:
        try:
            parts.append(resume.pdf_text(pdf.read_bytes()))
        except ValueError:
            pass
    if DRAFT_TEXT.exists():
        parts.append(DRAFT_TEXT.read_text().strip())
    return "\n\n".join(p for p in parts if p)


def _postings() -> list[str]:
    return [
        f'<saved_posting title="{row.title}" company="{row.company}" saved="{row.created_at[:10]}"'
        + (f' link="{row.url}"' if row.url else "")
        + ">\n"
        f"{row.body}\n</saved_posting>"
        for row in db.list_postings().itertuples()
    ]


def _score(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "n/a"


def _report_digest(report: reports.Report) -> str:
    result = report.result
    reqs = "\n".join(
        f"- [{r.category}, {r.match_percent}%] {r.requirement}"
        + (f" | gaps: {'; '.join(r.weaknesses)}" if r.weaknesses else "")
        for r in result.requirements
    )
    return (
        f'<comparison_report job="{result.job_title}" company="{result.company}" '
        f'saved="{report.created_at[:10]}" minimum_match="{_score(average_match(result, "minimum"))}" '
        f'preferred_match="{_score(average_match(result, "preferred"))}">\n'
        f"Summary: {result.overall_summary}\n"
        f"Inferred skills: {', '.join(result.inferred_skills)}\n"
        f"Requirements:\n{reqs}\n</comparison_report>"
    )


def _plan_digest(saved: plans.Plan) -> str:
    skills = "\n".join(
        f"- {s.skill} (impact {s.impact}/5, ease {s.ease}/5, {s.gap_type.replace('_', ' ')}): "
        + "; ".join(a.task for a in s.actions)
        for s in saved.plan.skills_to_improve
    )
    return (
        f'<improvement_plan saved="{saved.created_at[:10]}" '
        f'postings="{"; ".join(p.label for p in saved.postings)}" to_dos_done="{len(saved.done)}">\n'
        f"Summary: {saved.plan.summary}\nSkills to work on:\n{skills}\n</improvement_plan>"
    )


def _interview_digest(saved: interview.SavedInterview) -> str:
    plan = saved.plan
    return (
        f'<interview_plan job="{saved.job_title}" company="{saved.company}" saved="{saved.created_at[:10]}">\n'
        f"Summary: {plan.summary}\n"
        f"Prep: {'; '.join(t.task for t in plan.prep)}\n"
        f"Recommendations: {'; '.join(r.title for r in plan.recommendations)}\n"
        f"Practice: {'; '.join(p.exercise for p in plan.practice)}\n"
        "Possible questions:\n" + "\n".join(f"- ({q.type}) {q.question}" for q in plan.questions)
        + "\n</interview_plan>"
    )
