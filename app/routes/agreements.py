from __future__ import annotations

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import SessionLocal
from app.models import Client, Agreement, Organization, PricingItem
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.audit_service import log as audit_log
from app.services.agreements import render_default_agreement, DEFAULT_AGREEMENT_TITLE
import json
from pathlib import Path

agreements_bp = Blueprint("agreements", __name__, url_prefix="/agreements")

@agreements_bp.get("/client/<int:client_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def for_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    items = db.query(Agreement).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(Agreement.created_at.desc()).all()
    pricing_items = (
        db.query(PricingItem)
        .filter_by(org_id=current_user.org_id, active=True)
        .order_by(PricingItem.sort_order.asc(), PricingItem.name.asc())
        .all()
    )
    brand = _brand_header()
    return render_template("agreements/staff_list.html", client=c, items=items, brand=brand, pricing_items=pricing_items)


@agreements_bp.post("/client/<int:client_id>/new")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def create_for_client(client_id: int):
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return "Not found", 404
    title = (request.form.get("title") or DEFAULT_AGREEMENT_TITLE).strip()
    body = (request.form.get("body") or "").strip()
    selected_ids = request.form.getlist("pricing_item_ids")
    selected_ids = [int(i) for i in selected_ids if i.isdigit()]
    selected_services = []
    if selected_ids:
        selected_items = (
            db.query(PricingItem)
            .filter(PricingItem.org_id == current_user.org_id)
            .filter(PricingItem.id.in_(selected_ids))
            .all()
        )
        for item in selected_items:
            if item.is_percent:
                rate = item.percent_of_backpay or 0
                selected_services.append(f"{item.name} ({rate:.2f}% of backpay)")
            else:
                price = (item.price_cents or 0) / 100
                selected_services.append(f"{item.name} (${price:.2f})")
    if not body:
        org = db.get(Organization, current_user.org_id)
        org_name = org.name if org else "Veteran Benefits Assistance"
        body = render_default_agreement(c.display_name(), org_name, selected_services)
    elif selected_services:
        services_block = "Selected Services:\n" + "\n".join(f"- {s}" for s in selected_services)
        body = body + "\n\n" + services_block
    item = Agreement(
        org_id=current_user.org_id,
        client_id=c.id,
        created_by_user_id=current_user.id,
        title=title,
        body=body,
        status="SENT",
    )
    db.add(item)
    db.commit()
    audit_log(current_user.org_id, current_user.id, "AGREEMENT_CREATED", "Agreement", item.id)
    flash("Agreement created and sent to client portal.", "success")
    return redirect(url_for("agreements.for_client", client_id=c.id))


@agreements_bp.get("/view/<int:agreement_id>")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def view_agreement(agreement_id: int):
    db = SessionLocal()
    item = db.get(Agreement, agreement_id)
    if not item or item.org_id != current_user.org_id:
        return "Not found", 404
    c = db.get(Client, item.client_id)
    brand = _brand_header()
    return render_template("agreements/staff_view.html", client=c, item=item, brand=brand)


# Client portal views
@agreements_bp.get("/portal")
@login_required
def portal_list():
    if current_user.role != Role.CLIENT:
        return redirect(url_for("clients.list_clients"))
    db = SessionLocal()
    c = db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()
    if not c or not c.portal_enabled:
        return "Portal not enabled.", 403
    items = db.query(Agreement).filter_by(org_id=current_user.org_id, client_id=c.id).order_by(Agreement.created_at.desc()).all()
    brand = _brand_header()
    return render_template("agreements/portal_list.html", client=c, items=items, brand=brand)


@agreements_bp.get("/portal/<int:agreement_id>")
@login_required
def portal_view(agreement_id: int):
    if current_user.role != Role.CLIENT:
        return "Forbidden", 403
    db = SessionLocal()
    item = db.get(Agreement, agreement_id)
    if not item or item.org_id != current_user.org_id:
        return "Not found", 404
    c = db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()
    if not c or item.client_id != c.id:
        return "Forbidden", 403
    brand = _brand_header()
    return render_template("agreements/portal_view.html", client=c, item=item, brand=brand)


def _brand_header() -> dict:
    path = Path("app/data/brand_header.json")
    if not path.exists():
        return {"brand": "Veteran Benefits Assistance", "email": "", "phone": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"brand": "Veteran Benefits Assistance", "email": "", "phone": ""}


@agreements_bp.post("/portal/<int:agreement_id>/sign")
@login_required
def portal_sign(agreement_id: int):
    if current_user.role != Role.CLIENT:
        return "Forbidden", 403
    db = SessionLocal()
    item = db.get(Agreement, agreement_id)
    if not item or item.org_id != current_user.org_id:
        return "Not found", 404
    c = db.query(Client).filter(Client.portal_user_id == current_user.id, Client.org_id == current_user.org_id).first()
    if not c or item.client_id != c.id:
        return "Forbidden", 403

    name = (request.form.get("signed_name") or "").strip()
    email = (request.form.get("signed_email") or "").strip()
    ack = (request.form.get("ack") or "").strip()
    if not name or ack != "yes":
        flash("Enter your name and confirm agreement to sign.", "error")
        return redirect(url_for("agreements.portal_view", agreement_id=item.id))

    item.signed_name = name
    item.signed_email = email
    item.signed_at = datetime.utcnow()
    item.signature_type = "typed"
    item.signature_ip = request.remote_addr or ""
    item.signature_user_agent = (request.headers.get("User-Agent") or "")[:255]
    item.status = "SIGNED"
    item.updated_at = datetime.utcnow()
    db.commit()
    audit_log(current_user.org_id, current_user.id, "AGREEMENT_SIGNED", "Agreement", item.id)
    flash("Agreement signed. Thank you.", "success")
    return redirect(url_for("agreements.portal_list"))


