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
    from app.services.email_service import email_status
    last = db.query(EmailLog).order_by(EmailLog.created_at.desc()).first()
    last_fail = db.query(EmailLog).filter(EmailLog.ok == False).order_by(EmailLog.created_at.desc()).first()  # noqa: E712
    return render_template("email_logs/index.html", rows=rows, st=email_status(), last=last, last_fail=last_fail)


@email_logs_bp.post("/test")
@login_required
@require_roles(Role.DIRECTOR)
def send_test():
    from flask import flash, redirect, url_for
    from app.services.email_service import send_email_html
    to = (request.form.get("to") or current_user.email or "").strip()
    ok, msg = send_email_html(to, "Test email from Veteran Benefits Assistance",
                              "If you can read this, email is working.",
                              "<p>If you can read this, <strong>email is working</strong>.</p>",
                              context="TEST", org_id=current_user.org_id, actor_user_id=current_user.id)
    flash(f"Test email sent to {to}. Check the inbox (and spam folder)." if ok else f"Test email failed: {msg}",
          "success" if ok else "error")
    return redirect(url_for("email_logs.index"))
