import json
from datetime import datetime
import os
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, current_app
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, FormData, RenderedArtifact
from app.services.audit_service import log as audit_log
from app.services.va_forms import get_va_forms
from app.services.va_pdf import fill_pdf, list_pdf_fields
from app.services.ssn_crypto import decrypt_ssn, encrypt_ssn

forms_bp = Blueprint("forms", __name__, url_prefix="/forms")

@forms_bp.get("/client/<int:client_id>/intake")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def intake(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
    data = {}
    if fd and fd.data_json:
        try: data = json.loads(fd.data_json)
        except Exception: data = {}
    return render_template("forms/intake.html", client=c, data=data)

@forms_bp.post("/client/<int:client_id>/intake")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def intake_save(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    payload = {
        "full_legal_name": request.form.get("full_legal_name",""),
        "dob": request.form.get("dob",""),
        "ssn_full": "",
        "ssn_last4": request.form.get("ssn_last4",""),
        "phone": request.form.get("phone",""),
        "branch": request.form.get("branch",""),
        "service_entry_date": request.form.get("service_entry_date",""),
        "service_discharge_date": request.form.get("service_discharge_date",""),
        "service_dates": request.form.get("service_dates",""),
        "mailing_address1": request.form.get("mailing_address1",""),
        "mailing_address2": request.form.get("mailing_address2",""),
        "mailing_city": request.form.get("mailing_city",""),
        "mailing_state": request.form.get("mailing_state",""),
        "mailing_zip": request.form.get("mailing_zip",""),
        "current_rating": request.form.get("current_rating",""),
        "issues": request.form.get("issues",""),
        "deadlines": request.form.get("deadlines",""),
        "notes": request.form.get("notes",""),
    }
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
    if not fd:
        fd = FormData(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1", created_by_user_id=current_user.id)
        db.add(fd)
    fd.data_json = json.dumps(payload, indent=2)
    fd.updated_at = datetime.utcnow()
    c.full_legal_name = (payload.get("full_legal_name") or "").strip() or c.full_legal_name
    c.dob = (payload.get("dob") or "").strip() or c.dob
    raw_ssn = (request.form.get("ssn_full") or "").strip()
    if raw_ssn:
        c.ssn_encrypted = encrypt_ssn(raw_ssn)
        c.ssn_last4 = raw_ssn[-4:] if len(raw_ssn) >= 4 else raw_ssn
        c.ssn_full = ""
    if not c.ssn_last4:
        c.ssn_last4 = (payload.get("ssn_last4") or "").strip()
    c.phone = (payload.get("phone") or "").strip() or c.phone
    c.service_branch = (payload.get("branch") or "").strip() or c.service_branch
    c.service_entry_date = (payload.get("service_entry_date") or "").strip() or c.service_entry_date
    c.service_discharge_date = (payload.get("service_discharge_date") or "").strip() or c.service_discharge_date
    c.mailing_address1 = (payload.get("mailing_address1") or "").strip() or c.mailing_address1
    c.mailing_address2 = (payload.get("mailing_address2") or "").strip() or c.mailing_address2
    c.mailing_city = (payload.get("mailing_city") or "").strip() or c.mailing_city
    c.mailing_state = (payload.get("mailing_state") or "").strip() or c.mailing_state
    c.mailing_zip = (payload.get("mailing_zip") or "").strip() or c.mailing_zip
    db.commit()
    audit_log(current_user.org_id, current_user.id, "FORM_SAVED", "FormData", fd.id, detail="INTAKE_V1")
    flash("Intake form saved.", "success")
    return redirect(url_for("forms.intake", client_id=c.id))


@forms_bp.get("/client/<int:client_id>/va-forms")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def va_packet(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    forms = get_va_forms()
    return render_template("forms/va_packet.html", client=c, forms=forms)


@forms_bp.post("/client/<int:client_id>/va-forms")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def va_packet_create(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    selected = request.form.getlist("form_key")
    forms = [f for f in get_va_forms() if f.key in selected]
    if not forms:
        flash("Select at least one VA form.", "error")
        return redirect(url_for("forms.va_packet", client_id=c.id))

    meta = {
        "forms": [
            {
                "key": f.key,
                "title": f.title,
                "url": f.url,
                "use": f.use,
                "category": f.category,
                "template": f.template,
            }
            for f in forms
        ]
    }
    artifact = RenderedArtifact(
        org_id=current_user.org_id,
        client_id=c.id,
        created_by_user_id=current_user.id,
        artifact_type="FORM",
        title="VA Forms Packet",
        web_copy="Official VA forms packet generated for this client.",
        meta_json=json.dumps(meta, indent=2),
    )
    db.add(artifact)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "VA_PACKET_CREATED", "RenderedArtifact", artifact.id, detail=str(len(forms)))
    flash("VA forms packet created.", "success")
    return redirect(url_for("forms.va_packet_view", artifact_id=artifact.id))


@forms_bp.get("/va-forms/packet/<int:artifact_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def va_packet_view(artifact_id: int):
    db = SessionLocal()
    a = db.get(RenderedArtifact, artifact_id)
    if not a or a.org_id != current_user.org_id:
        return "Not found", 404
    try:
        meta = json.loads(a.meta_json or "{}")
    except Exception:
        meta = {}
    return render_template("forms/va_packet_view.html", artifact=a, forms=meta.get("forms") or [])


@forms_bp.get("/client/<int:client_id>/va-forms/prefill/<form_key>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def va_prefill_single(client_id: int, form_key: str):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404

    forms = {f.key: f for f in get_va_forms()}
    form = forms.get(form_key)
    if not form or not form.template:
        flash("Template not configured for this form.", "error")
        return redirect(url_for("forms.va_packet", client_id=c.id))

    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="INTAKE_V1").first()
    data = {}
    if fd and fd.data_json:
        try:
            data = json.loads(fd.data_json)
        except Exception:
            data = {}

    if not data:
        data = {}
    if c.full_legal_name and not data.get("full_legal_name"):
        data["full_legal_name"] = c.full_legal_name
    if not data.get("full_legal_name"):
        name_parts = [p for p in [c.first_name, c.last_name] if p]
        if name_parts:
            data["full_legal_name"] = " ".join(name_parts).strip()
    if c.dob and not data.get("dob"):
        data["dob"] = c.dob
    if c.ssn_last4 and not data.get("ssn_last4"):
        data["ssn_last4"] = c.ssn_last4
    if c.phone and not data.get("phone"):
        data["phone"] = c.phone
    if c.service_branch and not data.get("branch"):
        data["branch"] = c.service_branch
    if c.service_entry_date and not data.get("service_entry_date"):
        data["service_entry_date"] = c.service_entry_date
    if c.service_discharge_date and not data.get("service_discharge_date"):
        data["service_discharge_date"] = c.service_discharge_date
    if c.mailing_address1 and not data.get("mailing_address1"):
        data["mailing_address1"] = c.mailing_address1
    if c.mailing_address2 and not data.get("mailing_address2"):
        data["mailing_address2"] = c.mailing_address2
    if c.mailing_city and not data.get("mailing_city"):
        data["mailing_city"] = c.mailing_city
    if c.mailing_state and not data.get("mailing_state"):
        data["mailing_state"] = c.mailing_state
    if c.mailing_zip and not data.get("mailing_zip"):
        data["mailing_zip"] = c.mailing_zip

    template_path = os.path.join(current_app.config["VA_FORMS_DIR"], form.template)
    if not os.path.exists(template_path):
        flash("PDF template not found on server.", "error")
        return redirect(url_for("forms.va_packet", client_id=c.id))

    field_map = form.fields or {}
    field_values = {}
    for field_name, source in field_map.items():
        if isinstance(source, dict) and source.get("source") == "INTAKE_V1":
            key = source.get("key")
            if key:
                field_values[field_name] = data.get(key, "")
        elif isinstance(source, str):
            field_values[field_name] = source

    full_name = (data.get("full_legal_name") or "").strip()
    ssn_full = (data.get("ssn_full") or "").strip()
    if not ssn_full and getattr(c, "ssn_encrypted", ""):
        ssn_full = decrypt_ssn(c.ssn_encrypted)
    ssn_last4 = (data.get("ssn_last4") or "").strip()
    if not ssn_last4 and ssn_full and len(ssn_full) >= 4:
        ssn_last4 = ssn_full[-4:]
    service_entry = (data.get("service_entry_date") or "").strip()
    service_discharge = (data.get("service_discharge_date") or "").strip()
    service_dates = (data.get("service_dates") or "").strip()
    if not service_dates and (service_entry or service_discharge):
        service_dates = "-".join([p for p in [service_entry, service_discharge] if p])
    service_branch = (data.get("branch") or "").strip()
    dob = (data.get("dob") or "").strip()
    phone = (data.get("phone") or "").strip()
    address1 = (data.get("mailing_address1") or "").strip()
    address2 = (data.get("mailing_address2") or "").strip()
    city = (data.get("mailing_city") or "").strip()
    state = (data.get("mailing_state") or "").strip()
    zip_code = (data.get("mailing_zip") or "").strip()
    name_parts = [p for p in full_name.split(" ") if p]
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[-1] if len(name_parts) > 1 else ""
    middle_name = " ".join(name_parts[1:-1]) if len(name_parts) > 2 else ""

    for field_name in list_pdf_fields(template_path):
        lower = field_name.lower()
        if full_name:
            if "veteran_service_member_first_name" in lower or ("first" in lower and "name" in lower):
                field_values.setdefault(field_name, first_name)
                continue
            if "veteran_service_member_last_name" in lower or ("last" in lower and "name" in lower):
                field_values.setdefault(field_name, last_name)
                continue
            if "veteran_service_member_middle_initial" in lower or ("middle" in lower and "name" in lower) or "mi" in lower:
                field_values.setdefault(field_name, middle_name[:1])
                continue
            if "full" in lower and "name" in lower:
                field_values.setdefault(field_name, full_name)
                continue

        if ssn_full and ("socialsecuritynumber" in lower or "ssn" in lower or "social" in lower):
            if "firstthree" in lower or "first_three" in lower or "first3" in lower:
                field_values.setdefault(field_name, ssn_full[:3])
                continue
            if "secondtwo" in lower or "second_two" in lower or "middle2" in lower:
                field_values.setdefault(field_name, ssn_full[3:5])
                continue
            if "lastfour" in lower or "last4" in lower or "last_four" in lower:
                field_values.setdefault(field_name, ssn_full[-4:])
                continue
            field_values.setdefault(field_name, ssn_full)
            continue

        if ssn_last4 and ("ssn" in lower or "social" in lower):
            if "last4" in lower or "last_four" in lower or "lastfour" in lower or ("last" in lower and "four" in lower):
                field_values.setdefault(field_name, ssn_last4)
                continue

        if dob and ("date_of_birth" in lower or "birth" in lower or "dob" in lower):
            if "month" in lower:
                field_values.setdefault(field_name, dob[5:7] if len(dob) >= 7 else dob)
                continue
            if "day" in lower:
                field_values.setdefault(field_name, dob[8:10] if len(dob) >= 10 else dob)
                continue
            if "year" in lower:
                field_values.setdefault(field_name, dob[0:4] if len(dob) >= 4 else dob)
                continue
            field_values.setdefault(field_name, dob)
            continue

        if phone and ("telephone" in lower or "phone" in lower):
            digits = "".join([c for c in phone if c.isdigit()])
            if len(digits) >= 10 and ("area" in lower or "area_code" in lower):
                field_values.setdefault(field_name, digits[0:3])
                continue
            if len(digits) >= 10 and ("middle" in lower or "middle_three" in lower):
                field_values.setdefault(field_name, digits[3:6])
                continue
            if len(digits) >= 10 and ("last" in lower and "four" in lower):
                field_values.setdefault(field_name, digits[6:10])
                continue
            field_values.setdefault(field_name, phone)
            continue

        if address1 and ("currentmailingaddress_numberandstreet" in lower or ("address" in lower and "street" in lower)):
            field_values.setdefault(field_name, address1)
            continue

        if address2 and ("address2" in lower or "line2" in lower or "unit" in lower or "apt" in lower):
            field_values.setdefault(field_name, address2)
            continue

        if city and "currentmailingaddress_city" in lower:
            field_values.setdefault(field_name, city)
            continue

        if state and "currentmailingaddress_stateorprovince" in lower:
            field_values.setdefault(field_name, state)
            continue

        if zip_code and "currentmailingaddress_ziporpostalcode" in lower:
            if "lastfour" in lower and len(zip_code) >= 9:
                field_values.setdefault(field_name, zip_code[-4:])
                continue
            field_values.setdefault(field_name, zip_code[:5] if len(zip_code) >= 5 else zip_code)
            continue

        if service_branch and ("branch" in lower or "servicebranch" in lower):
            field_values.setdefault(field_name, service_branch)
            continue

    safe_client = f"{c.id}"
    output_dir = os.path.join(current_app.config["VA_PREFILL_DIR"], safe_client)
    output_name = f"{form.key}-{c.id}.pdf"
    output_path = os.path.join(output_dir, output_name)
    fill_pdf(template_path, output_path, field_values)

    return send_file(output_path, as_attachment=True, download_name=output_name)
