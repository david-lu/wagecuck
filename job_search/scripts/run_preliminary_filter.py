"""Filter an input CSV for salary and remote/Los Angeles eligibility."""
import sys

from wagecuck_search.cli import main

LOCATION_PROMPT = (
    "Remote roles explicitly open to workers located in the United States, or roles located "
    "in the Los Angeles metropolitan area. A remote role limited to another country or region "
    "does not match. Remote without evidence that U.S.-based workers are eligible does not "
    "match. Unknown location and unknown remote-country eligibility do not establish a match."
)

if __name__ == "__main__":
    raise SystemExit(main([
        "filter",
        "--min-salary", "180000",
        "--salary-basis", "maximum",
        "--salary-currency", "USD",
        "--salary-period", "year",
        "--include-unknown",
        "--location-prompt",
        LOCATION_PROMPT,
        *sys.argv[1:],
    ]))
