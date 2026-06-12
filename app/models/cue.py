from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base


class CueMotion(Base):
    __tablename__ = "cue_motions"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), index=True)
    created_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    decision_type: Mapped[str] = mapped_column(String(80), default="")
    decision_date: Mapped[str] = mapped_column(String(40), default="")
    decision_office: Mapped[str] = mapped_column(String(120), default="")
    issues_text: Mapped[str] = mapped_column(Text, default="")
    appealed: Mapped[str] = mapped_column(String(20), default="")
    appeal_outcome: Mapped[str] = mapped_column(Text, default="")
    appeal_date: Mapped[str] = mapped_column(String(40), default="")

    screener_json: Mapped[str] = mapped_column(Text, default="{}")
    risk_flag: Mapped[str] = mapped_column(String(20), default="yellow")
    status: Mapped[str] = mapped_column(String(20), default="draft")

    remedy_requested: Mapped[str] = mapped_column(String(120), default="")
    requested_rating: Mapped[str] = mapped_column(String(40), default="")
    requested_effective_date: Mapped[str] = mapped_column(String(60), default="")
    veteran_statement: Mapped[str] = mapped_column(Text, default="")

    full_legal_name: Mapped[str] = mapped_column(String(255), default="")
    file_number_last4: Mapped[str] = mapped_column(String(40), default="")
    dob: Mapped[str] = mapped_column(String(20), default="")
    mailing_address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    signature_name: Mapped[str] = mapped_column(String(255), default="")
    signature_date: Mapped[str] = mapped_column(String(40), default="")
    signature_image_path: Mapped[str] = mapped_column(String(255), default="")

    rep_name: Mapped[str] = mapped_column(String(255), default="")
    rep_org: Mapped[str] = mapped_column(String(255), default="")
    rep_contact: Mapped[str] = mapped_column(String(255), default="")

    submission_method: Mapped[str] = mapped_column(String(40), default="")
    submission_date: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    error_blocks = relationship("CueErrorBlock", back_populates="motion", cascade="all, delete-orphan")
    files = relationship("CueFile", back_populates="motion", cascade="all, delete-orphan")


class CueErrorBlock(Base):
    __tablename__ = "cue_error_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    cue_motion_id: Mapped[int] = mapped_column(Integer, ForeignKey("cue_motions.id"), index=True)
    category: Mapped[str] = mapped_column(String(120), default="")
    va_error_text: Mapped[str] = mapped_column(Text, default="")
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    law_text: Mapped[str] = mapped_column(Text, default="")
    outcome_text: Mapped[str] = mapped_column(Text, default="")

    motion = relationship("CueMotion", back_populates="error_blocks")


class CueFile(Base):
    __tablename__ = "cue_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    cue_motion_id: Mapped[int] = mapped_column(Integer, ForeignKey("cue_motions.id"), index=True)
    docx_path: Mapped[str] = mapped_column(String(255), default="")
    pdf_path: Mapped[str] = mapped_column(String(255), default="")
    decision_path: Mapped[str] = mapped_column(String(255), default="")
    confirmation_path: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    motion = relationship("CueMotion", back_populates="files")


class CueDisclaimerAcceptance(Base):
    __tablename__ = "cue_disclaimer_acceptance"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
