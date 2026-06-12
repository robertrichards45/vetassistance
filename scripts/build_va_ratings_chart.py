import json
import os
import re
from datetime import datetime

import pdfplumber


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PDF_CANDIDATES = [
    os.path.join(ROOT, "38 CFR Part 4 (up to date as of 1-22-2026).pdf"),
    os.path.join(ROOT, "eCFR __ Title 38 of the CFR -- Pensions, Bonuses, and Veterans' Relief.pdf"),
]
OUT_PATH = os.path.join(ROOT, "cfr_data", "va_ratings_chart.json")
CFR_JSON_CANDIDATES = [
    os.path.join(ROOT, "cfr_data", "cfr38_full.json"),
    os.path.join(ROOT, "cfr_data", "cfr38_rules.json"),
]
GARBAGE_MARKERS = [
    "VerDate", "Jkt", "RFC", "DORP", "SGML", "Pt. 4", "Ch. I",
    "Frm", "Fmt", "Sfmt", "Sec.", "Y:\\SGML", "262149",
    "sraepsj", "htiw",
]


PERCENT_LINE_RE = re.compile(r"\b(100|90|80|70|60|50|40|30|20|10|0)\s*(percent|%)\b", re.IGNORECASE)
PERCENT_GARBAGE_RE = re.compile(r"\b(note|authority|u\.s\.c|fr)\b", re.IGNORECASE)
SECTION_RE = re.compile(r"§\s*4\.\d+[a-z]*", re.IGNORECASE)
DC_INLINE_RE = re.compile(r"(.+?)\s*\(Diagnostic Code\s*(\d{4})\)", re.IGNORECASE)
DC_PREFIX_RE = re.compile(r"^(?:Diagnostic Code|DC)\s*(\d{4})\b[:\-–]?\s*(.*)$", re.IGNORECASE)
DC_LEAD_RE = re.compile(r"^(\d{4})\s+(.+)$")
DOT_LEADER_RE = re.compile(r"\.{2,}.*$")


def _choose_pdf() -> str:
    for p in PDF_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("No CFR PDF found. Expected one of: " + ", ".join(PDF_CANDIDATES))


def _normalize_line(line: str) -> str:
    s = line.replace("\u00a0", " ").strip()
    s = re.sub(r"\s{2,}", " ", s)
    return s


def _clean_condition_name(text: str) -> str:
    s = DOT_LEADER_RE.sub("", text).strip()
    s = re.sub(r"\b\d+\b", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s).strip(" -–")
    return s


def _clean_criteria_text(text: str) -> str:
    s = text.strip(" -–")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _extract_percent(line: str):
    if PERCENT_GARBAGE_RE.search(line):
        return None
    m = PERCENT_LINE_RE.search(line)
    if not m:
        return None
    return int(m.group(1))


def _extract_section(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"4\.\d+[a-z]*", str(text).lower())
    return m.group(0) if m else ""


def _clean_title(text: str) -> str:
    s = str(text).replace("[Removed]", "").strip()
    s = re.sub(r"(?i)\b(added|removed)\b[^.]*\.?", "", s).strip()
    s = re.sub(r"\.{2,}.*", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s).strip(" -–")
    return s


def _clean_criteria_line(text: str) -> str:
    s = str(text).replace("[Removed]", "").strip()
    if any(m in s for m in GARBAGE_MARKERS):
        return ""
    s = DOT_LEADER_RE.sub("", s).strip()
    s = re.sub(r"(?i)\(authority:.*?\)", "", s).strip()
    s = re.sub(r"(?i)\b\d{2}\s*fr\b[^.]*\.?", "", s).strip()
    s = re.sub(r"(?i)\b38\s*u\.?s\.?c\.?\s*\d+[^\n]*", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _is_useful_note(text: str) -> bool:
    s = text.strip()
    if len(s) < 20:
        return False
    if not re.search(r"[A-Za-z]", s):
        return False
    if s.endswith("through"):
        return False
    return True


def _load_cfr_json():
    for p in CFR_JSON_CANDIDATES:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    return {"rules": []}


def build_chart():
    pdf_path = _choose_pdf()
    conditions = []
    pdf_by_dc = {}

    current_section = ""
    current = None
    current_percent = None
    current_lines = []

    def flush_percent():
        nonlocal current_percent, current_lines
        if current and current_percent is not None and current_lines:
            text = _clean_criteria_text(" ".join(current_lines))
            if text:
                current["criteria"].append({"percent": current_percent, "text": text})
        current_percent = None
        current_lines = []

    def flush_condition():
        nonlocal current
        if current and current.get("criteria"):
            conditions.append(current)
        current = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            raw_lines = text.splitlines()
            lines = []
            i = 0
            while i < len(raw_lines):
                line = _normalize_line(raw_lines[i])
                if line.endswith("-") and i + 1 < len(raw_lines):
                    nxt = _normalize_line(raw_lines[i + 1])
                    if nxt and nxt[0].isalpha():
                        line = line[:-1] + nxt
                        i += 1
                if line:
                    lines.append(line)
                i += 1

            for line in lines:
                if line.lower().startswith("title 38") or line.lower().startswith("part 4"):
                    continue
                if "ecfr" in line.lower():
                    continue

                sec = SECTION_RE.search(line)
                if sec:
                    current_section = sec.group(0).replace("§", "").strip()

                dc_inline = DC_INLINE_RE.search(line)
                dc_prefix = DC_PREFIX_RE.match(line)
                dc_lead = DC_LEAD_RE.match(line)

                if dc_inline:
                    flush_percent()
                    flush_condition()
                    name = _clean_condition_name(dc_inline.group(1))
                    dc = dc_inline.group(2)
                    current = {
                        "condition": name,
                        "diagnostic_code": dc,
                        "cfr_section": current_section,
                        "criteria": [],
                    }
                    continue
                if dc_prefix:
                    flush_percent()
                    flush_condition()
                    dc = dc_prefix.group(1)
                    name = _clean_condition_name(dc_prefix.group(2) or "")
                    current = {
                        "condition": name if name else f"Diagnostic Code {dc}",
                        "diagnostic_code": dc,
                        "cfr_section": current_section,
                        "criteria": [],
                    }
                    continue
                if dc_lead and not line.startswith("§"):
                    dc = dc_lead.group(1)
                    name = _clean_condition_name(dc_lead.group(2))
                    if name and len(name) > 2:
                        flush_percent()
                        flush_condition()
                        current = {
                            "condition": name,
                            "diagnostic_code": dc,
                            "cfr_section": current_section,
                            "criteria": [],
                        }
                        continue

                if current:
                    pct = _extract_percent(line)
                    if pct is not None:
                        flush_percent()
                        current_percent = pct
                        stripped = PERCENT_LINE_RE.sub("", line)
                        stripped = stripped.replace("–", "-")
                        stripped = stripped.strip(" -")
                        if stripped:
                            current_lines = [stripped]
                        else:
                            current_lines = []
                        continue

                    if current_percent is not None:
                        if SECTION_RE.search(line) or DC_PREFIX_RE.match(line) or DC_INLINE_RE.search(line):
                            flush_percent()
                            continue
                        current_lines.append(line)

    flush_percent()
    flush_condition()

    for c in conditions:
        if c.get("diagnostic_code"):
            pdf_by_dc[c["diagnostic_code"]] = c

    combined = []
    seen = set()
    cfr_data = _load_cfr_json()
    for r in cfr_data.get("rules", []):
        dc = str(r.get("diagnostic_code") or "").strip()
        if not dc.isdigit():
            continue
        title = _clean_title(r.get("title") or r.get("key") or "")
        if not title:
            title = f"Diagnostic Code {dc}"
        section = _extract_section(r.get("cfr") or "")
        criteria_entries = []
        notes = []
        for line in r.get("rating_criteria", []) or []:
            line = _clean_criteria_line(line)
            pct = _extract_percent(line)
            if pct is None:
                if line and _is_useful_note(line):
                    notes.append(line)
                continue
            cleaned = PERCENT_LINE_RE.sub("", line).strip(" -–")
            if cleaned:
                criteria_entries.append({"percent": pct, "text": cleaned})
        base = {
            "condition": title,
            "diagnostic_code": dc,
            "cfr_section": section,
            "criteria": criteria_entries,
            "notes": notes,
        }
        if dc in pdf_by_dc and pdf_by_dc[dc].get("criteria"):
            base["criteria"] = pdf_by_dc[dc]["criteria"]
            if pdf_by_dc[dc].get("cfr_section"):
                base["cfr_section"] = pdf_by_dc[dc]["cfr_section"]
            if pdf_by_dc[dc].get("condition"):
                base["condition"] = pdf_by_dc[dc]["condition"]
        if base["criteria"] or base["notes"]:
            combined.append(base)
        seen.add(dc)

    for dc, c in pdf_by_dc.items():
        if dc not in seen:
            combined.append(c)

    payload = {
        "source_pdf": os.path.basename(pdf_path),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "conditions": combined,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return OUT_PATH, len(combined)


if __name__ == "__main__":
    path, count = build_chart()
    print(f"Wrote {count} conditions to {path}")
