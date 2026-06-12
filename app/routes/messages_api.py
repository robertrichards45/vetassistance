from datetime import datetime
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, MessageThread, Message

messages_api_bp = Blueprint("messages_api", __name__, url_prefix="/api/messages")


def _ensure_staff_access(c: Client) -> bool:
    if current_user.role == Role.DIRECTOR:
        return True
    return getattr(c, "assigned_user_id", None) == current_user.id


@messages_api_bp.get("/inbox")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def inbox_api():
    unread_only = (request.args.get("unreadOnly") or "").strip() == "1"
    db = SessionLocal()
    if current_user.role == Role.DIRECTOR:
        clients = db.query(Client).filter_by(org_id=current_user.org_id, is_archived=False).all()
    else:
        clients = db.query(Client).filter_by(org_id=current_user.org_id, assigned_user_id=current_user.id, is_archived=False).all()

    rows = []
    for c in clients:
        thread = db.query(MessageThread).filter_by(org_id=current_user.org_id, client_id=c.id).first()
        if not thread:
            continue
        last = db.query(Message).filter_by(org_id=current_user.org_id, thread_id=thread.id).order_by(Message.created_at.desc()).first()
        since = thread.last_staff_seen_at or datetime.min
        unread = db.query(Message).filter_by(org_id=current_user.org_id, thread_id=thread.id, sender_role="CLIENT").filter(Message.created_at > since).count()
        if unread_only and unread == 0:
            continue
        rows.append({
            "thread_id": thread.id,
            "client_id": c.id,
            "client_name": c.display_name(),
            "last_message": last.body[:200] if last else "",
            "last_at": last.created_at.isoformat() if last else thread.created_at.isoformat(),
            "unread": unread,
        })
    rows.sort(key=lambda r: r["last_at"], reverse=True)
    return jsonify({"items": rows})


@messages_api_bp.post("/<int:thread_id>/read")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def mark_read(thread_id: int):
    db = SessionLocal()
    thread = db.get(MessageThread, thread_id)
    if not thread or thread.org_id != current_user.org_id:
        return jsonify({"error": "Not found"}), 404
    c = db.get(Client, thread.client_id)
    if not c or not _ensure_staff_access(c):
        return jsonify({"error": "Forbidden"}), 403
    thread.last_staff_seen_at = datetime.utcnow()
    db.commit()
    return jsonify({"ok": True})


@messages_api_bp.post("/<int:thread_id>/reply")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def reply(thread_id: int):
    db = SessionLocal()
    thread = db.get(MessageThread, thread_id)
    if not thread or thread.org_id != current_user.org_id:
        return jsonify({"error": "Not found"}), 404
    c = db.get(Client, thread.client_id)
    if not c or not _ensure_staff_access(c):
        return jsonify({"error": "Forbidden"}), 403
    body = (request.get_json(silent=True) or {}).get("body", "").strip()
    if not body:
        return jsonify({"error": "Message cannot be blank"}), 400
    m = Message(org_id=current_user.org_id, thread_id=thread.id, client_id=c.id, sender_user_id=current_user.id, sender_role=current_user.role.value, body=body)
    db.add(m)
    thread.last_staff_seen_at = datetime.utcnow()
    db.commit()
    return jsonify({"ok": True})
