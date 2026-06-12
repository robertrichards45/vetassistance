import json
from app.extensions import SessionLocal
from app.models import Client, FormData, AIRun, Organization

def build_context(org_id: int, client_id: int) -> dict:
    db = SessionLocal()
    c = db.get(Client, client_id)
    org = db.get(Organization, org_id)
    rep_name = ''
    if c and getattr(c,'assigned_user_id',None):
        from app.models import User
        rep = db.get(User, c.assigned_user_id)
        if rep and rep.org_id == org_id:
            rep_name = rep.full_name or rep.email
    ctx = {
        "rep_name": rep_name,

        "brand_name": (org.name if org else "Veteran Benefits Assistance"),
        "brand_email": getattr(org, "email", "") if org else "",
        "client_name": (c.display_name() if c else ""),
        "client_email": (c.email if c else ""),
        "client_phone": (getattr(c, "phone", "") if c else ""),
    }

    fd = db.query(FormData).filter_by(org_id=org_id, client_id=client_id, form_key="INTAKE_V1").first()
    if fd and fd.data_json:
        try:
            ctx.update(json.loads(fd.data_json))
        except Exception:
            pass

    latest = db.query(AIRun).filter_by(org_id=org_id, client_id=client_id).order_by(AIRun.created_at.desc()).first()
    if latest and latest.result_json:
        try:
            parsed = json.loads(latest.result_json)
            ctx["ai_summary"] = parsed.get("summary", "")
            ctx["ai_next_actions"] = parsed.get("next_actions", [])
            ctx["ai_gaps"] = parsed.get("gaps", [])
            ctx["ai_conflicts"] = parsed.get("conflicts", [])
            ctx["ai_conditions"] = parsed.get("conditions", [])
        except Exception:
            ctx["ai_summary"] = ""
    return ctx


