from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash

onboard_bp = Blueprint("onboard", __name__)

@onboard_bp.get("/become-client")
def become_client():
    return render_template("public/become_client.html")

@onboard_bp.post("/become-client")
def become_client_post():
    """Save the request as a new client (stage: New intake), alert the team, and confirm by email."""
    from app.extensions import SessionLocal
    from app.models import Client, ClientNote, Organization, User
    from app.models.user import Role
    from app.services.bot_guard import looks_like_bot, gibberish_name
    from app.services.notify import notify_user
    from app.services.email_service import send_email_html

    first = (request.form.get("first_name") or "").strip()[:120]
    last = (request.form.get("last_name") or "").strip()[:120]
    if not first and request.form.get("full_name"):
        parts = request.form["full_name"].strip().split(" ", 1)
        first, last = parts[0][:120], (parts[1] if len(parts) > 1 else "")[:120]
    email = (request.form.get("email") or "").strip().lower()[:255]
    phone = (request.form.get("phone") or "").strip()[:40]
    branch = (request.form.get("branch") or "").strip()[:120]
    help_text = (request.form.get("help_text") or "").strip()[:4000]

    if looks_like_bot() or (gibberish_name(first) and not last):
        flash("Thanks. We received your request and will follow up.", "success")
        return redirect(url_for("public.home"))
    if not email or "@" not in email or not first:
        flash("Please add your name and an email address so we can follow up.", "error")
        return redirect(url_for("onboard.become_client"))

    db = SessionLocal()
    org = db.query(Organization).first()
    existing = db.query(Client).filter(Client.org_id == org.id, Client.email.ilike(email)).first() if org else None
    c = existing or Client(org_id=org.id, first_name=first, last_name=last, email=email, phone=phone,
                           service_branch=branch, status="New intake", referral_source="Website: Become a client")
    if not existing:
        db.add(c)
        db.commit()
    if help_text:
        db.add(ClientNote(org_id=c.org_id, client_id=c.id, body=f"From the website request form:\n{help_text}", tags="Intake"))
        db.commit()
    who = f"{first} {last}".strip()
    for d in db.query(User).filter(User.org_id == c.org_id, User.role == Role.DIRECTOR, User.is_active.is_(True)).all():
        notify_user(d.id, f"New request from {who}", (help_text or "Website request")[:240],
                    url_for("clients.view_client", client_id=c.id), kind="intake", email=d.email, cta="Open client",
                    org_id=c.org_id)
    send_email_html(email, "We received your request",
                    f"Hi {first},\n\nThanks for reaching out. A member of our team will contact you within one business day.\n\n"
                    "If you're in crisis, call 988 and press 1, or text 838255.",
                    f"<p>Hi {first},</p><p>Thanks for reaching out. A member of our team will contact you within one business day.</p>"
                    "<p style='color:#4A5D73;font-size:13px'>If you're in crisis, call 988 and press 1, or text 838255.</p>",
                    context="INTAKE_CONFIRM", org_id=c.org_id, background=True)
    return render_template("public/request_received.html", first=first, email=email)
