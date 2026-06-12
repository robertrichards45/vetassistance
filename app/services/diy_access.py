from datetime import datetime, timedelta
from app.extensions import SessionLocal
from app.models import DIYAccount, DIYSubscription


def get_diy_account(user_id: int):
    db = SessionLocal()
    try:
        return db.query(DIYAccount).filter(DIYAccount.user_id == user_id).first()
    finally:
        db.close()


def has_diy_access(user_id: int) -> bool:
    db = SessionLocal()
    try:
        acct = db.query(DIYAccount).filter(DIYAccount.user_id == user_id).first()
        if not acct:
            return False
        sub = db.query(DIYSubscription).filter_by(diy_id=acct.id).order_by(DIYSubscription.created_at.desc()).first()
        if not sub:
            return False
        return sub.status in ("active", "trialing", "canceling")
    finally:
        db.close()


def is_within_refund_window(started_at: datetime | None) -> bool:
    if not started_at:
        return False
    return datetime.utcnow() <= started_at + timedelta(hours=72)
