from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ClientPayment(Base):
    __tablename__ = "client_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="usd")
    status: Mapped[str] = mapped_column(String(40), default="")
    stripe_session_id: Mapped[str] = mapped_column(String(255), default="")
    stripe_payment_intent_id: Mapped[str] = mapped_column(String(255), default="")
    receipt_url: Mapped[str] = mapped_column(String(1024), default="")
    invoice_id: Mapped[str] = mapped_column(String(255), default="")
    invoice_url: Mapped[str] = mapped_column(String(1024), default="")
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
