from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash

onboard_bp = Blueprint("onboard", __name__)

@onboard_bp.get("/become-client")
def become_client():
    return render_template("public/become_client.html")

@onboard_bp.post("/become-client")
def become_client_post():
    # Minimal stub for now (captures form fields, shows confirmation)
    name = (request.form.get("full_name") or request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    if not email:
        flash("Please provide an email so we can follow up.", "error")
        return redirect(url_for("onboard.become_client"))
    flash("Thanks — we received your request and will follow up.", "success")
    return redirect(url_for("public.contact"))
