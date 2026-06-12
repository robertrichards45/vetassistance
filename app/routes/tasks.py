from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, ClientTask
from app.services.onboarding import ensure_tasks
from app.services.audit_service import log as audit_log

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")

@tasks_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def staff_tasks(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    ensure_tasks(current_user.org_id, c.id)
    tasks = db.query(ClientTask).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(ClientTask.created_at.asc()).all()
    return render_template("tasks/staff_tasks.html", client=c, tasks=tasks)

@tasks_bp.post("/complete/<int:task_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def complete(task_id: int):
    db = SessionLocal()
    t = db.get(ClientTask, task_id)
    if not t or t.org_id != current_user.org_id:
        return "Not found", 404
    t.is_completed = True
    t.completed_by_user_id = current_user.id
    t.completed_at = datetime.utcnow()
    db.commit()
    audit_log(current_user.org_id, current_user.id, "TASK_COMPLETED", "ClientTask", t.id, detail=t.title)
    flash("Task marked completed.", "success")
    return redirect(url_for("tasks.staff_tasks", client_id=t.client_id))

@tasks_bp.post("/reopen/<int:task_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def reopen(task_id: int):
    db = SessionLocal()
    t = db.get(ClientTask, task_id)
    if not t or t.org_id != current_user.org_id:
        return "Not found", 404
    t.is_completed = False
    t.completed_by_user_id = None
    t.completed_at = None
    db.commit()
    audit_log(current_user.org_id, current_user.id, "TASK_REOPENED", "ClientTask", t.id, detail=t.title)
    flash("Task reopened.", "success")
    return redirect(url_for("tasks.staff_tasks", client_id=t.client_id))
