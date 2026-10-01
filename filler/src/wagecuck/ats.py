import json
from pathlib import Path
from urllib.parse import urlsplit

RULES = json.loads(Path(__file__).with_name("ats.json").read_text(encoding="utf-8"))


def detect(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for name, rule in RULES.items():
        if any(host == domain or host.endswith("." + domain) for domain in rule["hosts"]):
            return name
    return "generic"


async def annotate(frame):
    rule = RULES.get(detect(frame.url))
    if not rule:
        return
    for kind, attribute in (("fields", "data-wagecuck-fact"), ("actions", "data-wagecuck-action")):
        for key, selector in rule[kind].items():
            await frame.locator(selector).evaluate_all(
                "(els, a) => els.forEach(e => e.setAttribute(a.attribute, a.key))",
                {"attribute": attribute, "key": key},
            )
