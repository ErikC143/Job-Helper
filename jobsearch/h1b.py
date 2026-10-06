"""Look up an employer's H-1B petition history in the USCIS H-1B Employer Data Hub.

https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub

The yearly CSV files are downloaded once into data/ (git-ignored) and searched locally.
"""

import csv
import re
import urllib.request
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
URL = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
# The newest fiscal years USCIS has published as CSV files.
YEARS = (2021, 2022, 2023)
SOURCE = f"USCIS H-1B Employer Data Hub, FY{YEARS[0]}–FY{YEARS[-1]}"

# Words that don't help tell employers apart, dropped before matching names.
IGNORED_WORDS = {
    "THE", "A", "AN", "OF", "AND", "INC", "INCORPORATED", "LLC", "LLP", "LP", "LTD", "LIMITED",
    "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "PC", "GROUP", "HOLDINGS", "DBA", "USA", "US",
}


class H1BEmployer(BaseModel):
    """One employer's petitions, summed over every year and office it filed from."""

    employer: str
    locations: list[str]
    years: list[int]
    initial_approvals: int  # new H-1B employment, e.g. a student's first H-1B after OPT
    initial_denials: int
    continuing_approvals: int  # extensions, amendments, and transfers from other employers
    continuing_denials: int

    @property
    def approvals(self) -> int:
        return self.initial_approvals + self.continuing_approvals


def _words(name: str) -> list[str]:
    return [w for w in re.sub(r"[^A-Z0-9]+", " ", name.upper()).split() if w not in IGNORED_WORDS]


def _download(year: int) -> Path:
    path = DATA_DIR / f"h1b_datahubexport-{year}.csv"
    if not path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(URL.format(year=year), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        path.write_bytes(data)
    return path


@lru_cache(maxsize=1)
def _rows() -> list[dict]:
    rows = []
    for year in YEARS:
        with _download(year).open(encoding="utf-8", errors="replace", newline="") as f:
            rows += csv.DictReader(f)
    return rows


def search(names: list[str], limit: int = 15) -> list[H1BEmployer]:
    """Employers whose name contains every distinctive word of one of names, most approvals first.

    Downloads the data files on first use, so this can raise URLError.
    """
    queries = [set(words) for name in names if (words := _words(name))]
    found: dict[str, H1BEmployer] = {}
    for row in _rows():
        name = (row["Employer"] or "").strip()
        if not name or not any(q <= set(_words(name)) for q in queries):
            continue
        e = found.setdefault(name, H1BEmployer(
            employer=name, locations=[], years=[], initial_approvals=0, initial_denials=0,
            continuing_approvals=0, continuing_denials=0,
        ))
        e.initial_approvals += int(row["Initial Approval"] or 0)
        e.initial_denials += int(row["Initial Denial"] or 0)
        e.continuing_approvals += int(row["Continuing Approval"] or 0)
        e.continuing_denials += int(row["Continuing Denial"] or 0)
        if (year := int(row["Fiscal Year"])) not in e.years:
            e.years.append(year)
        location = ", ".join(p for p in (row["City"].title(), row["State"]) if p)
        if location and location not in e.locations:
            e.locations.append(location)
    return sorted(found.values(), key=lambda e: -e.approvals)[:limit]
