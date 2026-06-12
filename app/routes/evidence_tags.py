from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models.evidence_tag import DocumentTag
from app.models import Document, Client

tags_bp = Blueprint("tags", __name__, url_prefix="/tags")

DEFAULT_CONDITIONS = [
    "PTSD","Tinnitus","Hearing Loss","Back Pain","Knee Pain","Sleep Apnea","Migraines","Anxiety","Depression"
]
DEFAULT_EVIDENCE_TYPES = [
    "Service Treatment Records","C&P Exam","Nexus Letter","Buddy Statement","Private Medical Records","DBQ"
]

@tags_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def client_tags(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    docs = db.query(Document).filter(Document.client_id == c.id, Document.org_id == current_user.org_id).order_by(Document.id.desc()).all()
    tags = db.query(DocumentTag).filter(DocumentTag.client_id == c.id, DocumentTag.org_id == current_user.org_id).order_by(DocumentTag.created_at.desc()).all()
    return render_template("tags/client_tags.html", client=c, docs=docs, tags=tags, conditions=DEFAULT_CONDITIONS, etypes=DEFAULT_EVIDENCE_TYPES)

@tags_bp.post("/apply")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def apply():
    db = SessionLocal()
    client_id = int(request.form.get("client_id"))
    document_id = int(request.form.get("document_id"))
    tag = (request.form.get("tag") or "").strip()
    category = (request.form.get("category") or "").strip()
    if not tag:
        flash("Tag required.", "error")
        return redirect(url_for("tags.client_tags", client_id=client_id))
    doc = db.get(Document, document_id)
    if not doc or doc.org_id != current_user.org_id:
        return "Not found", 404
    row = DocumentTag(org_id=current_user.org_id, client_id=client_id, document_id=document_id, tag=tag, category=category)
    db.add(row); db.commit()
    flash("Tag applied.", "success")
    return redirect(url_for("tags.client_tags", client_id=client_id))
