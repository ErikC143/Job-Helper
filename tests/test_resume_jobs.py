import time

from jobsearch import comparison, jobs, resume


def test_resume_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, "INPUTS_DIR", tmp_path)
    monkeypatch.setattr(resume, "RESUME_TXT", tmp_path / "resume.txt")
    monkeypatch.setattr(resume, "LEGACY_PDF_DIR", tmp_path / "resume")
    assert resume.load() == ""
    resume.save_upload("cv.txt", b"  Jane Doe\nPython  \n")
    assert resume.load() == "Jane Doe\nPython"


def test_job_runs_in_background_and_saves(monkeypatch):
    def fake_compare(resume_text, posting_text, on_event=None):
        on_event("thinking", "hmm")
        time.sleep(0.2)
        return "RESULT"

    monkeypatch.setattr(comparison, "compare", fake_compare)
    monkeypatch.setattr(comparison, "check_work_authorization", lambda resume_text, posting_text: "AUTH")
    saved = []
    job = jobs.submit("test:1", "Job", "resume", "posting", on_success=lambda r, a: saved.append((r, a)) or "ID")
    assert jobs.submit("test:1", "Job", "resume", "posting", on_success=None) is job  # no duplicate
    for _ in range(50):
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done" and job.output == "ID" and saved == [("RESULT", "AUTH")]
    assert job.thinking == "hmm"
    jobs.clear("test:1")


def test_failed_work_authorization_check_keeps_comparison(monkeypatch):
    def failing_check(resume_text, posting_text):
        raise RuntimeError("boom")

    monkeypatch.setattr(comparison, "compare", lambda resume_text, posting_text, on_event=None: "RESULT")
    monkeypatch.setattr(comparison, "check_work_authorization", failing_check)
    saved = []
    job = jobs.submit("test:2", "Job", "resume", "posting", on_success=lambda r, a: saved.append((r, a)))
    for _ in range(50):
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done" and saved == [("RESULT", None)]
    jobs.clear("test:2")


def test_candidate_notes_live_in_resume_file_and_survive_new_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, "INPUTS_DIR", tmp_path)
    monkeypatch.setattr(resume, "RESUME_TXT", tmp_path / "resume.txt")
    monkeypatch.setattr(resume, "LEGACY_PDF_DIR", tmp_path / "resume")
    resume.save_upload("cv.txt", b"Jane Doe\nPython")
    assert resume.split_notes(resume.load()) == ("Jane Doe\nPython", "", "")

    resume.save_notes("  Graduating June 2027  ")
    assert resume.load() == f"Jane Doe\nPython\n\n{resume.NOTES_HEADER}\nGraduating June 2027"

    # The work authorization is the first line of the notes section, and each part saves on its own.
    resume.save_notes(work_authorization="F-1 student visa (CPT / OPT / STEM OPT)")
    assert resume.load() == (
        f"Jane Doe\nPython\n\n{resume.NOTES_HEADER}\n"
        "Work authorization: F-1 student visa (CPT / OPT / STEM OPT)\n\nGraduating June 2027"
    )

    resume.save_upload("cv2.txt", b"Jane Doe\nRust")
    assert resume.split_notes(resume.load()) == (
        "Jane Doe\nRust", "F-1 student visa (CPT / OPT / STEM OPT)", "Graduating June 2027",
    )

    resume.save_notes(notes="")
    assert resume.split_notes(resume.load()) == ("Jane Doe\nRust", "F-1 student visa (CPT / OPT / STEM OPT)", "")
    resume.save_notes(work_authorization="")
    assert resume.load() == "Jane Doe\nRust"


def test_us_citizens_skip_the_work_authorization_check(monkeypatch):
    def check_should_not_run(resume_text, posting_text):
        raise AssertionError("work authorization check ran for a U.S. citizen")

    monkeypatch.setattr(comparison, "compare", lambda resume_text, posting_text, on_event=None: "RESULT")
    monkeypatch.setattr(comparison, "check_work_authorization", check_should_not_run)
    citizen = f"Jane Doe\n\n{resume.NOTES_HEADER}\n{resume.WORK_AUTH_PREFIX}{resume.US_CITIZEN}"
    saved = []
    job = jobs.submit("test:3", "Job", citizen, "posting", on_success=lambda r, a: saved.append((r, a)))
    for _ in range(50):
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done" and saved == [("RESULT", None)]
    jobs.clear("test:3")


def test_is_us_citizen_only_when_picked():
    def with_status(status: str) -> str:
        return f"Jane Doe\n\n{resume.NOTES_HEADER}\n{resume.WORK_AUTH_PREFIX}{status}"

    assert resume.is_us_citizen(with_status("U.S. citizen"))
    assert not resume.is_us_citizen(with_status("U.S. permanent resident (green card)"))
    assert not resume.is_us_citizen("Jane Doe")  # not set
