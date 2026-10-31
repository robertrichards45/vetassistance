import io
import re

import pytest

from app import create_app
from app.services import decision_letter as dl

HTTPS = {"X-Forwarded-Proto": "https", "Host": "www.vetassistance.org", "X-Forwarded-For": "203.0.113.50"}
LETTER = ("Department of Veterans Affairs. We made a decision on your claim for service connection. "
          "Tinnitus is granted with an evaluation of 10 percent effective March 2, 2026. "
          "Service connection for left knee strain is denied because the evidence does not show a link to service. "
          "Your SSN 123-45-6789 appears here. ") * 3


def test_normalize_adds_deadline_and_cleans():
    raw = {"decision_date": "2026-04-10", "summary": "One granted, one denied.", "items": [
        {"condition": "Tinnitus", "outcome": "Granted", "percent": "10%", "reason": "Noise exposure."},
        {"condition": "", "outcome": "denied"}, {"condition": "Knee", "outcome": "weird"}],
        "next_steps": [{"option": "Supplemental Claim", "fits_when": "Add a nexus opinion."}]}
    out = dl.normalize(raw)
    assert out["review_deadline"] == "April 10, 2027"
    assert [i["outcome"] for i in out["decisions"]] == ["granted", "other"]


def test_clean_text_removes_ssn():
    assert "123-45-6789" not in dl.clean_text(LETTER)


@pytest.fixture()
def client(monkeypatch):
    app = create_app(); app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, OPENAI_API_KEY="test", RATELIMIT_ENABLED=False)
    seen = {}
    def fake(system, user):
        seen["user"] = user
        return {"decision_date": "2026-04-10", "summary": "VA granted tinnitus and denied the knee.",
                "items": [{"condition": "Tinnitus", "outcome": "granted", "percent": "10%", "reason": "In-service noise."}],
                "next_steps": [{"option": "Supplemental Claim", "fits_when": "Add a medical opinion for the knee."}]}
    monkeypatch.setattr("app.services.claim_builder._ai_json", fake)
    return app.test_client(), seen


def test_explainer_page_and_result(client):
    c, seen = client
    assert c.get("/tools/decision-letter", headers=HTTPS).status_code == 200
    r = c.post("/tools/decision-letter", data={"letter_text": LETTER}, headers=HTTPS)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Tinnitus" in html and "April 10, 2027" in html
    assert "123-45-6789" not in seen["user"]


def test_explainer_rejects_non_letters(client):
    c, _ = client
    r = c.post("/tools/decision-letter", data={"letter_text": "hello"}, headers=HTTPS)
    assert r.status_code == 302


def test_itf_reminders_flow(client):
    from datetime import date, timedelta
    from app.services import itf_reminders
    from app.extensions import SessionLocal
    from app.models.itf_reminder import ItfReminder
    c, _ = client
    sent = []
    filed = date.today() - timedelta(days=300)
    assert c.get("/tools/intent-to-file", headers=HTTPS).status_code == 200
    r = itf_reminders.create("vet@example.test", filed, "https://x.test", lambda *a: sent.append(a))
    assert "reminder is set" in sent[0][1]
    deadline = itf_reminders.deadline(filed)
    assert itf_reminders.run_due(deadline - timedelta(days=59), "https://x.test", lambda *a: sent.append(a)) >= 1
    assert "expires in 59 days" in sent[-1][1] and "/tools/intent-to-file/stop/" in sent[-1][2]
    before = len(sent)
    itf_reminders.run_due(deadline - timedelta(days=58), "https://x.test", lambda *a: sent.append(a))
    assert len([s for s in sent[before:] if s[0] == "vet@example.test"]) == 0  # no repeat until the 30-day mark
    assert c.get(f"/tools/intent-to-file/stop/{r.token}", headers=HTTPS).status_code == 302
    before = len(sent)
    itf_reminders.run_due(deadline - timedelta(days=6), "https://x.test", lambda *a: sent.append(a))
    assert len([s for s in sent[before:] if s[0] == "vet@example.test"]) == 0  # stopped


def test_itf_form_rejects_old_dates(client):
    c, _ = client
    r = c.post("/tools/intent-to-file", data={"email": "a@b.test", "filed_on": "2020-01-01"}, headers=HTTPS)
    assert r.status_code == 302


def test_cp_prep_sheets(client):
    c, _ = client
    assert "Choose a condition" in c.get("/tools/cp-exam-prep", headers=HTTPS).get_data(as_text=True)
    for k in ["back", "knee", "ptsd", "sleep-apnea", "tinnitus", "general"]:
        html = c.get(f"/tools/cp-exam-prep?condition={k}", headers=HTTPS).get_data(as_text=True)
        assert "Be ready to describe" in html


def test_denials_always_list_the_three_review_paths():
    out = dl.normalize({"decision_date": "2026-04-10", "items": [{"condition": "Knee", "outcome": "denied"}],
                        "next_steps": [{"option": "Other", "fits_when": "This text does not describe review options."}]})
    names = [s["option"] for s in out["next_steps"]]
    assert names == ["Supplemental Claim", "Higher-Level Review", "Board Appeal"]
