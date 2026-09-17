import asyncio

from wagecuck_search.ats_apis import AtsApiVerifier, api_target


def test_public_ats_api_targets_are_derived_from_hosted_posting_urls():
    assert api_target("https://job-boards.greenhouse.io/acme/jobs/123") == (
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?questions=true",
    )
    assert api_target("https://jobs.lever.co/acme/abc/apply") == (
        "lever",
        "https://api.lever.co/v0/postings/acme/abc",
    )
    assert api_target("https://jobs.eu.lever.co/acme/abc") == (
        "lever",
        "https://api.eu.lever.co/v0/postings/acme/abc",
    )
    assert api_target("https://jobs.ashbyhq.com/acme/abc/application") == (
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/acme",
    )
    assert api_target("https://jobs.smartrecruiters.com/Acme/12345-senior-engineer") == (
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/Acme/postings/12345",
    )


def test_ashby_board_response_is_shared_across_postings():
    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "jobs": [
                    {
                        "title": "Software Engineer",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/one",
                        "applyUrl": "https://jobs.ashbyhq.com/acme/one/application",
                    },
                    {
                        "title": "Frontend Engineer",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/two",
                        "applyUrl": "https://jobs.ashbyhq.com/acme/two/application",
                    },
                ]
            }

        async def aclose(self):
            pass

    class Client:
        def __init__(self):
            self.calls = 0

        async def get(self, url):
            self.calls += 1
            await asyncio.sleep(0)
            return Response()

    async def scenario():
        client = Client()
        verifier = AtsApiVerifier(client)
        one, two = await asyncio.gather(
            verifier.verify("https://jobs.ashbyhq.com/acme/one"),
            verifier.verify("https://jobs.ashbyhq.com/acme/two/application"),
        )
        assert one.title == "Software Engineer"
        assert two.title == "Frontend Engineer"
        assert client.calls == 1

    asyncio.run(scenario())
