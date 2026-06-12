import json
from flask import Blueprint, send_file, redirect, url_for, flash, request, render_template
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import RenderedArtifact, Client
from app.services.audit_service import log as audit_log

exports_bp = Blueprint("exports", __name__, url_prefix="/exports")

@exports_bp.get("/artifact/<int:artifact_id>/download")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def download_artifact(artifact_id: int):
    db = SessionLocal()
    a = db.get(RenderedArtifact, artifact_id)
    if not a or a.org_id != current_user.org_id:
        return "Not found", 404
    if not a.docx_path:
        return "No DOCX available.", 404
    audit_log(current_user.org_id, current_user.id, "ARTIFACT_DOWNLOADED", "RenderedArtifact", a.id, detail=a.title)
    return send_file(a.docx_path, as_attachment=True, download_name=(a.title or "document") + ".docx")

@exports_bp.get("/artifact/<int:artifact_id>/download/pdf")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def download_artifact_pdf(artifact_id: int):
    db = SessionLocal()
    a = db.get(RenderedArtifact, artifact_id)
    if not a or a.org_id != current_user.org_id:
        return "Not found", 404
    if not a.pdf_path:
        return "No PDF available.", 404
    audit_log(current_user.org_id, current_user.id, "ARTIFACT_DOWNLOADED", "RenderedArtifact", a.id, detail=a.title + " (pdf)")
    return send_file(a.pdf_path, as_attachment=True, download_name=(a.title or "document") + ".pdf")

@exports_bp.get("/artifact/<int:artifact_id>/email")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def email_draft(artifact_id: int):
    db = SessionLocal()
    a = db.get(RenderedArtifact, artifact_id)
    if not a or a.org_id != current_user.org_id:
        return "Not found", 404
    c = db.get(Client, a.client_id)
    # generate subject/body
    subject = a.title or "Veteran Benefits Assistance — Update"
    body = a.web_copy or ""
    # If client has email, provide mailto convenience
    to_email = (c.email if c else "") or ""
    return render_template("exports/email_draft.html", artifact=a, client=c, to_email=to_email, subject=subject, body=body)


