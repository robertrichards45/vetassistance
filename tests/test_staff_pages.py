"""Every rebuilt staff page renders for a signed-in director, with real records behind it."""
import json
import re
from datetime import datetime

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import Agreement, Client, Document, Organization, RenderedArtifact, Template, User
from app.models.cue import CueMotion
from app.models.intake import IntakeMeta, IntakeRecord
from app.models.user import Role

HTTPS = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9", "Host": "www.vetassistance.org"}


@pytest.fixture(scope="module")
def setup():
    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db = SessionLocal()
        org = db.query(Organization).first()
        if not org:
            org = Organization(name="Veteran Benefits Assistance")
            db.add(org); db.commit()
        u = db.query(User).filter_by(email="pages@test.local").first()
        if not u:
            u = User(org_id=org.id, email="pages@test.local", full_name="Page Tester", role=Role.DIRECTOR)
            u.set_password("Correct-Horse-9")
            db.add(u); db.commit()
        c = Client(org_id=org.id, first_name="Pat", last_name="Example", email="pat@example.test")
        db.add(c); db.commit()
        ag = Agreement(org_id=org.id, client_id=c.id, title="Service Agreement", body="Terms <b>here</b>", status="SIGNED",
                       signed_name="Pat Example", signed_at=datetime.utcnow())
        art = RenderedArtifact(org_id=org.id, client_id=c.id, artifact_type="FORM", title="VA forms packet",
                               web_copy="Hello", meta_json=json.dumps({"forms": [{"title": "VA Form 21-4138", "use": "Statement", "category": "General", "url": "https://www.va.gov/find-forms/", "key": "21-4138"}]}))
        doc = Document(org_id=org.id, client_id=c.id, filename="dd214.pdf", mime_type="application/pdf", storage_path="/nonexistent")
        tpl = Template(org_id=org.id, name="Status update", body="Hi {{ first_name }}")
        intake = IntakeRecord(email="new@example.test", filename="upload.pdf", file_hash="a" * 64)
        db.add_all([ag, art, doc, tpl, intake]); db.commit()
        db.add(IntakeMeta(intake_id=intake.id, full_name="New Veteran", phone="555-0100"))
        motion = CueMotion(org_id=org.id, client_id=c.id, created_by_user_id=u.id)
        db.add(motion); db.commit()
        ids = dict(client=c.id, agreement=ag.id, artifact=art.id, doc=doc.id, template=tpl.id, intake=intake.id, motion=motion.id)
        db.close()
    tc = app.test_client()
    html = tc.get("/auth/login", headers=HTTPS).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    tc.post("/auth/login", data={"email": "pages@test.local", "password": "Correct-Horse-9", "csrf_token": token}, headers=HTTPS)
    return tc, ids, token


def _ok(tc, path):
    r = tc.get(path, headers=HTTPS)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


def test_rebuilt_pages_render(setup):
    tc, i, _ = setup
    assert "Waiting for signature" not in _ok(tc, f"/agreements/client/{i['client']}")
    html = _ok(tc, f"/agreements/view/{i['agreement']}")
    assert "Signed by" in html and "&lt;b&gt;" in html  # agreement text is escaped
    assert "VA forms packet" in _ok(tc, f"/artifacts/client/{i['client']}")
    assert "Blank form" in _ok(tc, f"/forms/va-forms/packet/{i['artifact']}")
    assert 'data-copy="#body"' in _ok(tc, f"/exports/artifact/{i['artifact']}/email")
    html = _ok(tc, f"/intakes/{i['intake']}")
    assert "New Veteran" in html and "Create client file" in html
    assert "Create a client file" in _ok(tc, f"/intakes/{i['intake']}/convert")
    assert "VA QuickSubmit" in _ok(tc, f"/employee/cue/{i['client']}/submit/{i['motion']}")


def test_preview_and_template_render(setup):
    tc, i, token = setup
    html = tc.post(f"/documents/client/{i['client']}/preview", data={"doc_ids": [str(i["doc"])], "csrf_token": token}, headers=HTTPS)
    assert html.status_code == 200 and "dd214.pdf" in html.get_data(as_text=True)
    r = tc.post(f"/templates/client/{i['client']}/render", data={"template_id": str(i["template"]), "csrf_token": token}, headers=HTTPS)
    assert r.status_code == 200 and "Status update" in r.get_data(as_text=True)


def test_backup_requires_password_and_contains_data(setup):
    import io, zipfile
    tc, i, token = setup
    page = tc.get("/director/backup", headers=HTTPS)
    assert page.status_code == 200 and "Download backup" in page.get_data(as_text=True)
    bad = tc.post("/director/backup/download", data={"password": "wrong", "csrf_token": token}, headers=HTTPS)
    assert bad.status_code == 302
    good = tc.post("/director/backup/download", data={"password": "Correct-Horse-9", "csrf_token": token}, headers=HTTPS)
    assert good.status_code == 200 and good.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(good.data))
    names = z.namelist()
    assert "README.txt" in names and "database/clients.jsonl" in names
    assert b"Pat" in z.read("database/clients.jsonl")
