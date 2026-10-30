"""Condition library: index, condition page, official section text, AI guide (background), write-up stream and save."""
import json
import time
import types

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import ConditionGuide, Letter
from tests.seed_demo import seed
from tests.test_workflows import login, token, H

GUIDE = {"overview": "Knee flexion is rated by degrees of motion.", "levels": [{"percent": 10, "in_plain_english": "Bends to 45 degrees.",
         "evidence_that_supports_it": ["goniometer reading"]}], "exam_checklist": ["Measure flexion with a goniometer"],
         "common_secondary_conditions": ["Hip strain"], "common_mistakes": ["No flare-up estimate"], "writeup_tips": ["Cite 4.59"]}


class _C:
    def create(self, model, messages, stream=False, **kw):
        if stream:
            return [types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=t))]) for t in ("The evidence ", "supports 10%.")]
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(GUIDE)))])


class _AI:
    def __init__(self, *a, **k):
        self.chat = types.SimpleNamespace(completions=_C())


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config.update(TESTING=True, OPENAI_API_KEY="test")
    from app.extensions import limiter
    limiter.enabled = False
    with a.app_context():
        seed()
    return a


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", _AI)


def test_index_condition_and_section(app):
    c, _ = login(app, "employee")
    assert "Condition library" in c.get("/library", headers=H).get_data(as_text=True)
    html = c.get("/library/5260", headers=H).get_data(as_text=True)
    assert "Leg, limitation of flexion of" in html and "Flexion limited to 45" in html and "38 CFR 4.59" in html
    sec = c.get("/library/section/4.71a", headers=H).get_data(as_text=True)
    assert 'id="dc-5260"' in sec and "<script" not in sec.split('class="ws-card reg-text"')[1].split("</article>")[0]
    assert c.get("/library/9999", headers=H).status_code == 404
    assert c.get("/library/section/../../etc", headers=H).status_code == 404


def test_guide_generates_in_background(app):
    c, _ = login(app, "employee")
    tok = token(c, "/library/5260")
    c.post("/library/5260/guide", data={"csrf_token": tok}, headers=H)
    for _ in range(40):
        st = c.get("/library/5260/guide/status", headers=H).get_json()
        if st["status"] in ("READY", "FAILED"):
            break
        time.sleep(0.1)
    assert st["status"] == "READY", st
    assert "Measure flexion with a goniometer" in c.get("/library/5260", headers=H).get_data(as_text=True)


def test_writeup_stream_and_save(app):
    c, _ = login(app, "director")
    tok = token(c, "/library/5260")
    r = c.post("/library/5260/writeup", data={"csrf_token": tok, "kind": "analysis", "target": "10%", "facts": "flexion 40", "client_id": "1"}, headers=H)
    assert "supports 10%" in r.get_data(as_text=True)
    r = c.post("/library/5260/save", data={"csrf_token": tok, "client_id": "1", "body": "Draft text", "kind": "analysis"}, headers=H)
    assert r.get_json()["ok"]
    db = SessionLocal()
    l = db.query(Letter).filter(Letter.title.like("Rating analysis%")).first()
    assert l and l.is_visible_to_client is False
    db.close()


def test_clients_cannot_open_library(app):
    c, _ = login(app, "client")
    assert c.get("/library", headers=H).status_code in (302, 403)
