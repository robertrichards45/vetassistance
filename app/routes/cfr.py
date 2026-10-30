from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import current_user, login_required
from app.extensions import csrf
from app.models.user import Role
from app.services.diy_access import has_diy_access
from app.services.cfr_service import CFRService, load_va_ratings_chart
from app.services.ai_engine import draft_rating_justification
import json
import os

cfr_bp = Blueprint("cfr", __name__, url_prefix="/cfr")

@cfr_bp.get("/")
def cfr_home():
    return render_template("public/cfr.html", meta_title="CFR References", meta_description="Find CFR Part 4 references and rating criteria.")

@cfr_bp.get("/search")
def cfr_search():
    q = (request.args.get("q") or "").strip()
    svc = CFRService(base_dir=current_app.root_path + "/..")
    svc.load()
    return jsonify({"query": q, "results": svc.search(q, limit=25) if q else []})


@cfr_bp.get("/ratings-chart")
def ratings_chart():
    if not current_user.is_authenticated:
        return render_template("public/diy_paywall.html")
    if current_user.role == Role.DIY and not has_diy_access(current_user.id):
        return render_template("public/diy_paywall.html")
    clients = []
    is_staff = current_user.role in (Role.DIRECTOR, Role.EMPLOYEE)
    if is_staff:
        from app.models import Client
        from app.extensions import SessionLocal
        q = SessionLocal().query(Client).filter(Client.org_id == current_user.org_id, Client.is_archived == False)  # noqa: E712
        if current_user.role != Role.DIRECTOR:
            q = q.filter(Client.assigned_user_id == current_user.id)
        clients = sorted(q.all(), key=lambda c: (c.last_name or "", c.first_name or ""))
    path = os.path.join(current_app.root_path, "..", "cfr_data", "rating_explorer.json")
    meta = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        meta = {"count": len(payload.get("conditions", [])), "effective_date": payload.get("effective_date"),
                "source_part_url": payload.get("source_part_url"), "version": int(os.path.getmtime(path))}
    return render_template(
        "cfr/rating_explorer.html",
        meta_title="VA Rating Schedule",
        meta_description="Search the VA rating schedule (38 CFR Part 4) and calculate combined ratings.",
        meta=meta, is_staff=is_staff, clients=clients,
        ai_ok=bool(current_app.config.get("OPENAI_API_KEY")),
        selected_client=request.args.get("client_id", ""),
    )


@cfr_bp.get("/ratings-chart/explorer.json")
def explorer_data():
    """Rating schedule data for the explorer, gzip-compressed and cached by the browser."""
    import gzip
    if not current_user.is_authenticated:
        return jsonify({"error": "access_denied"}), 403
    if current_user.role == Role.DIY and not has_diy_access(current_user.id):
        return jsonify({"error": "access_denied"}), 403
    path = os.path.join(current_app.root_path, "..", "cfr_data", "rating_explorer.json")
    if not os.path.exists(path):
        return jsonify({"conditions": [], "formulas": [], "error": "missing_data"}), 404
    raw = open(path, "rb").read()
    from flask import Response
    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        resp = Response(gzip.compress(raw, 6), mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
    else:
        resp = Response(raw, mimetype="application/json")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


@cfr_bp.post("/worksheet/<int:client_id>")
@login_required
def save_worksheet(client_id: int):
    """Save the calculator worksheet to a client's file (staff only)."""
    from app.models import Client, FormData
    from app.extensions import SessionLocal
    if current_user.role not in (Role.DIRECTOR, Role.EMPLOYEE):
        return jsonify({"error": "forbidden"}), 403
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    rows = []
    for r in (body.get("rows") or [])[:60]:
        try:
            rows.append({"dc": str(r.get("dc") or "")[:6], "name": str(r.get("name") or "")[:160],
                         "percent": max(0, min(100, int(r.get("percent") or 0))), "bilateral": bool(r.get("bilateral")),
                         "note": str(r.get("note") or "")[:300]})
        except (TypeError, ValueError):
            continue
    fd = db.query(FormData).filter_by(org_id=c.org_id, client_id=c.id, form_key="RATING_WORKSHEET").first()
    if not fd:
        fd = FormData(org_id=c.org_id, client_id=c.id, form_key="RATING_WORKSHEET")
        db.add(fd)
    payload = {"rows": rows, "combined": body.get("combined"), "saved_by": current_user.full_name or current_user.email}
    fd.data_json = json.dumps(payload)
    from datetime import datetime as _dt
    fd.updated_at = _dt.utcnow()
    db.commit()
    return jsonify({"ok": True})


@cfr_bp.get("/worksheet/<int:client_id>")
@login_required
def load_worksheet(client_id: int):
    from app.models import Client, FormData
    from app.extensions import SessionLocal
    if current_user.role not in (Role.DIRECTOR, Role.EMPLOYEE):
        return jsonify({"error": "forbidden"}), 403
    db = SessionLocal()
    c = db.get(Client, client_id)
    if not c or c.org_id != current_user.org_id:
        return jsonify({"error": "not_found"}), 404
    fd = db.query(FormData).filter_by(org_id=c.org_id, client_id=c.id, form_key="RATING_WORKSHEET").first()
    return jsonify(json.loads(fd.data_json) if fd and fd.data_json else {"rows": []})


@cfr_bp.get("/ratings-chart/data")
def ratings_chart_data():
    if not current_user.is_authenticated:
        return jsonify({"error": "access_denied"}), 403
    if current_user.role == Role.DIY and not has_diy_access(current_user.id):
        return jsonify({"error": "access_denied"}), 403
    base_dir = current_app.root_path + "/.."
    data_path = os.path.join(base_dir, "cfr_data", "va_ratings_chart.json")
    if not os.path.exists(data_path):
        return jsonify({"conditions": [], "error": "missing_data"}), 404
    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return jsonify(payload)


@cfr_bp.post("/justification")
@csrf.exempt
def justification():
    if not current_user.is_authenticated:
        return jsonify({"error": "access_denied"}), 403
    if current_user.role == Role.DIY and not has_diy_access(current_user.id):
        return jsonify({"error": "access_denied"}), 403
    body = request.get_json(silent=True) or {}
    selections = body.get("selections") or []
    if not selections:
        return jsonify({"error": "Select at least one condition first."}), 400
    notes = str(body.get("notes") or "")[:4000]
    text = draft_rating_justification(selections, notes)
    return jsonify({"justification": text})
