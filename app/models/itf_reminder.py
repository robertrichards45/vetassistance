from datetime import date, datetime
from sqlalchemy import Date, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ItfReminder(Base):
    """Email reminders before a veteran's Intent to File expires (one year after VA receives it)."""
    __tablename__ = "itf_reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    filed_on: Mapped[date] = mapped_column(Date)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sent: Mapped[str] = mapped_column(String(40), default="")  # comma list of reminder keys already sent: 60,30,7
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
