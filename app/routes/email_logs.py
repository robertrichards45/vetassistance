from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models.email_log import EmailLog

email_logs_bp = Blueprint("email_logs", __name__, url_prefix="/email-logs")

@email_logs_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def index():
    db = SessionLocal()
    q = (request.args.get("q") or "").strip().lower()
    qry = db.query(EmailLog).order_by(EmailLog.created_at.desc())
    if current_user.role.value != "DIRECTOR":
        # Employees only see their own sends
        qry = qry.filter(EmailLog.actor_user_id == current_user.id)
    if q:
        qry = qry.filter(EmailLog.to_email.ilike(f"%{q}%") | EmailLog.subject.ilike(f"%{q}%") | EmailLog.context.ilike(f"%{q}%"))
    rows = qry.limit(300).all()
    return render_template("email_logs/index.html", rows=rows)
