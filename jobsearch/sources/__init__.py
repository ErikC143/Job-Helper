from .base import JobSource
from .example import ExampleSource

# Register new sources here.
SOURCES: list[JobSource] = [ExampleSource()]

__all__ = ["JobSource", "SOURCES"]
