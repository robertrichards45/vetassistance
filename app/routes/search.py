from __future__ import annotations

from flask import Blueprint, jsonify, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import SessionLocal
from app.models import Client
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.case_stages import stage_label

search_bp = Blueprint("search", __name__, url_prefix="/api/search")


@search_bp.get("")
@login_required
@require_roles(Role.DIRECTOR, Role.EMPLOYEE)
def api_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"clients": []})
    like = f"%{q}%"
    digits = "".join(ch for ch in q if ch.isdigit())
    db = SessionLocal()
    try:
        conds = [
            Client.first_name.ilike(like),
            Client.last_name.ilike(like),
            Client.full_legal_name.ilike(like),
            Client.email.ilike(like),
            Client.claim_number.ilike(like),
        ]
        if len(digits) >= 3:
            conds.append(Client.phone.ilike(f"%{digits}%"))
        parts = q.split()
        if len(parts) >= 2:
            conds.append(Client.first_name.ilike(f"%{parts[0]}%") & Client.last_name.ilike(f"%{parts[-1]}%"))
        rows = (
            db.query(Client)
            .filter(Client.org_id == current_user.org_id, or_(*conds))
            .order_by(Client.is_archived.asc(), Client.created_at.desc())
            .limit(8)
            .all()
        )
        out = []
        for c in rows:
            detail = stage_label(c.status)
            if c.is_archived:
                detail = "Archived"
            out.append({"name": c.display_name(), "url": url_for("clients.view_client", client_id=c.id), "detail": detail})
        return jsonify({"clients": out})
    finally:
        db.close()
