from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class OrgBilling(Base):
    __tablename__ = "org_billing"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True, unique=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(255), default="")
    stripe_subscription_id: Mapped[str] = mapped_column(String(255), default="")
    plan: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(80), default="")
    current_period_end: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
