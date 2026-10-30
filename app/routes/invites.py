"""One-time invitation links for clients and staff.

Tokens are stored only as hashes and expire after seven days.  Invites are
created by a director, and signing up via the link proves email possession.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user, login_user

from app.routes._authz import require_roles
from app.models.user import Role
from app.extensions import SessionLocal, limiter
from app.models import InviteLink, Client, User
from app.services.tokens import new_token, hash_token
from app.services.audit_service import log as audit_log

invites_bp = Blueprint("invites", __name__, url_prefix="/director/invites")


def _valid_invite(db, token):
    invite = db.query(InviteLink).filter_by(token_hash=hash_token(token), is_used=False).first()
    if not invite or not invite.created_at or invite.created_at < datetime.utcnow() - timedelta(days=7):
        return None
    if invite.role not in ("CLIENT", "EMPLOYEE"):
        return None
    return invite


@invites_bp.get("/")
@login_required
@require_roles(Role.DIRECTOR)
def index():
    db = SessionLocal()
    invites = (db.query(InviteLink).filter_by(org_id=current_user.org_id)
               .order_by(InviteLink.created_at.desc()).limit(50).all())
    return render_template("director/invites.html", invites=invites)


@invites_bp.post("/create")
@login_required
@require_roles(Role.DIRECTOR)
def create():
    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "CLIENT").strip().upper()
    if not email or "@" not in email:
        flash("A valid email address is required.", "error")
        return redirect(url_for("invites.index"))
    if role not in ("CLIENT", "EMPLOYEE"):
        flash("Invalid invitation role.", "error")
        return redirect(url_for("invites.index"))

    db = SessionLocal()
    if db.query(User).filter_by(email=email).first():
        flash("This email already has an account. Use the existing account or password reset.", "error")
        return redirect(url_for("invites.index"))

    token = new_token()
    inv = InviteLink(org_id=current_user.org_id, email=email, token_hash=hash_token(token),
                     role=role, is_used=False)
    db.add(inv)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "INVITE_CREATED", "InviteLink", inv.id,
              detail=f"{role}:{email}")
    link = url_for("invites.accept", token=token, _external=True)

    # If delivery fails, allow the director to share the one-time link manually.
    try:
        from app.services.email_service import send_email_html
        ok, msg = send_email_html(
            email, "Your invitation to Veteran Benefits Assistance",
            f"You've been invited to set up your account. This link expires in 7 days and can only be used once:\n{link}",
            f'<p>You have been invited to set up your account.</p>'
            f'<p><a href="{link}">Accept your invitation</a></p>'
            f'<p>This link expires in 7 days and can only be used once.</p>',
            context="ACCOUNT_INVITE", org_id=current_user.org_id, actor_user_id=current_user.id)
    except Exception as exc:
        ok, msg = False, str(exc)
    if ok:
        flash(f"Invitation emailed to {email}. The link expires in seven days.", "success")
    else:
        flash(f"Email could not be sent ({msg}). Copy this one-time link now: {link}", "warning")
    return redirect(url_for("invites.index"))


@invites_bp.route("/accept/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute; 50 per hour")
def accept(token):
    db = SessionLocal()
    invite = _valid_invite(db, token)
    if not invite:
        flash("This invitation link is invalid, expired, or already used.", "error")
        return redirect(url_for("auth.login"))
    if current_user.is_authenticated:
        flash("Sign out before accepting an invitation to another account.", "warning")
        return redirect(url_for("public.home"))

    if request.method == "GET":
        return render_template("auth/accept_invite.html", invite=invite)

    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    full_name = (request.form.get("full_name") or "").strip()[:255]
    if not full_name or len(password) < 8 or password != confirm:
        flash("Enter your name and matching passwords (at least 8 characters).", "error")
        return redirect(url_for("invites.accept", token=token))

    # Locked row on Postgres prevents a token being redeemed concurrently.
    invite = (db.query(InviteLink).filter_by(id=invite.id, is_used=False)
              .with_for_update().first())
    if not invite or not _valid_invite(db, token):
        flash("Invitation is no longer valid.", "error")
        return redirect(url_for("auth.login"))
    if db.query(User).filter_by(email=invite.email).first():
        flash("An account already exists for this email. Please log in.", "error")
        return redirect(url_for("auth.login"))

    role = Role.CLIENT if invite.role == "CLIENT" else Role.EMPLOYEE
    user = User(org_id=invite.org_id, email=invite.email, full_name=full_name,
                role=role, is_active=True, must_reset_password=False)
    user.set_password(password)
    db.add(user)
    db.flush()

    client = None
    if role == Role.CLIENT:
        client = (db.query(Client)
                  .filter(Client.org_id == invite.org_id, Client.email.ilike(invite.email))
                  .first())
        if client and client.portal_user_id:
            db.rollback()
            flash("A portal is already linked to this client. Contact support.", "error")
            return redirect(url_for("auth.login"))
        if not client:
            parts = full_name.split(" ", 1)
            client = Client(org_id=invite.org_id, first_name=parts[0],
                            last_name=parts[1] if len(parts) > 1 else "",
                            email=invite.email, status="New intake")
            db.add(client)
            db.flush()
        client.portal_user_id = user.id
        client.portal_enabled = True
        client.account_type = "verified"
        client.email_verified = True
        client.email_verified_at = datetime.utcnow()

    invite.is_used = True
    invite.used_at = datetime.utcnow()
    db.commit()
    audit_log(invite.org_id, user.id, "INVITE_ACCEPTED", "InviteLink", invite.id, detail=invite.role)
    if client:
        from app.services.onboarding import ensure_tasks
        ensure_tasks(invite.org_id, client.id)
    login_user(user)
    flash("Your account is ready.", "success")
    return redirect(url_for("portal.dashboard" if role == Role.CLIENT else "employee.dashboard"))
