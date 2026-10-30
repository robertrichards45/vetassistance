"""Claim builder: analysis + drafting run in the background; answers land in the right 21-526EZ / 21-4138 fields."""
import io
import json
import os
import tempfile
import time
import types

import fitz
import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import ClaimPacket, Client, Document
from tests.seed_demo import seed
from tests.test_workflows import login, token, H

ANALYSIS = {
    "veteran": {"first_name": "Marcus", "middle_initial": "J", "last_name": "Reed", "dob": "04/12/1984",
                "service_number": "", "va_file_number": "", "filed_before": False, "other_names": []},
    "service": {"branch": "Army", "component": "Active", "entry_date": "08/15/2002", "exit_date": "08/14/2006",
                "place_of_separation": "Fort Bragg, NC", "additional_periods": [], "combat_zone_since_911": True,
                "reserves_or_guard": False, "pow": False, "receiving_retired_pay": False, "separation_pay": None,
                "evidence": "DD214_Reed.pdf"},
    "exposures": {"gulf_war": {"served": True, "from": "03/2004", "to": "02/2005"},
                  "herbicide": {"served": False}, "other": {"mos_toxin": True}, "additional_details": "Camp Taji burn pit"},
    "treatment_facilities": [{"disability": "Right knee", "facility": "Valdosta VA CBOC, Valdosta GA", "date": "05/2019"}],
    "claims": [
        {"condition": "Tinnitus", "diagnostic_code": "6260", "claim_type": "direct", "event": "Artillery noise",
         "date_began": "06/2004", "likelihood": 84, "likelihood_reason": "Diagnosis and noise exposure documented.",
         "elements": {"diagnosis": {"status": "met", "evidence": "audiology"}}, "estimated_rating": 10,
         "evidence_needed": [], "supporting_forms": [], "statement_facts": ["ringing since 2004"]},
        {"condition": "Right knee patellofemoral syndrome", "diagnostic_code": "5260", "claim_type": "direct",
         "event": "Parachute landing injury", "date_began": "06/2003", "likelihood": 52,
         "likelihood_reason": "No nexus opinion yet.", "elements": {}, "estimated_rating": 10},
        {"condition": "Seasonal allergies", "diagnostic_code": "6522", "claim_type": "direct", "likelihood": 120},
    ],
    "not_recommended": [{"condition": "Hearing loss", "reason": "Hearing within normal limits"}],
    "missing_info": ["Have you filed a VA claim before?"],
    "strategy": "File tinnitus and the knee together.",
}
STATEMENT = {"statement": "RE: Tinnitus, claimed as direct service connection\n\nMY SERVICE AND WHAT HAPPENED\nI served "
             "with the 82nd Airborne. [VETERAN TO CONFIRM: unit name]\n\nI certify that the statements above are true.",
             "form_explanation": "Ringing began during artillery training in 2004", "placeholders": ["[VETERAN TO CONFIRM: unit name]"],
             "exam_points": ["Say when the ringing started"]}


class _Completions:
    def create(self, model, messages, **kw):
        time.sleep(0.2)
        body = ANALYSIS if "Recommend every claim" in messages[0]["content"] else STATEMENT
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(body)))])


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


def _wait(c, cid, done):
    for _ in range(80):
        st = c.get(f"/claims/client/{cid}/status", headers=H).get_json()
        if st["status"] in done:
            return st
        time.sleep(0.1)
    return st


def _fields(pdf_bytes):
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = {}
    for p in d:
        for w in p.widgets():
            if w.field_type == fitz.PDF_WIDGET_TYPE_TEXT and w.field_value:
                out[w.field_name] = w.field_value
            elif w.field_type in (fitz.PDF_WIDGET_TYPE_RADIOBUTTON, fitz.PDF_WIDGET_TYPE_CHECKBOX) and w.field_value not in ("Off", False, None, ""):
                out.setdefault(w.field_name, set()).add(str(w.on_state()))
    return out, d.page_count


def test_full_claim_builder_flow(app):
    db = SessionLocal()
    cl = db.query(Client).filter_by(first_name="Marcus").first()
    cid, org = cl.id, cl.org_id
    cl.mailing_address1, cl.mailing_city, cl.mailing_state, cl.mailing_zip = "1200 Liberty Ave", "Valdosta", "GA", "31601"
    path = os.path.join(tempfile.mkdtemp(), "DD214_Reed.txt")
    open(path, "w").write("DD 214. Army. Entered 2002-08-15. Separated 2006-08-14 Fort Bragg NC. Audiology: tinnitus.")
    db.add(Document(org_id=org, client_id=cid, filename="DD214_Reed.txt", storage_path=path))
    db.commit(); db.close()

    c, _ = login(app, "employee")
    assert "Analyze" in c.get(f"/claims/client/{cid}", headers=H).get_data(as_text=True)
    tok = token(c, f"/claims/client/{cid}")
    t = time.time()
    r = c.post(f"/claims/client/{cid}/analyze", data={"csrf_token": tok}, headers=H)
    assert r.status_code in (302, 303) and time.time() - t < 0.3
    assert _wait(c, cid, ("READY", "FAILED"))["status"] == "READY"

    html = c.get(f"/claims/client/{cid}", headers=H).get_data(as_text=True)
    assert "Recommended claims" in html and "84%" in html and "Strong" in html and "52%" in html
    assert "95%" in html, "scores are capped below 100"
    assert "Hearing within normal limits" in html and "Have you filed a VA claim before?" in html

    tok = token(c, f"/claims/client/{cid}")
    c.post(f"/claims/client/{cid}/draft", data={"csrf_token": tok, "claim": ["0", "1"]}, headers=H)
    assert _wait(c, cid, ("DRAFTED", "READY", "FAILED"))["status"] == "DRAFTED"
    html = c.get(f"/claims/client/{cid}", headers=H).get_data(as_text=True)
    assert "Review the forms" in html and "1 to confirm" in html and "Ringing began during artillery" in html

    # Edit an answer, then render the 526: every answer must be in its own field.
    tok = token(c, f"/claims/client/{cid}")
    page = c.get(f"/claims/client/{cid}", headers=H).get_data(as_text=True)
    assert 'name="claim.1.condition"' in page
    data = {"csrf_token": tok, "claim_process": "STANDARD", "id.first": "Marcus", "id.last": "Reed", "id.dob": "04/12/1984",
            "id.filed_before": "no", "id.street": "1200 Liberty Ave", "id.city": "Valdosta", "id.state": "GA", "id.zip": "31601",
            "id.country": "US", "service.branch": "Army", "service.component": "Active", "service.entry": "08/15/2002",
            "service.exit": "08/14/2006", "service.place_sep": "Fort Bragg, NC", "service.combat_911": "yes",
            "exposure.claiming_toxic": "yes", "exposure.gulf_war": "yes", "exposure.gw_from": "03/2004", "exposure.gw_to": "02/2005",
            "exposure.mos_toxin": "1",
            "claim.0.condition": "Tinnitus", "claim.0.event": "Artillery noise", "claim.0.explanation": "Ringing since 2004", "claim.0.date": "06/2004",
            "claim.1.condition": "Right knee patellofemoral syndrome", "claim.1.event": "Parachute landing injury",
            "claim.1.explanation": "Knee injured on a jump", "claim.1.date": "06/2003",
            "treat.0.text": "Right knee: Valdosta VA CBOC, Valdosta GA", "treat.0.date": "05/2019",
            "statement.0": STATEMENT["statement"], "statement.1": "Knee statement text.", "next": "526"}
    r = c.post(f"/claims/client/{cid}/save", data=data, headers=H)
    assert r.status_code in (302, 303) and r.headers["Location"].endswith("/pdf/526")
    r = c.get(f"/claims/client/{cid}/pdf/526", headers=H)
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    f, pages = _fields(r.data)
    assert pages == 7, "only the application pages (9-15) are included"
    P9, P10, P11, P12 = "F[0].Page_10[0].", "F[0].#subform[9].", "F[0].#subform[10].", "F[0].#subform[11]."
    assert f[P9 + "Veteran_Service_Member_First_Name[0]"] == "Marcus" and f[P9 + "Veteran_Service_Member_Last_Name[0]"] == "Reed"
    assert f[P9 + "Date_Of_Birth_Month[0]"] == "04" and f[P9 + "Date_Of_Birth_Year[0]"] == "1984"
    assert f[P9 + "CurrentMailingAddress_ZIPOrPostalCode_FirstFiveNumbers[0]"] == "31601"
    assert f[P9 + "RadioButtonList2[0]"] == {"1"}, "item 4: never filed = No"
    assert "Standard_Claim_Process[0]" in "".join(k for k in f if k.startswith(P9))
    assert f[P11 + "CURRENTDISABILITY[0]"] == "Tinnitus"
    assert "patellofemoral" in f[P11 + "CURRENTDISABILITY[1]"]
    assert f[P11 + "Date_Disability_Began_Or_Worsened[0]"] == "06/2004" and f[P11 + "Date12[0]"] == "06/2003"
    assert f[P11 + "Enter_Disability_Treated_And_Name_And_Location_Of_Treatment_Facility[2]"].startswith("Right knee"), "top row is field [2]"
    assert f[P11 + "Date_Of_Treatment_Month[0]"] == "05" and f[P11 + "Date_Of_Treatment_Year[0]"] == "2019"
    assert f[P10 + "RadioButtonList2[2]"] == {"0"} and f[P10 + "RadioButtonList2[3]"] == {"0"}, "15A and 15B = Yes"
    assert f[P10 + "From_Date_Month[0]"] == "03" and f[P10 + "To_Date_Year[0]"] == "2005"
    assert P10 + "Military_Occupational_Specialty_MOS_Related_Toxin[0]" in f
    assert f[P12 + "RadioButtonList[10]"] == {"0"}, "19A Army"
    assert f[P12 + "RadioButtonList[12]"] == {"0"}, "19B Active"
    assert f[P12 + "RadioButtonList[1]"] == {"0"}, "20C combat zone = Yes"
    assert f[P12 + "EntryDate_Month[0]"] == "08" and f[P12 + "ExitDate_Year[0]"] == "2006"
    assert f[P12 + "PlaceOfLastOrAnticipatedSeparation[0]"] == "Fort Bragg, NC"

    r = c.get(f"/claims/client/{cid}/pdf/4138-0", headers=H)
    f, pages = _fields(r.data)
    assert f["form1[0].#subform[0].Veterans_Beneficiary_First_Name[0]"] == "Marcus"
    assert "82nd Airborne" in f["form1[0].#subform[0].REMARKS[0]"]

    r = c.get(f"/claims/client/{cid}/pdf/packet", headers=H)
    d = fitz.open(stream=r.data, filetype="pdf")
    text = "".join(p.get_text() for p in d)
    assert "Claim packet checklist" in text and "PO Box 4444" in text and "VETERAN TO CONFIRM" in text
    assert d.page_count >= 1 + 7 + 2 * 2


def test_claim_builder_requires_staff(app):
    db = SessionLocal(); cid = db.query(Client).filter_by(first_name="Marcus").first().id; db.close()
    c, _ = login(app, "client")
    assert c.get(f"/claims/client/{cid}", headers=H).status_code in (302, 403, 404)
    assert c.get(f"/claims/client/{cid}/pdf/526", headers=H).status_code in (302, 403, 404)
