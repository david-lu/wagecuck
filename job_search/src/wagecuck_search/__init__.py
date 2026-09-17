"""Job search with no dependency on wagecuck's application package."""

from .models import JobPosting, Salary, SearchCriteria
from .pipeline import search

__all__ = ["JobPosting", "Salary", "SearchCriteria", "search"]
