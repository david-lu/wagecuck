"""Filter a supplied CSV for frontend/full-stack roles and interpreted web stacks."""
import sys
from pathlib import Path

from wagecuck_search.cli import main

DEFAULT_FILTERS = Path(__file__).resolve().parents[1] / "examples" / "web-filters.json"

if __name__ == "__main__":
    raise SystemExit(main(["filter", "--filters", str(DEFAULT_FILTERS), *sys.argv[1:]]))
