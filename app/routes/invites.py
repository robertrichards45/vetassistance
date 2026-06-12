from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal
from app.models import InviteLink
from app.services.tokens import new_token, hash_token
from app.services.audit_service import log as audit_log

invites_bp = Blueprint("invites", __name__, url_prefix="/director/invites")

@invites_bp.get("/")
@login_required
@require_roles(Role.DIRECTOR)
def index():
    db = SessionLocal()
    invites = db.query(InviteLink).filter_by(org_id=current_user.org_id).order_by(InviteLink.created_at.desc()).limit(50).all()
    return render_template("director/invites.html", invites=invites)

@invites_bp.post("/create")
@login_required
@require_roles(Role.DIRECTOR)
def create():
    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "CLIENT").strip().upper()
    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("invites.index"))
    token = new_token()
    db = SessionLocal()
    inv = InviteLink(org_id=current_user.org_id, email=email, token_hash=hash_token(token), role=role, is_used=False)
    db.add(inv); db.commit()
    audit_log(current_user.org_id, current_user.id, "INVITE_CREATED", "InviteLink", inv.id, detail=f"{role}:{email}")
    link = url_for("public.accept_invite", token=token, _external=False)
    # show token once on page (not stored in plaintext)
    flash(f"Invite created. Copy this link now: {link}", "success")
    return redirect(url_for("invites.index"))
