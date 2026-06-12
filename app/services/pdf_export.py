import os
import json
import textwrap
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


def _load_brand_header() -> dict:
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "brand_header.json"))
    if not os.path.exists(path):
        return {"brand": "", "email": "", "phone": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "brand": (data.get("brand") or "").strip(),
            "email": (data.get("email") or "").strip(),
            "phone": (data.get("phone") or "").strip(),
        }
    except Exception:
        return {"brand": "", "email": "", "phone": ""}


def _sanitize_filename(name: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name.strip())
    return clean[:180] or "document"


def _draw_header(c: canvas.Canvas) -> None:
    brand = _load_brand_header()
    x = 0.75 * inch
    y = 10.6 * inch
    logo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "brand", "logo.png"))
    if os.path.exists(logo):
        try:
            img = ImageReader(logo)
            c.drawImage(img, x, y - 0.2 * inch, width=1.1 * inch, height=0.3 * inch, preserveAspectRatio=True, mask="auto")
            x += 1.2 * inch
        except Exception:
            pass
    if brand.get("brand"):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, brand.get("brand"))
    contact = " | ".join([v for v in [brand.get("email"), brand.get("phone")] if v])
    if contact:
        c.setFont("Helvetica", 8)
        c.drawString(x, y - 12, contact)
    c.setLineWidth(0.5)
    c.setStrokeColorRGB(0.75, 0.78, 0.82)
    c.line(0.75 * inch, 10.4 * inch, 7.75 * inch, 10.4 * inch)


def export_text_pdf(storage_root: str, org_id: int, client_id: int, filename: str, title: str, body: str) -> str:
    folder = os.path.join(storage_root, f"org_{org_id}", f"client_{client_id}", "_exports")
    os.makedirs(folder, exist_ok=True)
    filename = _sanitize_filename(filename)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    path = os.path.join(folder, filename)

    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    margin_x = 0.85 * inch
    margin_y = 0.9 * inch
    max_width = 95
    line_height = 14

    def new_page():
        c.showPage()
        _draw_header(c)

    _draw_header(c)
    y = height - margin_y - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, title)
    y -= 22

    c.setFont("Helvetica", 10)
    lines = []
    for raw_line in (body or "").splitlines():
        if not raw_line.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(raw_line, width=max_width))

    for line in lines:
        if y <= margin_y + 40:
            new_page()
            y = height - margin_y - 20
            c.setFont("Helvetica", 10)
        c.drawString(margin_x, y, line)
        y -= line_height

    c.save()
    return path
