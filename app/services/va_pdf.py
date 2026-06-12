from __future__ import annotations

from pathlib import Path
from io import BytesIO
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, NameObject

def _load_brand_header() -> dict:
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'brand_header.json'))
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

def _build_header_overlay(page_width: float, page_height: float, brand: dict) -> Any:
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
    except Exception:
        return None

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    x = 36
    y = page_height - 36
    logo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static', 'brand', 'logo.png'))
    if os.path.exists(logo):
        try:
            img = ImageReader(logo)
            c.drawImage(img, x, y - 22, width=80, height=22, preserveAspectRatio=True, mask='auto')
            x += 90
        except Exception:
            pass
    if brand.get("brand"):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y - 8, brand.get("brand"))
    contact = " | ".join([v for v in [brand.get("email"), brand.get("phone")] if v])
    if contact:
        c.setFont("Helvetica", 8)
        c.drawString(x, y - 20, contact)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]

def _apply_brand_header(writer: PdfWriter) -> None:
    brand = _load_brand_header()
    cache = {}
    for page in writer.pages:
        try:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            key = (width, height, brand.get("brand"), brand.get("email"), brand.get("phone"))
            if key not in cache:
                cache[key] = _build_header_overlay(width, height, brand)
            overlay = cache.get(key)
            if overlay:
                page.merge_page(overlay)
        except Exception:
            continue


def list_pdf_fields(pdf_path: str | Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    fields = reader.get_fields() or {}
    return sorted(fields.keys())


def _update_xfa_dataset(acroform: Any, field_values: dict[str, Any]) -> bool:
    xfa = acroform.get("/XFA")
    if not xfa:
        return False
    datasets_xml = None
    datasets_stream = None
    if isinstance(xfa, ArrayObject):
        for idx in range(0, len(xfa), 2):
            if str(xfa[idx]) == "datasets":
                datasets_stream = xfa[idx + 1].get_object()
                datasets_xml = datasets_stream.get_data()
                break
    else:
        try:
            datasets_stream = xfa.get_object()
            datasets_xml = datasets_stream.get_data()
        except Exception:
            datasets_xml = None
    if not datasets_xml:
        return False

    try:
        root = ET.fromstring(datasets_xml)
    except ET.ParseError:
        return False

    def local_name(tag: str) -> str:
        return tag.split("}", 1)[-1]

    def find_child(node: ET.Element, name: str) -> ET.Element | None:
        for child in node:
            if local_name(child.tag) == name:
                return child
        return None

    def normalize_part(part: str) -> str:
        part = re.sub(r"\[.*?\]", "", part)
        part = part.lstrip("#")
        return part

    data_node = None
    for child in root:
        if local_name(child.tag) == "data":
            data_node = child
            break
    if data_node is None:
        return False

    for field_name, value in field_values.items():
        parts = [normalize_part(p) for p in field_name.split(".")]
        parts = [p for p in parts if p and not p.lower().startswith("subform")]
        if not parts:
            continue
        node = data_node
        for part in parts:
            child = find_child(node, part)
            if child is None:
                node = None
                break
            node = child
        if node is None:
            leaf = parts[-1]
            for elem in root.iter():
                if local_name(elem.tag) == leaf:
                    elem.text = "" if value is None else str(value)
            continue
        node.text = "" if value is None else str(value)

    updated_xml = ET.tostring(root, encoding="utf-8")
    try:
        if datasets_stream is not None:
            datasets_stream.set_data(updated_xml)
            return True
    except Exception:
        if datasets_stream is None:
            return False
        try:
            datasets_stream[NameObject("/Filter")] = NameObject("/FlateDecode")
            datasets_stream.set_data(updated_xml)
            return True
        except Exception:
            return False


def fill_pdf(template_path: str | Path, output_path: str | Path, field_values: dict[str, Any]) -> None:
    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    # ensure output pages/fields render values in viewers
    try:
        writer.set_need_appearances_writer()
    except Exception:
        pass

    has_acroform = False
    acroform_obj = None
    try:
        root = writer._root_object
        if root and "/AcroForm" in root:
            has_acroform = True
            acroform_obj = root.get("/AcroForm")
            if acroform_obj:
                acroform_obj = acroform_obj.get_object()
    except Exception:
        has_acroform = False

    if field_values and has_acroform:
        # Try both XFA dataset updates and AcroForm values for broader viewer support.
        if acroform_obj and "/XFA" in acroform_obj:
            _update_xfa_dataset(acroform_obj, field_values)
        try:
            if reader.get_fields():
                writer.update_page_form_field_values(writer.pages, field_values)
        except PyPdfError:
            # Some PDFs have incompatible AcroForm metadata; return the original PDF without filling.
            pass

    # Do not add company header to VA forms.

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)
