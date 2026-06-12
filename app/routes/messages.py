from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, MessageThread, Message, User
from app.services.audit_service import log as audit_log
from datetime import datetime

messages_bp = Blueprint("messages", __name__, url_prefix="/messages")

def _ensure_staff_access(c: Client):
    if current_user.role == Role.DIRECTOR:
        return True
    # Employees: must be assigned to client
    return getattr(c, 'assigned_user_id', None) == current_user.id


def _get_or_create_thread(db, client: Client) -> MessageThread:
    thread = db.query(MessageThread).filter_by(org_id=client.org_id, client_id=client.id).first()
    if thread:
        return thread
    thread = MessageThread(org_id=client.org_id, client_id=client.id, subject="Secure Messages")
    db.add(thread)
    db.commit()
    return thread


@messages_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_thread(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    if not _ensure_staff_access(c):
        return "Forbidden", 403
    thread = _get_or_create_thread(db, c)
    thread.last_staff_seen_at = datetime.utcnow()
    db.commit()
    msgs = db.query(Message).filter_by(org_id=current_user.org_id, client_id=c.id, thread_id=thread.id).order_by(Message.created_at.asc()).all()
    return render_template("messages/thread_staff.html", client=c, thread=thread, messages=msgs)

@messages_bp.post("/client/<int:client_id>/send")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_send(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    if not _ensure_staff_access(c):
        return "Forbidden", 403
    thread = _get_or_create_thread(db, c)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Message cannot be blank.", "error")
        return redirect(url_for("messages.staff_thread", client_id=c.id))
    m = Message(org_id=current_user.org_id, thread_id=thread.id, client_id=c.id, sender_user_id=current_user.id, sender_role=current_user.role.value, body=body)
    db.add(m); db.commit()
    thread.last_staff_seen_at = datetime.utcnow()
    db.commit()
    # Email notify client if possible
    if c.email:
        try:
            from app.services.email_service import send_email_html
            subject = f"New portal message from {current_user.full_name or current_user.email}"
            body_text = "You have a new portal message. Log in to view and reply."
            body_html = "<p>You have a new portal message. Log in to view and reply.</p>"
            send_email_html(c.email.strip().lower(), subject, body_text, body_html, context="MESSAGE_NOTIFY", org_id=current_user.org_id, actor_user_id=current_user.id)
        except Exception:
            pass
    audit_log(current_user.org_id, current_user.id, "MESSAGE_SENT", "Client", c.id, detail="staff->client")
    flash("Message sent.", "success")
    return redirect(url_for("messages.staff_thread", client_id=c.id))


@messages_bp.get("/inbox")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def inbox():
    db = SessionLocal()
    # Director sees all open threads; Employee sees assigned clients only
    if current_user.role == Role.DIRECTOR:
        clients = db.query(Client).filter_by(org_id=current_user.org_id).filter(Client.is_archived == False).all()
    else:
        clients = db.query(Client).filter_by(org_id=current_user.org_id, assigned_user_id=current_user.id).filter(Client.is_archived == False).all()

    rows = []
    for c in clients:
        thread = db.query(MessageThread).filter_by(org_id=current_user.org_id, client_id=c.id).first()
        if not thread:
            continue
        last = db.query(Message).filter_by(org_id=current_user.org_id, thread_id=thread.id).order_by(Message.created_at.desc()).first()
        # unread for staff: client messages after last_staff_seen_at
        since = thread.last_staff_seen_at or datetime.min
        unread = db.query(Message).filter_by(org_id=current_user.org_id, thread_id=thread.id, sender_role="CLIENT").filter(Message.created_at > since).count()
        rows.append({
            "client": c,
            "thread": thread,
            "last": last,
            "unread": unread,
        })
    # sort by latest activity
    rows.sort(key=lambda r: (r["last"].created_at if r["last"] else r["thread"].created_at), reverse=True)
    return render_template("messages/inbox.html", rows=rows)
