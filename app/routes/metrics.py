from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models.email_log import EmailLog
from app.models.intake import IntakeRecord, IntakeStatus
from app.models import User, Document
from datetime import datetime, timedelta

metrics_bp = Blueprint("metrics", __name__, url_prefix="/metrics")

@metrics_bp.get("")
@login_required
@require_roles(Role.DIRECTOR)
def index():
    db = SessionLocal()
    days = int(request.args.get("days", 30))
    since = datetime.utcnow() - timedelta(days=days)

    staff = db.query(User).filter(User.org_id == current_user.org_id, User.role.in_([Role.DIRECTOR, Role.EMPLOYEE])).all()

    # Emails sent per staff (last N days)
    email_counts = {u.id: 0 for u in staff}
    for row in db.query(EmailLog).filter(EmailLog.org_id == current_user.org_id, EmailLog.created_at >= since).all():
        if row.actor_user_id in email_counts:
            email_counts[row.actor_user_id] += 1

    # Intake conversions (proxy: converted intakes in last N days) - intake table doesn't have actor yet; show total only
    converted = db.query(IntakeRecord).filter(IntakeRecord.status == IntakeStatus.CONVERTED, IntakeRecord.created_at >= since).count()

    # Documents uploaded per staff (if uploaded_by_user_id exists)
    doc_counts = {u.id: 0 for u in staff}
    for d in db.query(Document).filter(Document.org_id == current_user.org_id).all():
        if getattr(d, "uploaded_by_user_id", None) in doc_counts:
            doc_counts[d.uploaded_by_user_id] += 1

    rows = []
    for u in staff:
        rows.append({
            "name": u.full_name or u.email,
            "role": u.role.value,
            "emails": email_counts.get(u.id,0),
            "docs": doc_counts.get(u.id,0),
        })

    return render_template("metrics/index.html", rows=rows, days=days, converted=converted)
