"""AI case review: runs in the background (never inside the request), reads documents, renders the claim map."""
import json
import os
import tempfile
import time
import types

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import AIRun, Client, Document
from tests.seed_demo import seed
from tests.test_workflows import login, token, H

REVIEW = {
    "summary": "Army veteran claiming tinnitus and a knee condition.",
    "recommended_next_step": "Get a nexus opinion for the knee.",
    "conditions": [{
        "name": "Tinnitus", "diagnostic_code": "6260", "claim_type": "new", "strength": 82,
        "elements": {"diagnosis": {"status": "met", "evidence": "audiology note"},
                     "in_service": {"status": "met", "evidence": "flight line duty"},
                     "nexus": {"status": "partial", "evidence": "none"}, "severity": {"status": "met", "evidence": "recurrent"}},
        "supported_rating": 10, "rating_reasoning": "Recurrent tinnitus is rated 10%.",
        "missing_evidence": ["Nexus letter for tinnitus"], "exam_tips": ["Document onset in service"]}],
    "action_plan": [{"title": "Ask Dr. Lee for a nexus letter", "why": "Links tinnitus to service", "owner": "client",
                     "kind": "request_document", "priority": "high"}],
    "gaps": [], "conflicts": [], "deadlines": [], "presumptive": {"eligible": False, "conditions": []},
}


class _Completions:
    def create(self, model, messages, **kw):
        time.sleep(0.3)  # the request must not wait for this
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(REVIEW)))])


class _FakeOpenAI:
    def __init__(self, *a, **k):
        self.chat = types.SimpleNamespace(completions=_Completions())


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
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)


def test_case_review_runs_in_background_and_renders(app):
    db = SessionLocal()
    cid = db.query(Client).filter_by(first_name="Marcus").first().id
    path = os.path.join(tempfile.mkdtemp(), "audiology.txt")
    open(path, "w").write("Audiology consult: veteran reports ringing in both ears since flight line duty in 2009.")
    db.add(Document(org_id=db.get(Client, cid).org_id, client_id=cid, filename="audiology.txt", storage_path=path))
    db.commit(); db.close()

    c, _ = login(app, "employee")
    tok = token(c, f"/ai/client/{cid}")
    t = time.time()
    r = c.post(f"/ai/client/{cid}/run", data={"csrf_token": tok}, headers=H)
    assert r.status_code in (302, 303) and time.time() - t < 0.3, "the request must return before the AI finishes"

    for _ in range(50):
        st = c.get(f"/ai/client/{cid}/status", headers=H).get_json()
        if st["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.1)
    assert st["status"] == "COMPLETED", st
    assert st["docs_read"] >= 1

    html = c.get(f"/ai/client/{cid}", headers=H).get_data(as_text=True)
    assert "Claim map" in html and "Tinnitus" in html and "Get a nexus opinion for the knee." in html
    assert "DC 6260" in html and "Send to client" in html
    db = SessionLocal()
    run = db.query(AIRun).filter_by(client_id=cid, status="COMPLETED").order_by(AIRun.id.desc()).first()
    res = json.loads(run.result_json)
    assert res["conditions"][0]["criteria"], "official criteria are attached server-side"
    db.close()


def test_send_action_to_client(app):
    db = SessionLocal(); cid = db.query(Client).filter_by(first_name="Marcus").first().id; db.close()
    c, _ = login(app, "employee")
    tok = token(c, f"/ai/client/{cid}")
    r = c.post(f"/tasks/client/{cid}/add", data={"title": "Ask Dr. Lee for a nexus letter", "kind": "document",
                                                 "next": f"/ai/client/{cid}", "csrf_token": tok}, headers=H)
    assert r.headers["Location"].endswith(f"/ai/client/{cid}")
    assert "Added" in c.get(f"/ai/client/{cid}", headers=H).get_data(as_text=True)


def test_word_report_includes_claim_map(app):
    db = SessionLocal(); cid = db.query(Client).filter_by(first_name="Marcus").first().id; db.close()
    c, _ = login(app, "employee")
    r = c.get(f"/ai/client/{cid}/cfr-report.docx", headers=H)
    assert r.status_code == 200 and r.data[:2] == b"PK"
    import io, zipfile
    xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode()
    assert "Claim map" in xml and "Tinnitus" in xml
