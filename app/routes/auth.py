from __future__ import annotations

from urllib.parse import urlparse, urljoin
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import SessionLocal, csrf
from app.models import User, Client
from app.models.user import Role

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _is_safe_url(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (test_url.scheme in ("http", "https")) and (ref_url.netloc == test_url.netloc)


@auth_bp.get("/login")
def login():
    # If already logged in, send them home (or to portal later).
    if current_user.is_authenticated:
        return redirect(url_for("public.home"))
    return render_template("auth/login.html")


@auth_bp.post("/login")
@csrf.exempt  # prevent CSRF causing "POST clears form" during local/dev; re-enable in production later
def login_post():
    identifier = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.args.get("next") or request.form.get("next") or ""

    if not identifier or not password:
        flash("Please enter your email or Client ID and password.", "danger")
        return redirect(url_for("auth.login"))

    db = SessionLocal()
    try:
        identifier_lc = identifier.lower()
        if "@" in identifier_lc:
            user = db.query(User).filter(User.email == identifier_lc).first()
        elif identifier_lc == "director":
            user = (
                db.query(User)
                .filter(User.role == Role.DIRECTOR, User.is_active.is_(True))
                .order_by(User.created_at.asc())
                .first()
            )
        else:
            user = None
    finally:
        db.close()

    if not user or not user.check_password(password):
        flash("Invalid email or Client ID or password.", "danger")
        return redirect(url_for("auth.login"))

    login_user(user)
    # Track last login time
    db = SessionLocal()
    try:
        u = db.get(User, user.id)
        if u:
            u.last_login_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()

    # Safe redirect
    if next_url and _is_safe_url(next_url):
        return redirect(next_url)

    # Default landing
    if user.role == Role.DIY:
        return redirect(url_for("diy.tools"))
    return redirect(url_for("public.home"))


@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("public.home"))


@auth_bp.get("/change-password")
@login_required
def change_password():
    return render_template("auth/change_password.html")


@auth_bp.post("/change-password")
@login_required
def change_password_post():
    old_password = request.form.get("old_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not old_password or not new_password:
        flash("Please fill out all password fields.", "danger")
        return redirect(url_for("auth.change_password"))
    if new_password != confirm_password:
        flash("New passwords do not match.", "danger")
        return redirect(url_for("auth.change_password"))

    db = SessionLocal()
    try:
        user = db.get(User, current_user.id)
        if not user or not user.check_password(old_password):
            flash("Old password is incorrect.", "danger")
            return redirect(url_for("auth.change_password"))
        user.set_password(new_password)
        db.commit()
        flash("Password updated.", "success")
        return redirect(url_for("public.home"))
    finally:
        db.close()
