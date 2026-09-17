from types import SimpleNamespace

from wagecuck_search.matching import matches
from wagecuck_search.models import SearchCriteria
from wagecuck_search.providers import BrowserProvider
from wagecuck_search.simplify import search_postings, search_response


def record(**changes):
    return {
        "document": {
            "posting_id": "12345678-1234-1234-1234-123456789012",
            "title": "Software Engineer",
            "company_name": "Acme",
            "experience_level": ["Senior"],
            "locations": ["Canada"],
            "travel_requirements": "Remote",
            "type": "Full-Time",
            "min_salary": 150000,
            "max_salary": 210000,
            "currency_type": "CAD",
            "salary_period": 0,
            "sponsors_h1b": True,
            "start_date": 1788453288,
            "updated_date": 1788454276,
            **changes,
        }
    }


def test_response_capture_only_accepts_requested_query_and_page():
    response = SimpleNamespace(
        url="https://js-ha.simplify.jobs/multi_search",
        request=SimpleNamespace(
            post_data_json={"searches": [{"q": "software engineer", "page": 2}]}
        ),
    )
    assert search_response(response, "software engineer", 1)
    assert not search_response(response, "software engineer", 0)
    assert not search_response(response, "*", 1)


def test_public_results_include_promoted_records_and_preserve_explicit_metadata():
    payload = {
        "results": [
            {"hits": [record()]},
            {
                "grouped_hits": [
                    {
                        "hits": [
                            record(
                                posting_id="22345678-1234-1234-1234-123456789012",
                                title="Staff Software Engineer",
                            )
                        ]
                    }
                ]
            },
        ]
    }
    jobs = list(search_postings(payload).values())
    assert len(jobs) == 2
    assert jobs[0].location == "Remote; Canada"
    assert jobs[0].salary.currency == "CAD"
    assert jobs[0].salary.period is None  # Do not guess the meaning of numeric enums.
    assert jobs[0].sponsors_visa is None  # Company H1B history is not a job-level promise.
    assert jobs[0].last_updated is not None
    assert matches(jobs[0], SearchCriteria("software engineer", seniority=("senior", "staff")))[0]


def test_detail_enrichment_retains_listing_experience_and_handles_missing_detail():
    seed = next(iter(search_postings({"results": [{"hits": [record()]}]}).values()))
    assert BrowserProvider._enrich_seed(seed, None).experience_levels == ("senior",)
    assert "Detail could not be parsed" in seed.note


def test_invalid_posting_id_is_not_constructed_into_a_url():
    assert search_postings({"results": [{"hits": [record(posting_id="../bad")]}]}) == {}
