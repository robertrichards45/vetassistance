from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
import re
import difflib
from flask_login import login_required, current_user

from app.services.locator import fetch_va_facilities, zip_to_latlon, city_state_to_latlon, haversine_miles
from app.extensions import SessionLocal, csrf
from app.models import Resource
from app.models.user import Role
from app.routes._authz import require_roles
from app.services.audit_service import log as audit_log

resources_bp = Blueprint("resources", __name__, url_prefix="/resources")

def _get_default_org_id(db) -> int | None:
    default_name = current_app.config.get("DEFAULT_TENANT", "Veteran Benefits Assistance")
    try:
        from app.models import Organization
        org_row = db.query(Organization).filter(Organization.name == default_name).first()
        if org_row:
            return org_row.id
        fallback = db.query(Organization).first()
        return fallback.id if fallback else None
    except Exception:
        return None


def _normalize_category(raw: str) -> str:
    v = (raw or "").strip().lower().replace(" ", "_")
    synonyms = {
        "food_bank": "food",
        "food_banks": "food",
        "homeless": "shelter",
        "homeless_shelter": "shelter",
        "shelters": "shelter",
        "mental": "mental_health",
        "mentalhealth": "mental_health",
        "vetcenter": "vet_center",
    }
    return synonyms.get(v, v or "other")


def _parse_resource_lines(text: str) -> list[dict]:
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        while len(parts) < 9:
            parts.append("")
        name, category, phone, address1, city, state, postal_code, website, description = parts[:9]
        rows.append({
            "name": name,
            "category": _normalize_category(category),
            "phone": phone,
            "address1": address1,
            "city": city,
            "state": state,
            "postal_code": postal_code,
            "website": website,
            "description": description,
        })
    return rows


def _auto_seed_resources(
    db,
    org_id: int,
    *,
    zip_code: str = "",
    city: str = "",
    state: str = "",
    category: str = "",
    county: str = "",
    count: int = 20,
) -> int:
    api_key = current_app.config.get("OPENAI_API_KEY") or current_app.config.get("OPENAI_KEY")
    if not api_key:
        return 0
    location = ""
    if zip_code:
        location = f"ZIP: {zip_code}"
    elif city and state:
        location = f"City/State: {city}, {state}"
    else:
        return 0

    cat_label = category or "all"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
        max_count = min(max(count, 5), 40)
        focus = "veteran resources, food banks, shelters, housing, legal aid, employment, mental health, transportation, financial aid, family support"
        prompt = (
            "Return a list of local resources in this exact pipe format (one per line):\n"
            "Name | Category | Phone | Address | City | State | ZIP | Website | Description\n"
            f"{location}\n"
            f"Category: {cat_label}\n"
            f"County: {county or 'any'}\n"
            f"Focus: {focus}\n"
            f"Count: {max_count}\n"
            "Use categories: benefits, health, mental_health, food, shelter, housing, employment, legal, counseling, financial, transportation, education, family, emergency, vet_center, cemetery, other."
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You return strictly formatted pipe-delimited resource rows."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
    except Exception:
        return 0

    rows = _parse_resource_lines(text)
    if not rows:
        return 0

    existing = {
        ((r.name or "").strip().lower(), (r.city or "").strip().lower(), (r.state or "").strip().lower())
        for r in db.query(Resource).filter(Resource.org_id == org_id).all()
    }
    added = 0
    for row in rows:
        key = ((row.get("name") or "").strip().lower(), (row.get("city") or "").strip().lower(), (row.get("state") or "").strip().lower())
        if key in existing:
            continue
        db.add(Resource(
            org_id=org_id,
            name=row["name"],
            category=row["category"],
            description=row["description"],
            phone=row["phone"],
            address1=row["address1"],
            city=row["city"],
            county="",
            state=row["state"],
            postal_code=row["postal_code"],
            website=row["website"],
            is_active=True,
            verification_status="AI-Discovered / Pending Review",
        ))
        added += 1
    if added:
        db.commit()
    return added


@resources_bp.get("/benefits-finder")
def benefits_finder():
    return render_template("resources/benefits_finder.html")


@resources_bp.get("/api/benefits-finder/search")
def benefits_finder_search():
    zip_code = (request.args.get("zip") or "").strip()
    city = (request.args.get("city") or "").strip()
    state = (request.args.get("state") or "").strip()
    county = (request.args.get("county") or "").strip()
    radius_raw = (request.args.get("radius") or "100").strip()
    category = (request.args.get("category") or "").strip()

    try:
        radius = int(radius_raw)
    except ValueError:
        radius = 25

    if city and not state:
        if "," in city:
            parts = [p.strip() for p in city.split(",") if p.strip()]
            if len(parts) >= 2:
                city = parts[0]
                state = parts[1]
        else:
            m = re.match(r"^(.*)\\s+([A-Za-z]{2})$", city)
            if m:
                city = m.group(1).strip()
                state = m.group(2).strip()

    if not zip_code and not city:
        return jsonify({"count": 0, "results": [], "error": "Enter a ZIP code or city."})

    latlon = zip_to_latlon(zip_code) if zip_code else None
    if not latlon and city:
        if not state:
            return jsonify({"count": 0, "results": [], "error": "Enter a state for city search."})

        # Quick typo fixes for common misspellings.
        fixups = {
            "alanta": "atlanta",
        }
        city_norm = city.strip().lower()
        if city_norm in fixups:
            city = fixups[city_norm].title()

        latlon = city_state_to_latlon(city, state)
        if not latlon:
            # Fuzzy match against known resource cities for this state.
            db = SessionLocal()
            city_rows = (
                db.query(Resource.city)
                .filter(Resource.state.ilike(state), Resource.city.isnot(None))
                .distinct()
                .all()
            )
            known = [c[0].strip() for c in city_rows if c and c[0] and c[0].strip()]
            if known:
                best = difflib.get_close_matches(city, known, n=1, cutoff=0.78)
                if best:
                    city = best[0]
                    latlon = city_state_to_latlon(city, state)
    if not latlon:
        return jsonify({"count": 0, "results": [], "error": "Location not found."})

    lat, lon = latlon

    def _search(radius_miles: int):
        va_category = category if category in ("health", "benefits", "cemetery", "vet_center") else ""
        results = fetch_va_facilities(lat, lon, radius_miles=radius_miles, facility_type=va_category)
        for item in results:
            item["exact_match"] = False

        db = SessionLocal()
        org_id = _get_default_org_id(db)

        qry = db.query(Resource).filter(Resource.is_active == True)  # noqa: E712
        if org_id:
            qry = qry.filter(Resource.org_id == org_id)
        if category:
            qry = qry.filter(Resource.category == category)
        if county:
            qry = qry.filter(Resource.county.ilike(f"%{county}%"))

        zip_cache: dict[str, tuple[float, float] | None] = {}
        custom = []
        for r in qry.all():
            dist = None
            rlat = None
            rlon = None
            exact_match = False
            if r.latitude is not None and r.longitude is not None:
                dist = round(haversine_miles(lat, lon, r.latitude, r.longitude), 2)
                rlat, rlon = r.latitude, r.longitude
                if dist > radius_miles:
                    continue
            elif r.postal_code:
                z = r.postal_code.strip()[:5]
                if zip_code and z == zip_code[:5]:
                    dist = None
                    rlat, rlon = lat, lon
                    exact_match = True
                else:
                    if z not in zip_cache:
                        zip_cache[z] = zip_to_latlon(z)
                    if zip_cache[z]:
                        rlat, rlon = zip_cache[z]
                        dist = round(haversine_miles(lat, lon, rlat, rlon), 2)
                        if dist > radius_miles:
                            continue
                    else:
                        continue
            else:
                continue

            if not exact_match and not zip_code and city and state:
                if (r.city or "").strip().lower() == city.strip().lower() and (r.state or "").strip().lower() == state.strip().lower():
                    exact_match = True

            custom.append({
                "source": "CUSTOM",
                "category": r.category,
                "name": r.name,
                "description": r.description,
                "phone": r.phone,
                "website": r.website,
                "address": ", ".join([p for p in [r.address1, r.city, r.state, r.postal_code] if p]) or None,
                "lat": rlat,
                "lon": rlon,
                "distance_miles": dist,
                "exact_match": exact_match,
                "verification": r.verification_status or "AI-Discovered / Pending Review",
            })

        combined = results + custom
        combined.sort(key=lambda x: x.get("distance_miles") if x.get("distance_miles") is not None else 9e9)
        return combined

    combined = _search(radius)
    if not combined:
        db = SessionLocal()
        org_id = _get_default_org_id(db)
        if org_id:
            _auto_seed_resources(
                db,
                org_id,
                zip_code=zip_code,
                city=city,
                state=state,
                category=category,
                county=county,
                count=20,
            )
            combined = _search(radius)
    exact_count = sum(1 for item in combined if item.get("exact_match"))
    payload = {
        "count": len(combined),
        "count_exact": exact_count,
        "count_nearby": len(combined) - exact_count,
        "results": combined,
        "origin": {"lat": lat, "lon": lon, "zip": zip_code[:5] if zip_code else None, "city": city or None, "state": state or None, "county": county or None},
    }
    return jsonify(payload)


@resources_bp.get("/admin")
@login_required
@require_roles(Role.DIRECTOR)
def resources_admin():
    db = SessionLocal()
    q = (request.args.get("q") or "").strip().lower()
    category = (request.args.get("category") or "").strip()
    status = (request.args.get("status") or "").strip()
    qry = db.query(Resource).filter(Resource.org_id == current_user.org_id)
    if q:
        qry = qry.filter(
            (Resource.name.ilike(f"%{q}%"))
            | (Resource.description.ilike(f"%{q}%"))
            | (Resource.city.ilike(f"%{q}%"))
        )
    if category:
        qry = qry.filter(Resource.category == category)
    if status == "active":
        qry = qry.filter(Resource.is_active == True)  # noqa: E712
    if status == "hidden":
        qry = qry.filter(Resource.is_active == False)  # noqa: E712

    items = qry.order_by(Resource.created_at.desc()).all()
    return render_template("resources/admin.html", items=items, q=q, category=category, status=status)


@resources_bp.post("/admin")
@csrf.exempt
@login_required
@require_roles(Role.DIRECTOR)
def resources_admin_post():
    action = (request.form.get("action") or "").strip()
    db = SessionLocal()

    if action == "import":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("No file selected.", "error")
            return redirect(url_for("resources.resources_admin"))

        name = f.filename.lower()
        text = ""
        try:
            if name.endswith(".docx"):
                from docx import Document
                doc = Document(f)
                text = "\n".join(p.text for p in doc.paragraphs)
            elif name.endswith(".pdf"):
                import pdfplumber
                with pdfplumber.open(f) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            else:
                text = f.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            flash(f"Import failed: {exc}", "error")
            return redirect(url_for("resources.resources_admin"))

        rows = _parse_resource_lines(text)
        if not rows:
            flash("No valid rows found. Use the 'Name | Category | Phone | Address | City | State | ZIP | Website | Description' format.", "error")
            return redirect(url_for("resources.resources_admin"))

        for row in rows:
            db.add(Resource(
                org_id=current_user.org_id,
                name=row["name"],
                category=row["category"],
                description=row["description"],
                phone=row["phone"],
                address1=row["address1"],
                city=row["city"],
                county="",
                state=row["state"],
                postal_code=row["postal_code"],
                website=row["website"],
                is_active=True,
                verification_status="Admin Verified",
            ))
        db.commit()
        audit_log(current_user.org_id, current_user.id, "RESOURCES_IMPORTED", "Resource", "bulk", detail=str(len(rows)))
        flash(f"Imported {len(rows)} resources.", "success")
        return redirect(url_for("resources.resources_admin"))

    if action == "ai_suggest" or action == "ai_bulk_seed":
        zip_code = (request.form.get("zip") or "").strip()
        category = (request.form.get("category") or "").strip()
        count = int(request.form.get("count") or 8)
        query = (request.form.get("query") or "").strip() or "veteran resources"
        if not zip_code:
            flash("ZIP is required for AI lookup.", "error")
            return redirect(url_for("resources.resources_admin"))

        api_key = current_app.config.get("OPENAI_API_KEY") or current_app.config.get("OPENAI_KEY")
        if not api_key:
            flash("OPENAI_API_KEY missing in .env", "error")
            return redirect(url_for("resources.resources_admin"))

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
            max_count = min(max(count, 1), 40)
            cat_label = category or "all"
            if action == "ai_bulk_seed":
                cat_label = "all"
                query = "veteran resources, food banks, shelters, housing, legal aid, employment, mental health, transportation"
            prompt = (
                "Return a list of local resources in this exact pipe format (one per line):\n"
                "Name | Category | Phone | Address | City | State | ZIP | Website | Description\n"
                f"ZIP: {zip_code}\n"
                f"Category: {cat_label}\n"
                f"Focus: {query}\n"
                f"Count: {max_count}\n"
                "Use categories: benefits, health, mental_health, food, shelter, housing, employment, legal, counseling, financial, transportation, education, family, emergency, vet_center, cemetery, other."
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You return strictly formatted pipe-delimited resource rows."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            text = resp.choices[0].message.content or ""
        except Exception as exc:
            flash(f"AI lookup failed: {exc}", "error")
            return redirect(url_for("resources.resources_admin"))

        rows = _parse_resource_lines(text)
        if not rows:
            flash("AI returned no usable rows.", "error")
            return redirect(url_for("resources.resources_admin"))

        for row in rows:
            db.add(Resource(
                org_id=current_user.org_id,
                name=row["name"],
                category=row["category"],
                description=row["description"],
                phone=row["phone"],
                address1=row["address1"],
                city=row["city"],
                county="",
                state=row["state"],
                postal_code=row["postal_code"],
                website=row["website"],
                is_active=True,
                verification_status="AI-Discovered / Pending Review",
            ))
        db.commit()
        audit_log(current_user.org_id, current_user.id, "AI_RESOURCES_ADDED", "Resource", "bulk", detail=str(len(rows)))
        flash(f"AI added {len(rows)} resources.", "success")
        return redirect(url_for("resources.resources_admin"))

    if action == "create":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        if not name or not category:
            flash("Name and category are required.", "error")
            return redirect(url_for("resources.resources_admin"))

        def _float(val):
            try:
                return float(val)
            except Exception:
                return None

        item = Resource(
            org_id=current_user.org_id,
            name=name,
            category=category,
            description=(request.form.get("description") or "").strip(),
            phone=(request.form.get("phone") or "").strip(),
            website=(request.form.get("website") or "").strip(),
            address1=(request.form.get("address1") or "").strip(),
            city=(request.form.get("city") or "").strip(),
            county=(request.form.get("county") or "").strip(),
            state=(request.form.get("state") or "").strip(),
            postal_code=(request.form.get("postal_code") or "").strip(),
            latitude=_float(request.form.get("latitude") or ""),
            longitude=_float(request.form.get("longitude") or ""),
            is_active=True,
            verification_status=(request.form.get("verification_status") or "Admin Verified").strip(),
        )
        db.add(item)
        db.commit()
        audit_log(current_user.org_id, current_user.id, "RESOURCE_CREATED", "Resource", item.id)
        flash("Resource created.", "success")
        return redirect(url_for("resources.resources_admin"))

    if action == "update":
        item_id = request.form.get("item_id")
        item = db.get(Resource, int(item_id)) if item_id and item_id.isdigit() else None
        if not item or item.org_id != current_user.org_id:
            flash("Resource not found.", "error")
            return redirect(url_for("resources.resources_admin"))

        def _float(val):
            try:
                return float(val)
            except Exception:
                return None

        item.name = (request.form.get("name") or "").strip()
        item.category = (request.form.get("category") or "").strip()
        item.description = (request.form.get("description") or "").strip()
        item.phone = (request.form.get("phone") or "").strip()
        item.website = (request.form.get("website") or "").strip()
        item.address1 = (request.form.get("address1") or "").strip()
        item.city = (request.form.get("city") or "").strip()
        item.county = (request.form.get("county") or "").strip()
        item.state = (request.form.get("state") or "").strip()
        item.postal_code = (request.form.get("postal_code") or "").strip()
        item.latitude = _float(request.form.get("latitude") or "")
        item.longitude = _float(request.form.get("longitude") or "")
        item.verification_status = (request.form.get("verification_status") or item.verification_status or "AI-Discovered / Pending Review").strip()
        db.commit()
        audit_log(current_user.org_id, current_user.id, "RESOURCE_UPDATED", "Resource", item.id)
        flash("Resource updated.", "success")
        return redirect(url_for("resources.resources_admin"))

    if action == "toggle":
        item_id = request.form.get("item_id")
        item = db.get(Resource, int(item_id)) if item_id and item_id.isdigit() else None
        if not item or item.org_id != current_user.org_id:
            flash("Resource not found.", "error")
            return redirect(url_for("resources.resources_admin"))
        item.is_active = not item.is_active
        db.commit()
        audit_log(current_user.org_id, current_user.id, "RESOURCE_TOGGLED", "Resource", item.id, detail=str(item.is_active))
        flash("Resource status updated.", "success")
        return redirect(url_for("resources.resources_admin"))

    if action == "delete":
        item_id = request.form.get("item_id")
        item = db.get(Resource, int(item_id)) if item_id and item_id.isdigit() else None
        if not item or item.org_id != current_user.org_id:
            flash("Resource not found.", "error")
            return redirect(url_for("resources.resources_admin"))
        db.delete(item)
        db.commit()
        audit_log(current_user.org_id, current_user.id, "RESOURCE_DELETED", "Resource", item.id)
        flash("Resource deleted.", "success")
        return redirect(url_for("resources.resources_admin"))

    flash("Unknown action.", "error")
    return redirect(url_for("resources.resources_admin"))


