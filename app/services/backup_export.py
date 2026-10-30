"""Build a downloadable backup: every database table as JSON lines plus every uploaded file."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.extensions import SessionLocal
from app.models.base import Base


def _json(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(v)).decode()}
    if hasattr(v, "value"):  # enums
        return v.value
    return str(v)


def build(upload_dirs: list[str]) -> tuple[str, dict]:
    """Write the backup zip to a temp file. Returns (path, summary)."""
    import app.models  # noqa: F401  (registers every table on Base.metadata)
    fd, path = tempfile.mkstemp(prefix="vetassistance-backup-", suffix=".zip")
    os.close(fd)
    summary = {"tables": 0, "rows": 0, "files": 0, "bytes": 0}
    db = SessionLocal()
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for table in Base.metadata.sorted_tables:
                try:
                    rows = db.execute(select(table)).mappings()
                except Exception:
                    db.rollback()
                    continue  # table not created in this database
                count = 0
                with z.open(f"database/{table.name}.jsonl", "w", force_zip64=True) as out:
                    for r in rows:
                        out.write((json.dumps(dict(r), default=_json, ensure_ascii=False) + "\n").encode())
                        count += 1
                summary["tables"] += 1
                summary["rows"] += count
            seen = set()
            for root_dir in upload_dirs:
                if not root_dir or not os.path.isdir(root_dir):
                    continue
                real_root = os.path.realpath(root_dir)
                if real_root in seen:
                    continue
                seen.add(real_root)
                for dirpath, _dirs, files in os.walk(real_root):
                    for f in files:
                        full = os.path.join(dirpath, f)
                        if os.path.islink(full):
                            continue
                        rel = os.path.relpath(full, real_root)
                        z.write(full, f"files/{os.path.basename(real_root)}/{rel}")
                        summary["files"] += 1
                        summary["bytes"] += os.path.getsize(full)
            z.writestr("README.txt", (
                f"VetAssistance backup made {datetime.utcnow():%Y-%m-%d %H:%M} UTC.\n\n"
                "database/  one file per table, one JSON record per line\n"
                "files/     every uploaded document, in its original folder layout\n\n"
                "Social Security numbers stay encrypted inside the database files.\n"
                "This file contains veterans' personal records and documents. Store it somewhere private\n"
                "(an encrypted drive or a password-protected folder), never in email.\n"
            ))
            z.writestr("summary.json", json.dumps(summary, indent=2))
    finally:
        db.close()
    return path, summary
