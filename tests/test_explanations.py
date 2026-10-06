from jobsearch import comparison, reports
from jobsearch.comparison import (
    ComparisonResult,
    KeyTerm,
    RequirementExplanation,
    RequirementExplanations,
    RequirementMatch,
)


def make_result() -> ComparisonResult:
    req = dict(explanation="", context="", talking_points=[], strengths=[], weaknesses=[])
    return ComparisonResult(
        job_title="Test Engineer",
        company="Acme",
        inferred_skills=[],
        overall_summary="",
        requirements=[
            RequirementMatch(requirement="Experience with LabVIEW", category="minimum", match_percent=50, **req),
            RequirementMatch(requirement="Familiarity with ISO 13485", category="preferred", match_percent=0, **req),
        ],
    )


def explanation(number: int) -> RequirementExplanation:
    return RequirementExplanation(
        number=number,
        plain_english="asks for",
        key_terms=[KeyTerm(term="LabVIEW", definition="graphical programming tool")],
        in_this_role="used for test stands",
    )


def test_explain_requirements_numbers_requirements_and_drops_unknown(monkeypatch):
    sent = {}

    def fake_parse(system, content, output_format, client, on_event=None, *, effort):
        sent["content"] = content
        return RequirementExplanations(explanations=[explanation(1), explanation(2), explanation(3)])

    monkeypatch.setattr(comparison, "_parse", fake_parse)
    result = comparison.explain_requirements(make_result(), "Full posting text")
    assert [e.number for e in result] == [1, 2]
    assert "1. [minimum] Experience with LabVIEW" in sent["content"]
    assert "2. [preferred] Familiarity with ISO 13485" in sent["content"]
    assert "Full posting text" in sent["content"]

    comparison.explain_requirements(make_result())
    assert "<job_posting>" not in sent["content"]


def test_report_saves_and_loads_explanations(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path)
    report = reports.save(make_result(), "key")
    assert reports.load(report.id).explanations == []

    reports.save_explanations(report, [explanation(2)])
    loaded = reports.load(report.id)
    assert loaded.explanations == [explanation(2)]
    assert loaded.result == make_result()
