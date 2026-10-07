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


def test_posting_link(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    pid = db.add_posting("Engineer", "Acme", "body", "https://acme.com/jobs/1")
    assert db.list_postings().iloc[0]["url"] == "https://acme.com/jobs/1"
    db.update_posting_url(pid, "")
    assert db.list_postings().iloc[0]["url"] == ""


def test_old_database_gets_link_column(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE postings (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
        "company TEXT DEFAULT '', body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute("INSERT INTO postings (title, body) VALUES ('Old', 'body')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    assert db.list_postings().iloc[0]["url"] == ""
