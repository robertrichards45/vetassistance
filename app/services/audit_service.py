from app.extensions import SessionLocal
from app.models import AuditLog

def log(org_id: int, actor_user_id: int | None, action: str, entity_type: str = "", entity_id: str = "", detail: str = ""):
    db = SessionLocal()
    row = AuditLog(org_id=org_id, actor_user_id=actor_user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), detail=detail)
    db.add(row); db.commit()
