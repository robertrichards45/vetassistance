import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import Client, RenderedArtifact
from app.services.context_builder import build_context
from app.services.docx_export import export_docx
from app.services.audit_service import log as audit_log
from app.services.template_engine import render as render_template_text

nexus_bp = Blueprint("nexus", __name__, url_prefix="/nexus")

@nexus_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def builder(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    ctx = build_context(current_user.org_id, c.id)
    return render_template("nexus/builder.html", client=c, ctx=ctx)

@nexus_bp.post("/client/<int:client_id>/generate")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def generate(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404

    condition = (request.form.get("condition") or "").strip()
    provider = (request.form.get("provider") or "").strip()
    due = (request.form.get("due_date") or "").strip()
    if not condition:
        flash("Enter a condition.", "error")
        return redirect(url_for("nexus.builder", client_id=c.id))

    ctx = build_context(current_user.org_id, c.id)
    ctx.update({"condition": condition, "provider": provider, "due_date": due, "rep_name": current_user.full_name or current_user.email})

    # Provider-facing nexus request (operational)
    provider_letter = render_template_text(NEXUS_PROVIDER_TEMPLATE, ctx)
    docx_path = export_docx(current_app.config["STORAGE_ROOT"], current_user.org_id, c.id, f"Nexus Request — {condition}", provider_letter)
    a1 = RenderedArtifact(org_id=current_user.org_id, client_id=c.id, created_by_user_id=current_user.id,
                          artifact_type="NEXUS", title=f"Nexus Request — {condition}", web_copy=provider_letter,
                          docx_path=docx_path, meta_json=json.dumps({"condition":condition,"provider":provider,"due_date":due}))
    db.add(a1); db.commit()

    # Client email draft (what to do + attach request)
    client_email = render_template_text(NEXUS_CLIENT_EMAIL_TEMPLATE, ctx)
    docx_path2 = export_docx(current_app.config["STORAGE_ROOT"], current_user.org_id, c.id, f"Client Instructions — Nexus — {condition}", client_email)
    a2 = RenderedArtifact(org_id=current_user.org_id, client_id=c.id, created_by_user_id=current_user.id,
                          artifact_type="EMAIL", title=f"Client Instructions — Nexus — {condition}", web_copy=client_email,
                          docx_path=docx_path2, meta_json=json.dumps({"condition":condition}))
    db.add(a2); db.commit()

    audit_log(current_user.org_id, current_user.id, "NEXUS_PACKET_GENERATED", "RenderedArtifact", a1.id, detail=f"{condition}")
    flash("Nexus packet generated (provider request + client email).", "success")
    return redirect(url_for("artifacts.for_client", client_id=c.id))

NEXUS_PROVIDER_TEMPLATE = """{{ brand_name }}
{{ brand_email }}

DATE: {{ due_date }}

RE: Medical Nexus Opinion Request — {{ client_name }} — {{ condition }}

Dear {{ provider }},

I’m writing on behalf of {{ client_name }} to request a medical nexus opinion regarding {{ condition }} and its relationship to military service.

What we need (clear + direct):
1) Diagnosis: Confirm current diagnosis for {{ condition }}.
2) Nexus Standard: State whether it is **at least as likely as not (50% or greater probability)** that {{ condition }} began in service, was caused by service, or was aggravated beyond natural progression by service.
3) Rationale: Provide a brief medical rationale referencing clinical findings, treatment history, and known mechanisms (not just a conclusion).

Helpful structure (recommended):
- Summary of relevant history (service events/exposures + onset timeline)
- Objective findings/exams/diagnostics supporting diagnosis
- Explanation linking the history to the diagnosis
- Explicit nexus language (“at least as likely as not…”)

Evidence available (high-level):
{{ ai_summary }}

If you need additional records or specifics, please let us know and we will provide them promptly.

Respectfully,

{{ rep_name }}
{{ brand_name }}
"""

NEXUS_CLIENT_EMAIL_TEMPLATE = """Subject: Nexus Letter Request for {{ condition }} — Next Steps

Hi {{ client_name }},

We’re generating a nexus opinion request for **{{ condition }}**.

Here’s exactly what to do:
1) Identify the provider best suited to write this opinion (treating doctor, specialist, or qualified clinician).
2) Send/bring the attached “Nexus Request — {{ condition }}” letter to the provider.
3) Ask them to include:
   - Diagnosis confirmation
   - “At least as likely as not (50%+)” nexus statement
   - Medical rationale (the why)
4) If the provider requests more records, message us here and we’ll supply them.

Deadline target: {{ due_date }}

Reply in the portal with:
- Provider name
- Appointment date (if scheduled)
- Any questions/concerns

— {{ rep_name }}
{{ brand_name }}
"""
