from flask import Blueprint, render_template, request, redirect, url_for, flash
import json
from flask import current_app
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Template, Client, RenderedArtifact
from app.services.template_engine import render as render_template_text
from app.services.audit_service import log as audit_log
from app.services.docx_export import export_docx
from app.services.context_builder import build_context

templates_bp = Blueprint("templates", __name__, url_prefix="/templates")

@templates_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def for_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    templates = db.query(Template).filter(Template.org_id == current_user.org_id, Template.is_active == True).order_by(Template.category.asc(), Template.name.asc()).all()
    return render_template("templates/client_templates.html", client=c, templates=templates)

@templates_bp.post("/client/<int:client_id>/render")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def render_for_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    template_id_raw = (request.form.get("template_id") or "").strip()
    if not template_id_raw.isdigit():
        flash("Choose a template before rendering.", "error")
        return redirect(url_for("templates.for_client", client_id=c.id))
    template_id = int(template_id_raw)
    t = db.get(Template, template_id)
    if not t or t.org_id != current_user.org_id:
        return "Not found", 404
    context = build_context(current_user.org_id, c.id)
    context.update({"rep_name": current_user.full_name or current_user.email})
    output = render_template_text(t.body, context)

    # Persist artifact + export DOCX
    docx_path = export_docx(current_app.config["STORAGE_ROOT"], current_user.org_id, c.id, t.name, output)
    artifact = RenderedArtifact(
    org_id=current_user.org_id,
    client_id=c.id,
    created_by_user_id=current_user.id,
    artifact_type="LETTER",
    title=t.name,
    web_copy=output,
    docx_path=docx_path,
    meta_json=json.dumps({"template_id": t.id})
    )
    db.add(artifact); db.commit()

    audit_log(current_user.org_id, current_user.id, "TEMPLATE_RENDERED", "Template", t.id, detail=f"client_id={c.id}; artifact_id={artifact.id}")
    return render_template("templates/rendered.html", client=c, template=t, output=output, artifact=artifact)
