"""Rating schedule explorer: data quality, page, data endpoint, worksheet save/load."""
import json
import os
import re

import pytest

from app import create_app
from tests.seed_demo import seed
from tests.test_workflows import login, token, H

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cfr_data", "rating_explorer.json")


def _codes():
    return {c["diagnostic_code"]: c for c in json.load(open(DATA, encoding="utf-8"))["conditions"]}


def test_data_has_common_conditions_right():
    c = _codes()
    assert len(c) > 700
    assert [r["percent"] for r in c["6260"]["criteria"]] == [10]                     # tinnitus
    assert [r["percent"] for r in c["5260"]["criteria"]] == [30, 20, 10, 0]           # knee flexion
    assert [r["percent"] for r in c["5261"]["criteria"]][0] == 50                     # knee extension
    assert c["8520"]["condition"] == "Paralysis of the sciatic nerve"
    assert any("Spine" in f for f in c["5237"]["formula_names"])
    assert [r["percent"] for r in c["9411"]["criteria"]][:2] == [100, 70]             # PTSD
    assert [r["percent"] for r in c["6847"]["criteria"]] == [100, 50, 30, 0]          # sleep apnea
    assert "6100" in c and c["6100"]["instruction"]
    assert not any(x["condition"].lower().strip("[]. ") == "removed" for x in c.values())


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config.update(TESTING=True)
    from app.extensions import limiter
    limiter.enabled = False
    with a.app_context():
        seed()
    return a


def test_page_and_data_endpoint(app):
    c, _ = login(app, "employee")
    html = c.get("/cfr/ratings-chart", headers=H).get_data(as_text=True)
    assert "Rating schedule" in html and "Worksheet" in html
    r = c.get("/cfr/ratings-chart/explorer.json", headers={**H, "Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("Content-Encoding") == "gzip"
    assert c.get("/employee/hub/ratings", headers=H).status_code in (302, 303)


def test_worksheet_save_and_load(app):
    c, _ = login(app, "director")
    tok = token(c, "/cfr/ratings-chart")
    rows = [{"dc": "5260", "name": "Knee", "percent": 10, "bilateral": True}, {"dc": "6260", "name": "Tinnitus", "percent": 10}]
    r = c.post("/cfr/worksheet/1", json={"rows": rows, "combined": 20}, headers={**H, "X-CSRFToken": tok})
    assert r.get_json()["ok"]
    got = c.get("/cfr/worksheet/1", headers=H).get_json()
    assert [x["dc"] for x in got["rows"]] == ["5260", "6260"] and got["rows"][0]["bilateral"] is True


def test_clients_cannot_save_worksheets(app):
    c, _ = login(app, "client")
    tok = token(c, "/portal/dashboard")
    assert c.post("/cfr/worksheet/1", json={"rows": []}, headers={**H, "X-CSRFToken": tok}).status_code == 403
