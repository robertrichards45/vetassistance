"""Newer OpenAI models reject some settings; the wrapper drops them and retries."""
import httpx
import openai
from openai.resources.chat.completions import Completions

from app.services import ai_compat


def _bad(param):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": f"Unsupported value: '{param}' does not support 0.2 with this model.", "param": param, "type": "invalid_request_error"}})
    return openai.BadRequestError(f"Unsupported value: '{param}'", response=resp, body={"param": param})


def test_retries_without_rejected_params(monkeypatch):
    calls = []

    def fake(self, *a, **kw):
        calls.append(dict(kw))
        if "temperature" in kw:
            raise _bad("temperature")
        if "response_format" in kw:
            raise _bad("response_format")
        return "ok"

    monkeypatch.setattr(Completions, "create", fake)
    ai_compat.install()
    assert Completions.create(None, model="m", messages=[], temperature=0.2, response_format={"type": "json_object"}) == "ok"
    assert len(calls) == 3 and "temperature" not in calls[-1] and "response_format" not in calls[-1]


def test_other_errors_still_raise(monkeypatch):
    def fake(self, *a, **kw):
        raise _bad("messages")
    monkeypatch.setattr(Completions, "create", fake)
    ai_compat.install()
    try:
        Completions.create(None, model="m", messages=[])
        assert False
    except openai.BadRequestError:
        pass
