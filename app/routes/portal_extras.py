from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import SessionLocal
from app.models import Client, Letter, ClientTask, FormData, ClaimUpdate, EvidenceChecklist, EvidenceChecklistItem, Document, DocumentScan, Reminder, VACallLog, ClientNote
from app.models.user import Role
from app.services.onboarding import ensure_tasks
from app.services.ssn_crypto import encrypt_ssn
from app.services.doc_text import extract_text
import json
from datetime import datetime
import os
import json

portalx_bp = Blueprint("portalx", __name__, url_prefix="/portal")

def _linked_client(db):
    c = db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()
    return c

def _is_guest_client(c: Client | None) -> bool:
    if not c:
        return True
    if (c.account_type or "").lower() == "guest":
        return True
    if not (c.email or "").strip():
        return True
    return c.email_verified is False


def _scan_document(doc: Document) -> tuple[int, list[dict], str]:
    issues = []
    score = 100
    path = doc.storage_path or ""
    if not path or not os.path.exists(path):
        issues.append({"level": "red", "title": "Missing file", "detail": "Document file not found on disk."})
        return 0, issues, "File missing."
    size_kb = int(os.path.getsize(path) / 1024) if os.path.exists(path) else 0
    if size_kb < 50:
        issues.append({"level": "yellow", "title": "Very small file", "detail": "Low file size can mean missing pages or blank scans."})
        score -= 15
    text = ""
    try:
        text = extract_text(path)[:20000]
    except Exception:
        text = ""
    text_len = len((text or "").strip())
    if text_len < 200:
        issues.append({"level": "red", "title": "No readable text", "detail": "Likely a scanned image that needs OCR."})
        score -= 45
    elif text_len < 1000:
        issues.append({"level": "yellow", "title": "Low text volume", "detail": "Text extraction is limited. Verify readability."})
        score -= 20
    if text_len >= 1000 and size_kb > 50:
        issues.append({"level": "green", "title": "Readable text detected", "detail": "Text extraction looks healthy."})
    score = max(0, min(100, score))
    summary = "Manual review recommended." if score < 70 else "Looks readable."
    return score, issues, summary

@portalx_bp.get("/letters")
@login_required
def letters():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    letters = db.query(Letter).filter_by(org_id=current_user.org_id, client_id=c.id, is_visible_to_client=True).order_by(Letter.created_at.desc()).all()
    return render_template("portal/letters.html", client=c, letters=letters, is_guest=is_guest)

@portalx_bp.get("/checklist")
@login_required
def checklist():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    ensure_tasks(current_user.org_id, c.id)
    tasks = db.query(ClientTask).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(ClientTask.created_at.asc()).all()
    return render_template("portal/checklist.html", client=c, tasks=tasks, is_guest=is_guest)


@portalx_bp.get("/intake")
@login_required
def intake():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
    data = {
        "full_legal_name": c.full_legal_name or "",
        "dob": c.dob or "",
        "branch": c.service_branch or "",
        "service_dates": "",
        "phone": c.phone or "",
        "mailing_address1": c.mailing_address1 or "",
        "mailing_address2": c.mailing_address2 or "",
        "mailing_city": c.mailing_city or "",
        "mailing_state": c.mailing_state or "",
        "mailing_zip": c.mailing_zip or "",
        "issues": "",
        "deadlines": "",
        "notes": "",
    }
    if fd and fd.data_json:
        try:
            stored = json.loads(fd.data_json)
            if isinstance(stored, dict):
                data.update(stored)
        except Exception: data = {}
    return render_template("portal/intake.html", client=c, data=data, is_guest=is_guest)

@portalx_bp.post("/intake")
@login_required
def intake_save():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    payload = {
        "full_legal_name": request.form.get("full_legal_name",""),
        "dob": request.form.get("dob",""),
        "branch": request.form.get("branch",""),
        "service_dates": request.form.get("service_dates",""),
        "phone": request.form.get("phone",""),
        "mailing_address1": request.form.get("mailing_address1",""),
        "mailing_address2": request.form.get("mailing_address2",""),
        "mailing_city": request.form.get("mailing_city",""),
        "mailing_state": request.form.get("mailing_state",""),
        "mailing_zip": request.form.get("mailing_zip",""),
        "issues": request.form.get("issues",""),
        "deadlines": request.form.get("deadlines",""),
        "notes": request.form.get("notes",""),
    }
    ssn_full = (request.form.get("ssn_full") or "").strip()
    if ssn_full:
        c.ssn_encrypted = encrypt_ssn(ssn_full)
        c.ssn_last4 = ssn_full[-4:] if len(ssn_full) >= 4 else ssn_full
        c.ssn_full = ""
    c.full_legal_name = payload["full_legal_name"] or c.full_legal_name
    c.dob = payload["dob"] or c.dob
    c.service_branch = payload["branch"] or c.service_branch
    if payload["phone"]:
        c.phone = payload["phone"]
    if payload["mailing_address1"]:
        c.mailing_address1 = payload["mailing_address1"]
    if payload["mailing_address2"]:
        c.mailing_address2 = payload["mailing_address2"]
    if payload["mailing_city"]:
        c.mailing_city = payload["mailing_city"]
    if payload["mailing_state"]:
        c.mailing_state = payload["mailing_state"]
    if payload["mailing_zip"]:
        c.mailing_zip = payload["mailing_zip"]
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
    if not fd:
        fd = FormData(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1", created_by_user_id=current_user.id)
        db.add(fd)
    fd.data_json = json.dumps(payload, indent=2)
    fd.updated_at = datetime.utcnow()
    db.commit()
    return redirect(url_for("portal.dashboard"))


@portalx_bp.get("/profile")
@login_required
def profile():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    logs = (
        db.query(VACallLog)
        .filter_by(org_id=current_user.org_id, client_id=c.id)
        .order_by(VACallLog.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template("portal/profile.html", client=c, call_logs=logs)


@portalx_bp.get("/notes")
@login_required
def notes():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    rows = (
        db.query(ClientNote)
        .filter_by(org_id=current_user.org_id, client_id=c.id, is_internal=False)
        .order_by(ClientNote.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template("portal/notes.html", client=c, notes=rows)


@portalx_bp.post("/notes")
@login_required
def notes_post():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    body = (request.form.get("body") or "").strip()
    tags = (request.form.get("tags") or "").strip()
    if not body:
        flash("Note cannot be empty.", "error")
        return redirect(url_for("portalx.notes"))
    note = ClientNote(
        org_id=current_user.org_id,
        client_id=c.id,
        created_by_user_id=current_user.id,
        is_internal=False,
        body=body,
        tags=tags,
    )
    db.add(note)
    db.commit()
    flash("Note saved.", "success")
    return redirect(url_for("portalx.notes"))


@portalx_bp.post("/notes/<int:note_id>/delete")
@login_required
def notes_delete(note_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    n = db.get(ClientNote, note_id)
    if not n or n.client_id != c.id or n.org_id != current_user.org_id or n.is_internal:
        return "Not found", 404
    db.delete(n)
    db.commit()
    flash("Note deleted.", "success")
    return redirect(url_for("portalx.notes"))


@portalx_bp.post("/profile")
@login_required
def profile_save():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403

    c.full_legal_name = (request.form.get("full_legal_name") or "").strip()
    c.dob = (request.form.get("dob") or "").strip()
    c.phone = (request.form.get("phone") or "").strip()
    c.mailing_address1 = (request.form.get("mailing_address1") or "").strip()
    c.mailing_address2 = (request.form.get("mailing_address2") or "").strip()
    c.mailing_city = (request.form.get("mailing_city") or "").strip()
    c.mailing_state = (request.form.get("mailing_state") or "").strip()
    c.mailing_zip = (request.form.get("mailing_zip") or "").strip()
    c.physical_address1 = (request.form.get("physical_address1") or "").strip()
    c.physical_address2 = (request.form.get("physical_address2") or "").strip()
    c.physical_city = (request.form.get("physical_city") or "").strip()
    c.physical_state = (request.form.get("physical_state") or "").strip()
    c.physical_zip = (request.form.get("physical_zip") or "").strip()
    c.emergency_contact_name = (request.form.get("emergency_contact_name") or "").strip()
    c.emergency_contact_phone = (request.form.get("emergency_contact_phone") or "").strip()
    c.emergency_contact_relationship = (request.form.get("emergency_contact_relationship") or "").strip()
    c.service_branch = (request.form.get("service_branch") or "").strip()
    c.service_entry_date = (request.form.get("service_entry_date") or "").strip()
    c.service_discharge_date = (request.form.get("service_discharge_date") or "").strip()

    ssn_full = (request.form.get("ssn_full") or "").strip()
    if ssn_full:
        c.ssn_encrypted = encrypt_ssn(ssn_full)
        c.ssn_last4 = ssn_full[-4:] if len(ssn_full) >= 4 else ssn_full
        c.ssn_full = ""

    db.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("portalx.profile"))


@portalx_bp.get("/claim-tracker")
@login_required
def claim_tracker():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    updates = db.query(ClaimUpdate).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(ClaimUpdate.created_at.desc()).all()
    return render_template("portal/claim_tracker.html", client=c, updates=updates, is_guest=is_guest)


@portalx_bp.post("/claim-tracker")
@login_required
def claim_tracker_post():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("Please verify your email to add updates.", "error")
        return redirect(url_for("portalx.claim_tracker"))
    note = (request.form.get("note") or "").strip()
    if not note:
        flash("Enter a short update.", "error")
        return redirect(url_for("portalx.claim_tracker"))
    row = ClaimUpdate(
        org_id=current_user.org_id,
        client_id=c.id,
        status_label="Client update",
        note=note,
        source_role="CLIENT",
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    flash("Update submitted.", "success")
    return redirect(url_for("portalx.claim_tracker"))


@portalx_bp.get("/evidence-checklist")
@login_required
def evidence_checklist():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    checklist = db.query(EvidenceChecklist).filter_by(org_id=current_user.org_id, client_id=c.id, is_active=True).order_by(EvidenceChecklist.created_at.desc()).first()
    items = []
    if checklist:
        items = db.query(EvidenceChecklistItem).filter_by(checklist_id=checklist.id).order_by(EvidenceChecklistItem.id.asc()).all()
    return render_template("portal/evidence_checklist.html", client=c, checklist=checklist, items=items, is_guest=is_guest)


@portalx_bp.post("/evidence-checklist/item/<int:item_id>/toggle")
@login_required
def evidence_checklist_toggle(item_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("Please verify your email to update checklist items.", "error")
        return redirect(url_for("portalx.evidence_checklist"))
    item = db.get(EvidenceChecklistItem, item_id)
    if not item:
        flash("Checklist item not found.", "error")
        return redirect(url_for("portalx.evidence_checklist"))
    checklist = db.get(EvidenceChecklist, item.checklist_id)
    if not checklist or checklist.client_id != c.id or checklist.org_id != current_user.org_id:
        flash("Checklist item not found.", "error")
        return redirect(url_for("portalx.evidence_checklist"))
    item.is_done = not item.is_done
    item.done_at = datetime.utcnow() if item.is_done else None
    item.done_by_user_id = current_user.id if item.is_done else None
    db.commit()
    return redirect(url_for("portalx.evidence_checklist"))


@portalx_bp.get("/doc-scan")
@login_required
def doc_scan():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(Document.created_at.desc()).all()
    scan_id = int(request.args.get("scan_id") or 0)
    scan = db.get(DocumentScan, scan_id) if scan_id else None
    scan_issues = []
    if scan and scan.issues_json:
        try:
            parsed = json.loads(scan.issues_json)
            if isinstance(parsed, list):
                scan_issues = parsed
        except Exception:
            scan_issues = []
    return render_template("portal/doc_scan.html", client=c, is_guest=is_guest, documents=docs, scan=scan, scan_issues=scan_issues)


@portalx_bp.post("/doc-scan")
@login_required
def doc_scan_post():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("Please verify your email to scan documents.", "error")
        return redirect(url_for("portalx.doc_scan"))
    doc_id = int(request.form.get("document_id") or 0)
    doc = db.get(Document, doc_id)
    if not doc or doc.client_id != c.id or doc.org_id != current_user.org_id:
        flash("Document not found.", "error")
        return redirect(url_for("portalx.doc_scan"))
    score, issues, summary = _scan_document(doc)
    scan = DocumentScan(
        org_id=current_user.org_id,
        client_id=c.id,
        document_id=doc.id,
        summary=summary,
        score=score,
        issues_json=json.dumps(issues, ensure_ascii=True),
        created_by_user_id=current_user.id,
    )
    db.add(scan)
    db.commit()
    flash("Scan complete.", "success")
    return redirect(url_for("portalx.doc_scan", scan_id=scan.id))


@portalx_bp.get("/reminders")
@login_required
def reminders():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    rows = db.query(Reminder).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(Reminder.created_at.desc()).all()
    return render_template("portal/reminders.html", client=c, reminders=rows, is_guest=is_guest)
