"""Run comparisons on background threads so they survive page refreshes.

Jobs live in this module's memory for the lifetime of the Streamlit server process, so a
refreshed or newly opened page can reconnect to a comparison that is still running. They
are lost if the server stops.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from . import comparison, resume


@dataclass
class Job:
    key: str
    label: str
    posting_text: str
    status: Literal["running", "done", "error"] = "running"
    thinking: str = ""  # streamed reasoning summary
    answer: str = ""  # streamed JSON answer so far
    error: str = ""
    output: Any = None  # whatever on_success (or a submit_task task) returned
    started_at: float = field(default_factory=time.time)
    stage: Literal["compare", "interview"] = "compare"  # which Claude call is streaming

    def on_event(self, kind: str, text: str) -> None:
        """Collects a streaming Claude call's output; pass it as the call's on_event."""
        if kind == "thinking":
            self.thinking += text
        else:
            self.answer += text

    def start_stage(self, stage: Literal["compare", "interview"]) -> None:
        self.stage, self.thinking, self.answer = stage, "", ""


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="compare")
# Separate from _executor so a check can't wait behind the comparisons that started it.
_auth_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="work-auth")


def submit(
    key: str,
    label: str,
    resume_text: str,
    posting_text: str,
    on_success: Callable[[comparison.ComparisonResult, comparison.WorkAuthorization | None], Any],
) -> Job:
    """Start a comparison and work authorization check unless one with this key is already running.

    on_success runs on the worker thread with both results, typically to save them. The work
    authorization is None if its check failed, so a failed check doesn't lose the comparison.
    """
    with _lock:
        job = _jobs.get(key)
        if job and job.status == "running":
            return job
        job = _jobs[key] = Job(key, label, posting_text)
    _executor.submit(_run, job, resume_text, on_success)
    return job


def _run(job: Job, resume_text: str, on_success) -> None:
    def on_event(kind: str, text: str) -> None:
        if kind == "thinking":
            job.thinking += text
        else:
            job.answer += text

    # U.S. citizens can't be blocked by sponsorship rules, so they skip the check and its cost.
    auth = (
        None
        if resume.is_us_citizen(resume_text)
        else _auth_executor.submit(comparison.check_work_authorization, resume_text, job.posting_text)
    )
    try:
        result = comparison.compare(resume_text, job.posting_text, on_event=on_event)
        try:
            work_authorization = auth.result() if auth else None
        except Exception as e:
            print(f"Work authorization check failed: {comparison.error_message(e) or e!r}")
            work_authorization = None
        job.output = on_success(result, work_authorization)
        job.status = "done"
    except Exception as e:
        job.error = comparison.error_message(e) or f"Unexpected error: {e}"
        job.status = "error"


def submit_task(key: str, label: str, posting_text: str, task: Callable[[Job], Any]) -> Job:
    """Run task(job) on a worker thread unless a job with this key is already running.

    The task streams its Claude calls into the job (job.start_stage, job.on_event) so pages can
    show progress. Its return value becomes job.output.
    """
    with _lock:
        job = _jobs.get(key)
        if job and job.status == "running":
            return job
        job = _jobs[key] = Job(key, label, posting_text)
    _executor.submit(_run_task, job, task)
    return job


def _run_task(job: Job, task: Callable[[Job], Any]) -> None:
    try:
        job.output = task(job)
        job.status = "done"
    except Exception as e:
        job.error = comparison.error_message(e) or f"Unexpected error: {e}"
        job.status = "error"


def get(key: str) -> Job | None:
    return _jobs.get(key)


def with_prefix(prefix: str) -> list[Job]:
    """Jobs whose key starts with prefix, oldest first."""
    with _lock:
        return sorted((j for j in _jobs.values() if j.key.startswith(prefix)), key=lambda j: j.started_at)


def clear(key: str) -> None:
    with _lock:
        _jobs.pop(key, None)
