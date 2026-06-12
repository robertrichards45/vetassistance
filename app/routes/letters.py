from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
import json
import re
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, Letter
from app.services.audit_service import log as audit_log
from app.services import template_engine
from app.services.email_service import send_email_html

letters_bp = Blueprint("letters", __name__, url_prefix="/letters")


def _format_address(c: Client) -> str:
    parts = []
    if c.mailing_address1:
        parts.append(c.mailing_address1)
    if c.mailing_address2:
        parts.append(c.mailing_address2)
    city_state = " ".join([p for p in [c.mailing_city, c.mailing_state] if p]).strip()
    if city_state or c.mailing_zip:
        parts.append((city_state + " " + (c.mailing_zip or "")).strip())
    if parts:
        return "\n".join([p for p in parts if p])
    # fallback to physical address
    parts = []
    if c.physical_address1:
        parts.append(c.physical_address1)
    if c.physical_address2:
        parts.append(c.physical_address2)
    city_state = " ".join([p for p in [c.physical_city, c.physical_state] if p]).strip()
    if city_state or c.physical_zip:
        parts.append((city_state + " " + (c.physical_zip or "")).strip())
    return "\n".join([p for p in parts if p])


def _clean_letter(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = []
    for line in lines:
        if re.match(r"^\s*$", line):
            cleaned.append("")
            continue
        if re.match(r"^.+:\s*$", line):
            continue
        cleaned.append(line)
    out = []
    blank = 0
    for line in cleaned:
        if line.strip() == "":
            blank += 1
            if blank <= 2:
                out.append("")
        else:
            blank = 0
            out.append(line)
    return "\n".join(out).strip()


def _render_wizard_body(c: Client, wizard: dict, answers: dict, site_url: str) -> str:
    org_name = current_user.organization.name if getattr(current_user, "organization", None) else current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
    ctx = {
        "org_name": org_name,
        "org_email": current_app.config.get("SUPPORT_EMAIL", ""),
        "org_phone": current_app.config.get("SUPPORT_PHONE", ""),
        "site_url": site_url,
        "client_name": c.display_name(),
        "client_full_name": c.full_legal_name or c.display_name(),
        "client_claim_number": c.claim_number or "",
        "client_email": c.email or "",
        "client_phone": c.phone or "",
        "client_dob": c.dob or "",
        "client_ssn_last4": c.ssn_last4 or "",
        "client_address": _format_address(c),
        "client_service": " ".join([p for p in [c.service_branch, " to ".join([d for d in [c.service_entry_date, c.service_discharge_date] if d]) ] if p]).strip(),
        "rep_name": current_user.full_name or current_user.email,
    }
    for q in wizard.get("questions", []):
        key = q.get("id")
        if key:
            ctx[str(key)] = (answers.get(key) or "").strip()
    body = template_engine.render(wizard.get("template", ""), ctx)
    return _clean_letter(body)



@letters_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_letters(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    letters = db.query(Letter).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(Letter.created_at.desc()).all()
    templates = template_engine.DEFAULT_TEMPLATES
    excluded = {
        "Welcome Letter",
        "Service Agreement Summary",
        "Evidence Request Checklist",
        "Missing Records Reconstruction",
        "C&P Exam Preparation",
        "Decision Review Summary",
        "Dependency Update Request",
        "GI Bill Education Benefits Request",
        "VR&E Request",
        "Records Release Authorization Cover Letter",
    }
    wizards = {k: v for k, v in template_engine.LETTER_WIZARDS.items() if k not in excluded}
    return render_template("letters/staff_letters.html", client=c, letters=letters, templates=templates, wizards=wizards)

@letters_bp.post("/client/<int:client_id>/post")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def post_letter(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    title = (current_user.full_name or "Your Team") + " - Update"
    body = (
        "This is a secure portal message.\n\n"
        "We reviewed your file and will follow up with next steps here.\n\n"
        f"- {current_user.full_name or current_user.email}\n"
        f"{current_user.organization.name}"
    )
    l = Letter(org_id=current_user.org_id, client_id=c.id, title=title, body=body, created_by_user_id=current_user.id, is_visible_to_client=True)
    db.add(l); db.commit()
    audit_log(current_user.org_id, current_user.id, "LETTER_POSTED", "Letter", l.id, detail=f"client_id={c.id}")
    flash("Letter posted to client portal.", "success")
    return redirect(url_for("letters.staff_letters", client_id=c.id))

@letters_bp.post("/client/<int:client_id>/generate")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def generate_letter(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    letter_name = (request.form.get("letter_name") or "").strip()
    custom_body = (request.form.get("custom_body") or "").strip()
    share_to_client = (request.form.get("share_to_client") or "").strip() == "1"
    ai_polish = (request.form.get("ai_polish") or "").strip() == "1"

    if not letter_name:
        flash("Choose a letter type.", "error")
        return redirect(url_for("letters.staff_letters", client_id=c.id))

    body = ""
    title = ""
    if letter_name == "custom":
        if not custom_body:
            flash("Custom letter requires body text.", "error")
            return redirect(url_for("letters.staff_letters", client_id=c.id))
        title = "Custom Letter - " + c.display_name()
        body = custom_body
    else:
        tmpl = next((t for t in template_engine.DEFAULT_TEMPLATES if t.get("name") == letter_name), None)
        if not tmpl:
            flash("Template not found.", "error")
            return redirect(url_for("letters.staff_letters", client_id=c.id))
        ctx = {
            "client_name": c.display_name(),
            "rep_name": current_user.full_name or current_user.email,
        }
        title = tmpl.get("name", "Letter")
        body = template_engine.render(tmpl.get("body", ""), ctx)

    if ai_polish:
        try:
            from flask import current_app
            api_key = current_app.config.get("OPENAI_API_KEY")
        except Exception:
            api_key = None
        if not api_key:
            flash("AI is not configured. Saved the template version.", "warning")
        else:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
                prompt = (
                    "Rewrite the following letter to be clear, professional, and plain-English. "
                    "Do not add legal advice or outcome guarantees. Keep the same intent and facts.\n\n"
                    f"{body}"
                )
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "Return the rewritten letter only."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                )
                polished = (resp.choices[0].message.content or "").strip()
                if polished:
                    body = polished
            except Exception:
                flash("AI rewrite failed. Saved the template version.", "warning")

    l = Letter(
        org_id=current_user.org_id,
        client_id=c.id,
        title=title,
        body=body,
        created_by_user_id=current_user.id,
        is_visible_to_client=share_to_client,
    )
    db.add(l); db.commit()
    audit_log(current_user.org_id, current_user.id, "LETTER_GENERATED", "Letter", l.id, detail=f"client_id={c.id}")
    flash("Letter created.", "success")
    return redirect(url_for("letters.staff_letters", client_id=c.id))



@letters_bp.post("/client/<int:client_id>/wizard-preview")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def wizard_preview_letter(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404

    letter_name = (request.form.get("letter_name") or "").strip()
    share_to_client = (request.form.get("share_to_client") or "").strip() == "1"
    ai_polish = (request.form.get("ai_polish") or "").strip() == "1"
    payload_raw = (request.form.get("wizard_payload") or "").strip()
    if not letter_name:
        flash("Choose a letter type.", "error")
        return redirect(url_for("letters.staff_letters", client_id=c.id))
    wizard = template_engine.LETTER_WIZARDS.get(letter_name)
    if not wizard:
        flash("Wizard not found for that letter type.", "error")
        return redirect(url_for("letters.staff_letters", client_id=c.id))

    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except Exception:
        payload = {}
    answers = payload.get("answers") or {}

    site_url = request.url_root.rstrip("/")
    body = _render_wizard_body(c, wizard, answers, site_url)
    return render_template(
        "letters/wizard_preview.html",
        client=c,
        letter_name=letter_name,
        body=body,
        wizard_payload=payload_raw,
        share_to_client=share_to_client,
        ai_polish=ai_polish,
    )


@letters_bp.post("/client/<int:client_id>/wizard-generate")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def wizard_generate_letter(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404

    letter_name = (request.form.get("letter_name") or "").strip()
    share_to_client = (request.form.get("share_to_client") or "").strip() == "1"
    ai_polish = (request.form.get("ai_polish") or "").strip() == "1"
    payload_raw = (request.form.get("wizard_payload") or "").strip()
    letter_body = (request.form.get("letter_body") or "").strip()
    if not letter_name:
        flash("Choose a letter type.", "error")
        return redirect(url_for("letters.staff_letters", client_id=c.id))
    wizard = template_engine.LETTER_WIZARDS.get(letter_name)
    if not wizard:
        flash("Wizard not found for that letter type.", "error")
        return redirect(url_for("letters.staff_letters", client_id=c.id))

    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except Exception:
        payload = {}
    answers = payload.get("answers") or {}

    site_url = request.url_root.rstrip("/")
    if letter_body:
        body = _clean_letter(letter_body)
    else:
        body = _render_wizard_body(c, wizard, answers, site_url)

    if ai_polish:
        try:
            api_key = current_app.config.get("OPENAI_API_KEY")
        except Exception:
            api_key = None
        if not api_key:
            flash("AI is not configured. Saved the template version.", "warning")
        else:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
                prompt = (
                    "Rewrite the following letter to be clear, professional, and plain-English. "
                    "Do not add legal advice or outcome guarantees. Keep the same intent and facts.\n\n"
                    f"{body}"
                )
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "Return the rewritten letter only."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                )
                polished = (resp.choices[0].message.content or "").strip()
                if polished:
                    body = _clean_letter(polished)
            except Exception:
                flash("AI rewrite failed. Saved the template version.", "warning")

    l = Letter(
        org_id=current_user.org_id,
        client_id=c.id,
        title=letter_name,
        body=body,
        created_by_user_id=current_user.id,
        is_visible_to_client=share_to_client,
    )
    db.add(l); db.commit()
    audit_log(current_user.org_id, current_user.id, "LETTER_GENERATED", "Letter", l.id, detail=f"client_id={c.id}")
    flash("Letter created from call script.", "success")
    return redirect(url_for("letters.staff_letters", client_id=c.id))


@letters_bp.post("/client/<int:client_id>/send/<int:letter_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def send_letter(client_id: int, letter_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    l = db.get(Letter, letter_id)
    if not l or l.org_id != current_user.org_id or l.client_id != c.id:
        return "Not found", 404
    if not c.email:
        flash("Client email is missing.", "error")
        return redirect(url_for("letters.staff_letters", client_id=c.id))

    site_name = current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
    subject = f"{l.title} - {site_name}"
    body_text = l.body or ""
    body_html = "<p>" + (l.body or "").replace("\n", "<br>") + "</p>"
    ok, err = send_email_html(c.email.strip().lower(), subject, body_text, body_html, context="CLIENT_LETTER", org_id=current_user.org_id, actor_user_id=current_user.id)
    if ok:
        audit_log(current_user.org_id, current_user.id, "LETTER_SENT", "Letter", l.id, detail=f"client_id={c.id}")
        flash("Letter emailed to client.", "success")
    else:
        flash(f"Email failed: {err}", "error")
    return redirect(url_for("letters.staff_letters", client_id=c.id))


@letters_bp.post("/client/<int:client_id>/delete/<int:letter_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def delete_letter(client_id: int, letter_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    l = db.get(Letter, letter_id)
    if not l or l.org_id != current_user.org_id or l.client_id != c.id:
        return "Not found", 404
    db.delete(l)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "LETTER_DELETED", "Letter", letter_id, detail=f"client_id={c.id}")
    flash("Letter deleted.", "success")
    return redirect(url_for("letters.staff_letters", client_id=c.id))


@letters_bp.post("/client/<int:client_id>/unshare/<int:letter_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def unshare_letter(client_id: int, letter_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    l = db.get(Letter, letter_id)
    if not l or l.org_id != current_user.org_id or l.client_id != c.id:
        return "Not found", 404
    l.is_visible_to_client = False
    db.commit()
    audit_log(current_user.org_id, current_user.id, "LETTER_UNSHARED", "Letter", letter_id, detail=f"client_id={c.id}")
    flash("Letter unshared from client portal.", "success")
    return redirect(url_for("letters.staff_letters", client_id=c.id))


@letters_bp.post("/client/<int:client_id>/share/<int:letter_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def share_letter(client_id: int, letter_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    l = db.get(Letter, letter_id)
    if not l or l.org_id != current_user.org_id or l.client_id != c.id:
        return "Not found", 404
    l.is_visible_to_client = True
    db.commit()
    audit_log(current_user.org_id, current_user.id, "LETTER_SHARED", "Letter", letter_id, detail=f"client_id={c.id}")
    flash("Letter shared to client portal.", "success")
    return redirect(url_for("letters.staff_letters", client_id=c.id))


