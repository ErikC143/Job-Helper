from jobsearch import chat, db, interview, plans, reports, resume


def use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "CHAT_FILE", tmp_path / "chat.json")
    monkeypatch.setattr(chat, "DRAFT_TEXT", tmp_path / "posting.txt")
    monkeypatch.setattr(chat, "DRAFT_PDF_DIR", tmp_path / "posting")
    monkeypatch.setattr(resume, "RESUME_TXT", tmp_path / "resume.txt")
    monkeypatch.setattr(resume, "LEGACY_PDF_DIR", tmp_path / "resume")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(plans, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(plans, "LEGACY_PLAN", tmp_path / "plan.json")
    monkeypatch.setattr(interview, "INTERVIEWS_DIR", tmp_path / "interviews")


def test_history_round_trip(tmp_path, monkeypatch):
    use_tmp(tmp_path, monkeypatch)
    assert chat.load_history() == []
    chat.save_history([{"role": "user", "content": "hi"}])
    assert chat.load_history() == [{"role": "user", "content": "hi"}]
    chat.clear_history()
    assert chat.load_history() == []


def test_context_includes_materials(tmp_path, monkeypatch):
    use_tmp(tmp_path, monkeypatch)
    assert chat.build_context()[1] == "nothing yet"

    (tmp_path / "resume.txt").write_text("Jane Doe, Python engineer")
    (tmp_path / "posting.txt").write_text("Draft: Data Analyst")
    db.add_posting("Engineer", "Acme", "Requirements:\n- Python")
    context, described = chat.build_context()
    assert "Jane Doe" in context and "Draft: Data Analyst" in context and 'company="Acme"' in context
    assert described == "your resume, your draft posting, 1 saved posting"


def test_context_leaves_out_what_does_not_fit(tmp_path, monkeypatch):
    use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(chat, "MAX_CONTEXT_CHARS", 300)
    (tmp_path / "resume.txt").write_text("Jane Doe")
    db.add_posting("Engineer", "Acme", "x" * 1000)
    context, described = chat.build_context()
    assert "Jane Doe" in context and "xxx" not in context
    assert "1 older item left out" in described
