from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PricingItem(Base):
    __tablename__ = "pricing_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    is_percent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    percent_of_backpay: Mapped[int] = mapped_column(Integer, default=0)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
