from datetime import datetime
from sqlalchemy import String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class HumanVisit(Base):
    """A page view confirmed as a real person: the browser ran JavaScript AND someone scrolled, moved
    the mouse, tapped, or typed while the page was on screen. Crawlers and scanners never do both."""
    __tablename__ = "human_visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip_hash: Mapped[str] = mapped_column(String(80), default="", index=True)
    path: Mapped[str] = mapped_column(String(255), default="")
    referrer_host: Mapped[str] = mapped_column(String(120), default="")
    device: Mapped[str] = mapped_column(String(12), default="")  # mobile | desktop
    signal: Mapped[str] = mapped_column(String(20), default="")
    seconds_on_page: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
