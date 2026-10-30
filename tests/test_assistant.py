"""Ask AI: page renders, streaming answer is returned and saved, old dashboard form still works."""
import re
import types

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import User
from tests.seed_demo import seed
from tests.test_workflows import login, token, H


class _FakeCompletions:
    def create(self, model, messages, stream=False, **kw):
        self.last = messages
        text = "- Get the **audiogram**\n- File VA Form 21-526EZ"
        if stream:
            return [types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=t))]) for t in (text[:10], text[10:])]
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


class _FakeOpenAI:
    def __init__(self, api_key=None):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions())


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config.update(TESTING=True, OPENAI_API_KEY="test-key")
    from app.extensions import limiter
    limiter.enabled = False
    with a.app_context():
        seed()
    return a


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)


def test_ask_ai_stream_and_history(app):
    c, _ = login(app, "employee")
    assert c.get("/assistant", headers=H).status_code == 200
    tok = token(c, "/assistant")
    r = c.post("/assistant/stream", data={"question": "What does a tinnitus claim need?", "csrf_token": tok}, headers=H)
    assert r.status_code == 200 and "audiogram" in r.get_data(as_text=True)
    html = c.get("/assistant", headers=H).get_data(as_text=True)
    assert "What does a tinnitus claim need?" in html and "audiogram" in html


def test_ask_ai_with_client_context_and_fallback(app):
    c, _ = login(app, "director")
    tok = token(c, "/assistant")
    r = c.post("/assistant/ask", data={"question": "Next step?", "client_id": "1", "csrf_token": tok}, headers=H)
    assert r.status_code in (302, 303)
    assert "(about Marcus Reed)" in c.get("/assistant", headers=H).get_data(as_text=True)


def test_old_dashboard_guidance_form_still_works(app):
    c, _ = login(app, "employee")
    tok = token(c, "/employee")
    r = c.post("/employee/guidance", data={"question": "Hello?", "csrf_token": tok}, headers=H, follow_redirects=True)
    assert r.status_code == 200 and "Ask AI" in r.get_data(as_text=True)


def test_clients_cannot_use_ask_ai(app):
    c, _ = login(app, "client")
    assert c.get("/assistant", headers=H).status_code in (302, 403)
