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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


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


def add_posting(title: str, company: str, body: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO postings (title, company, body) VALUES (?, ?, ?)", (title, company, body)
        )
        return cur.lastrowid


def list_postings() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM postings ORDER BY created_at DESC, id DESC", conn)


def delete_posting(posting_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM postings WHERE id = ?", (posting_id,))

