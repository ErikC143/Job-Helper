from jobsearch import db
from jobsearch.models import Application


def test_add_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.add_application(Application("Acme", "Engineer"))
    df = db.list_applications()
    assert len(df) == 1
    assert df.iloc[0]["company"] == "Acme"


def test_postings(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    pid = db.add_posting("Engineer", "Acme", "Requirements:\n- Python")
    assert db.list_postings().iloc[0]["title"] == "Engineer"
    db.delete_posting(pid)
    assert db.list_postings().empty
