from io import BytesIO

import pytest
from pypdf import PdfReader

from jobsearch import pdf_report, plans, reports
from jobsearch.comparison import ComparisonResult, RequirementMatch
from jobsearch.interview import InterviewPlan, InterviewQuestion, PrepTask, SavedInterview
from tests.test_plans import make_plan


def make_report() -> reports.Report:
    result = ComparisonResult(
        job_title="Engineer", company="Acme & Sons", inferred_skills=["Python"], overall_summary="Good fit → apply.",
        requirements=[RequirementMatch(
            requirement="Python <3 years>", category="minimum", match_percent=80, explanation="e", context="c",
            talking_points=["Talk about the ETL project"], strengths=["s"], weaknesses=["Never used Spark"],
        )],
    )
    return reports.Report("r1", "2026-10-05T10:00:00", "Engineer", "Acme & Sons", "k", result)


def make_interview(report: reports.Report) -> SavedInterview:
    plan = InterviewPlan(
        summary="Lead with ETL.", prep=[PrepTask(task="Research Acme", details="d", time_estimate="1 hour")],
        recommendations=[], tips=[], practice=[],
        questions=[InterviewQuestion(question="Why Acme?", type="culture_fit", difficulty="easy", why_they_ask="w",
                                     answer_outline="Answer with the mission.", resume_evidence=[])],
    )
    return SavedInterview("i1", "2026-10-05T11:00:00", report.id, "", report.job_title, report.company, plan)


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)


@pytest.mark.parametrize("level", ["sparknotes", "simple", "complete"])
def test_every_level_builds_and_includes_each_section(level):
    report = make_report()
    plan = plans.Plan("p1", "2026-10-05T12:00:00", "", [plans.PostingRef("r1", "Engineer", "Acme & Sons")], make_plan())
    data = pdf_report.build_pdf([report], level, interviews=[make_interview(report)], plans=[plan],
                                posting_texts={"r1": "Original posting body"})
    text = pdf_text(data)

    assert data.startswith(b"%PDF")
    assert "Acme & Sons" in text and "Python <3 years>" in text  # escaped, not dropped
    assert "Interview notes" in text and "Why Acme?" in text
    assert "Improvement plan" in text and "SQL" in text
    assert ("Answer with the mission." in text) == (level != "sparknotes")
    assert ("Original posting body" in text) == (level == "complete")


def test_sections_can_be_left_out():
    text = pdf_text(pdf_report.build_pdf([make_report()], "simple", include_match=False,
                                         interviews=[make_interview(make_report())]))
    assert "Interview notes" in text and "Resume match" not in text and "Improvement plan" not in text
