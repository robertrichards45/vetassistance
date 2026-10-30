"""Become-a-client saves real leads; bots are dropped; director can clean up bot logins."""
import re
import time

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import Client, User
from app.models.user import Role
from tests.seed_demo import seed
from tests.test_workflows import login, token, H


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config.update(TESTING=True)
    from app.extensions import limiter
    limiter.enabled = False
    with a.app_context():
        seed()
    return a


def _form(c, path):
    html = c.get(path, headers=H).get_data(as_text=True)
    return (re.search(r'name="csrf_token" value="([^"]+)"', html).group(1),
            re.search(r'name="form_ts" value="([^"]+)"', html).group(1))


def test_become_client_saves_lead(app, monkeypatch):
    import app.services.bot_guard as bg
    monkeypatch.setattr(bg, "MIN_SECONDS", 0)
    c = app.test_client()
    csrf, ts = _form(c, "/become-client")
    r = c.post("/become-client", data={"csrf_token": csrf, "form_ts": ts, "first_name": "Sam", "last_name": "Rivera",
                                       "email": "sam.rivera@example.com", "branch": "Navy", "help_text": "Knee and back"}, headers=H)
    assert r.status_code == 200 and "We got your request" in r.get_data(as_text=True)
    db = SessionLocal()
    cl = db.query(Client).filter_by(email="sam.rivera@example.com").first()
    assert cl and cl.status == "New intake" and cl.service_branch == "Navy"
    db.close()


def test_bots_are_dropped(app):
    c = app.test_client()
    csrf, ts = _form(c, "/become-client")
    c.post("/become-client", data={"csrf_token": csrf, "form_ts": ts, "website": "spam.example", "first_name": "Bot",
                                   "email": "bot1@spam.example"}, headers=H)
    c.post("/become-client", data={"csrf_token": csrf, "form_ts": ts, "first_name": "Fast", "last_name": "Bot",
                                   "email": "bot2@spam.example"}, headers=H)  # submitted instantly
    csrf, ts = _form(c, "/portal/register")
    c.post("/portal/register", data={"csrf_token": csrf, "form_ts": ts, "full_name": "zrutmuwolk", "email": "bot3@spam.example",
                                     "password": "Password123!", "confirm_password": "Password123!", "consent_ack": "yes",
                                     "consent_review": "yes"}, headers=H)
    db = SessionLocal()
    assert db.query(Client).filter(Client.email.like("%@spam.example")).count() == 0
    assert db.query(User).filter(User.email.like("%@spam.example")).count() == 0
    db.close()


def test_users_page_flags_and_disables_bot_logins(app):
    db = SessionLocal()
    org = db.query(Client).first().org_id
    bot = User(org_id=org, email="zkxuvnkd@immenseignite.info", full_name="zrutmuwolk", role=Role.CLIENT)
    bot.set_password("x" * 12)
    db.add(bot); db.commit()
    db.add(Client(org_id=org, first_name="zrutmuwolk", email=bot.email, portal_user_id=bot.id)); db.commit()
    bid = bot.id; db.close()

    d, _ = login(app, "director")
    html = d.get("/director/users?tab=clients", headers=H).get_data(as_text=True)
    assert "Possible bot" in html and "look like bot sign-ups" in html
    tok = token(d, "/director/users?tab=clients")
    d.post("/director/users/bulk-disable", data={"csrf_token": tok, "user_ids": [str(bid)], "archive_clients": "1"}, headers=H)
    db = SessionLocal()
    assert db.get(User, bid).is_active is False
    assert db.query(Client).filter_by(portal_user_id=bid).first().is_archived
    db.close()
    assert "Team" in d.get("/director/users", headers=H).get_data(as_text=True)


def test_add_team_member_generates_password(app):
    d, _ = login(app, "director")
    tok = token(d, "/director/users")
    r = d.post("/director/users/create", data={"csrf_token": tok, "full_name": "New Helper", "email": "helper@example.org",
                                               "role": "EMPLOYEE"}, headers=H, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "Temporary password:" in body and "ChangeMe123" not in body


def test_director_can_edit_pricing_percent(app):
    from app.models import PricingItem
    db = SessionLocal()
    org = db.query(Client).first().org_id
    it = PricingItem(org_id=org, name="Claim support", is_percent=True, percent_of_backpay=20, price_cents=0, active=True,
                     description="Fee: 20%")
    db.add(it); db.commit(); iid = it.id; db.close()
    d, _ = login(app, "director")
    tok = token(d, "/director/pricing")
    d.post("/director/pricing", data={"csrf_token": tok, "action": "update", "item_id": str(iid), "name": "Claim support",
                                      "percent_of_backpay": "15", "description": "Fee: 15%"}, headers=H)
    db = SessionLocal()
    assert db.get(PricingItem, iid).percent_of_backpay == 15
    db.close()
