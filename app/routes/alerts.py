from datetime import datetime
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Alert

alerts_bp = Blueprint("alerts", __name__, url_prefix="/api/alerts")


@alerts_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def list_alerts():
    db = SessionLocal()
    rows = db.query(Alert).filter(
        Alert.user_id == current_user.id,
        Alert.read_at.is_(None),
        Alert.dismissed_at.is_(None),
    ).order_by(Alert.created_at.desc()).all()
    return jsonify({"items": [
        {"id": a.id, "type": a.type, "title": a.title, "body": a.body, "created_at": a.created_at.isoformat()}
        for a in rows
    ]})


@alerts_bp.post("/<int:alert_id>/dismiss")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def dismiss_alert(alert_id: int):
    db = SessionLocal()
    a = db.get(Alert, alert_id)
    if not a or a.user_id != current_user.id:
        return jsonify({"error": "Not found"}), 404
    a.dismissed_at = datetime.utcnow()
    db.commit()
    return jsonify({"ok": True})


@alerts_bp.post("/sync-email-status")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def sync_email_status():
    payload = request.get_json(silent=True) or {}
    keys = payload.get("source_keys") or []
    if not isinstance(keys, list) or not keys:
        return jsonify({"error": "source_keys required"}), 400
    db = SessionLocal()
    for key in keys:
        db.query(Alert).filter(
            Alert.user_id == current_user.id,
            Alert.source_key == key,
            Alert.read_at.is_(None),
            Alert.dismissed_at.is_(None),
        ).update({"read_at": datetime.utcnow()})
    db.commit()
    return jsonify({"ok": True})
