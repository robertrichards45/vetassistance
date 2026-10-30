"""Make every OpenAI chat call work across model generations.

Newer models reject settings older code passes (a custom temperature, max_tokens, sometimes
response_format). Instead of changing every call site, wrap Completions.create once: when the API
says a parameter is unsupported, drop or rename it and retry.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("vetassistance")
_DROPPABLE = ("temperature", "top_p", "presence_penalty", "frequency_penalty", "response_format", "logprobs", "seed")


def _bad_param(exc: Exception) -> str | None:
    p = getattr(exc, "param", None)
    if p:
        return str(p)
    m = re.search(r"'(temperature|top_p|max_tokens|response_format|presence_penalty|frequency_penalty|logprobs|seed)'", str(exc))
    return m.group(1) if m else None


def install() -> None:
    try:
        import openai
        from openai.resources.chat.completions import Completions
    except Exception:
        return
    if getattr(Completions.create, "_va_compat", False):
        return
    original = Completions.create

    def create(self, *args, **kwargs):
        for _ in range(4):
            try:
                return original(self, *args, **kwargs)
            except openai.BadRequestError as exc:
                p = _bad_param(exc)
                if p == "max_tokens" and "max_tokens" in kwargs:
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                elif p in _DROPPABLE and p in kwargs:
                    kwargs.pop(p)
                else:
                    raise
                log.info("OpenAI model rejected %s; retrying without it", p)
        return original(self, *args, **kwargs)

    create._va_compat = True
    Completions.create = create
