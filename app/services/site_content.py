from datetime import datetime
from app.extensions import SessionLocal
from app.models import SiteContent

DEFAULTS = {
  "HOME_HERO_TITLE": "Veterans Benefits",
  "HOME_HERO_SUBTITLE": "A premium claims intelligence ecosystem: portal, evidence discipline, and AI-guided strategy — built to win.",
  "HOME_CTA_PRIMARY": "Become a Client",
  "HOME_CTA_SECONDARY": "Client Login",
  "HOME_FEATURE_1_TITLE": "Evidence Discipline",
  "HOME_FEATURE_1_BODY": "Upload, categorize, and track evidence with checklists and timelines so nothing gets missed.",
  "HOME_FEATURE_2_TITLE": "AI Guidance per Client",
  "HOME_FEATURE_2_BODY": "Evidence scoring, gaps, conflicts, and next-best actions — tied to the client’s file.",
  "HOME_FEATURE_3_TITLE": "Documents + Letters on Demand",
  "HOME_FEATURE_3_BODY": "Generate DOCX, web copy, and email drafts in seconds. Nexus packets included.",
  "ALLOW_PORTAL_SELF_SIGNUP": "0",
}

def get_value(org_id: int, key: str) -> str:
    db = SessionLocal()
    row = db.query(SiteContent).filter_by(org_id=org_id, key=key).first()
    if row and row.value is not None:
        return row.value
    return DEFAULTS.get(key, "")

def set_value(org_id: int, key: str, value: str):
    db = SessionLocal()
    row = db.query(SiteContent).filter_by(org_id=org_id, key=key).first()
    if not row:
        row = SiteContent(org_id=org_id, key=key, value=value or "")
        db.add(row)
    else:
        row.value = value or ""
        row.updated_at = datetime.utcnow()
    db.commit()

def ensure_defaults(org_id: int):
    db = SessionLocal()
    for k,v in DEFAULTS.items():
        row = db.query(SiteContent).filter_by(org_id=org_id, key=k).first()
        if not row:
            db.add(SiteContent(org_id=org_id, key=k, value=v))
    db.commit()
