"""Save and load improvement plans as JSON files in inputs/plans/.

Each plan is one file, kept until the user deletes it:
    {"id", "created_at", "resume_hash", "postings": [...], "plan": {...}, "done": [...]}

"postings" lists the reports the plan was built from, in the order they were sent to Claude,
so a skill's relevance entry for comparison N refers to postings[N - 1].
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jobsearch import reports
from jobsearch.comparison import ImprovementPlan

INPUTS_DIR = Path(__file__).parent.parent / "inputs"
PLANS_DIR = INPUTS_DIR / "plans"
# Older versions of the app kept only the latest plan here; it's moved into PLANS_DIR.
LEGACY_PLAN = INPUTS_DIR / "plan.json"


@dataclass
class PostingRef:
    report_id: str
    job_title: str
    company: str

    @property
    def label(self) -> str:
        return f"{self.job_title} at {self.company}" if self.company else self.job_title


@dataclass
class Plan:
    id: str
    created_at: str
    resume_hash: str
    postings: list[PostingRef]
    plan: ImprovementPlan
    done: set[str] = field(default_factory=set)  # ids of checked-off to-dos

    @property
    def label(self) -> str:
        n = len(self.postings)
        return f"{self.created_at[:16].replace('T', ' ')} · {n} posting{'s' if n != 1 else ''}"

    def posting(self, comparison: int) -> PostingRef | None:
        """The posting a relevance entry refers to, by its 1-based comparison index."""
        return self.postings[comparison - 1] if 1 <= comparison <= len(self.postings) else None


def save(plan: ImprovementPlan, resume_hash: str, used: list[reports.Report]) -> Plan:
    now = datetime.now().isoformat(timespec="seconds")
    saved = Plan(
        id=_unique_id(now),
        created_at=now,
        resume_hash=resume_hash,
        postings=[PostingRef(r.id, r.job_title, r.company) for r in used],
        plan=plan,
    )
    _write(saved)
    return saved


def set_done(plan: Plan, action_id: str, done: bool) -> None:
    if done:
        plan.done.add(action_id)
    else:
        plan.done.discard(action_id)
    _write(plan)


def to_json(plan: Plan) -> str:
    data = {
        **plan.__dict__,
        "postings": [p.__dict__ for p in plan.postings],
        "plan": plan.plan.model_dump(),
        "done": sorted(plan.done),
    }
    return json.dumps(data, indent=2)


def load(plan_id: str) -> Plan | None:
    path = PLANS_DIR / f"{plan_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    # Plans built before relevance was added have none; show them without it.
    for skill in data["plan"]["skills_to_improve"]:
        skill.setdefault("relevance", [])
    try:
        plan = ImprovementPlan.model_validate(data["plan"])
    except ValueError:
        return None  # A format from an older version of the app that can't be shown.
    return Plan(
        id=data["id"],
        created_at=data["created_at"],
        resume_hash=data["resume_hash"],
        postings=[PostingRef(**p) for p in data["postings"]],
        plan=plan,
        done=set(data["done"]),
    )


def list_plans() -> list[Plan]:
    """All saved plans, newest first."""
    _move_legacy_plan()
    if not PLANS_DIR.exists():
        return []
    paths = sorted(PLANS_DIR.glob("*.json"), reverse=True)
    return [p for path in paths if (p := load(path.stem))]


def delete(plan_id: str) -> None:
    (PLANS_DIR / f"{plan_id}.json").unlink(missing_ok=True)


def _write(plan: Plan) -> None:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    (PLANS_DIR / f"{plan.id}.json").write_text(to_json(plan))


def _unique_id(created_at: str) -> str:
    base = "plan_" + re.sub(r"[^0-9T]", "", created_at)
    plan_id, n = base, 2
    while (PLANS_DIR / f"{plan_id}.json").exists():
        plan_id, n = f"{base}_{n}", n + 1
    return plan_id


def _move_legacy_plan() -> None:
    if not LEGACY_PLAN.exists():
        return
    data = json.loads(LEGACY_PLAN.read_text())
    # Its key was "<resume hash>:<comma-separated report ids>".
    resume_hash, _, ids = data["key"].partition(":")
    by_id = {r.id: r for r in reports.list_reports()}
    postings = [
        PostingRef(i, by_id[i].job_title, by_id[i].company) if i in by_id else PostingRef(i, i, "")
        for i in ids.split(",") if i
    ]
    created = datetime.fromtimestamp(LEGACY_PLAN.stat().st_mtime).isoformat(timespec="seconds")
    plan_id = _unique_id(created)
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    (PLANS_DIR / f"{plan_id}.json").write_text(json.dumps({
        "id": plan_id,
        "created_at": created,
        "resume_hash": resume_hash,
        "postings": [p.__dict__ for p in postings],
        "plan": data["plan"],
        "done": data["done"],
    }, indent=2))
    LEGACY_PLAN.unlink()
