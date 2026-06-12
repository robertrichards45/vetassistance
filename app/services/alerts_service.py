from datetime import datetime
from app.extensions import SessionLocal
from app.models import Alert

def create_alert(user_id: int, alert_type: str, source_key: str, title: str, body: str) -> None:
    db = SessionLocal()
    existing = db.query(Alert).filter(
        Alert.user_id == user_id,
        Alert.source_key == source_key,
    ).first()
    if existing:
        return
    alert = Alert(
        user_id=user_id,
        type=alert_type,
        source_key=source_key,
        title=title,
        body=body,
        created_at=datetime.utcnow(),
    )
    db.add(alert)
    db.commit()
