import os
import pdfplumber

_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

def _configure_tesseract() -> None:
    try:
        import pytesseract
    except Exception:
        return
    for path in _TESSERACT_PATHS:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            break

def _ocr_image(image) -> str:
    try:
        import pytesseract
    except Exception:
        return ""
    _configure_tesseract()
    try:
        return pytesseract.image_to_string(image) or ""
    except Exception:
        return ""

def _ocr_pdf(path: str) -> str:
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except Exception:
        return ""
    _configure_tesseract()
    text_parts = []
    try:
        doc = fitz.open(path)
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            mode = "RGBA" if pix.alpha else "RGB"
            img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            if mode == "RGBA":
                img = img.convert("RGB")
            t = _ocr_image(img)
            if t.strip():
                text_parts.append(t)
    except Exception:
        return ""
    return "\n\n".join(text_parts)

def _extract_docx(path: str) -> str:
    try:
        import docx2txt
    except Exception:
        return ""
    try:
        return docx2txt.process(path) or ""
    except Exception:
        return ""

def extract_text(path: str) -> str:
    # Safe, best-effort extraction. For OCR you'd add pytesseract + image conversion.
    if not os.path.exists(path):
        return ""
    ext = os.path.splitext(path.lower())[1]
    if ext == ".pdf":
        try:
            text_parts = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    if t.strip():
                        text_parts.append(t)
            text = "\n\n".join(text_parts)
            if text.strip():
                return text[:800000]  # guardrail
        except Exception:
            pass
        # fall back to OCR for scanned PDFs
        return _ocr_pdf(path)[:800000]
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif"):
        try:
            from PIL import Image
        except Exception:
            return ""
        try:
            if ext in (".heic", ".heif"):
                try:
                    from pillow_heif import register_heif_opener
                    register_heif_opener()
                except Exception:
                    pass
            img = Image.open(path)
            return _ocr_image(img)[:800000]
        except Exception:
            try:
                import imageio.v3 as iio
            except Exception:
                return ""
            try:
                arr = iio.imread(path)
                from PIL import Image
                img = Image.fromarray(arr)
                return _ocr_image(img)[:800000]
            except Exception:
                return ""
    if ext == ".docx":
        return _extract_docx(path)[:800000]
    if ext in (".txt", ".md"):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:800000]
        except Exception:
            return ""
    return ""
