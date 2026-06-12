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
        if text.strip():
            with open(base, "w", encoding="utf-8") as f:
                f.write(text)
            doc.extracted_text_path = base
            doc.category = classify(doc.filename, doc.mime_type or "", text)
        doc.status = "READY"
        db.commit()
    except Exception as e:
        doc.status = "FAILED"
        doc.error = str(e)
        db.commit()
