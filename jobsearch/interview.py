"""Generate interview prep plans with Claude, and save them as JSON files in inputs/interviews/.

A plan is built from the resume, the job posting, and the comparison report for the two, so
its advice can lean on the matches and gaps the comparison found.

Each saved plan is one file, kept until the user deletes it:
    {"id", "created_at", "report_id", "resume_hash", "job_title", "company", "plan": {...}, "done": [...]}
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from jobsearch import reports
from jobsearch.comparison import EFFORT, ComparisonResult, OnEvent, _parse

INTERVIEWS_DIR = Path(__file__).parent.parent / "inputs" / "interviews"

INTERVIEW_PROMPT = """\
You are an experienced interview coach and hiring manager. You will receive a candidate's \
resume, a job posting, and a comparison that scores the candidate against each of the \
posting's requirements, with strengths and weaknesses.

Write an interview preparation plan for this specific candidate and job, in five sections:

1. prep: concrete preparation tasks to do before the interview, such as researching the \
company and its products, reviewing specific technical topics the posting emphasizes, and \
preparing materials or examples. Order them by priority.
2. recommendations: strategy for this interview: which strengths and experiences to lead \
with, how to frame the gaps the comparison found honestly and constructively, and how to \
position the candidate for this role.
3. tips: practical advice for the interview itself, specific to this role, company, and \
candidate rather than generic advice.
4. practice: exercises to rehearse, such as mock answers, technical drills, whiteboard or \
hands-on problems, or a short pitch, each tied to a posting requirement.
5. questions: likely interview questions across behavioral, technical, situational, \
role-specific, resume, and culture-fit types. Weight them toward the posting's most \
important requirements and the candidate's gaps, since interviewers probe those. For each, \
explain why they would ask it and outline a strong answer built from the candidate's real \
experience on the resume. Never invent experience the resume doesn't support; when the \
candidate lacks it, outline how to answer honestly. Include 12 to 20 questions.

Base everything on the resume, posting, and comparison you receive. Say when a point about \
the company is an educated guess rather than stated in the posting."""


class PrepTask(BaseModel):
    task: str
    details: str
    time_estimate: str


class Recommendation(BaseModel):
    title: str
    details: str


class Tip(BaseModel):
    tip: str
    why: str


class PracticeExercise(BaseModel):
    exercise: str
    how_to: str
    related_requirement: str


class InterviewQuestion(BaseModel):
    question: str
    type: Literal["behavioral", "technical", "situational", "role_specific", "resume", "culture_fit"]
    difficulty: Literal["easy", "medium", "hard"]
    why_they_ask: str
    answer_outline: str
    resume_evidence: list[str]  # resume experiences to draw on; empty if the candidate has none


class InterviewPlan(BaseModel):
    summary: str
    prep: list[PrepTask]
    recommendations: list[Recommendation]
    tips: list[Tip]
    practice: list[PracticeExercise]
    questions: list[InterviewQuestion]


def generate(
    resume_text: str,
    posting_text: str,
    result: ComparisonResult,
    client: anthropic.Anthropic | None = None,
    on_event: OnEvent | None = None,
) -> InterviewPlan:
    content = (
        f"<resume>\n{resume_text}\n</resume>\n\n"
        f"<job_posting>\n{posting_text}\n</job_posting>\n\n"
        f"<comparison>\n{result.model_dump_json()}\n</comparison>"
    )
    # The plan is long, so it gets more room than the default.
    return _parse(
        INTERVIEW_PROMPT, content, InterviewPlan, client, on_event, max_tokens=32000, effort=EFFORT["interview"]
    )


# ---------- Saved plans ----------

@dataclass
class SavedInterview:
    id: str
    created_at: str
    report_id: str
    resume_hash: str
    job_title: str
    company: str
    plan: InterviewPlan
    done: set[str] = field(default_factory=set)  # checked-off prep and practice items

    @property
    def label(self) -> str:
        return f"{self.created_at[:16].replace('T', ' ')} · {self.job_title} at {self.company}"


def save(plan: InterviewPlan, report: reports.Report, resume_hash: str) -> SavedInterview:
    now = datetime.now().isoformat(timespec="seconds")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{report.company} {report.job_title}".lower()).strip("-")[:60]
    saved = SavedInterview(
        id=f"{now.replace(':', '')}_{slug}",
        created_at=now,
        report_id=report.id,
        resume_hash=resume_hash,
        job_title=report.job_title,
        company=report.company,
        plan=plan,
    )
    _write(saved)
    return saved


def set_done(saved: SavedInterview, item_id: str, done: bool) -> None:
    if done:
        saved.done.add(item_id)
    else:
        saved.done.discard(item_id)
    _write(saved)


def to_json(saved: SavedInterview) -> str:
    return json.dumps({**saved.__dict__, "plan": saved.plan.model_dump(), "done": sorted(saved.done)}, indent=2)


def load(interview_id: str) -> SavedInterview | None:
    path = INTERVIEWS_DIR / f"{interview_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    try:
        data["plan"] = InterviewPlan.model_validate(data["plan"])
    except ValueError:
        return None  # A format from an older version of the app that can't be shown.
    data["done"] = set(data["done"])
    return SavedInterview(**data)


def list_interviews() -> list[SavedInterview]:
    """All saved interview plans, newest first."""
    if not INTERVIEWS_DIR.exists():
        return []
    paths = sorted(INTERVIEWS_DIR.glob("*.json"), reverse=True)
    return [s for p in paths if (s := load(p.stem))]


def delete(interview_id: str) -> None:
    (INTERVIEWS_DIR / f"{interview_id}.json").unlink(missing_ok=True)


def _write(saved: SavedInterview) -> None:
    INTERVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    (INTERVIEWS_DIR / f"{saved.id}.json").write_text(to_json(saved))
