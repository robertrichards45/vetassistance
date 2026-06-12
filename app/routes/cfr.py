from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import current_user
from app.models.user import Role
from app.services.diy_access import has_diy_access
from app.services.cfr_service import CFRService
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
    data_path = os.path.join(base_dir, "cfr_data", "va_ratings_chart.json")
    source_pdf = None
    generated_at = None
    count = 0
    grouped = []
    categories = []
    if os.path.exists(data_path):
        with open(data_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        source_pdf = payload.get("source_pdf")
        generated_at = payload.get("generated_at")
        conditions = payload.get("conditions", [])
        count = len(conditions)

        def _category_for(section: str | None) -> str:
            s = (section or "").lower()
            if s.startswith("4.130"):
                return "Mental Health"
            if s.startswith(("4.16", "4.25", "4.26", "4.28", "4.29", "4.30")):
                return "Secondary"
            return "Physical Health"

        buckets = {}
        for c in conditions:
            cat = _category_for(c.get("cfr_section"))
            buckets.setdefault(cat, []).append(c)

        order = ["Physical Health", "Mental Health", "Secondary"]
        for cat in order:
            items = buckets.get(cat, [])
            if not items:
                continue
            items.sort(key=lambda x: (x.get("condition") or ""))
            grouped.append({"category": cat, "items_list": items})
        categories = [g["category"] for g in grouped]
    return render_template(
        "public/va_ratings_chart.html",
        has_data=os.path.exists(data_path),
        source_pdf=source_pdf,
        generated_at=generated_at,
        count=count,
        grouped=grouped,
        categories=categories,
        meta_title="VA Rating Chart",
        meta_description="Condition-by-condition VA rating criteria and percentage breakdowns from CFR Part 4.",
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
