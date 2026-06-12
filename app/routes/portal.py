# CLIENT portal enforcement
from flask import Blueprint, render_template, request, redirect, url_for, flash
import json
from datetime import datetime, timedelta
import secrets
from flask_login import login_required, current_user, login_user
from app.extensions import SessionLocal
from app.models import Client, MessageThread, Message, Document, Letter, ClientTask, User, Organization, PasswordResetToken, VACallLog, FormData, RenderedArtifact
import os
from app.services.evidence_classifier import classify
from app.models.user import Role
from app.services.storage import save_upload
from app.services.queue import get_queue
from app.services.jobs import extract_document_text
from app.services.audit_service import log as audit_log
from app.services.alerts_service import create_alert

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")

def _get_default_org(db):
    default_name = current_app().config.get("DEFAULT_TENANT", "Veteran Benefits Assistance")
    org = db.query(Organization).filter(Organization.name == default_name).first()
    if not org:
        org = db.query(Organization).first()
    return org

def _linked_client(db):
    # client users are linked by portal_user_id
    c = db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()
    return c

def _is_guest_client(c: Client | None) -> bool:
    if not c:
        return True
    if not (c.email or "").strip():
        return True
    return False

def _public_client_id(c: Client) -> str:
    return f"C-{c.id}"

def _get_or_create_thread(db, client: Client) -> MessageThread:
    thread = db.query(MessageThread).filter_by(org_id=client.org_id, client_id=client.id).first()
    if thread:
        return thread
    thread = MessageThread(org_id=client.org_id, client_id=client.id, subject="Secure Messages")
    db.add(thread)
    db.commit()
    return thread

@portal_bp.get("")
def portal_home():
    if not current_user.is_authenticated:
        return render_template("public/portal_login.html")
    if current_user.role.value != "CLIENT":
        return redirect(url_for("clients.list_clients"))
    return redirect(url_for("portal.dashboard"))


@portal_bp.get("/register")
def portal_register():
    if current_user.is_authenticated:
        return redirect(url_for("portal.dashboard"))
    return render_template("public/portal_register.html")


@portal_bp.post("/register")
def portal_register_post():
    email = (request.form.get("email") or "").strip().lower()
    full_name = (request.form.get("full_name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    dob = (request.form.get("dob") or "").strip()
    service_branch = (request.form.get("service_branch") or "").strip()
    service_dates = (request.form.get("service_dates") or "").strip()
    referred_by = (request.form.get("referred_by") or "").strip()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    consent_ack = (request.form.get("consent_ack") or "").strip().lower()
    consent_review = (request.form.get("consent_review") or "").strip().lower()

    if not full_name or not email or not password or not confirm:
        flash("Please complete all required fields.", "error")
        return redirect(url_for("portal.portal_register"))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("portal.portal_register"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("portal.portal_register"))
    if consent_ack != "yes" or consent_review != "yes":
        flash("Please confirm the consent statements to continue.", "error")
        return redirect(url_for("portal.portal_register"))

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            flash("An account already exists. Please log in.", "info")
            return redirect(url_for("portal.portal_home"))

        org = _get_default_org(db)
        if not org:
            flash("Organization not found. Contact support.", "error")
            return redirect(url_for("portal.portal_register"))

        client = db.query(Client).filter(Client.org_id == org.id, Client.email.ilike(email)).first()
        if not client:
            parts = [p for p in full_name.split(" ") if p]
            first = parts[0] if parts else ""
            last = " ".join(parts[1:]) if len(parts) > 1 else ""
            client = Client(
                org_id=org.id,
                email=email,
                first_name=first,
                last_name=last,
                portal_enabled=True,
                account_type="verified",
                email_verified=True,
                email_verified_at=datetime.utcnow(),
            )
            if phone:
                client.phone = phone
            if dob:
                client.dob = dob
            if service_branch:
                client.service_branch = service_branch
            if service_dates:
                client.service_entry_date = service_dates
            if referred_by:
                client.referral_source = referred_by
            db.add(client)
            db.commit()
        else:
            if not client.email:
                client.email = email
            if not client.account_type:
                client.account_type = "verified"
            if client.email_verified is False:
                client.email_verified = True
                client.email_verified_at = datetime.utcnow()
            if phone:
                client.phone = phone
            if dob:
                client.dob = dob
            if service_branch:
                client.service_branch = service_branch
            if service_dates:
                client.service_entry_date = service_dates
            if referred_by:
                client.referral_source = referred_by
            db.commit()

        if not client:
            flash("Unable to create client account. Contact support.", "error")
            return redirect(url_for("portal.portal_register"))

        user = User(org_id=org.id, email=email, full_name=full_name or client.display_name(), role=Role.CLIENT)
        user.set_password(password)
        db.add(user)
        db.commit()

        client.portal_user_id = user.id
        client.portal_enabled = True
        if not client.account_type:
            client.account_type = "verified"
        db.commit()

        audit_log(
            current_user.org_id if current_user.is_authenticated else org.id,
            current_user.id if current_user.is_authenticated else None,
            "PORTAL_ACCOUNT_CREATED",
            "Client",
            client.id,
            detail=f"type={client.account_type} verified={client.email_verified} consent=v1",
        )

        login_user(user)
        user.last_login_at = datetime.utcnow()
        db.commit()
        flash("Account created. Welcome to your client portal.", "success")
        return redirect(url_for("portal.dashboard"))
    finally:
        db.close()


@portal_bp.get("/forgot")
def portal_forgot():
    if current_user.is_authenticated:
        return redirect(url_for("portal.dashboard"))
    return render_template("public/portal_forgot.html")


@portal_bp.post("/forgot")
def portal_forgot_post():
    email = (request.form.get("email") or "").strip()
    if not email:
        flash("Enter your email address.", "error")
        return redirect(url_for("portal.portal_forgot"))
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).first()
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=2)
            row = PasswordResetToken(user_id=user.id, token=token, expires_at=expires_at)
            db.add(row)
            db.commit()

            reset_url = url_for("portal.portal_reset", token=token, _external=True)
            try:
                from app.services.email_service import send_email_html
                subject = "Reset your portal password"
                body_text = f"Use this link to reset your password (valid for 2 hours):\n{reset_url}\n"
                body_html = (
                    "<p>Use this link to reset your password (valid for 2 hours):</p>"
                    f"<p><a href=\"{reset_url}\">{reset_url}</a></p>"
                )
                send_email_html(user.email.strip().lower(), subject, body_text, body_html, context="PORTAL_RESET", org_id=user.org_id, actor_user_id=user.id)
            except Exception:
                pass
        flash("If the email exists, a reset link has been sent.", "success")
        return redirect(url_for("portal.portal_home"))
    finally:
        db.close()


@portal_bp.get("/reset/<token>")
def portal_reset(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("portal.dashboard"))
    db = SessionLocal()
    try:
        row = db.query(PasswordResetToken).filter_by(token=token).first()
        if not row or row.used_at is not None or row.expires_at < datetime.utcnow():
            flash("Reset link is invalid or expired.", "error")
            return redirect(url_for("portal.portal_home"))
        return render_template("public/portal_reset.html", token=token)
    finally:
        db.close()


@portal_bp.post("/reset/<token>")
def portal_reset_post(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("portal.dashboard"))
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not password or not confirm:
        flash("Please complete all fields.", "error")
        return redirect(url_for("portal.portal_reset", token=token))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("portal.portal_reset", token=token))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("portal.portal_reset", token=token))

    db = SessionLocal()
    try:
        row = db.query(PasswordResetToken).filter_by(token=token).first()
        if not row or row.used_at is not None or row.expires_at < datetime.utcnow():
            flash("Reset link is invalid or expired.", "error")
            return redirect(url_for("portal.portal_home"))
        user = db.get(User, row.user_id)
        if not user:
            flash("Account not found.", "error")
            return redirect(url_for("portal.portal_home"))
        user.set_password(password)
        row.used_at = datetime.utcnow()
        db.commit()
        flash("Password updated. You can log in now.", "success")
        return redirect(url_for("portal.portal_home"))
    finally:
        db.close()


@portal_bp.get("/dashboard")
@login_required
def dashboard():
    if current_user.role.value != "CLIENT":
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    docs_uploaded = []
    docs_shared = []
    letters = []
    if not is_guest:
        docs_uploaded = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id, uploaded_by_user_id=current_user.id).order_by(Document.created_at.desc()).limit(25).all()
        docs_shared = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id, is_client_visible=True).order_by(Document.created_at.desc()).limit(25).all()
        letters = db.query(Letter).filter_by(org_id=current_user.org_id, client_id=c.id, is_visible_to_client=True).order_by(Letter.created_at.desc()).limit(5).all()
    call_logs = []
    tasks = db.query(ClientTask).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(ClientTask.created_at.desc()).limit(10).all()
    tasks_total = db.query(ClientTask).filter_by(org_id=current_user.org_id, client_id=c.id).count()
    tasks_done = db.query(ClientTask).filter_by(org_id=current_user.org_id, client_id=c.id, is_completed=True).count()
    thread = None
    msgs = []
    if not is_guest:
        thread = _get_or_create_thread(db, c)
        msgs = db.query(Message).filter_by(org_id=current_user.org_id, client_id=c.id, thread_id=thread.id).order_by(Message.created_at.desc()).limit(10).all()
    return render_template(
        "portal/dashboard.html",
        client=c,
        is_guest=is_guest,
        public_client_id=_public_client_id(c),
        docs_uploaded=docs_uploaded,
        docs_shared=docs_shared,
        thread=thread,
        messages=list(reversed(msgs)),
        letters=letters,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks=tasks,
    )


@portal_bp.get("/crsc")
@login_required
def crsc_dashboard():
    if current_user.role.value != "CLIENT":
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    is_guest = _is_guest_client(c)
    crsc_form = {}
    fd = db.query(FormData).filter_by(org_id=current_user.org_id, client_id=c.id, form_key="CRSC_INTAKE").first()
    if fd and fd.data_json:
        try:
            crsc_form = json.loads(fd.data_json)
        except Exception:
            crsc_form = {}

    docs_count = db.query(Document).filter_by(org_id=current_user.org_id, client_id=c.id).count()
    narrative = (
        db.query(RenderedArtifact)
        .filter_by(org_id=current_user.org_id, client_id=c.id, artifact_type="CRSC_NEXUS")
        .order_by(RenderedArtifact.created_at.desc())
        .first()
    )
    packet = (
        db.query(RenderedArtifact)
        .filter_by(org_id=current_user.org_id, client_id=c.id, artifact_type="CRSC")
        .order_by(RenderedArtifact.created_at.desc())
        .first()
    )

    missing = []
    for key, label in [
        ("branch", "Branch of service"),
        ("retirement_type", "Retirement type"),
        ("va_rating", "VA rating percent"),
        ("combat_indicator", "Combat nexus category"),
        ("injury_origin", "Injury origin summary"),
        ("receives_va_comp", "VA compensation status"),
        ("receives_retired_pay", "DoD retired pay status"),
    ]:
        if not (crsc_form.get(key) or "").strip():
            missing.append(label)

    def _eligibility_label() -> str:
        retirement = (crsc_form.get("retirement_type") or "").lower()
        rating_raw = (crsc_form.get("va_rating") or "").strip()
        try:
            rating = int(rating_raw)
        except Exception:
            rating = 0
        indicator = (crsc_form.get("combat_indicator") or "").strip()
        if not retirement:
            return "Needs intake info"
        if "not" in retirement:
            return "Unlikely eligible"
        if rating and rating < 10:
            return "Unlikely eligible"
        if not indicator:
            return "Possibly eligible"
        return "Likely eligible"

    eligibility = _eligibility_label()
    readiness_items = [
        ("CRSC intake saved", bool(crsc_form)),
        ("Evidence uploaded", docs_count > 0),
        ("Nexus narrative prepared", narrative is not None),
        ("Packet generated", packet is not None),
    ]
    readiness_done = sum(1 for _, ok in readiness_items if ok)
    readiness_pct = int((readiness_done / len(readiness_items)) * 100) if readiness_items else 0

    return render_template(
        "portal/crsc.html",
        client=c,
        is_guest=is_guest,
        crsc_form=crsc_form,
        missing=missing,
        eligibility=eligibility,
        readiness_items=readiness_items,
        readiness_pct=readiness_pct,
        docs_count=docs_count,
        narrative=narrative,
        packet=packet,
    )


@portal_bp.get("/va-calls")
@login_required
def va_calls():
    if current_user.role.value != "CLIENT":
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
    logs = (
        db.query(VACallLog)
        .filter_by(org_id=current_user.org_id, client_id=c.id)
        .order_by(VACallLog.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template("portal/va_calls.html", client=c, logs=logs)


@portal_bp.post("/va-calls")
@login_required
def va_calls_post():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
    call_date = (request.form.get("call_date") or "").strip()
    call_time = (request.form.get("call_time") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    call_type = (request.form.get("call_type") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    agent_name = (request.form.get("agent_name") or "").strip()
    reference_id = (request.form.get("reference_id") or "").strip()
    outcome = (request.form.get("outcome") or "").strip()
    summary = (request.form.get("summary") or "").strip()
    next_steps = (request.form.get("next_steps") or "").strip()
    if not summary:
        flash("Please add a summary of the call.", "error")
        return redirect(url_for("portal.dashboard") + "#va-calls")
    row = VACallLog(
        org_id=current_user.org_id,
        client_id=c.id,
        call_date=call_date[:20],
        call_time=call_time[:20],
        phone=phone[:60],
        call_type=call_type[:80],
        topic=topic[:120],
        agent_name=agent_name[:120],
        reference_id=reference_id[:120],
        outcome=outcome[:200],
        summary=summary[:2000],
        next_steps=next_steps[:2000],
    )
    db.add(row)
    db.commit()
    flash("Call log saved.", "success")
    return redirect(url_for("portal.va_calls") + "#va-calls")


@portal_bp.post("/va-calls/<int:log_id>/edit")
@login_required
def va_calls_edit(log_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
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
        return redirect(url_for("portal.va_calls") + "#va-calls")
    log.summary = summary[:2000]
    log.next_steps = (request.form.get("next_steps") or log.next_steps or "").strip()[:2000]
    db.commit()
    flash("Call log updated.", "success")
    return redirect(url_for("portal.va_calls") + "#va-calls")


@portal_bp.post("/va-calls/<int:log_id>/delete")
@login_required
def va_calls_delete(log_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
    log = db.get(VACallLog, log_id)
    if not log or log.org_id != current_user.org_id or log.client_id != c.id:
        return "Not found", 404
    db.delete(log)
    db.commit()
    flash("Call log deleted.", "success")
    return redirect(url_for("portal.va_calls") + "#va-calls")

@portal_bp.post("/upload")
@login_required
def upload():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
    files = request.files.getlist("files")
    if not files:
        files = request.files.getlist("file")
    if not files:
        files = [] if "file" not in request.files else [request.files.get("file")]
    files = [f for f in files if f and (f.filename or '').strip()]
    if not files:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("portal.dashboard"))
    count = 0
    for f in files:
        full_path, saved_name = save_upload(current_app().config['STORAGE_ROOT'], current_user.org_id, c.id, f)
        doc = Document(org_id=current_user.org_id, client_id=c.id, filename=saved_name, mime_type=f.mimetype or '', storage_path=full_path, uploaded_by_user_id=current_user.id, category=classify(saved_name, f.mimetype or ''))
        db.add(doc); db.commit()
        # enqueue best-effort text extraction (PDF)
        try:
            q = get_queue('docs')
            q.enqueue(extract_document_text, doc.id)
        except Exception:
            pass
        audit_log(current_user.org_id, current_user.id, 'CLIENT_UPLOAD', 'Document', doc.id, detail=saved_name)
        count += 1
    # enqueue best-effort text extraction (PDF)
    try:
        q = get_queue('docs')
        q.enqueue(extract_document_text, doc.id)
    except Exception:
        pass
    audit_log(current_user.org_id, current_user.id, "CLIENT_UPLOAD", "Document", doc.id, detail=saved_name)
    flash(f"Uploaded {count} file(s) successfully.", "success")
    return redirect(url_for("portal.dashboard"))


@portal_bp.get("/documents/<int:doc_id>/download")
@login_required
def download_shared(doc_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        return "Email required.", 403
    d = db.get(Document, doc_id)
    if not d or d.org_id != current_user.org_id or d.client_id != c.id:
        return "Not found", 404
    # Allow only client uploads or shared docs
    if not (d.is_client_visible or d.uploaded_by_user_id == current_user.id):
        return "Forbidden", 403
    if not os.path.exists(d.storage_path):
        return "Missing file", 404
    from flask import send_file
    return send_file(d.storage_path, as_attachment=True, download_name=d.filename)

def current_app():
    # avoid circular import, tiny helper
    from flask import current_app as ca
    return ca

@portal_bp.get("/messages")
@login_required
def messages():
    if current_user.role.value != "CLIENT":
        return redirect(url_for("messages.inbox"))
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
    thread = _get_or_create_thread(db, c)
    thread.last_client_seen_at = datetime.utcnow()
    db.commit()
    msgs = db.query(Message).filter_by(org_id=current_user.org_id, client_id=c.id, thread_id=thread.id).order_by(Message.created_at.asc()).all()
    return render_template("portal/messages.html", client=c, thread=thread, messages=msgs)

@portal_bp.post("/messages/send")
@login_required
def send_message():
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    if _is_guest_client(c):
        flash("An email address is required to use the portal.", "error")
        return redirect(url_for("portal.dashboard"))
    thread = _get_or_create_thread(db, c)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Message cannot be blank.", "error")
        return redirect(url_for("portal.messages"))
    m = Message(org_id=current_user.org_id, thread_id=thread.id, client_id=c.id, sender_user_id=current_user.id, sender_role="CLIENT", body=body)
    db.add(m); db.commit()
    audit_log(current_user.org_id, current_user.id, "MESSAGE_SENT", "Client", c.id, detail="client->staff")
    # Alerts for staff (deduped)
    recipients = []
    if c.assigned_user_id:
        rep = db.get(User, c.assigned_user_id)
        if rep:
            recipients.append(rep.id)
    # always notify directors
    directors = db.query(User).filter(User.org_id == current_user.org_id, User.role == Role.DIRECTOR).all()
    recipients.extend([d.id for d in directors])
    recipients = list({r for r in recipients if r})
    for rid in recipients:
        create_alert(
            user_id=rid,
            alert_type="message",
            source_key=f"message_thread:{thread.id}",
            title=f"New message from {c.display_name()}",
            body=body[:240],
        )
    # Email notify staff if possible
    if recipients:
        try:
            from app.services.email_service import send_email_html
            subject = f"New client message: {c.display_name()}"
            body_text = "You have a new portal message. Log in to view and reply."
            body_html = "<p>You have a new portal message. Log in to view and reply.</p>"
            staff_emails = db.query(User).filter(User.id.in_(recipients)).all()
            for u in staff_emails:
                if not u.email:
                    continue
                send_email_html(u.email.strip().lower(), subject, body_text, body_html, context="MESSAGE_NOTIFY", org_id=current_user.org_id, actor_user_id=current_user.id)
        except Exception:
            pass
    flash("Message sent.", "success")
    return redirect(url_for("portal.messages"))


@portal_bp.post("/tasks/<int:task_id>/complete")
@login_required
def complete_task(task_id: int):
    if current_user.role.value != "CLIENT":
        return "Forbidden", 403
    db = SessionLocal()
    c = _linked_client(db)
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    task = db.get(ClientTask, task_id)
    if not task or task.org_id != current_user.org_id or task.client_id != c.id:
        return "Not found", 404
    task.is_completed = True
    db.commit()
    flash("Task marked complete.", "success")
    return redirect(url_for("portal.dashboard"))


