import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from .models import Application

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobsearch.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Saved',
    url TEXT DEFAULT '',
    location TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    date_added TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT DEFAULT '',
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    url TEXT NOT NULL DEFAULT ''
);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        _add_missing_columns(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Upgrade databases created by older versions of the app."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(postings)")}
    if "url" not in columns:
        conn.execute("ALTER TABLE postings ADD COLUMN url TEXT NOT NULL DEFAULT ''")


def add_application(app: Application) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications (company, title, status, url, location, notes, date_added)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (app.company, app.title, app.status, app.url, app.location, app.notes, app.date_added),
        )
        return cur.lastrowid


def list_applications() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM applications ORDER BY date_added DESC, id DESC", conn)


def update_status(app_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, app_id))


def delete_application(app_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))


def add_posting(title: str, company: str, body: str, url: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO postings (title, company, body, url) VALUES (?, ?, ?, ?)", (title, company, body, url)
        )
        return cur.lastrowid


def update_posting_url(posting_id: int, url: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE postings SET url = ? WHERE id = ?", (url, posting_id))


def list_postings() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM postings ORDER BY created_at DESC, id DESC", conn)


def delete_posting(posting_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM postings WHERE id = ?", (posting_id,))

