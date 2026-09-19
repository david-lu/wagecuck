"""Discover software engineer jobs; override the query or sources with CLI options."""
import sys

from wagecuck_search.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["search", "--query", "software engineer", *sys.argv[1:]]))
