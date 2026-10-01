from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Locator

from wagecuck.models import Action

Target = Locator

Write = Callable[[object, Target, Action, str | bool], Awaitable[None]]
Matches = Callable[[object, Target, Action, str | bool], Awaitable[bool]]


@dataclass(frozen=True)
class ControlHandler:
    write: Write
    matches: Matches
    validates_natively: bool = True
    may_change_form: bool = False


def normalize(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


async def native_value_is_valid(target: Target) -> bool:
    return await target.evaluate("e => !e.willValidate || e.validity.valid")