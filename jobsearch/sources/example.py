from ..models import JobListing
from .base import JobSource


class ExampleSource(JobSource):
    """Returns static fake data. Copy this file to build a real source."""

    name = "Example (fake data)"

    def search(self, query: str, location: str = "") -> list[JobListing]:
        return [
            JobListing(
                title=f"{query or 'Software'} Engineer",
                company="Example Corp",
                url="https://example.com/jobs/1",
                location=location or "Remote",
                description="Placeholder listing from the example source.",
            )
        ]
