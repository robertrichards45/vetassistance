from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import SessionLocal
from app.models import PricingItem
from app.models.user import Role
from app.routes._authz import require_roles


pricing_bp = Blueprint("pricing", __name__, url_prefix="/director/pricing")


@pricing_bp.get("")
@login_required
@require_roles(Role.DIRECTOR)
def list_pricing():
    db = SessionLocal()
    items = (
        db.query(PricingItem)
        .filter(PricingItem.org_id == current_user.org_id)
        .order_by(PricingItem.sort_order.asc(), PricingItem.created_at.desc())
        .all()
    )
    return render_template("director/pricing_list.html", items=items)


def _parse_price_cents(raw: str) -> int:
    if not raw:
        return 0
    try:
        dollars = Decimal(raw)
    except InvalidOperation:
        return 0
    if dollars < 0:
        return 0
    return int(dollars * 100)


@pricing_bp.post("")
@login_required
@require_roles(Role.DIRECTOR)
def pricing_actions():
    action = (request.form.get("action") or "").strip()
    db = SessionLocal()

    if action == "seed_non_va":
        services = [
            {
                "name": "Intelligent Benefits Finder (Local Resources)",
                "description": (
                    "Location-based local, county, and state resources grouped by category with plain-language summaries. "
                    "Compliance: informational/administrative only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 0,
                "sort_order": 10,
            },
            {
                "name": "State Veteran Benefits Lookup (Non-VA)",
                "description": (
                    "State-specific benefits summary with links to official sources and eligibility notes. "
                    "Included with paid consultation when applicable. "
                    "Compliance: informational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 2500,
                "sort_order": 20,
            },
            {
                "name": "Veteran Tax & Financial Benefit Summary (Non-VA)",
                "description": (
                    "Tax and financial benefit summary by state and veteran status, including property, income, and vehicle notes. "
                    "Compliance: informational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 3500,
                "sort_order": 30,
            },
            {
                "name": "Document Review & Organization (No Claim Filing)",
                "description": (
                    "File cleanup, categorization, missing-document checklist, and plain-English notes. "
                    "Compliance: administrative only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 7500,
                "sort_order": 40,
            },
            {
                "name": "Evidence Readiness Assessment (Pre-Claim Only)",
                "description": (
                    "Evidence gap analysis with strengths, weaknesses, and next-step suggestions. "
                    "Compliance: informational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 10000,
                "sort_order": 50,
            },
            {
                "name": "Buddy Statement Draft (Non-Representative)",
                "description": (
                    "AI-assisted buddy statement draft based on veteran-provided facts. "
                    "Compliance: drafting support only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 4000,
                "sort_order": 60,
            },
            {
                "name": "Personal Statement (Lay Evidence) Draft",
                "description": (
                    "Structured personal statement draft in VA-appropriate language from client-provided facts. "
                    "Compliance: drafting support only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 4000,
                "sort_order": 70,
            },
            {
                "name": "Employer Statement Draft",
                "description": (
                    "Work-impact statement draft for employer or VA use based on client-provided facts. "
                    "Compliance: drafting support only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 4000,
                "sort_order": 80,
            },
            {
                "name": "Records Request Letter Drafting",
                "description": (
                    "Formal request letters for medical, service, or civilian records. "
                    "Compliance: drafting support only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 2500,
                "sort_order": 90,
            },
            {
                "name": "Benefits Education & Guidance Session (30 minutes)",
                "description": (
                    "One-on-one session covering VA vs non-VA benefits, evidence types, and common mistakes. "
                    "Compliance: educational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 7500,
                "sort_order": 100,
            },
            {
                "name": "Benefits Education & Guidance Session (60 minutes)",
                "description": (
                    "Extended one-on-one session for deeper guidance and Q&A. "
                    "Compliance: educational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 12500,
                "sort_order": 110,
            },
            {
                "name": "Client Portal Access (Included with paid service)",
                "description": (
                    "Secure portal for uploads, messages, tasks, and letter downloads. "
                    "Compliance: administrative access only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 0,
                "sort_order": 120,
            },
            {
                "name": "Client Portal Access (Standalone Monthly)",
                "description": (
                    "Standalone portal access billed monthly. "
                    "Compliance: administrative access only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 1000,
                "sort_order": 130,
            },
            {
                "name": "Benefits Monitoring & Updates (Monthly)",
                "description": (
                    "Monthly updates on state benefit changes and new programs. "
                    "Compliance: informational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 500,
                "sort_order": 140,
            },
            {
                "name": "Benefits Monitoring & Updates (Annual)",
                "description": (
                    "Annual updates on state benefit changes and new programs. "
                    "Compliance: informational only; no VA claim filing or representation; no legal or medical advice; no guarantees."
                ),
                "price_cents": 5000,
                "sort_order": 150,
            },
        ]

        existing = {
            name for name, in db.query(PricingItem.name)
            .filter(PricingItem.org_id == current_user.org_id)
            .all()
        }
        created = 0
        for svc in services:
            if svc["name"] in existing:
                continue
            db.add(PricingItem(
                org_id=current_user.org_id,
                name=svc["name"],
                description=svc["description"],
                is_percent=False,
                percent_of_backpay=0,
                price_cents=svc["price_cents"],
                sort_order=svc["sort_order"],
                active=True,
            ))
            created += 1
        if created:
            db.commit()
            flash(f"Added {created} Non-VA services.", "success")
        else:
            flash("Non-VA services already exist.", "info")
        return redirect(url_for("pricing.list_pricing"))

    if action == "create":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Service name is required.", "error")
            return redirect(url_for("pricing.list_pricing"))

        sort_order = int(request.form.get("sort_order") or 0)
        is_percent = request.form.get("is_percent") == "on"
        price_cents = _parse_price_cents(request.form.get("price_dollars") or "")
        percent = int(request.form.get("percent_of_backpay") or 0)
        description = (request.form.get("description") or "").strip()

        item = PricingItem(
            org_id=current_user.org_id,
            name=name,
            description=description,
            is_percent=is_percent,
            percent_of_backpay=percent if is_percent else 0,
            price_cents=0 if is_percent else price_cents,
            sort_order=sort_order,
            active=True,
        )
        db.add(item)
        db.commit()
        flash("Pricing item created.", "success")
        return redirect(url_for("pricing.list_pricing"))

    item_id = request.form.get("item_id")
    item = db.get(PricingItem, int(item_id)) if item_id and item_id.isdigit() else None
    if not item or item.org_id != current_user.org_id:
        flash("Pricing item not found.", "error")
        return redirect(url_for("pricing.list_pricing"))

    if action == "toggle":
        item.active = not item.active
        db.commit()
        flash("Pricing item updated.", "success")
        return redirect(url_for("pricing.list_pricing"))

    if action == "delete":
        db.delete(item)
        db.commit()
        flash("Pricing item deleted.", "success")
        return redirect(url_for("pricing.list_pricing"))

    flash("Unknown action.", "error")
    return redirect(url_for("pricing.list_pricing"))
