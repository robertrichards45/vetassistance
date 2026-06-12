from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, RenderedArtifact
from app.services.audit_service import log as audit_log
import os

artifacts_bp = Blueprint("artifacts", __name__, url_prefix="/artifacts")

@artifacts_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def for_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    q = (request.args.get("q") or "").strip()
    a_q = db.query(RenderedArtifact).filter_by(org_id=current_user.org_id, client_id=c.id)
    if q:
        a_q = a_q.filter(RenderedArtifact.title.ilike(f"%{q}%"))
    arts = a_q.order_by(RenderedArtifact.created_at.desc()).all()
    return render_template("artifacts/client_artifacts.html", client=c, arts=arts, q=q)


@artifacts_bp.post("/delete/<int:artifact_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def delete_artifact(artifact_id: int):
    db = SessionLocal()
    a = db.get(RenderedArtifact, artifact_id)
    if not a or a.org_id != current_user.org_id:
        return "Not found", 404
    client_id = a.client_id
    try:
        if a.docx_path and os.path.exists(a.docx_path):
            os.remove(a.docx_path)
    except Exception:
        pass
    db.delete(a)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "ARTIFACT_DELETED", "RenderedArtifact", artifact_id, detail=f"client_id={client_id}")
    flash("Generated document deleted.", "success")
    return redirect(url_for("artifacts.for_client", client_id=client_id))
