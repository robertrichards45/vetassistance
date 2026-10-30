"""Claim builder: AI-recommended claims, then a filled 21-526EZ and 21-4138 statements for staff review."""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import SessionLocal
from app.models import ClaimPacket, Client, Document, Letter, User
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.audit_service import log as audit_log

claims_bp = Blueprint("claims", __name__, url_prefix="/claims")
STAFF = (Role.DIRECTOR, Role.EMPLOYEE)
RUNNING = ("QUEUED", "READING", "ANALYZING", "DRAFTING")


def _client(client_id: int) -> Client:
    c = SessionLocal().get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        abort(404)
    return c


def _packet(client_id: int) -> ClaimPacket | None:
    return (SessionLocal().query(ClaimPacket).filter_by(org_id=current_user.org_id, client_id=client_id)
            .order_by(ClaimPacket.created_at.desc()).first())


def _stale(p: ClaimPacket) -> bool:
    return p.status in RUNNING and (datetime.utcnow() - (p.updated_at or p.created_at)).total_seconds() > 1200


def _loads(raw: str, default):
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _ssn(c: Client) -> str:
    from app.services.ssn_crypto import decrypt_ssn
    raw = getattr(c, "ssn_encrypted", "") or ""
    val = decrypt_ssn(raw) if raw else ""
    if not val and len(re.sub(r"\D", "", c.ssn_full or "")) == 9:  # older records stored before encryption
        val = c.ssn_full
    return val


@claims_bp.get("/client/<int:client_id>")
@login_required
@require_roles(*STAFF)
def builder(client_id: int):
    c = _client(client_id)
    db = SessionLocal()
    p = _packet(c.id)
    if p and _stale(p):
        p.status, p.error = ("READY" if p.analysis_json else "FAILED"), "The last run stopped before finishing. Run it again."
        db.merge(p); db.commit()
    docs = db.query(Document).filter_by(org_id=c.org_id, client_id=c.id).all()
    portal_user = db.get(User, c.portal_user_id) if c.portal_user_id else None
    return render_template(
        "claims/builder.html", client=c, packet=p, portal_user=portal_user, tool_title="Claim builder",
        analysis=_loads(p.analysis_json, {}) if p else {}, selected=_loads(p.selected_json, []) if p else [],
        form=_loads(p.form_json, {}) if p else {}, statements=_loads(p.statements_json, []) if p else [],
        docs=docs, docs_read=sum(1 for d in docs if d.extracted_text_path), running=bool(p and p.status in RUNNING),
        ai_ok=bool(current_app.config.get("OPENAI_API_KEY")), has_ssn=bool(_ssn(c)),
    )


@claims_bp.post("/client/<int:client_id>/analyze")
@login_required
@require_roles(*STAFF)
def analyze(client_id: int):
    from app.services.claim_builder import run_analysis
    from app.services.queue import enqueue
    c = _client(client_id)
    p = _packet(c.id)
    if p and p.status in RUNNING and not _stale(p):
        return redirect(url_for("claims.builder", client_id=c.id))
    db = SessionLocal()
    p = ClaimPacket(org_id=c.org_id, client_id=c.id, created_by_user_id=current_user.id, status="QUEUED")
    db.add(p); db.commit()
    audit_log(current_user.org_id, current_user.id, "CLAIM_ANALYSIS_STARTED", "Client", c.id)
    enqueue(run_analysis, p.id, queue="ai")
    return redirect(url_for("claims.builder", client_id=c.id))


@claims_bp.get("/client/<int:client_id>/status")
@login_required
@require_roles(*STAFF)
def status(client_id: int):
    p = _packet(_client(client_id).id)
    if not p:
        return jsonify({"status": None})
    return jsonify({"status": p.status, "error": (p.error or "")[:400],
                    "statements": len(_loads(p.statements_json, [])) if p.status == "DRAFTING" else None})


@claims_bp.post("/client/<int:client_id>/draft")
@login_required
@require_roles(*STAFF)
def draft(client_id: int):
    from app.services.claim_builder import run_drafting
    from app.services.queue import enqueue
    c = _client(client_id)
    p = _packet(c.id)
    if not p or not p.analysis_json:
        flash("Run the analysis first.", "error")
        return redirect(url_for("claims.builder", client_id=c.id))
    n = len(_loads(p.analysis_json, {}).get("claims") or [])
    chosen = sorted({int(i) for i in request.form.getlist("claim") if i.isdigit() and int(i) < n})
    if not chosen:
        flash("Choose at least one claim to put on the forms.", "error")
        return redirect(url_for("claims.builder", client_id=c.id))
    db = SessionLocal()
    p = db.get(ClaimPacket, p.id)
    p.selected_json = json.dumps(chosen)
    p.statements_json, p.form_json, p.status, p.error = "", "", "DRAFTING", ""
    p.updated_at = datetime.utcnow()
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLAIM_FORMS_DRAFTED", "Client", c.id, detail=f"claims={len(chosen)}")
    enqueue(run_drafting, p.id, queue="ai")
    return redirect(url_for("claims.builder", client_id=c.id) + "#forms")


def _form_from_request(old: dict) -> dict:
    """Rebuild the editable 526EZ answers from the review form, keeping the shape build_form() makes."""
    g = request.form.get
    form = json.loads(json.dumps(old or {}))
    form["claim_process"] = "FDC" if g("claim_process") == "FDC" else "STANDARD"
    for section, keys in {
        "id": ["first", "mi", "last", "va_file", "filed_before", "dob", "service_number", "phone", "email",
               "street", "apt", "city", "state", "country", "zip"],
        "service": ["other_names", "branch", "component", "entry", "exit", "place_sep", "addl_from", "addl_to",
                    "combat_911", "reserves", "pow", "retired_pay", "sep_pay"],
        "exposure": ["claiming_toxic", "gulf_war", "gw_from", "gw_to", "herbicide", "herb_locations", "herb_from",
                     "herb_to", "other", "exp_from", "exp_to", "additional"],
    }.items():
        form.setdefault(section, {})
        for k in keys:
            v = g(f"{section}.{k}")
            if v is not None:
                form[section][k] = v.strip()
    form["id"]["electronic"] = g("id.electronic") == "1"
    for k in ("asbestos", "shad", "camp_lejeune", "mustard_gas", "mos_toxin", "radiation"):
        form["exposure"][k] = g(f"exposure.{k}") == "1"
    rows = []
    for i in range(40):
        if g(f"claim.{i}.condition") is None:
            break
        if g(f"claim.{i}.remove") == "1":
            continue
        rows.append({k: (g(f"claim.{i}.{k}") or "").strip() for k in ("condition", "event", "explanation", "date")})
    form["claims"] = [r for r in rows if r["condition"]]
    tr = []
    for i in range(3):
        t = (g(f"treat.{i}.text") or "").strip()
        if t:
            d = (g(f"treat.{i}.date") or "").strip()
            tr.append({"text": t, "date": d, "no_date": not d})
    form["treatment"] = tr
    return form


@claims_bp.post("/client/<int:client_id>/save")
@login_required
@require_roles(*STAFF)
def save(client_id: int):
    c = _client(client_id)
    p = _packet(c.id)
    if not p or not p.form_json:
        abort(400)
    db = SessionLocal()
    p = db.get(ClaimPacket, p.id)
    p.form_json = json.dumps(_form_from_request(_loads(p.form_json, {})))
    sts = _loads(p.statements_json, [])
    for i, st in enumerate(sts):
        v = request.form.get(f"statement.{i}")
        if v is not None:
            st["text"] = v.replace("\r\n", "\n").strip()
            st["placeholders"] = re.findall(r"\[VETERAN TO CONFIRM:[^\]]*\]", st["text"])
    p.statements_json = json.dumps(sts)
    p.updated_at = datetime.utcnow()
    db.commit()
    flash("Saved.", "success")
    nxt = request.form.get("next") or ""
    if nxt in ("526", "packet") or nxt.startswith("4138-"):
        return redirect(url_for("claims.pdf", client_id=c.id, which=nxt))
    return redirect(url_for("claims.builder", client_id=c.id) + "#forms")


@claims_bp.get("/client/<int:client_id>/pdf/<which>")
@login_required
@require_roles(*STAFF)
def pdf(client_id: int, which: str):
    from app.services import claim_pdf
    c = _client(client_id)
    p = _packet(c.id)
    if not p or not p.form_json:
        abort(404)
    form = _loads(p.form_json, {})
    sts = _loads(p.statements_json, [])
    ssn = _ssn(c)
    last = re.sub(r"[^A-Za-z]", "", c.last_name or "veteran") or "veteran"
    if which == "526":
        data, _ = claim_pdf.fill_526(form, ssn)
        name = f"VA-21-526EZ-{last}.pdf"
    elif which.startswith("4138-") and which[5:].isdigit() and int(which[5:]) < len(sts):
        st = sts[int(which[5:])]
        data, _ = claim_pdf.fill_4138(form, st["text"], ssn)
        name = f"VA-21-4138-{last}-{re.sub(r'[^A-Za-z0-9]+', '-', st['condition'])[:40]}.pdf"
    elif which == "packet":
        f526, notes = claim_pdf.fill_526(form, ssn)
        parts = [f526]
        for st in sts:
            parts.append(claim_pdf.fill_4138(form, st["text"], ssn)[0])
        open_items = sum(len(re.findall(r"\[VETERAN TO CONFIRM:", st["text"])) for st in sts)
        if open_items:
            notes = notes + [f"{open_items} [VETERAN TO CONFIRM] item{'s' if open_items != 1 else ''} still need the veteran's answer."]
        if not ssn:
            notes = notes + ["No SSN on file: the veteran must write it on every page."]
        cover = claim_pdf.cover_sheet(form, [r.get("condition", "") for r in form.get("claims") or []], len(sts), notes)
        data = claim_pdf.merge([cover] + parts)
        name = f"Claim-packet-{last}.pdf"
    else:
        abort(404)
    audit_log(current_user.org_id, current_user.id, "CLAIM_PDF_GENERATED", "Client", c.id, detail=which)
    disp = "attachment" if request.args.get("dl") else "inline"
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{name}"', "Cache-Control": "no-store"})


@claims_bp.post("/client/<int:client_id>/statement/<int:idx>/to-letters")
@login_required
@require_roles(*STAFF)
def statement_to_letters(client_id: int, idx: int):
    c = _client(client_id)
    p = _packet(c.id)
    sts = _loads(p.statements_json, []) if p else []
    if idx >= len(sts):
        abort(404)
    db = SessionLocal()
    db.add(Letter(org_id=c.org_id, client_id=c.id, created_by_user_id=current_user.id, is_visible_to_client=False,
                  title=f"21-4138 statement draft: {sts[idx]['condition']}"[:250], body=sts[idx]["text"][:60000]))
    db.commit()
    flash("Saved to Letters as a team-only draft. Share it from there when the veteran should review it.", "success")
    return redirect(url_for("claims.builder", client_id=c.id) + f"#st{idx}")
