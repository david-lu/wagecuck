"""CapSolver transport is separate from Playwright detection and token delivery."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

from .models import ApplicationError, Code


@dataclass
class Challenge:
    kind: str
    site_key: str
    frame: int
    website_url: str
    callback: str = ""
    action: str = ""
    cdata: str = ""
    enterprise: bool = False
    invisible: bool = False
    data_s: str = ""

    def task(self):
        if self.kind == "recaptcha_v2":
            task = {
                "type": "ReCaptchaV2EnterpriseTaskProxyLess"
                if self.enterprise
                else "ReCaptchaV2TaskProxyLess",
                "websiteURL": self.website_url,
                "websiteKey": self.site_key,
                "isInvisible": self.invisible,
            }
            if self.action:
                task["pageAction"] = self.action
            if self.data_s:
                task["enterprisePayload" if self.enterprise else "recaptchaDataSValue"] = (
                    {"s": self.data_s} if self.enterprise else self.data_s
                )
            return task
        if self.kind == "turnstile":
            return {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": self.website_url,
                "websiteKey": self.site_key,
                "metadata": {
                    k: v for k, v in {"action": self.action, "cdata": self.cdata}.items() if v
                },
            }
        raise ApplicationError(Code.CAPTCHA_UNSUPPORTED, f"Unsupported CAPTCHA type: {self.kind}.")


class CapSolver:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 90,
        poll_interval: float = 2,
        transport=None,
    ):
        self.api_key = api_key or os.environ.get("CAPSOLVER_API_KEY", "")
        self.timeout, self.poll_interval, self.transport = timeout, poll_interval, transport

    def validate(self, challenge: Challenge):
        challenge.task()
        if not self.api_key:
            raise ApplicationError(
                Code.CAPTCHA_KEY_MISSING, "Set CAPSOLVER_API_KEY to enable CAPTCHA solving."
            )

    async def solve(self, challenge: Challenge) -> str:
        self.validate(challenge)

        async def request(client, method, **body):
            response = await client.post(
                f"https://api.capsolver.com/{method}", json={"clientKey": self.api_key, **body}
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Expected a CapSolver response object")
            if data.get("errorId") or data.get("status") == "failed":
                code = str(data.get("errorCode", "UNKNOWN"))
                safe_code = code if re.fullmatch(r"[A-Z_]{1,80}", code) else "UNKNOWN"
                raise ApplicationError(Code.CAPTCHA_SOLVE_FAILED, f"CapSolver error: {safe_code}.")
            return data

        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                    timeout=20, transport=self.transport, trust_env=False
                ) as client:
                    data = await request(client, "createTask", task=challenge.task())
                    task_id = data.get("taskId")
                    for _ in range(60):
                        if data.get("status") == "ready":
                            solution = data.get("solution", {})
                            if not isinstance(solution, dict):
                                raise TypeError("Expected a CapSolver solution object")
                            token = solution.get("gRecaptchaResponse") or solution.get("token")
                            if not isinstance(token, str) or not token:
                                raise ApplicationError(
                                    Code.CAPTCHA_SOLVE_FAILED, "CapSolver returned no token."
                                )
                            return token
                        if not task_id:
                            raise ApplicationError(
                                Code.CAPTCHA_SOLVE_FAILED, "CapSolver returned no task ID."
                            )
                        await asyncio.sleep(self.poll_interval)
                        data = await request(client, "getTaskResult", taskId=task_id)
            raise ApplicationError(Code.CAPTCHA_TIMEOUT, "CapSolver polling limit reached.")
        except TimeoutError as exc:
            raise ApplicationError(Code.CAPTCHA_TIMEOUT, "CapSolver task timed out.") from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ApplicationError(
                Code.CAPTCHA_SOLVE_FAILED, "CapSolver request failed or returned malformed data."
            ) from exc


async def detect_challenge(page) -> Challenge | None:
    for index, frame in enumerate(page.frames):
        widgets = await frame.locator(
            ".g-recaptcha[data-sitekey], .cf-turnstile[data-sitekey], .h-captcha[data-sitekey]"
        ).evaluate_all(
            """els => els.map(e => ({kind:e.classList.contains('g-recaptcha') ? 'recaptcha_v2' : e.classList.contains('cf-turnstile') ? 'turnstile' : 'hcaptcha', key:e.dataset.sitekey, callback:e.dataset.callback || '', action:e.dataset.action || '', cdata:e.dataset.cdata || '', invisible:e.dataset.size === 'invisible', data_s:e.dataset.s || ''}))"""
        )
        for widget in widgets:
            return Challenge(
                widget["kind"],
                widget["key"],
                index,
                frame.url,
                callback=widget["callback"],
                action=widget["action"],
                cdata=widget["cdata"],
                invisible=widget["invisible"],
                data_s=widget["data_s"],
            )
        for src in await frame.locator('iframe[src*="recaptcha"][src*="anchor"]').evaluate_all(
            "els => els.map(e => e.src)"
        ):
            query = parse_qs(urlsplit(src).query)
            if query.get("k"):
                return Challenge(
                    "recaptcha_v2",
                    query["k"][0],
                    index,
                    frame.url,
                    enterprise="/enterprise/" in src,
                    invisible=query.get("size") == ["invisible"],
                    action=query.get("sa", [""])[0],
                    data_s=query.get("s", [""])[0],
                )
    return None


async def deliver_token(page, challenge: Challenge, token: str):
    frame = page.frames[challenge.frame]
    name = "cf-turnstile-response" if challenge.kind == "turnstile" else "g-recaptcha-response"
    delivered = await frame.evaluate(
        """({name, token, callback}) => {
      let count = 0;
      for (const el of document.getElementsByName(name)) {
        el.value = token;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true})); count++;
      }
      // Only an explicitly named callback, never arbitrary functions in vendor internals.
      if (callback && /^[a-zA-Z_$][\\w$]*(\\.[a-zA-Z_$][\\w$]*)*$/.test(callback)) {
        let fn = window;
        for (const key of callback.split('.')) fn = fn?.[key];
        if (typeof fn === 'function') { fn(token); count++; }
      }
      return count;
    }""",
        {"name": name, "token": token, "callback": challenge.callback},
    )
    if not delivered:
        raise ApplicationError(
            Code.CAPTCHA_UNSUPPORTED,
            "No supported CAPTCHA response field or named callback was found.",
        )
