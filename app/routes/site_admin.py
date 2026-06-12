from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.services.site_content import get_value, set_value, ensure_defaults
from app.services.audit_service import log as audit_log

site_admin_bp = Blueprint("site_admin", __name__, url_prefix="/admin/site")

KEYS = [
  ("HOME_HERO_TITLE", "Homepage hero title"),
  ("HOME_HERO_SUBTITLE", "Homepage hero subtitle"),
  ("HOME_CTA_PRIMARY", "Primary CTA button"),
  ("HOME_CTA_SECONDARY", "Secondary CTA button"),
  ("HOME_FEATURE_1_TITLE", "Feature 1 title"),
  ("HOME_FEATURE_1_BODY", "Feature 1 body"),
  ("HOME_FEATURE_2_TITLE", "Feature 2 title"),
  ("HOME_FEATURE_2_BODY", "Feature 2 body"),
  ("HOME_FEATURE_3_TITLE", "Feature 3 title"),
  ("HOME_FEATURE_3_BODY", "Feature 3 body"),
  ("ALLOW_PORTAL_SELF_SIGNUP", "Allow client self-signup (1 to enable, 0 to disable)"),
]

@site_admin_bp.get("/")
@login_required
@require_roles(Role.DIRECTOR)
def edit():
    ensure_defaults(current_user.org_id)
    values = [(k, label, get_value(current_user.org_id, k)) for k,label in KEYS]
    return render_template("admin/site_edit.html", values=values)

@site_admin_bp.post("/")
@login_required
@require_roles(Role.DIRECTOR)
def save():
    ensure_defaults(current_user.org_id)
    for k, _label in KEYS:
        v = (request.form.get(k) or "").strip()
        set_value(current_user.org_id, k, v)
    audit_log(current_user.org_id, current_user.id, "SITE_CONTENT_UPDATED", "SiteContent", 0, detail="homepage")
    flash("Homepage text updated.", "success")
    return redirect(url_for("site_admin.edit"))
