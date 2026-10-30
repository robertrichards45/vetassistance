from __future__ import annotations

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request
from flask_login import login_required, current_user

from app.extensions import SessionLocal
from app.models import Alert

notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notifications_bp.get("")
@login_required
def index():
    db = SessionLocal()
    rows = (db.query(Alert).filter(Alert.user_id == current_user.id, Alert.dismissed_at.is_(None))
            .order_by(Alert.created_at.desc()).limit(100).all())
    return render_template("notifications/index.html", rows=rows)


@notifications_bp.get("/<int:alert_id>/open")
@login_required
def open_(alert_id: int):
    db = SessionLocal()
    a = db.get(Alert, alert_id)
    if not a or a.user_id != current_user.id:
        return redirect(url_for("notifications.index"))
    if not a.read_at:
        a.read_at = datetime.utcnow()
        db.commit()
    link = a.link or ""
    if not link.startswith("/"):
        link = url_for("notifications.index")
    return redirect(link)


@notifications_bp.post("/read-all")
@login_required
def read_all():
    db = SessionLocal()
    now = datetime.utcnow()
    db.query(Alert).filter(Alert.user_id == current_user.id, Alert.read_at.is_(None)).update({Alert.read_at: now})
    db.commit()
    return redirect(request.referrer if (request.referrer or "").startswith(request.host_url) else url_for("notifications.index"))
