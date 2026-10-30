from datetime import datetime
import os
from app.extensions import SessionLocal
from app.models import Document
from app.services.evidence_classifier import classify
from app.services.doc_text import extract_text

def extract_document_text(document_id: int) -> None:
    db = SessionLocal()
    doc = db.get(Document, document_id)
    if not doc:
        return
    try:
        doc.status = "PROCESSING"
        db.commit()
        text = extract_text(doc.storage_path)
        # store extracted text next to file
        base = doc.storage_path + ".txt"
        # Always write the file (even when empty) so the document counts as read and isn't retried forever.
        with open(base, "w", encoding="utf-8") as f:
            f.write(text or "")
        doc.extracted_text_path = base
        if text.strip():
            doc.category = classify(doc.filename, doc.mime_type or "", text)
        doc.status = "READY"
        db.commit()
    except Exception as e:
        doc.status = "FAILED"
        doc.error = str(e)
        db.commit()


def extract_many(document_ids: list) -> None:
    """Read several documents one after another (OCR is CPU and memory heavy; never run dozens at once)."""
    for did in document_ids:
        try:
            extract_document_text(did)
        except Exception:
            SessionLocal().rollback()
