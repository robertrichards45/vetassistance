from __future__ import annotations

import os
import shutil
import mimetypes
import secrets
from typing import Dict, List, Optional

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from app.extensions import SessionLocal
from app.models import Client, Document, MessageThread, User
from app.models.intake import IntakeMeta, IntakeRecord, IntakeStatus
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.email_service import build_portal_invite, send_email_html
from app.services.storage import client_root, ensure_dir


intakes_bp = Blueprint("intakes", __name__, url_prefix="/intakes")


def _get_reps(db) -> List[User]:
    return (
        db.query(User)
        .filter(
            User.org_id == current_user.org_id,
            User.role.in_([Role.DIRECTOR, Role.EMPLOYEE]),
        )
        .order_by(User.full_name.asc())
        .all()
    )


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _attach_intake_file_to_client(db, org_id: int, client_id: int, intake_filename: str) -> None:
    """Copy intake file from instance/intakes into uploads/org_x/client_y and create a Document row."""
    if not intake_filename:
        return

    intake_dir = os.path.join(current_app.instance_path, "intakes")
    src = os.path.join(intake_dir, intake_filename)
    if not os.path.exists(src):
        return

    storage_root = current_app.config.get("UPLOAD_FOLDER")
    dest_dir = client_root(storage_root, org_id, client_id)
    ensure_dir(dest_dir)

    safe_name = secure_filename(intake_filename)
    dest_path = os.path.join(dest_dir, safe_name)

    if os.path.exists(dest_path):
        base, ext = os.path.splitext(safe_name)
        dest_path = os.path.join(dest_dir, f"{base}__{secrets.token_hex(4)}{ext}")
        safe_name = os.path.basename(dest_path)

    shutil.copy2(src, dest_path)

    mime, _ = mimetypes.guess_type(dest_path)
    doc = Document(
        org_id=org_id,
        client_id=client_id,
        filename=safe_name,
        mime_type=mime or "",
        storage_path=dest_path,
        category="Intake",
        tags="intake,auto",
        uploaded_by_user_id=current_user.id,
        status="READY",
    )
    db.add(doc)
    db.commit()


def _ensure_message_thread(db, org_id: int, client_id: int) -> None:
    """Ensure a default secure message thread exists for the client."""
    try:
        existing = (
            db.query(MessageThread)
            .filter(MessageThread.org_id == org_id, MessageThread.client_id == client_id)
            .first()
        )
        if existing:
            return
        # Minimal constructor (works with our Phase 7 model)
        th = MessageThread(org_id=org_id, client_id=client_id, title="Secure Messages")
        db.add(th)
        db.commit()
    except Exception:
        # Never block conversion if messaging schema differs
        db.rollback()


@intakes_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def queue():
    db = SessionLocal()

    status = (request.args.get("status") or "NEW").upper()
    try:
        st = IntakeStatus(status)
    except Exception:
        st = IntakeStatus.NEW

    q = (request.args.get("q") or "").strip().lower()
    assigned = (request.args.get("assigned") or "").strip()
    has_portal = (request.args.get("has_portal") or "").strip()

    qry = db.query(IntakeRecord).filter(IntakeRecord.status == st)

    # Optional filters (safe joins)
    if assigned.isdigit():
        qry = qry.join(Client, Client.email == IntakeRecord.email).filter(Client.assigned_user_id == int(assigned))

    if has_portal == "1":
        qry = qry.join(Client, Client.email == IntakeRecord.email).filter(Client.portal_enabled == True)  # noqa: E712

    if q:
        qry = qry.filter(
            (IntakeRecord.email.ilike(f"%{q}%"))
            | (IntakeRecord.filename.ilike(f"%{q}%"))
        )

    rows = qry.order_by(IntakeRecord.created_at.desc()).limit(200).all()

    ids = [x.id for x in rows]
    meta_map: Dict[int, IntakeMeta] = {}
    if ids:
        metas = db.query(IntakeMeta).filter(IntakeMeta.intake_id.in_(ids)).all()
        meta_map = {m.intake_id: m for m in metas}

    reps = _get_reps(db)
    return render_template(
        "intakes/queue.html",
        rows=rows,
        status=st.value,
        meta_map=meta_map,
        reps=reps,
        selected_assigned=assigned,
        selected_has_portal=has_portal,
        q=q,
    )


@intakes_bp.get("/<int:intake_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def view(intake_id: int):
    db = SessionLocal()
    row = db.get(IntakeRecord, intake_id)
    if not row:
        return "Not found", 404

    meta = db.query(IntakeMeta).filter(IntakeMeta.intake_id == row.id).first()
    reps = _get_reps(db)
    return render_template("intakes/view.html", row=row, meta=meta, reps=reps)


@intakes_bp.get("/<int:intake_id>/download")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def download_file(intake_id: int):
    db = SessionLocal()
    row = db.get(IntakeRecord, intake_id)
    if not row:
        return "Not found", 404

    intake_dir = os.path.join(current_app.instance_path, "intakes")
    return send_from_directory(intake_dir, row.filename, as_attachment=True)


@intakes_bp.post("/<int:intake_id>/mark-reviewed")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def mark_reviewed(intake_id: int):
    db = SessionLocal()
    row = db.get(IntakeRecord, intake_id)
    if not row:
        return "Not found", 404

    row.status = IntakeStatus.REVIEWED
    db.commit()
    flash("Intake marked as reviewed.", "success")
    return redirect(url_for("intakes.view", intake_id=intake_id))


@intakes_bp.get("/<int:intake_id>/convert")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def convert_preview(intake_id: int):
    db = SessionLocal()
    row = db.get(IntakeRecord, intake_id)
    if not row:
        return "Not found", 404

    meta = db.query(IntakeMeta).filter(IntakeMeta.intake_id == row.id).first()
    reps = _get_reps(db)
    return render_template("intakes/convert_wizard.html", row=row, meta=meta, reps=reps)


@intakes_bp.post("/<int:intake_id>/convert")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def convert_submit(intake_id: int):
    """Convert an intake to a Client + (optional) portal user + attach intake document + create message thread."""
    db = SessionLocal()
    row = db.get(IntakeRecord, intake_id)
    if not row:
        return "Not found", 404

    email = (row.email or "").strip().lower()
    if not email:
        flash("Cannot convert intake: email missing.", "error")
        return redirect(url_for("intakes.view", intake_id=intake_id))

    # Optional overrides
    override_full_name = (request.form.get("override_full_name") or "").strip()
    override_phone = (request.form.get("override_phone") or "").strip()

    meta = db.query(IntakeMeta).filter(IntakeMeta.intake_id == row.id).first()
    if override_full_name or override_phone:
        if not meta:
            meta = IntakeMeta(intake_id=row.id)
            db.add(meta)
        if override_full_name:
            meta.full_name = override_full_name
        if override_phone:
            meta.phone = override_phone
        db.commit()

    assigned_user_id_raw = (request.form.get("assigned_user_id") or "").strip()
    assigned_user_id = int(assigned_user_id_raw) if assigned_user_id_raw.isdigit() else None

    # Reuse existing client if present
    client = db.query(Client).filter(Client.org_id == current_user.org_id, Client.email == email).first()
    if not client:
        client = Client(org_id=current_user.org_id, email=email, first_name="", last_name="", phone="")
        db.add(client)
        db.commit()

    # Fill/override client profile if blanks
    if assigned_user_id is not None:
        try:
            client.assigned_user_id = assigned_user_id
        except Exception:
            pass

    if meta:
        if (not getattr(client, "phone", "")) and meta.phone:
            client.phone = meta.phone
        if (not getattr(client, "first_name", "")) and meta.full_name:
            fn, ln = _split_name(meta.full_name)
            client.first_name = fn
            if not getattr(client, "last_name", ""):
                client.last_name = ln
    db.commit()

    # Attach intake file into client docs system
    _attach_intake_file_to_client(db, current_user.org_id, client.id, row.filename)

    # Ensure message thread exists
    _ensure_message_thread(db, current_user.org_id, client.id)

    # Portal creation / enable
    make_portal = (request.form.get("make_portal") == "1")
    if make_portal:
        u = db.query(User).filter(User.org_id == current_user.org_id, User.email == email).first()
        temp_pw = None
        if not u:
            temp_pw = secrets.token_urlsafe(10)
            u = User(
                org_id=current_user.org_id,
                email=email,
                full_name=(meta.full_name if meta and meta.full_name else email),
                role=Role.CLIENT,
                password_hash=generate_password_hash(temp_pw),
                must_reset_password=True,
                is_active=True,
            )
            db.add(u)
            db.commit()
        else:
            # If user exists but not a client, we still allow portal enable
            pass

        client.portal_user_id = u.id
        client.portal_enabled = True
        db.commit()

        # Send branded invite if we have a temp password (new user) OR if caller requested send anyway
        send_anyway = (request.form.get("send_invite") == "1")
        if temp_pw or send_anyway:
            if not temp_pw:
                temp_pw = secrets.token_urlsafe(10)
                u.password_hash = generate_password_hash(temp_pw)
                u.must_reset_password = True
                db.commit()

            login_url = request.url_root.rstrip("/") + url_for("auth.login")
            site_name = current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
            support_email = current_app.config.get("SUPPORT_EMAIL", "veteranclaimsassistance@gmail.com")
            support_phone = current_app.config.get("SUPPORT_PHONE", "229-848-1633")
            subject, body_text, body_html = build_portal_invite(
                site_name,
                support_email,
                support_phone,
                login_url,
                email,
                temp_pw,
            )
            ok, msg = send_email_html(
                email,
                subject,
                body_text,
                body_html,
                context="PORTAL_INVITE",
                org_id=current_user.org_id,
                actor_user_id=current_user.id,
            )
            if not ok:
                flash(f"Invite email not sent: {msg}", "warning")
            else:
                flash("Invite email sent.", "success")

    row.status = IntakeStatus.CONVERTED
    db.commit()

    flash("Intake converted to client.", "success")
    return redirect(url_for("clients.view_client", client_id=client.id))


@intakes_bp.post("/<int:intake_id>/resend-invite")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def resend_invite(intake_id: int):
    db = SessionLocal()
    row = db.get(IntakeRecord, intake_id)
    if not row:
        return "Not found", 404

    email = (row.email or "").strip().lower()
    if not email:
        flash("Cannot resend invite: intake email missing.", "error")
        return redirect(url_for("intakes.view", intake_id=intake_id))

    u = db.query(User).filter(User.org_id == current_user.org_id, User.email == email).first()
    if not u:
        flash("No portal user exists for this intake yet. Convert intake first.", "error")
        return redirect(url_for("intakes.view", intake_id=intake_id))

    temp_pw = secrets.token_urlsafe(10)
    u.password_hash = generate_password_hash(temp_pw)
    u.must_reset_password = True
    u.is_active = True
    db.commit()

    login_url = request.url_root.rstrip("/") + url_for("auth.login")
    site_name = current_app.config.get("SITE_NAME", "Veteran Benefits Assistance")
    support_email = current_app.config.get("SUPPORT_EMAIL", "veteranclaimsassistance@gmail.com")
    support_phone = current_app.config.get("SUPPORT_PHONE", "229-848-1633")

    subject, body_text, body_html = build_portal_invite(site_name, support_email, support_phone, login_url, email, temp_pw)
    ok, msg = send_email_html(
        email,
        subject,
        body_text,
        body_html,
        context="PORTAL_INVITE",
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )

    if not ok:
        flash(f"Invite email not sent: {msg}", "warning")
    else:
        flash("Invite email resent.", "success")

    return redirect(url_for("intakes.view", intake_id=intake_id))


