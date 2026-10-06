import json

from jobsearch import plans, reports
from jobsearch.comparison import Action, ImprovementPlan, PostingRelevance, SkillToImprove


def make_plan(relevance=True) -> ImprovementPlan:
    return ImprovementPlan(summary="s", strongest_areas=[], skills_to_improve=[SkillToImprove(
        skill="SQL", impact=4, ease=3, gap_type="missing_skill", why_it_matters="w", related_requirements=[],
        actions=[Action(task="t", explanation="e", impact=3, effort="low", time_estimate="1 day")],
        time_estimate="1 week",
        relevance=[PostingRelevance(comparison=1, relevance=5, why="required")] if relevance else [],
    )])


def use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(plans, "INPUTS_DIR", tmp_path)
    monkeypatch.setattr(plans, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(plans, "LEGACY_PLAN", tmp_path / "plan.json")
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path / "reports")


def test_plans_are_kept_and_track_done(tmp_path, monkeypatch):
    use_tmp(tmp_path, monkeypatch)
    report = reports.Report("r1", "2026-10-05T10:00:00", "Engineer", "Acme", "k", None)
    first = plans.save(make_plan(), "h", [report])
    second = plans.save(make_plan(), "h", [report])
    assert first.id != second.id and len(plans.list_plans()) == 2

    plans.set_done(first, "a1", True)
    loaded = plans.load(first.id)
    assert loaded.done == {"a1"}
    assert loaded.posting(1).label == "Engineer at Acme"
    assert loaded.plan.skills_to_improve[0].relevance[0].why == "required"
    assert json.loads(plans.to_json(loaded))["postings"][0]["report_id"] == "r1"


def test_legacy_plan_is_moved(tmp_path, monkeypatch):
    use_tmp(tmp_path, monkeypatch)
    data = make_plan().model_dump()
    del data["skills_to_improve"][0]["relevance"]  # older plans had no relevance
    (tmp_path / "plan.json").write_text(json.dumps({"key": "h:r1,r2", "plan": data, "done": ["x"]}))
    [moved] = plans.list_plans()
    assert not (tmp_path / "plan.json").exists()
    assert moved.done == {"x"} and [p.report_id for p in moved.postings] == ["r1", "r2"]
    assert moved.plan.skills_to_improve[0].relevance == []
