from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, DateTime, Float, Boolean, Integer, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # e.g., FOOD, SHELTER, COUNSELING, LEGAL, BENEFITS, OTHER
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)

    address1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    county: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(80), default="AI-Discovered / Pending Review", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

Index("ix_resources_org_cat", Resource.org_id, Resource.category)
