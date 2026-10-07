"""Compare a resume against a job posting with Claude.

Usage:
    python -m jobsearch.comparison resume.txt posting.txt [--json]

Uses the Claude API with ANTHROPIC_API_KEY, or the Claude Code CLI signed in to your Claude
account when CLAUDE_BACKEND=cli (see jobsearch/llm.py). Both can be set in .env.
"""

import argparse
import sys
from pathlib import Path
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

from . import h1b, llm

MODEL = "claude-sonnet-5-5"

# How much Claude reasons before answering, per task. Higher effort is slower and uses more
# tokens, so it's spent where judgment matters most.
EFFORT: dict[str, llm.Effort] = {
    "compare": "low",  # matching resume evidence to each requirement
    "explain": "low",  # plain-English definitions
    "interview": "low",  # long-form writing grounded in the comparison
    "improvement_plan": "medium",  # weighing and ranking gaps across several postings
    "work_authorization": "high",  # subtle visa rules, where a wrong answer can cost an opportunity
}

SYSTEM_PROMPT = """\
You are an experienced technical recruiter and career coach.

Take the user's resume, infer skills and qualification information based on the context, \
and match it to the uploaded job posting. Infer skills that are implied but not stated \
(for example, a role that "built REST APIs in Flask" implies Python, HTTP, and API design), \
and say when a match rests on inference rather than an explicit statement.

For each bullet point of the job posting's requirements and qualifications, rank the \
percentage match (0-100) and explain the decision, including:
- talking points the candidate can use in an interview or cover letter
- context: the specific resume evidence the score is based on
- strengths: where the candidate meets or exceeds the requirement
- weaknesses: gaps, missing evidence, or areas an interviewer may probe

Classify each bullet as "minimum" (required/basic qualifications), "preferred" \
(preferred/nice-to-have/bonus qualifications), or "other" (responsibilities or other \
bullets that still describe what the job needs). Copy each bullet's text from the posting \
verbatim. Be honest: do not inflate scores, and give 0 when there is no evidence.

The resume may end with candidate notes the candidate wrote for these comparisons. Treat them \
as true facts about the candidate."""


class RequirementMatch(BaseModel):
    requirement: str
    category: Literal["minimum", "preferred", "other"]
    match_percent: int
    explanation: str
    context: str
    talking_points: list[str]
    strengths: list[str]
    weaknesses: list[str]


class ComparisonResult(BaseModel):
    job_title: str
    company: str
    inferred_skills: list[str]
    overall_summary: str
    requirements: list[RequirementMatch]


IMPROVEMENT_PROMPT = """\
You are a career coach. You will receive a candidate's resume and one or more comparisons \
of that resume against job postings the candidate is interested in. Each comparison scores \
the candidate against each posting requirement and lists strengths and weaknesses.

Produce a prioritized plan of skills the candidate should work on or improve. Focus on gaps \
that recur across postings or that block minimum qualifications, ahead of one-off preferred \
qualifications. Also call out skills the candidate likely has but does not show clearly on \
the resume, since fixing the resume is cheaper than learning a skill. For each skill, give \
concrete, specific actions (projects to build, certifications, courses, or resume changes) \
and a realistic time estimate.

Score every skill and every action so the candidate can rank them:
- impact (1-5): how much it improves the candidate's chances across these postings. \
5 = unblocks minimum qualifications in several postings; 1 = minor preferred-only gain.
- ease (1-5, skills only): how easy the whole skill is to close. 5 = an afternoon or a \
resume edit; 1 = months of learning.
- effort (actions only): "low" (under a day), "medium" (days to a couple of weeks), \
or "high" (weeks or more).
Give each action a short imperative task (a to-do item) and an explanation of what to do \
and why it helps.

For each skill, also break down its relevance to each job posting: one entry for every \
comparison you received (use its index), with:
- relevance (1-5): 5 = the posting requires it as a minimum qualification and the candidate \
falls short; 3 = a preferred qualification or part of the day-to-day work; 1 = barely relevant \
to this posting.
- why: which of the posting's requirements this skill relates to, quoting or naming them, and \
how closing the gap would change the candidate's match for that posting."""


class PostingRelevance(BaseModel):
    comparison: int  # 1-based index of the comparison (job posting) in the request
    relevance: int
    why: str


class Action(BaseModel):
    task: str
    explanation: str
    impact: int
    effort: Literal["low", "medium", "high"]
    time_estimate: str


class SkillToImprove(BaseModel):
    skill: str
    impact: int
    ease: int
    gap_type: Literal["missing_skill", "needs_more_depth", "not_shown_on_resume"]
    why_it_matters: str
    related_requirements: list[str]
    actions: list[Action]
    time_estimate: str
    relevance: list[PostingRelevance]


class ImprovementPlan(BaseModel):
    summary: str
    strongest_areas: list[str]
    skills_to_improve: list[SkillToImprove]


EXPLAIN_PROMPT = """\
You are an experienced technical recruiter who helps candidates understand job postings. \
You will receive a job's title and company, the posting text when available, and a numbered \
list of the posting's requirements.

For every numbered requirement, explain what it is really asking for:
- plain_english: what the employer wants, in plain language, including the depth or amount \
of experience the wording implies (for example, "familiarity with" versus "expert in", or \
what a degree or certification requirement usually allows as an equivalent).
- key_terms: define each technical term, acronym, tool, standard, or piece of jargon in the \
requirement in one or two sentences that someone new to the field would understand. Use an \
empty list when there are none.
- in_this_role: how this requirement most likely applies to this specific job, based on the \
company, the role, and the rest of the posting: what the person would probably use it for \
day to day. Say when this is an educated guess rather than stated in the posting.

Return one explanation per requirement, with its number. Explain the requirement only; do not \
assess any candidate."""


class KeyTerm(BaseModel):
    term: str
    definition: str


class RequirementExplanation(BaseModel):
    number: int  # 1-based position in ComparisonResult.requirements
    plain_english: str
    key_terms: list[KeyTerm]
    in_this_role: str


class RequirementExplanations(BaseModel):
    explanations: list[RequirementExplanation]


WORK_AUTH_PROMPT = """\
You are an experienced recruiter who knows U.S. work authorization rules. You will receive a \
candidate's resume, which may end with candidate notes the candidate wrote, and a job posting.

First, determine the candidate's U.S. work authorization. The candidate notes may start with \
a "Work authorization:" line the candidate picked from a list; treat it as their status, and \
use the rest of the resume and notes for detail (for example a STEM degree or OPT dates, or \
the specifics when they picked "Other"). Without that line, use any status the resume or notes \
state. If none is stated anywhere, assume the candidate is a U.S. citizen and set \
status_stated to false. Set needs_sponsorship to true when the status depends on the employer \
accepting or sponsoring a visa, now or later (for example F-1, H-1B, TN), and false for U.S. \
citizens and permanent residents.

Then read the posting for anything about who can be hired: citizenship or "U.S. person" \
requirements, export control (ITAR/EAR), security clearances, visa sponsorship, OPT/CPT, or \
"authorized to work" language. Copy each relevant sentence verbatim into posting_evidence. \
Set posting_stance for the candidate's status:
- "accepts": the posting explicitly allows it (for example "OPT/CPT candidates welcome", or \
the candidate meets a stated citizenship requirement).
- "rejects": the posting explicitly rules it out (for example "U.S. citizens only", or "must \
not require sponsorship now or in the future" for a candidate who will need it).
- "not_stated": the posting doesn't settle it.
Be precise about what each statement means for this status: "no sponsorship" usually does not \
rule out an F-1 student starting on OPT, but does rule out staying past OPT; ITAR "U.S. person" \
roles accept citizens and permanent residents but not F-1 students.

List employer_names to search for in the USCIS H-1B Employer Data Hub: the hiring company's \
likely legal entity names, a shorter distinctive form of its name, and its parent company, for \
example ["Fluke Electronics Corporation", "Fluke", "Fortive"]. Avoid generic words that would \
match unrelated employers.

Give confidence (0-100) that the employer will accept the candidate's status for this job based \
on the posting alone, a short summary, and considerations: practical points such as OPT timing, \
STEM OPT requiring an E-Verify employer, or questions to ask the recruiter."""

SPONSORSHIP_PROMPT = """\
You are an experienced recruiter who knows U.S. work authorization rules. A candidate wants to \
know whether an employer will accept their work authorization for a job. The posting doesn't \
explicitly allow it, so you also have the employer's H-1B petition history from the USCIS H-1B \
Employer Data Hub. If the posting explicitly rules the status out, that decides this job, and \
the data only shows whether the company sponsors for other roles.

The search matches names loosely, so first decide which records are actually the hiring \
company or its parent, and list their exact employer names in matched_employers. Initial \
approvals are new H-1B employment (such as a student moving from OPT to H-1B); continuing \
approvals are extensions, amendments, and transfers from other employers. Recent initial \
approvals are the strongest sign the employer sponsors. No records at all suggests the \
employer rarely or never sponsors, though a small employer or a different legal name can also \
explain it.

Give confidence (0-100) that the employer will accept the candidate's status for this job, a \
short summary that cites the numbers, and considerations: practical points such as OPT timing, \
STEM OPT requiring an E-Verify employer, or questions to ask the recruiter. Say what the data \
can't tell you."""


class PostingCheck(BaseModel):
    status: str  # the candidate's work authorization, e.g. "F-1 student visa"
    status_stated: bool  # False when the candidate is assumed to be a U.S. citizen
    needs_sponsorship: bool
    posting_stance: Literal["accepts", "rejects", "not_stated"]
    posting_evidence: list[str]
    employer_names: list[str]
    confidence: int
    summary: str
    considerations: list[str]


class SponsorshipAssessment(BaseModel):
    matched_employers: list[str]
    confidence: int
    summary: str
    considerations: list[str]


class WorkAuthorization(BaseModel):
    """Whether a job will accept the candidate's work authorization: the posting, then H-1B data."""

    posting: PostingCheck
    database_searched: bool = False
    database_error: str = ""
    h1b_matches: list[h1b.H1BEmployer] = []
    assessment: SponsorshipAssessment | None = None  # set once the H-1B data has been reviewed

    @property
    def confidence(self) -> int:
        return (self.assessment or self.posting).confidence

    @property
    def summary(self) -> str:
        return (self.assessment or self.posting).summary

    @property
    def considerations(self) -> list[str]:
        return (self.assessment or self.posting).considerations


OnEvent = llm.OnEvent


def _parse(
    system: str,
    content: str,
    output_format: type[BaseModel],
    client: anthropic.Anthropic | None,
    on_event: OnEvent | None = None,
    max_tokens: int = 16000,
    *,
    effort: llm.Effort,
):
    return llm.parse(
        system, content, output_format,
        model=MODEL, effort=effort, on_event=on_event, max_tokens=max_tokens, client=client,
    )


def compare(
    resume_text: str,
    posting_text: str,
    client: anthropic.Anthropic | None = None,
    on_event: OnEvent | None = None,
) -> ComparisonResult:
    content = (
        f"<resume>\n{resume_text}\n</resume>\n\n"
        f"<job_posting>\n{posting_text}\n</job_posting>"
    )
    result = _parse(SYSTEM_PROMPT, content, ComparisonResult, client, on_event, effort=EFFORT["compare"])
    for req in result.requirements:
        req.match_percent = max(0, min(100, req.match_percent))
    return result


def improvement_plan(
    resume_text: str, results: list[ComparisonResult], client: anthropic.Anthropic | None = None
) -> ImprovementPlan:
    comparisons = "\n\n".join(
        f'<comparison index="{i}">\n{r.model_dump_json()}\n</comparison>' for i, r in enumerate(results, 1)
    )
    content = f"<resume>\n{resume_text}\n</resume>\n\n<comparisons>\n{comparisons}\n</comparisons>"
    plan = _parse(IMPROVEMENT_PROMPT, content, ImprovementPlan, client, effort=EFFORT["improvement_plan"])
    for skill in plan.skills_to_improve:
        skill.impact = max(1, min(5, skill.impact))
        skill.ease = max(1, min(5, skill.ease))
        for action in skill.actions:
            action.impact = max(1, min(5, action.impact))
        skill.relevance = [r for r in skill.relevance if 1 <= r.comparison <= len(results)]
        for r in skill.relevance:
            r.relevance = max(1, min(5, r.relevance))
    return plan


def explain_requirements(
    result: ComparisonResult, posting_text: str | None = None, client: anthropic.Anthropic | None = None
) -> list[RequirementExplanation]:
    """Explain what each requirement in result asks for. Doesn't need the resume.

    posting_text adds context about the company and role; without it, only the requirements are used.
    """
    numbered = "\n".join(f"{i}. [{r.category}] {r.requirement}" for i, r in enumerate(result.requirements, 1))
    content = f"<job>{result.job_title} at {result.company}</job>\n\n"
    if posting_text:
        content += f"<job_posting>\n{posting_text}\n</job_posting>\n\n"
    content += f"<requirements>\n{numbered}\n</requirements>"
    parsed = _parse(EXPLAIN_PROMPT, content, RequirementExplanations, client, effort=EFFORT["explain"])
    return [e for e in parsed.explanations if 1 <= e.number <= len(result.requirements)]


def check_work_authorization(
    resume_text: str, posting_text: str, client: anthropic.Anthropic | None = None
) -> WorkAuthorization:
    """Check the posting for the candidate's visa status, then the employer's H-1B history if needed."""
    content = (
        f"<resume>\n{resume_text}\n</resume>\n\n"
        f"<job_posting>\n{posting_text}\n</job_posting>"
    )
    posting = _parse(WORK_AUTH_PROMPT, content, PostingCheck, client, effort=EFFORT["work_authorization"])
    posting.confidence = max(0, min(100, posting.confidence))
    auth = WorkAuthorization(posting=posting)
    # The H-1B data only matters when the posting doesn't already say the status is accepted.
    if not posting.needs_sponsorship or posting.posting_stance == "accepts":
        return auth

    try:
        auth.h1b_matches = h1b.search(posting.employer_names)
    except OSError as e:
        auth.database_error = f"Couldn't download the {h1b.SOURCE} data: {e}"
        return auth
    auth.database_searched = True

    records = "\n".join(m.model_dump_json() for m in auth.h1b_matches) or "No matching employers."
    content += (
        f"\n\n<posting_check>\n{posting.model_dump_json()}\n</posting_check>\n\n"
        f'<h1b_records source="{h1b.SOURCE}" searched_names="{", ".join(posting.employer_names)}">\n'
        f"{records}\n</h1b_records>"
    )
    assessment = _parse(
        SPONSORSHIP_PROMPT, content, SponsorshipAssessment, client, effort=EFFORT["work_authorization"]
    )
    assessment.confidence = max(0, min(100, assessment.confidence))
    found = {m.employer for m in auth.h1b_matches}
    assessment.matched_employers = [name for name in assessment.matched_employers if name in found]
    auth.assessment = assessment
    return auth


def error_message(e: Exception) -> str | None:
    """A readable message for an error from a Claude call in this module, or None if unexpected."""
    if isinstance(e, anthropic.AuthenticationError):
        return "Invalid API key. Check ANTHROPIC_API_KEY in your .env file."
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the API. Wait a minute and try again."
    if isinstance(e, anthropic.APIStatusError):
        return f"API error ({e.status_code}): {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Could not reach the Claude API. Check your internet connection."
    if isinstance(e, RuntimeError):
        return str(e)
    if isinstance(e, TypeError) and "authentication method" in str(e):
        return "No API key found. Copy .env.example to .env and set ANTHROPIC_API_KEY."
    return None


def average_match(result: ComparisonResult, category: str) -> float | None:
    scores = [r.match_percent for r in result.requirements if r.category == category]
    return sum(scores) / len(scores) if scores else None


def to_markdown(result: ComparisonResult) -> str:
    lines = [f"# {result.job_title} at {result.company}", "", result.overall_summary, ""]

    for category, label in [("minimum", "Minimum qualifications"), ("preferred", "Preferred qualifications")]:
        avg = average_match(result, category)
        if avg is not None:
            lines.append(f"**{label} average match:** {avg:.0f}%  ")
    lines += ["", "**Inferred skills:** " + ", ".join(result.inferred_skills), ""]

    for category, label in [("minimum", "Minimum qualifications"), ("preferred", "Preferred qualifications"), ("other", "Other requirements")]:
        reqs = [r for r in result.requirements if r.category == category]
        if not reqs:
            continue
        lines += [f"## {label}", ""]
        for r in reqs:
            lines += [f"### {r.match_percent}% | {r.requirement}", "", r.explanation, "", f"**Context:** {r.context}", ""]
            for heading, items in [("Talking points", r.talking_points), ("Strengths", r.strengths), ("Weaknesses", r.weaknesses)]:
                if items:
                    lines.append(f"**{heading}:**")
                    lines += [f"- {item}" for item in items]
                    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Match a resume to a job posting with Claude.")
    parser.add_argument("resume", type=Path, help="Path to resume .txt file")
    parser.add_argument("posting", type=Path, help="Path to job posting .txt file")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of Markdown")
    args = parser.parse_args()

    for path in (args.resume, args.posting):
        if not path.is_file():
            sys.exit(f"File not found: {path}")

    load_dotenv()
    try:
        result = compare(args.resume.read_text(), args.posting.read_text())
    except Exception as e:
        if (message := error_message(e)) is None:
            raise
        sys.exit(message)

    print(result.model_dump_json(indent=2) if args.json else to_markdown(result))


if __name__ == "__main__":
    main()
