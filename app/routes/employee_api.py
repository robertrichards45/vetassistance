from datetime import datetime
import json
from pathlib import Path
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import OnboardingItem, EmployeeOnboardingProgress

employee_api_bp = Blueprint("employee_api", __name__, url_prefix="/api/employee")


def _seed_onboarding_items(db):
    data_path = Path(current_app.root_path) / "data" / "onboarding_items.json"
    if not data_path.exists():
        return
    if db.query(OnboardingItem).count():
        return
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        return
    for row in payload:
        db.add(OnboardingItem(
            title=row.get("title", "").strip(),
            description=row.get("description", "").strip(),
            task_url=row.get("task_url", "").strip(),
            sort_order=int(row.get("sort_order") or 0),
            is_active=bool(row.get("is_active", True)),
            is_required=bool(row.get("is_required", True)),
        ))
    db.commit()


@employee_api_bp.get("/hub/summary")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def hub_summary():
    db = SessionLocal()
    _seed_onboarding_items(db)
    items = db.query(OnboardingItem).filter_by(is_active=True).all()
    completed = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, is_complete=True).all()
    done_ids = {c.item_id for c in completed}
    required_items = [i for i in items if i.is_required]
    progress = int((len([i for i in required_items if i.id in done_ids]) / len(required_items)) * 100) if required_items else 0
    return jsonify({
        "onboarding_progress": progress,
        "unread_messages": 0,
        "quick_links": [
            {"label": "Onboarding", "href": "/employee/hub/onboarding"},
            {"label": "Compliance", "href": "/employee/hub/compliance"},
            {"label": "How-To", "href": "/employee/hub/howto"},
        ],
        "compliance_banner": "No legal advice. No outcome guarantees. Document only what is supported."
    })


@employee_api_bp.get("/onboarding")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def onboarding_status():
    db = SessionLocal()
    _seed_onboarding_items(db)
    items = db.query(OnboardingItem).filter_by(is_active=True).order_by(OnboardingItem.sort_order.asc()).all()
    completed = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, is_complete=True).all()
    done_ids = {c.item_id for c in completed}
    return jsonify({
        "items": [{"id": i.id, "title": i.title, "description": i.description, "task_url": i.task_url, "is_required": i.is_required} for i in items],
        "completed": list(done_ids),
    })


@employee_api_bp.post("/onboarding/ack")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def onboarding_ack():
    payload = request.get_json(silent=True) or {}
    item_id = int(payload.get("item_id") or 0)
    if not item_id:
        return jsonify({"error": "item_id required"}), 400
    db = SessionLocal()
    row = db.query(EmployeeOnboardingProgress).filter_by(employee_id=current_user.id, item_id=item_id).first()
    if not row:
        row = EmployeeOnboardingProgress(employee_id=current_user.id, item_id=item_id)
        db.add(row)
    row.is_complete = True
    row.completed_at = datetime.utcnow()
    db.commit()
    return jsonify({"ok": True})


@employee_api_bp.get("/compliance")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def compliance():
    return jsonify({
        "sections": [
            {"title": "Ethical Boundaries", "body": "No outcome guarantees. No medical or legal advice."},
            {"title": "Evidence Standards", "body": "Document diagnosis, in-service event, and nexus."},
            {"title": "Privacy", "body": "Use approved channels only; no external sharing."},
        ]
    })


@employee_api_bp.get("/howto")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def howto():
    return jsonify({
        "guides": [
            {"title": "New Client Setup", "steps": ["Create client", "Assign rep", "Enable portal", "Send invite"]},
            {"title": "Evidence Workflow", "steps": ["Upload", "Categorize", "AI scan", "Review gaps"]},
        ]
    })


@employee_api_bp.get("/issues")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def issues():
    return jsonify({
        "issues": [
            {"problem": "Client cannot log in", "cause": "Portal disabled", "fix": "Enable portal and resend invite."},
            {"problem": "No AI results", "cause": "No extracted text", "fix": "Wait for extraction or re-upload."},
        ]
    })
