"""Filter an input CSV for salary and remote/Los Angeles eligibility."""
import sys

from wagecuck_search.cli import main

if __name__ == "__main__":
    raise SystemExit(main([
        "filter",
        "--min-salary", "180000",
        "--salary-basis", "maximum",
        "--salary-currency", "USD",
        "--salary-period", "year",
        "--include-unknown",
        "--location-prompt",
        "Remote or in the Los Angeles metropolitan area. "
        "Unknown location and unknown remote status do not establish a match.",
        *sys.argv[1:],
    ]))
