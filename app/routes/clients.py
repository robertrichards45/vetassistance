from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.extensions import SessionLocal
from app.models import Client, User, MessageThread, Message, Document, Letter, ClientTask, FormData, Agreement, RenderedArtifact, AIRun, DocumentTag, ClientPayment, VACallLog, ClientNote
from app.services.onboarding import ensure_tasks, post_welcome_letter
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.audit_service import log as audit_log
from app.services.alerts_service import create_alert
from app.services.email_service import send_email_html, build_portal_invite
from app.services.ssn_crypto import encrypt_ssn, decrypt_ssn
import secrets
from werkzeug.security import generate_password_hash
import os

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")

@clients_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def list_clients():
    db = SessionLocal()
    q = (request.args.get("q") or "").strip()
    show_archived = (request.args.get("archived") == "1")
    stmt = select(Client).where(Client.org_id == current_user.org_id)
    if not show_archived:
        stmt = stmt.where(Client.is_archived == False)
    if q:
        stmt = stmt.where((Client.first_name.ilike(f"%{q}%")) | (Client.last_name.ilike(f"%{q}%")) | (Client.email.ilike(f"%{q}%")))
    clients = db.execute(stmt.order_by(Client.created_at.desc())).scalars().all()
    user_ids = [c.portal_user_id for c in clients if c.portal_user_id]
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: u for u in users}
    client_ids = [c.id for c in clients]
    crsc_map = {}
    if client_ids:
        fd_rows = (
            db.query(FormData)
            .filter(
                FormData.org_id == current_user.org_id,
                FormData.client_id.in_(client_ids),
                FormData.form_key == "CRSC_INTAKE",
            )
            .all()
        )
        intake_ids = {r.client_id for r in fd_rows if r.data_json}
        artifacts = (
            db.query(RenderedArtifact)
            .filter(
                RenderedArtifact.org_id == current_user.org_id,
                RenderedArtifact.client_id.in_(client_ids),
                RenderedArtifact.artifact_type.in_(["CRSC", "CRSC_NEXUS"]),
            )
            .all()
        )
        packet_ids = {a.client_id for a in artifacts if a.artifact_type == "CRSC"}
        nexus_ids = {a.client_id for a in artifacts if a.artifact_type == "CRSC_NEXUS"}
        for c in clients:
            has_intake = c.id in intake_ids
            has_packet = c.id in packet_ids
            has_nexus = c.id in nexus_ids
            if has_intake and has_packet and has_nexus:
                crsc_map[c.id] = {"label": "Ready", "class": "pill ready", "sort": "ready"}
            elif has_intake or has_packet or has_nexus:
                crsc_map[c.id] = {"label": "In progress", "class": "pill warn", "sort": "in_progress"}
            else:
                if (c.retirement_status or "").strip() or (c.va_rating_percent or 0) > 0:
                    crsc_map[c.id] = {"label": "Not started", "class": "pill warn", "sort": "not_started"}
                else:
                    crsc_map[c.id] = {"label": "Not flagged", "class": "pill", "sort": "not_flagged"}
    return render_template("clients/list.html", clients=clients, q=q, show_archived=show_archived, user_map=user_map, crsc_map=crsc_map)

@clients_bp.get("/new")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def new_client():
    return render_template("clients/new.html")

@clients_bp.post("/new")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def new_client_post():
    db = SessionLocal()
    c = Client(org_id=current_user.org_id,
               first_name=request.form.get("first_name",""),
               last_name=request.form.get("last_name",""),
               email=request.form.get("email",""),
               phone=request.form.get("phone",""),
               notes=request.form.get("notes",""),
               assigned_user_id=current_user.id)
    db.add(c); db.commit()
    # ensure thread exists
    th = MessageThread(org_id=current_user.org_id, client_id=c.id, subject="Secure Messages")
    db.add(th); db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_CREATED", "Client", c.id)
    flash("Client created.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))

@clients_bp.get("/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def view_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    portal_user = db.get(User, c.portal_user_id) if c.portal_user_id else None
    thread = db.query(MessageThread).filter_by(org_id=current_user.org_id, client_id=c.id).first()
    employees = db.query(User).filter(User.org_id == current_user.org_id, User.role.in_([Role.DIRECTOR, Role.EMPLOYEE])).all()
    payments = db.query(ClientPayment).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(ClientPayment.created_at.desc()).limit(20).all()
    call_logs = (
        db.query(VACallLog)
        .filter_by(org_id=current_user.org_id, client_id=c.id)
        .order_by(VACallLog.created_at.desc())
        .limit(25)
        .all()
    )
    return render_template("clients/view.html", client=c, thread=thread, employees=employees, payments=payments, call_logs=call_logs, portal_user=portal_user)


@clients_bp.get("/<int:client_id>/notes")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_notes(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    notes = (
        db.query(ClientNote)
        .filter_by(org_id=current_user.org_id, client_id=c.id)
        .order_by(ClientNote.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template("clients/notes.html", client=c, notes=notes)


@clients_bp.post("/<int:client_id>/notes")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_notes_post(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    body = (request.form.get("body") or "").strip()
    tags = (request.form.get("tags") or "").strip()
    is_internal = request.form.get("is_internal") == "1"
    if not body:
        flash("Note cannot be empty.", "error")
        return redirect(url_for("clients.client_notes", client_id=c.id))
    note = ClientNote(
        org_id=current_user.org_id,
        client_id=c.id,
        created_by_user_id=current_user.id,
        is_internal=is_internal,
        body=body,
        tags=tags,
    )
    db.add(note)
    db.commit()
    flash("Note saved.", "success")
    return redirect(url_for("clients.client_notes", client_id=c.id))


@clients_bp.post("/<int:client_id>/notes/<int:note_id>/edit")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_notes_edit(client_id: int, note_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    note = db.get(ClientNote, note_id)
    if not note or note.client_id != c.id or note.org_id != current_user.org_id:
        return "Not found", 404
    note.body = (request.form.get("body") or "").strip()
    note.tags = (request.form.get("tags") or "").strip()
    note.is_internal = request.form.get("is_internal") == "1"
    db.commit()
    flash("Note updated.", "success")
    return redirect(url_for("clients.client_notes", client_id=c.id))


@clients_bp.post("/<int:client_id>/notes/<int:note_id>/delete")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_notes_delete(client_id: int, note_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    note = db.get(ClientNote, note_id)
    if not note or note.client_id != c.id or note.org_id != current_user.org_id:
        return "Not found", 404
    db.delete(note)
    db.commit()
    flash("Note deleted.", "success")
    return redirect(url_for("clients.client_notes", client_id=c.id))


@clients_bp.post("/<int:client_id>/details")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def update_details(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    prev_rating = c.va_rating_percent or 0
    c.full_legal_name = (request.form.get("full_legal_name") or "").strip()
    raw_ssn = (request.form.get("ssn_full") or "").strip()
    if raw_ssn:
        c.ssn_encrypted = encrypt_ssn(raw_ssn)
        c.ssn_last4 = raw_ssn[-4:] if len(raw_ssn) >= 4 else raw_ssn
        c.ssn_full = ""
    c.dob = (request.form.get("dob") or "").strip()
    c.phone = (request.form.get("phone") or "").strip()
    c.mailing_address1 = (request.form.get("mailing_address1") or "").strip()
    c.mailing_address2 = (request.form.get("mailing_address2") or "").strip()
    c.mailing_city = (request.form.get("mailing_city") or "").strip()
    c.mailing_state = (request.form.get("mailing_state") or "").strip()
    c.mailing_zip = (request.form.get("mailing_zip") or "").strip()
    c.service_entry_date = (request.form.get("service_entry_date") or "").strip()
    c.service_discharge_date = (request.form.get("service_discharge_date") or "").strip()
    c.service_branch = (request.form.get("service_branch") or "").strip()
    c.retirement_status = (request.form.get("retirement_status") or "").strip()
    rating_raw = (request.form.get("va_rating_percent") or "").strip()
    try:
        c.va_rating_percent = max(0, min(100, int(rating_raw))) if rating_raw else c.va_rating_percent
    except Exception:
        pass
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_DETAILS_UPDATED", "Client", c.id)
    # CRSC alert on VA rating change for retirees
    if c.va_rating_percent != prev_rating and (c.retirement_status or "").lower() in ("regular retirement", "medical retirement", "tdrl/pdrl"):
        recipients = []
        if c.assigned_user_id:
            recipients.append(c.assigned_user_id)
        directors = db.query(User).filter(User.org_id == current_user.org_id, User.role == Role.DIRECTOR).all()
        recipients.extend([d.id for d in directors])
        recipients = list({r for r in recipients if r})
        for rid in recipients:
            create_alert(
                user_id=rid,
                alert_type="crsc",
                source_key=f"crsc_rating_change:{c.id}:{c.va_rating_percent}",
                title=f"CRSC review suggested for {c.display_name()}",
                body=f"VA rating changed to {c.va_rating_percent}%. Retiree status: {c.retirement_status}. Consider CRSC review.",
            )
    flash("Client details updated.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))


@clients_bp.post("/<int:client_id>/notes")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def update_notes(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    c.notes = (request.form.get("notes") or "").strip()
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_NOTES_UPDATED", "Client", c.id)
    flash("Client notes updated.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))


@clients_bp.post("/<int:client_id>/va-calls")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def add_va_call_log(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    call_date = (request.form.get("call_date") or "").strip()
    call_time = (request.form.get("call_time") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    agent_name = (request.form.get("agent_name") or "").strip()
    reference_id = (request.form.get("reference_id") or "").strip()
    outcome = (request.form.get("outcome") or "").strip()
    summary = (request.form.get("summary") or "").strip()
    next_steps = (request.form.get("next_steps") or "").strip()
    if not summary:
        flash("Please add a summary of the call.", "error")
        return redirect(url_for("clients.view_client", client_id=c.id) + "#va-calls")
    row = VACallLog(
        org_id=current_user.org_id,
        client_id=c.id,
        call_date=call_date[:20],
        call_time=call_time[:20],
        phone=phone[:60],
        call_type=(request.form.get("call_type") or "").strip()[:80],
        topic=(request.form.get("topic") or "").strip()[:120],
        agent_name=agent_name[:120],
        reference_id=reference_id[:120],
        outcome=outcome[:200],
        summary=summary[:2000],
        next_steps=next_steps[:2000],
    )
    db.add(row)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "VA_CALL_LOG_CREATED", "Client", c.id)
    flash("Call log saved.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id) + "#va-calls")


@clients_bp.post("/<int:client_id>/va-calls/<int:log_id>/edit")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def edit_va_call_log(client_id: int, log_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    log = db.get(VACallLog, log_id)
    if not log or log.org_id != current_user.org_id or log.client_id != c.id:
        return "Not found", 404
    log.call_date = (request.form.get("call_date") or log.call_date or "").strip()[:20]
    log.call_time = (request.form.get("call_time") or log.call_time or "").strip()[:20]
    log.phone = (request.form.get("phone") or log.phone or "").strip()[:60]
    log.call_type = (request.form.get("call_type") or log.call_type or "").strip()[:80]
    log.topic = (request.form.get("topic") or log.topic or "").strip()[:120]
    log.agent_name = (request.form.get("agent_name") or log.agent_name or "").strip()[:120]
    log.reference_id = (request.form.get("reference_id") or log.reference_id or "").strip()[:120]
    log.outcome = (request.form.get("outcome") or log.outcome or "").strip()[:200]
    summary = (request.form.get("summary") or log.summary or "").strip()
    if not summary:
        flash("Summary cannot be blank.", "error")
        return redirect(url_for("clients.view_client", client_id=c.id) + "#va-calls")
    log.summary = summary[:2000]
    log.next_steps = (request.form.get("next_steps") or log.next_steps or "").strip()[:2000]
    db.commit()
    audit_log(current_user.org_id, current_user.id, "VA_CALL_LOG_UPDATED", "Client", c.id, detail=str(log.id))
    flash("Call log updated.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id) + "#va-calls")


@clients_bp.post("/<int:client_id>/va-calls/<int:log_id>/delete")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def delete_va_call_log(client_id: int, log_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    log = db.get(VACallLog, log_id)
    if not log or log.org_id != current_user.org_id or log.client_id != c.id:
        return "Not found", 404
    db.delete(log)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "VA_CALL_LOG_DELETED", "Client", c.id, detail=str(log.id))
    flash("Call log deleted.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id) + "#va-calls")

@clients_bp.post("/<int:client_id>/assign")
@login_required
@require_roles(Role.DIRECTOR)
def assign_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    assigned = int(request.form.get("assigned_user_id"))
    c.assigned_user_id = assigned
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_ASSIGNED", "Client", c.id, detail=f"assigned_user_id={assigned}")
    flash("Assigned representative updated.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))

@clients_bp.post("/<int:client_id>/create-portal")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def create_portal(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    if not c.email:
        flash("Client must have an email to create a portal login.", "error")
        return redirect(url_for("clients.view_client", client_id=c.id))
    if c.portal_user_id:
        c.portal_enabled = True
        db.commit()
        ensure_tasks(current_user.org_id, c.id)
        # Post welcome letter if none exists
        post_welcome_letter(current_user.org_id, c.id, current_user.full_name or current_user.email, current_user.id)
        flash("Client portal enabled.", "success")
        return redirect(url_for("clients.view_client", client_id=c.id))
    email_norm = c.email.strip().lower()
    existing = db.query(User).filter_by(email=email_norm).first()
    if existing:
        if existing.org_id != current_user.org_id:
            flash("A user with that email already exists in another organization.", "error")
            return redirect(url_for("clients.view_client", client_id=c.id))
        if existing.role != Role.CLIENT:
            flash("That email is already used by a staff account. Use a different email for the client portal.", "error")
            return redirect(url_for("clients.view_client", client_id=c.id))
        temp_pw = secrets.token_urlsafe(10)
        try:
            existing.set_password(temp_pw)
        except Exception:
            existing.password_hash = generate_password_hash(temp_pw)
        existing.must_reset_password = True
        existing.is_active = True
        db.commit()
        u = existing
    else:
        # create portal user with temp password
        temp_pw = secrets.token_urlsafe(10)
        u = User(org_id=current_user.org_id, email=email_norm, full_name=c.display_name(), role=Role.CLIENT, is_active=True, must_reset_password=True)
        try:
            u.set_password(temp_pw)
        except Exception:
            u.password_hash = generate_password_hash(temp_pw)
        db.add(u)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(User).filter_by(email=email_norm).first()
            if not existing or existing.org_id != current_user.org_id or existing.role != Role.CLIENT:
                flash("A user with that email already exists. Please use a different email.", "error")
                return redirect(url_for("clients.view_client", client_id=c.id))
            temp_pw = secrets.token_urlsafe(10)
            try:
                existing.set_password(temp_pw)
            except Exception:
                existing.password_hash = generate_password_hash(temp_pw)
            existing.must_reset_password = True
            existing.is_active = True
            db.commit()
            u = existing
    # Send branded invite (if SMTP configured)
    try:
        from flask import current_app, request
        login_url = request.url_root.rstrip("/") + url_for("auth.login")
        site_name = current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
        support_email = current_app.config.get("SUPPORT_EMAIL", "veteranclaimsassistance@gmail.com")
        support_phone = current_app.config.get("SUPPORT_PHONE", "229-848-1633")
        subject, body_text, body_html = build_portal_invite(site_name, support_email, support_phone, login_url, email_norm, temp_pw)
        ok, msg = send_email_html(email_norm, subject, body_text, body_html, context="PORTAL_INVITE", org_id=current_user.org_id, actor_user_id=current_user.id)
        if ok:
            flash("Portal invite email sent.", "success")
        else:
            flash(f"Portal invite not sent: {msg}", "warning")
    except Exception:
        pass
    c.portal_user_id = u.id
    c.portal_enabled = True
    db.commit()
    ensure_tasks(current_user.org_id, c.id)
    post_welcome_letter(current_user.org_id, c.id, current_user.full_name or current_user.email, current_user.id)
    audit_log(current_user.org_id, current_user.id, "CLIENT_PORTAL_CREATED", "Client", c.id, detail=f"portal_user_id={u.id}")
    flash("Client portal created. Temporary password emailed (reset required on login).", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))


@clients_bp.post("/<int:client_id>/assign")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def assign_rep(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    rep_id = request.form.get("assigned_user_id")
    rep_id = int(rep_id) if rep_id and rep_id.isdigit() else None
    if rep_id:
        rep = db.get(User, rep_id)
        if not rep or rep.org_id != current_user.org_id or rep.role not in [Role.EMPLOYEE, Role.DIRECTOR]:
            flash("Invalid assignee.", "error")
            return redirect(url_for("clients.view_client", client_id=c.id))
        c.assigned_user_id = rep.id
    else:
        c.assigned_user_id = None
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_ASSIGNED", "Client", c.id, detail=str(c.assigned_user_id))
    flash("Assigned representative updated.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))

@clients_bp.post("/<int:client_id>/archive")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def archive_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    c.is_archived = True
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_ARCHIVED", "Client", c.id, detail="")
    flash("Client archived.", "success")
    return redirect(url_for("clients.list_clients"))

@clients_bp.post("/<int:client_id>/restore")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def restore_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    c.is_archived = False
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_RESTORED", "Client", c.id, detail="")
    flash("Client restored.", "success")
    return redirect(url_for("clients.view_client", client_id=c.id))


@clients_bp.post("/<int:client_id>/delete")
@login_required
@require_roles(Role.DIRECTOR)
def delete_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    _delete_client_cascade(db, c)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "CLIENT_DELETED", "Client", client_id, detail="")
    flash("Client deleted.", "success")
    return redirect(url_for("clients.list_clients"))


@clients_bp.post("/bulk/archive")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def bulk_archive_clients():
    ids = request.form.getlist("client_ids")
    ids = [int(i) for i in ids if i.isdigit()]
    if not ids:
        flash("Select at least one client.", "error")
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    try:
        rows = db.query(Client).filter(Client.org_id == current_user.org_id, Client.id.in_(ids)).all()
        for c in rows:
            c.is_archived = True
        db.commit()
        audit_log(current_user.org_id, current_user.id, "CLIENTS_ARCHIVED_BULK", "Client", "bulk", detail=f"count={len(rows)}")
        flash(f"Archived {len(rows)} client(s).", "success")
        return redirect(url_for("clients.list_clients"))
    finally:
        db.close()


@clients_bp.post("/bulk/delete")
@login_required
@require_roles(Role.DIRECTOR)
def bulk_delete_clients():
    ids = request.form.getlist("client_ids")
    ids = [int(i) for i in ids if i.isdigit()]
    if not ids:
        flash("Select at least one client.", "error")
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    try:
        rows = db.query(Client).filter(Client.org_id == current_user.org_id, Client.id.in_(ids)).all()
        for c in rows:
            _delete_client_cascade(db, c)
        db.commit()
        audit_log(current_user.org_id, current_user.id, "CLIENTS_DELETED_BULK", "Client", "bulk", detail=f"count={len(rows)}")
        flash(f"Deleted {len(rows)} client(s).", "success")
        return redirect(url_for("clients.list_clients"))
    finally:
        db.close()


@clients_bp.post("/<int:client_id>/resend-portal-invite")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def resend_portal_invite(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    if not c.email or not c.portal_user_id:
        flash("Client portal is not created yet for this client.", "error")
        return redirect(url_for("clients.view_client", client_id=c.id))
    u = db.get(User, c.portal_user_id)
    if not u:
        flash("Portal user record missing.", "error")
        return redirect(url_for("clients.view_client", client_id=c.id))

    temp_pw = secrets.token_urlsafe(10)
    try:
        u.set_password(temp_pw)
    except Exception:
        u.password_hash = generate_password_hash(temp_pw)
    u.must_reset_password = True
    u.is_active = True
    db.commit()

    try:
        from flask import current_app, request
        login_url = request.url_root.rstrip("/") + url_for("auth.login")
        site_name = current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
        support_email = current_app.config.get("SUPPORT_EMAIL", "veteranclaimsassistance@gmail.com")
        support_phone = current_app.config.get("SUPPORT_PHONE", "229-848-1633")
        subject, body_text, body_html = build_portal_invite(site_name, support_email, support_phone, login_url, c.email.strip().lower(), temp_pw)
        ok, msg = send_email_html(c.email.strip().lower(), subject, body_text, body_html, context="PORTAL_INVITE", org_id=current_user.org_id, actor_user_id=current_user.id)
        if ok:
            flash("Portal invite resent (temporary password rotated).", "success")
        else:
            flash(f"Invite not sent: {msg}", "warning")
    except Exception as e:
        flash(f"Invite email failed: {e}", "warning")

    return redirect(url_for("clients.view_client", client_id=c.id))


@clients_bp.post("/<int:client_id>/ssn")
@login_required
@require_roles(Role.DIRECTOR)
def reveal_ssn(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return jsonify({"error": "Not found"}), 404
    full = decrypt_ssn(c.ssn_encrypted or "")
    if not full:
        return jsonify({"error": "SSN not available"}), 404
    audit_log(current_user.org_id, current_user.id, "CLIENT_SSN_REVEALED", "Client", c.id)
    return jsonify({"ssn": full})
def _delete_client_cascade(db, c: Client) -> None:
    docs = db.query(Document).filter_by(org_id=c.org_id, client_id=c.id).all()
    for d in docs:
        try:
            if d.storage_path and os.path.exists(d.storage_path):
                os.remove(d.storage_path)
        except Exception:
            pass
        try:
            if d.extracted_text_path and os.path.exists(d.extracted_text_path):
                os.remove(d.extracted_text_path)
        except Exception:
            pass
        db.delete(d)
    artifacts = db.query(RenderedArtifact).filter_by(org_id=c.org_id, client_id=c.id).all()
    for a in artifacts:
        try:
            if a.docx_path and os.path.exists(a.docx_path):
                os.remove(a.docx_path)
        except Exception:
            pass
        db.delete(a)
    db.query(DocumentTag).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(Message).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(MessageThread).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(Letter).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(ClientTask).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(FormData).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(Agreement).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.query(AIRun).filter_by(org_id=c.org_id, client_id=c.id).delete(synchronize_session=False)
    db.delete(c)


