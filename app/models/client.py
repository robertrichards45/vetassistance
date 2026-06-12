from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base

class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    organization = relationship("Organization")

    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    full_legal_name: Mapped[str] = mapped_column(String(255), default="")
    ssn_full: Mapped[str] = mapped_column(String(20), default="")
    ssn_encrypted: Mapped[str] = mapped_column(String(800), default="")
    ssn_last4: Mapped[str] = mapped_column(String(4), default="")
    dob: Mapped[str] = mapped_column(String(20), default="")
    mailing_address1: Mapped[str] = mapped_column(String(255), default="")
    mailing_address2: Mapped[str] = mapped_column(String(255), default="")
    mailing_city: Mapped[str] = mapped_column(String(120), default="")
    mailing_state: Mapped[str] = mapped_column(String(40), default="")
    mailing_zip: Mapped[str] = mapped_column(String(20), default="")
    physical_address1: Mapped[str] = mapped_column(String(255), default="")
    physical_address2: Mapped[str] = mapped_column(String(255), default="")
    physical_city: Mapped[str] = mapped_column(String(120), default="")
    physical_state: Mapped[str] = mapped_column(String(40), default="")
    physical_zip: Mapped[str] = mapped_column(String(20), default="")
    emergency_contact_name: Mapped[str] = mapped_column(String(255), default="")
    emergency_contact_phone: Mapped[str] = mapped_column(String(40), default="")
    emergency_contact_relationship: Mapped[str] = mapped_column(String(120), default="")
    service_entry_date: Mapped[str] = mapped_column(String(20), default="")
    service_discharge_date: Mapped[str] = mapped_column(String(20), default="")
    service_branch: Mapped[str] = mapped_column(String(120), default="")
    referral_source: Mapped[str] = mapped_column(String(255), default="")

    # portal linkage (optional)
    portal_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    portal_user = relationship("User", foreign_keys=[portal_user_id])
    portal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    account_type: Mapped[str] = mapped_column(String(20), default="verified")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(120), default="Intake Received")
    claim_number: Mapped[str] = mapped_column(String(120), default="")
    va_rating_percent: Mapped[int] = mapped_column(Integer, default=0)
    retirement_status: Mapped[str] = mapped_column(String(120), default="")
    assigned_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def display_name(self) -> str:
        n = f"{self.first_name} {self.last_name}".strip()
        return n if n else f"Client #{self.id}"
