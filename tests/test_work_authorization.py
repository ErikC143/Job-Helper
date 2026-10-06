import pytest

from jobsearch import comparison, h1b
from jobsearch.comparison import PostingCheck, SponsorshipAssessment

CSV_HEADER = '"Fiscal Year",Employer,"Initial Approval","Initial Denial","Continuing Approval","Continuing Denial",NAICS,"Tax ID",State,City,ZIP\n'


@pytest.fixture
def h1b_data(tmp_path, monkeypatch):
    monkeypatch.setattr(h1b, "DATA_DIR", tmp_path)
    monkeypatch.setattr(h1b, "YEARS", (2022, 2023))
    h1b._rows.cache_clear()
    (tmp_path / "h1b_datahubexport-2022.csv").write_text(
        CSV_HEADER
        + '2022,"FLUKE ELECTRONICS CORPORATION",2,0,3,1,33,1817,WA,EVERETT,98203\n'
        + '2022,"FORTIVE CORPORATION",0,0,1,0,33,4583,WA,EVERETT,98203\n'
    )
    (tmp_path / "h1b_datahubexport-2023.csv").write_text(
        CSV_HEADER
        + '2023,"FLUKE ELECTRONICS CORPORATION",1,0,3,0,33,1817,WA,EVERETT,98203\n'
        + '2023,"FLUKE ELECTRONICS CORPORATION",0,0,1,0,33,1817,,,\n'
        + '2023,"FLUKEY BAKERY LLC",1,0,0,0,72,0001,OR,PORTLAND,97201\n'
    )
    yield
    h1b._rows.cache_clear()


def test_h1b_search_sums_years_and_ignores_legal_suffixes(h1b_data):
    [fluke] = h1b.search(["Fluke Electronics Corp."])
    assert fluke.employer == "FLUKE ELECTRONICS CORPORATION"
    assert (fluke.initial_approvals, fluke.continuing_approvals, fluke.continuing_denials) == (3, 7, 1)
    assert fluke.years == [2022, 2023] and fluke.locations == ["Everett, WA"]

    # Whole words only: "Fluke" doesn't match "Flukey". A generic-only name matches nothing.
    assert [e.employer for e in h1b.search(["Fluke", "Fortive Inc"])] == [
        "FLUKE ELECTRONICS CORPORATION", "FORTIVE CORPORATION",
    ]
    assert h1b.search(["Corporation"]) == []


def posting_check(**overrides) -> PostingCheck:
    fields = dict(
        status="F-1 student visa", status_stated=True, needs_sponsorship=True, posting_stance="not_stated",
        posting_evidence=[], employer_names=["Fluke"], confidence=50, summary="posting", considerations=[],
    )
    return PostingCheck(**{**fields, **overrides})


def fake_parse(monkeypatch, posting: PostingCheck) -> list[str]:
    calls = []

    def parse(system, content, output_format, client, on_event=None, *, effort):
        assert effort == comparison.EFFORT["work_authorization"]
        calls.append(content)
        if output_format is PostingCheck:
            return posting
        return SponsorshipAssessment(
            matched_employers=["FLUKE ELECTRONICS CORPORATION", "MADE UP INC"],
            confidence=140, summary="data", considerations=["Ask about STEM OPT"],
        )

    monkeypatch.setattr(comparison, "_parse", parse)
    return calls


def test_unclear_posting_falls_back_to_h1b_data(h1b_data, monkeypatch):
    calls = fake_parse(monkeypatch, posting_check())
    auth = comparison.check_work_authorization("resume", "posting")
    assert len(calls) == 2 and "FLUKE ELECTRONICS CORPORATION" in calls[1]
    assert auth.database_searched and [m.employer for m in auth.h1b_matches] == ["FLUKE ELECTRONICS CORPORATION"]
    assert auth.assessment.matched_employers == ["FLUKE ELECTRONICS CORPORATION"]  # made-up names dropped
    assert (auth.confidence, auth.summary, auth.considerations) == (100, "data", ["Ask about STEM OPT"])


@pytest.mark.parametrize("overrides", [
    {"status": "U.S. citizen", "status_stated": False, "needs_sponsorship": False},
    {"posting_stance": "accepts"},
])
def test_no_h1b_search_when_posting_settles_it(monkeypatch, overrides):
    calls = fake_parse(monkeypatch, posting_check(**overrides))
    monkeypatch.setattr(h1b, "search", lambda names: pytest.fail("should not search"))
    auth = comparison.check_work_authorization("resume", "posting")
    assert len(calls) == 1 and not auth.database_searched and auth.assessment is None
    assert (auth.confidence, auth.summary) == (50, "posting")


def test_download_failure_keeps_posting_result(monkeypatch):
    def offline(names):
        raise OSError("no network")

    fake_parse(monkeypatch, posting_check())
    monkeypatch.setattr(h1b, "search", offline)
    auth = comparison.check_work_authorization("resume", "posting")
    assert "no network" in auth.database_error and auth.confidence == 50


def test_report_saves_and_loads_work_authorization(h1b_data, tmp_path, monkeypatch):
    from jobsearch import reports
    from tests.test_explanations import make_result

    fake_parse(monkeypatch, posting_check())
    auth = comparison.check_work_authorization("resume", "posting")
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path / "reports")
    report = reports.save(make_result(), "key", "hash", auth)
    assert reports.load(report.id).work_authorization == auth

    old = reports.save(make_result(), "key2")
    assert reports.load(old.id).work_authorization is None
