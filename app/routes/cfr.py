from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import current_user
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
    base_dir = current_app.root_path + "/.."
    data = load_va_ratings_chart(base_dir)
    return render_template(
        "public/va_ratings_chart.html",
        meta_title="VA Rating Chart",
        meta_description="Condition-by-condition VA rating criteria and percentage breakdowns from CFR Part 4.",
        **data,
    )


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
