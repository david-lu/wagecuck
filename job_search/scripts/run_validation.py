"""Validate a supplied job CSV and generate fields from the destination pages."""
import sys
from pathlib import Path

from wagecuck_search.cli import main

DEFAULT_FIELDS = Path(__file__).resolve().parents[1] / "examples" / "validation-fields.json"

if __name__ == "__main__":
    raise SystemExit(main(["validate", "--fields", str(DEFAULT_FIELDS), *sys.argv[1:]]))
