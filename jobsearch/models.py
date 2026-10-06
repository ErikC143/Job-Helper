from dataclasses import dataclass
from datetime import date

STATUSES = ["Saved", "Applied", "Interviewing", "Offer", "Rejected", "Withdrawn"]


@dataclass
class Application:
    company: str
    title: str
    status: str = "Saved"
    url: str = ""
    location: str = ""
    notes: str = ""
    date_added: str = ""
    id: int | None = None

    def __post_init__(self):
        if not self.date_added:
            self.date_added = date.today().isoformat()


@dataclass
class JobListing:
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
