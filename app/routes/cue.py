import json
import os
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, send_file
from flask_login import login_required, current_user

from app.extensions import SessionLocal
from app.models import Client, CueMotion, CueErrorBlock, CueFile, CueDisclaimerAcceptance, Document
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.storage import save_upload
from app.services.docx_export import export_docx_named
from app.services.pdf_export import export_text_pdf
from app.services.audit_service import log as audit_log

cue_bp = Blueprint("cue", __name__)


def _portal_client(db):
    return db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()


def _staff_client(db, client_id: int):
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return None
    return c


def _get_disclaimer(db, client_id: int) -> bool:
    row = db.query(CueDisclaimerAcceptance).filter_by(org_id=current_user.org_id, client_id=client_id).first()
    return bool(row)


def _ensure_disclaimer(db, client_id: int) -> None:
    row = db.query(CueDisclaimerAcceptance).filter_by(org_id=current_user.org_id, client_id=client_id).first()
    if row:
        return
    entry = CueDisclaimerAcceptance(
        org_id=current_user.org_id,
        client_id=client_id,
        accepted_at=datetime.utcnow(),
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        user_agent=request.headers.get("User-Agent", ""),
    )
    db.add(entry)
    db.commit()


def _get_motion(db, client_id: int, motion_id: int | None = None) -> CueMotion:
    motion = None
    if motion_id:
        motion = db.get(CueMotion, motion_id)
        if motion and motion.org_id != current_user.org_id:
            motion = None
    if not motion:
        motion = (
            db.query(CueMotion)
            .filter_by(org_id=current_user.org_id, client_id=client_id)
            .order_by(CueMotion.updated_at.desc())
            .first()
        )
    if not motion:
        motion = CueMotion(org_id=current_user.org_id, client_id=client_id, created_by_user_id=current_user.id)
        db.add(motion)
        db.commit()
    return motion


def _get_file_row(db, motion: CueMotion) -> CueFile:
    row = db.query(CueFile).filter_by(cue_motion_id=motion.id).first()
    if not row:
        row = CueFile(cue_motion_id=motion.id)
        db.add(row)
        db.commit()
    return row


def _risk_flag(screener: dict) -> str:
    red_flags = [
        screener.get("new_evidence") == "yes",
        screener.get("weigh_evidence") == "yes",
    ]
    green_flags = [
        screener.get("wrong_law") == "yes",
        screener.get("missing_evidence") == "yes",
        screener.get("undebatable") == "yes",
    ]
    if any(red_flags):
        return "red"
    if any(green_flags):
        return "green"
    return "yellow"


def _compute_risk(db, motion: CueMotion, client_id: int) -> tuple[str, str]:
    try:
        screener = json.loads(motion.screener_json or "{}")
    except Exception:
        screener = {}

    if screener.get("new_evidence") == "yes" or screener.get("weigh_evidence") == "yes":
        return ("red", "Relies on new evidence or disagreement with how evidence was weighed.")

    if not motion.decision_date.strip() or not motion.issues_text.strip() or not motion.error_blocks:
        return ("red", "Missing required decision details or error allegations.")

    docs = db.query(Document).filter_by(org_id=current_user.org_id, client_id=client_id).all()
    has_docs = len(docs) > 0
    has_decision = any(
        ("decision" in (d.category or "").lower()) or ("decision" in (d.filename or "").lower())
        for d in docs
    )
    if not has_docs:
        return ("yellow", "No documents found in the client file.")
    if not has_decision:
        return ("yellow", "Decision letter not detected in the client file.")

    if screener.get("wrong_law") == "yes" or screener.get("missing_evidence") == "yes" or screener.get("undebatable") == "yes":
        return ("green", "Meets core CUE screening criteria and supporting records exist.")

    return ("yellow", "Possible, but higher risk without clear undebatable error evidence.")


def _extract_error_blocks(form) -> list[dict]:
    categories = form.getlist("error_category[]")
    va_errors = form.getlist("error_va_error[]")
    evidence = form.getlist("error_evidence[]")
    laws = form.getlist("error_law[]")
    outcomes = form.getlist("error_outcome[]")
    blocks = []
    count = max(len(categories), len(va_errors), len(evidence), len(laws), len(outcomes))
    for idx in range(count):
        blocks.append(
            {
                "category": (categories[idx] if idx < len(categories) else "").strip(),
                "va_error_text": (va_errors[idx] if idx < len(va_errors) else "").strip(),
                "evidence_text": (evidence[idx] if idx < len(evidence) else "").strip(),
                "law_text": (laws[idx] if idx < len(laws) else "").strip(),
                "outcome_text": (outcomes[idx] if idx < len(outcomes) else "").strip(),
            }
        )
    return [b for b in blocks if any(b.values())]


def _save_motion_from_form(db, motion: CueMotion, form, files) -> None:
    motion.decision_type = (form.get("decision_type") or "").strip()
    motion.decision_date = (form.get("decision_date") or "").strip()
    motion.decision_office = (form.get("decision_office") or "").strip()
    motion.issues_text = (form.get("issues_text") or "").strip()
    motion.appealed = (form.get("appealed") or "").strip()
    motion.appeal_outcome = (form.get("appeal_outcome") or "").strip()
    motion.appeal_date = (form.get("appeal_date") or "").strip()

    screener = {
        "new_evidence": (form.get("screener_new_evidence") or "").strip().lower(),
        "weigh_evidence": (form.get("screener_weigh_evidence") or "").strip().lower(),
        "wrong_law": (form.get("screener_wrong_law") or "").strip().lower(),
        "missing_evidence": (form.get("screener_missing_evidence") or "").strip().lower(),
        "undebatable": (form.get("screener_undebatable") or "").strip().lower(),
    }
    motion.screener_json = json.dumps(screener)
    motion.risk_flag = _risk_flag(screener)

    motion.remedy_requested = (form.get("remedy_requested") or "").strip()
    motion.requested_rating = (form.get("requested_rating") or "").strip()
    motion.requested_effective_date = (form.get("requested_effective_date") or "").strip()
    motion.veteran_statement = (form.get("veteran_statement") or "").strip()

    motion.full_legal_name = (form.get("full_legal_name") or "").strip()
    motion.file_number_last4 = (form.get("file_number_last4") or "").strip()
    motion.dob = (form.get("dob") or "").strip()
    motion.mailing_address = (form.get("mailing_address") or "").strip()
    motion.phone = (form.get("phone") or "").strip()
    motion.email = (form.get("email") or "").strip()
    motion.signature_name = (form.get("signature_name") or "").strip()
    motion.signature_date = (form.get("signature_date") or "").strip()
    motion.rep_name = (form.get("rep_name") or "").strip()
    motion.rep_org = (form.get("rep_org") or "").strip()
    motion.rep_contact = (form.get("rep_contact") or "").strip()
    motion.updated_at = datetime.utcnow()

    blocks = _extract_error_blocks(form)
    motion.error_blocks.clear()
    for b in blocks:
        motion.error_blocks.append(
            CueErrorBlock(
                category=b["category"],
                va_error_text=b["va_error_text"],
                evidence_text=b["evidence_text"],
                law_text=b["law_text"],
                outcome_text=b["outcome_text"],
            )
        )

    if files:
        signature_image = files.get("signature_image")
        if signature_image and (signature_image.filename or "").strip():
            full_path, _ = save_upload(current_app.config["STORAGE_ROOT"], motion.org_id, motion.client_id, signature_image)
            motion.signature_image_path = full_path

        decision_letter = files.get("decision_letter")
        if decision_letter and (decision_letter.filename or "").strip():
            full_path, _ = save_upload(current_app.config["STORAGE_ROOT"], motion.org_id, motion.client_id, decision_letter)
            file_row = _get_file_row(db, motion)
            file_row.decision_path = full_path

    db.commit()


def _validate_motion(motion: CueMotion) -> list[str]:
    errors = []
    if not motion.decision_date.strip():
        errors.append("Decision date is required.")
    if not motion.issues_text.strip():
        errors.append("Issue(s) challenged are required.")
    if not motion.error_blocks:
        errors.append("Add at least one CUE error allegation.")
    for idx, b in enumerate(motion.error_blocks, start=1):
        if not b.outcome_text.strip():
            errors.append(f"Error block #{idx} requires an outcome explanation.")
    return errors


def _last_name(name: str) -> str:
    parts = [p for p in (name or "").split(" ") if p]
    return parts[-1] if parts else "Client"


def _build_cue_body(motion: CueMotion) -> str:
    lines = []
    if motion.full_legal_name:
        lines.append(motion.full_legal_name)
    if motion.file_number_last4:
        lines.append(f"VA File Number / Last 4 SSN: {motion.file_number_last4}")
    if motion.mailing_address:
        lines.append(motion.mailing_address)
    contact = " | ".join([v for v in [motion.phone, motion.email] if v])
    if contact:
        lines.append(contact)
    lines.append(f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("Department of Veterans Affairs")
    lines.append("Attention: Evidence Intake Center")
    lines.append("")
    subject = f"RE: Motion for Revision Based on CUE - {motion.decision_date or 'Decision Date'} - {motion.issues_text or 'Issue(s)'}"
    lines.append(subject)
    lines.append("")
    lines.append(
        "This is a Motion for Revision Based on Clear and Unmistakable Error under 38 CFR 3.105(a). "
        "I challenge the decision identified above based on the record and law in effect at the time."
    )
    lines.append("")
    lines.append("Standard of Review")
    lines.append(
        "A motion for revision based on Clear and Unmistakable Error (CUE) is a collateral attack on a final VA decision. "
        "CUE exists when the correct facts, as they were known at the time, were not before the adjudicator, or when the "
        "statutory or regulatory provisions in effect at the time were incorrectly applied. The error must be undebatable, "
        "and had the error not been made, the outcome would have been manifestly different. This motion is based only on the "
        "evidence of record and the law in effect at the time of the challenged decision, consistent with 38 C.F.R. § 3.105(a)."
    )
    lines.append("")
    for idx, b in enumerate(motion.error_blocks, start=1):
        lines.append(f"Allegation #{idx}: {b.category or 'CUE Error'}")
        if b.va_error_text:
            lines.append(f"What VA did incorrectly: {b.va_error_text}")
        if b.evidence_text:
            lines.append(f"Evidence of record at the time: {b.evidence_text}")
        if b.law_text:
            lines.append(f"Law/rating criteria in effect: {b.law_text}")
        if b.outcome_text:
            lines.append(f"Outcome would have been different: {b.outcome_text}")
        lines.append("")
    lines.append("Relief Requested")
    relief = motion.remedy_requested or "Revision of the decision under 38 CFR 3.105(a)."
    lines.append(relief)
    if motion.requested_rating:
        lines.append(f"Requested rating: {motion.requested_rating}")
    if motion.requested_effective_date:
        lines.append(f"Requested effective date: {motion.requested_effective_date}")
    if motion.veteran_statement:
        lines.append(motion.veteran_statement)
    lines.append("")
    lines.append("Signature")
    sig = motion.signature_name or motion.full_legal_name or ""
    if sig:
        lines.append(sig)
    if motion.signature_date:
        lines.append(motion.signature_date)
    if motion.rep_name:
        rep_line = "Representative: " + motion.rep_name
        if motion.rep_org:
            rep_line += f" ({motion.rep_org})"
        lines.append(rep_line)
        if motion.rep_contact:
            lines.append(motion.rep_contact)
    lines.append("")
    lines.append("Attachments")
    lines.append("- Decision letter (if available)")
    for b in motion.error_blocks:
        if b.evidence_text:
            lines.append(f"- Evidence: {b.evidence_text}")
    lines.append("")
    lines.append("Disclaimer: This document was generated using an organizational drafting tool to assist with formatting and clarity. "
                 "It does not guarantee VA approval and is not legal advice. The veteran is responsible for ensuring accuracy and completeness prior to submission.")
    return "\n".join(lines)


def _generate_files(db, motion: CueMotion, client: Client) -> CueFile:
    title = "MOTION FOR REVISION BASED ON CUE - 38 CFR §3.105(a)"
    decision_date = (motion.decision_date or "Decision").replace("/", "-")
    last = _last_name(motion.full_legal_name or client.display_name())
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename_base = f"CUE_Motion_{last}_{decision_date}_Generated_{date_str}"
    body = _build_cue_body(motion)

    docx_path = export_docx_named(current_app.config["STORAGE_ROOT"], motion.org_id, motion.client_id, filename_base + ".docx", title, body)
    pdf_path = export_text_pdf(current_app.config["STORAGE_ROOT"], motion.org_id, motion.client_id, filename_base + ".pdf", title, body)

    file_row = _get_file_row(db, motion)
    file_row.docx_path = docx_path
    file_row.pdf_path = pdf_path
    db.commit()
    return file_row


@cue_bp.get("/portal/cue")
@login_required
def portal_builder():
    if current_user.role.value != "CLIENT":
        return redirect(url_for("clients.list_clients"))
    flash("CUE Motion Builder is available to staff only.", "error")
    return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        motion_id = request.args.get("motion_id", type=int)
        motion = _get_motion(db, client.id, motion_id)
        motions = db.query(CueMotion).filter_by(org_id=current_user.org_id, client_id=client.id).order_by(CueMotion.updated_at.desc()).all()
        accepted = _get_disclaimer(db, client.id)
        try:
            screener = json.loads(motion.screener_json or "{}")
        except Exception:
            screener = {}
        return render_template(
            "cue/builder.html",
            client=client,
            motion=motion,
            motions=motions,
            disclaimer_accepted=accepted,
            is_staff=False,
            screener=screener,
        )
    finally:
        db.close()


@cue_bp.post("/portal/cue/disclaimer")
@login_required
def portal_disclaimer():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    flash("CUE Motion Builder is available to staff only.", "error")
    return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        _ensure_disclaimer(db, client.id)
        flash("Disclaimer accepted. You can continue.", "success")
        return redirect(url_for("cue.portal_builder"))
    finally:
        db.close()


@cue_bp.post("/portal/cue/save")
@login_required
def portal_save():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    flash("CUE Motion Builder is available to staff only.", "error")
    return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        motion_id = request.form.get("motion_id", type=int)
        motion = _get_motion(db, client.id, motion_id)
        if not _get_disclaimer(db, client.id):
            if request.args.get("autosave") == "1":
                return jsonify({"error": "Disclaimer not accepted."}), 400
            flash("You must accept the disclaimer first.", "error")
            return redirect(url_for("cue.portal_builder", motion_id=motion.id))

        _save_motion_from_form(db, motion, request.form, request.files)
        action = (request.form.get("action") or "save").strip().lower()
        if action == "generate":
            errors = _validate_motion(motion)
            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("cue.portal_builder", motion_id=motion.id))
            _generate_files(db, motion, client)
            motion.status = "generated"
            motion.updated_at = datetime.utcnow()
            db.commit()
            audit_log(current_user.org_id, current_user.id, "CUE_GENERATED", "CueMotion", motion.id, detail=client.display_name())
            flash("CUE Motion generated.", "success")
            return redirect(url_for("cue.portal_submit", motion_id=motion.id))

        if request.args.get("autosave") == "1":
            return jsonify({"ok": True, "updated_at": motion.updated_at.isoformat()})
        flash("Draft saved.", "success")
        return redirect(url_for("cue.portal_builder", motion_id=motion.id))
    finally:
        db.close()


@cue_bp.get("/portal/cue/submit/<int:motion_id>")
@login_required
def portal_submit(motion_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    flash("CUE Motion Builder is available to staff only.", "error")
    return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.client_id != client.id or motion.org_id != current_user.org_id:
            return "Not found", 404
        file_row = db.query(CueFile).filter_by(cue_motion_id=motion.id).first()
        return render_template("cue/submit.html", client=client, motion=motion, file_row=file_row, is_staff=False)
    finally:
        db.close()


@cue_bp.post("/portal/cue/submit/<int:motion_id>")
@login_required
def portal_mark_submitted(motion_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    flash("CUE Motion Builder is available to staff only.", "error")
    return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.client_id != client.id or motion.org_id != current_user.org_id:
            return "Not found", 404
        method = (request.form.get("submission_method") or "").strip()
        motion.submission_method = method
        motion.submission_date = datetime.utcnow().strftime("%Y-%m-%d")
        motion.status = "submitted"
        confirmation = request.files.get("confirmation_upload")
        if confirmation and (confirmation.filename or "").strip():
            full_path, _ = save_upload(current_app.config["STORAGE_ROOT"], motion.org_id, motion.client_id, confirmation)
            file_row = _get_file_row(db, motion)
            file_row.confirmation_path = full_path
        db.commit()
        flash("Submission recorded.", "success")
        return redirect(url_for("cue.portal_submit", motion_id=motion.id))
    finally:
        db.close()


@cue_bp.get("/portal/cue/download/<int:motion_id>/<string:kind>")
@login_required
def portal_download(motion_id: int, kind: str):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    flash("CUE Motion Builder is available to staff only.", "error")
    return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.client_id != client.id or motion.org_id != current_user.org_id:
            return "Not found", 404
        file_row = db.query(CueFile).filter_by(cue_motion_id=motion.id).first()
        if not file_row:
            return "Not found", 404
        path = ""
        if kind == "docx":
            path = file_row.docx_path
        elif kind == "pdf":
            path = file_row.pdf_path
        elif kind == "decision":
            path = file_row.decision_path
        elif kind == "confirmation":
            path = file_row.confirmation_path
        if not path or not os.path.exists(path):
            return "Not found", 404
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    finally:
        db.close()


@cue_bp.get("/employee/cue/download/<int:motion_id>/<string:kind>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_download(motion_id: int, kind: str):
    db = SessionLocal()
    try:
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.org_id != current_user.org_id:
            return "Not found", 404
        file_row = db.query(CueFile).filter_by(cue_motion_id=motion.id).first()
        if not file_row:
            return "Not found", 404
        path = ""
        if kind == "docx":
            path = file_row.docx_path
        elif kind == "pdf":
            path = file_row.pdf_path
        elif kind == "decision":
            path = file_row.decision_path
        elif kind == "confirmation":
            path = file_row.confirmation_path
        if not path or not os.path.exists(path):
            return "Not found", 404
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    finally:
        db.close()


@cue_bp.get("/employee/cue/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_builder(client_id: int):
    db = SessionLocal()
    try:
        client = _staff_client(db, client_id)
        if not client:
            return "Not found", 404
        motion_id = request.args.get("motion_id", type=int)
        motion = _get_motion(db, client.id, motion_id)
        risk_flag, risk_note = _compute_risk(db, motion, client.id)
        motions = db.query(CueMotion).filter_by(org_id=current_user.org_id, client_id=client.id).order_by(CueMotion.updated_at.desc()).all()
        accepted = _get_disclaimer(db, client.id)
        try:
            screener = json.loads(motion.screener_json or "{}")
        except Exception:
            screener = {}
        return render_template(
            "cue/builder.html",
            client=client,
            motion=motion,
            motions=motions,
            disclaimer_accepted=accepted,
            is_staff=True,
            screener=screener,
            risk_flag=risk_flag,
            risk_note=risk_note,
        )
    finally:
        db.close()


@cue_bp.post("/employee/cue/<int:client_id>/disclaimer")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_disclaimer(client_id: int):
    db = SessionLocal()
    try:
        client = _staff_client(db, client_id)
        if not client:
            return "Not found", 404
        _ensure_disclaimer(db, client.id)
        flash("Disclaimer accepted.", "success")
        return redirect(url_for("cue.staff_builder", client_id=client.id))
    finally:
        db.close()


@cue_bp.post("/employee/cue/<int:client_id>/save")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_save(client_id: int):
    db = SessionLocal()
    try:
        client = _staff_client(db, client_id)
        if not client:
            return "Not found", 404
        motion_id = request.form.get("motion_id", type=int)
        motion = _get_motion(db, client.id, motion_id)
        if not _get_disclaimer(db, client.id):
            if request.args.get("autosave") == "1":
                return jsonify({"error": "Disclaimer not accepted."}), 400
            flash("You must accept the disclaimer first.", "error")
            return redirect(url_for("cue.staff_builder", client_id=client.id, motion_id=motion.id))

        _save_motion_from_form(db, motion, request.form, request.files)
        action = (request.form.get("action") or "save").strip().lower()
        if action == "generate":
            errors = _validate_motion(motion)
            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("cue.staff_builder", client_id=client.id, motion_id=motion.id))
            _generate_files(db, motion, client)
            risk_flag, risk_note = _compute_risk(db, motion, client.id)
            motion.risk_flag = risk_flag
            motion.notes = risk_note
            motion.status = "generated"
            motion.updated_at = datetime.utcnow()
            db.commit()
            audit_log(current_user.org_id, current_user.id, "CUE_GENERATED", "CueMotion", motion.id, detail=client.display_name())
            flash("CUE Motion generated.", "success")
            return redirect(url_for("cue.staff_submit", client_id=client.id, motion_id=motion.id))

        if request.args.get("autosave") == "1":
            return jsonify({"ok": True, "updated_at": motion.updated_at.isoformat()})
        flash("Draft saved.", "success")
        return redirect(url_for("cue.staff_builder", client_id=client.id, motion_id=motion.id))
    finally:
        db.close()


@cue_bp.get("/employee/cue/<int:client_id>/submit/<int:motion_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_submit(client_id: int, motion_id: int):
    db = SessionLocal()
    try:
        client = _staff_client(db, client_id)
        if not client:
            return "Not found", 404
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.client_id != client.id or motion.org_id != current_user.org_id:
            return "Not found", 404
        file_row = db.query(CueFile).filter_by(cue_motion_id=motion.id).first()
        return render_template("cue/submit.html", client=client, motion=motion, file_row=file_row, is_staff=True)
    finally:
        db.close()


@cue_bp.post("/employee/cue/<int:client_id>/submit/<int:motion_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_mark_submitted(client_id: int, motion_id: int):
    db = SessionLocal()
    try:
        client = _staff_client(db, client_id)
        if not client:
            return "Not found", 404
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.client_id != client.id or motion.org_id != current_user.org_id:
            return "Not found", 404
        method = (request.form.get("submission_method") or "").strip()
        motion.submission_method = method
        motion.submission_date = datetime.utcnow().strftime("%Y-%m-%d")
        motion.status = "submitted"
        confirmation = request.files.get("confirmation_upload")
        if confirmation and (confirmation.filename or "").strip():
            full_path, _ = save_upload(current_app.config["STORAGE_ROOT"], motion.org_id, motion.client_id, confirmation)
            file_row = _get_file_row(db, motion)
            file_row.confirmation_path = full_path
        db.commit()
        flash("Submission recorded.", "success")
        return redirect(url_for("cue.staff_submit", client_id=client.id, motion_id=motion.id))
    finally:
        db.close()


@cue_bp.get("/employee/cue")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_list():
    db = SessionLocal()
    try:
        motions = db.query(CueMotion).filter_by(org_id=current_user.org_id).order_by(CueMotion.updated_at.desc()).all()
        clients = db.query(Client).filter_by(org_id=current_user.org_id).all()
        client_map = {c.id: c.display_name() for c in clients}
        return render_template("cue/list.html", motions=motions, client_map=client_map)
    finally:
        db.close()


@cue_bp.post("/employee/cue/<int:motion_id>/delete")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_delete_motion(motion_id: int):
    db = SessionLocal()
    try:
        motion = db.get(CueMotion, motion_id)
        if not motion or motion.org_id != current_user.org_id:
            return "Not found", 404
        file_row = db.query(CueFile).filter_by(cue_motion_id=motion.id).first()
        if file_row:
            for path in [file_row.docx_path, file_row.pdf_path, file_row.decision_path, file_row.confirmation_path]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            db.delete(file_row)
        db.delete(motion)
        db.commit()
        flash("CUE motion deleted.", "success")
        return redirect(url_for("cue.staff_list"))
    finally:
        db.close()


@cue_bp.get("/portal/filing-guide")
@login_required
def portal_filing_guide():
    if current_user.role.value != "CLIENT":
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    try:
        client = _portal_client(db)
        if not client:
            return "Not found", 404
        return render_template("portal/filing_guide.html", client=client, is_staff=False)
    finally:
        db.close()


@cue_bp.get("/employee/filing-guide/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_filing_guide(client_id: int):
    db = SessionLocal()
    try:
        client = _staff_client(db, client_id)
        if not client:
            return "Not found", 404
        return render_template("portal/filing_guide.html", client=client, is_staff=True)
    finally:
        db.close()
