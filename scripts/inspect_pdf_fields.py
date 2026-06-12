from __future__ import annotations

import sys
from pathlib import Path

from app.services.va_pdf import list_pdf_fields


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_pdf_fields.py <path-to-pdf>")
        return 2
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        return 1
    fields = list_pdf_fields(pdf_path)
    if not fields:
        print("No fields found.")
        return 0
    for name in fields:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
