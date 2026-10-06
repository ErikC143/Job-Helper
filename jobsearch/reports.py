"""Save and load comparison reports as JSON files in inputs/reports/.

Each report is one file:
    {"id", "created_at", "job_title", "company", "inputs_hash", "resume_hash",
     "result": {...}, "explanations": [...], "work_authorization": {...} or null}
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jobsearch.comparison import ComparisonResult, RequirementExplanation, WorkAuthorization

REPORTS_DIR = Path(__file__).parent.parent / "inputs" / "reports"


@dataclass
class Report:
    id: str
    created_at: str
    job_title: str
    company: str
    inputs_hash: str
    result: ComparisonResult
    resume_hash: str = ""  # empty for reports saved before this was recorded
    explanations: list[RequirementExplanation] = field(default_factory=list)  # empty until requested
    work_authorization: WorkAuthorization | None = None  # None if the check didn't run

    @property
    def label(self) -> str:
        return f"{self.created_at[:16].replace('T', ' ')} · {self.job_title} at {self.company}"


def inputs_hash(resume: str, posting: str) -> str:
    return hashlib.sha256(f"{resume}\0{posting}".encode()).hexdigest()


def save(
    result: ComparisonResult,
    key: str,
    resume_hash: str = "",
    work_authorization: WorkAuthorization | None = None,
) -> Report:
    now = datetime.now().isoformat(timespec="seconds")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{result.company} {result.job_title}".lower()).strip("-")[:60]
    report = Report(
        id=f"{now.replace(':', '')}_{slug}",
        created_at=now,
        job_title=result.job_title,
        company=result.company,
        inputs_hash=key,
        result=result,
        resume_hash=resume_hash,
        work_authorization=work_authorization,
    )
    _write(report)
    return report


def save_explanations(report: Report, explanations: list[RequirementExplanation]) -> None:
    report.explanations = explanations
    _write(report)


def save_work_authorization(report: Report, work_authorization: WorkAuthorization) -> None:
    report.work_authorization = work_authorization
    _write(report)


def _write(report: Report) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        **report.__dict__,
        "result": report.result.model_dump(),
        "explanations": [e.model_dump() for e in report.explanations],
        "work_authorization": report.work_authorization and report.work_authorization.model_dump(),
    }
    (REPORTS_DIR / f"{report.id}.json").write_text(json.dumps(data, indent=2))


def load(report_id: str) -> Report | None:
    path = REPORTS_DIR / f"{report_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    data["result"] = ComparisonResult.model_validate(data["result"])
    data["explanations"] = [RequirementExplanation.model_validate(e) for e in data.get("explanations", [])]
    if auth := data.get("work_authorization"):
        data["work_authorization"] = WorkAuthorization.model_validate(auth)
    return Report(**data)


def list_reports() -> list[Report]:
    """All saved reports, newest first."""
    if not REPORTS_DIR.exists():
        return []
    paths = sorted(REPORTS_DIR.glob("*.json"), reverse=True)
    return [r for p in paths if (r := load(p.stem))]


def delete(report_id: str) -> None:
    (REPORTS_DIR / f"{report_id}.json").unlink(missing_ok=True)
