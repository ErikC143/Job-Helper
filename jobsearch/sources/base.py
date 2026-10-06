from abc import ABC, abstractmethod

from ..models import JobListing


class JobSource(ABC):
    """Interface every job source plugin implements."""

    name: str = "unnamed"

    @abstractmethod
    def search(self, query: str, location: str = "") -> list[JobListing]:
        """Return listings matching the query."""
