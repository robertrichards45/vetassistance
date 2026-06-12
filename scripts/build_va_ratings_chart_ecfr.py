import json
import argparse
import os
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import requests


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_PATH = os.path.join(ROOT, "cfr_data", "va_ratings_chart.json")
CACHE_DIR = os.path.join(ROOT, "cfr_data", "ecfr_cache")
CFR_JSON = os.path.join(ROOT, "cfr_data", "cfr38_full.json")

BASE_PART_URL = "https://ecfr.io/Title-38/Part-4"
SECTION_URL = "https://ecfr.io/Title-38/Section-{}"

PERCENT_LINE_RE = re.compile(r"\|\s*(100|90|80|70|60|50|40|30|20|10|0)\s*$")
PERCENT_ONLY_RE = re.compile(r"^(100|90|80|70|60|50|40|30|20|10|0)\s*\|\s*$")
SECTION_RE = re.compile(r"\bSECTION\s+4\.\d+[a-z]*\b", re.IGNORECASE)
DC_LINE_RE = re.compile(r"^(\d{4})\s+(.+)$")
DC_MULTI_RE = re.compile(r"(\d{4})\s+(.+?)(?=\s+\d{4}\s+|$)")
DC_ANY_RE = re.compile(r"\b\d{4}\b")
DC_INLINE_RE = re.compile(r"\bDCs?\b\s*([0-9,\-\s]+)", re.IGNORECASE)
RANGE_RE = re.compile(r"(\d{4})\s*-\s*(\d{4})")
GARBAGE_RE = re.compile(r"\b(VerDate|Jkt|RFC|DORP|SGML|Frm|Fmt|Sfmt|Y:\\\\SGML)\b")
DOT_LEADER_RE = re.compile(r"\.{2,}.*$")
FORMULA_HEAD_RE = re.compile(r"^(general\s+)?rating formula", re.IGNORECASE)


def html_to_text(html: str) -> list[str]:
    s = html
    s = re.sub(r"(?is)<script.*?>.*?</script>", "", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", "", s)
    s = s.replace("</tr>", "\n").replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    s = s.replace("</td>", " | ").replace("</th>", " | ")
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = unescape(s)
    lines = [re.sub(r"\s{2,}", " ", line).strip() for line in s.splitlines()]
    return [line for line in lines if line]


def expand_dc_list(text: str) -> list[str]:
    out = []
    for part in re.split(r"[,\s]+", text):
        part = part.strip()
        if not part:
            continue
        m = RANGE_RE.match(part)
        if m:
            start = int(m.group(1))
            end = int(m.group(2))
            if start <= end:
                for dc in range(start, end + 1):
                    out.append(str(dc))
            continue
        if re.fullmatch(r"\d{4}", part):
            out.append(part)
    return out


def _load_section_html(section: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"section_{section}.html")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    url = SECTION_URL.format(section)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


def parse_section(section: str) -> dict:
    url = SECTION_URL.format(section)
    html = _load_section_html(section)
    lines = html_to_text(html)

    codes = []
    formulas = []
    formula_map = {}
    current_dc = None
    current_dc_name = None
    dc_criteria = {}
    current_formula = None
    current_formula_lines = []

    def flush_formula():
        nonlocal current_formula, current_formula_lines
        if current_formula and current_formula_lines:
            formulas.append({"name": current_formula, "criteria": current_formula_lines})
        current_formula = None
        current_formula_lines = []

    recent_codes = []
    buffer_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if GARBAGE_RE.search(line):
            i += 1
            continue
        if "privacy policy" in line.lower():
            i += 1
            continue
        if line.lower().startswith("table of contents"):
            i += 1
            continue

        if FORMULA_HEAD_RE.match(line.lower()):
            # merge with next line if heading wrapped
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if "|" not in nxt and not PERCENT_ONLY_RE.match(nxt) and not nxt.strip().isdigit():
                    line = f"{line} {nxt}".strip()
                    i += 1

        if FORMULA_HEAD_RE.match(line.lower()):
            flush_formula()
            current_formula = line.strip()
            current_formula_lines = []
            # map if line explicitly lists DCs
            dc_inline = DC_INLINE_RE.search(line)
            if dc_inline:
                for dc in expand_dc_list(dc_inline.group(1)):
                    formula_map.setdefault(dc, set()).add(current_formula)
            elif recent_codes:
                for dc in recent_codes:
                    formula_map.setdefault(dc, set()).add(current_formula)
            i += 1
            continue
        if "rating formula" in line.lower() and ("diagnostic code" in line.lower() or "diagnostic codes" in line.lower()):
            dcs = DC_ANY_RE.findall(line)
            if dcs:
                formula_name = line.strip()
                for dc in dcs:
                    formula_map.setdefault(dc, set()).add(formula_name)
            i += 1
            continue

        # extract multiple codes on the same line
        multi = list(DC_MULTI_RE.finditer(line))
        if multi:
            recent_codes = []
            for m in multi:
                dc = m.group(1)
                name = m.group(2).strip()
                codes.append({"dc": dc, "name": name})
                recent_codes.append(dc)
                if current_formula:
                    formula_map.setdefault(dc, set()).add(current_formula)
            i += 1
            continue

        dc_match = DC_LINE_RE.match(line)
        if dc_match:
            current_dc = dc_match.group(1)
            current_dc_name = dc_match.group(2).strip()
            codes.append({"dc": current_dc, "name": current_dc_name})
            recent_codes = [current_dc]
            if current_formula:
                formula_map.setdefault(current_dc, set()).add(current_formula)
            i += 1
            continue

        if current_formula:
            if line in {"|", "Rating |", "Rating |".lower()}:
                i += 1
                continue

        percent_match = PERCENT_LINE_RE.search(line)
        if percent_match:
            percent = int(percent_match.group(1))
            text = line[:percent_match.start()].strip().strip("|").strip()
            if current_formula:
                if buffer_lines and not text:
                    text = " ".join(buffer_lines).strip()
                current_formula_lines.append({"percent": percent, "text": text})
            elif current_dc:
                dc_criteria.setdefault(current_dc, []).append({"percent": percent, "text": text})
            buffer_lines = []
            i += 1
            continue

        if line.endswith("|") and i + 1 < len(lines) and PERCENT_ONLY_RE.match(lines[i + 1]):
            percent = int(PERCENT_ONLY_RE.match(lines[i + 1]).group(1))
            text = line.strip().strip("|").strip()
            if current_formula:
                if buffer_lines:
                    text = " ".join(buffer_lines + ([text] if text else [])).strip()
                current_formula_lines.append({"percent": percent, "text": text})
            elif current_dc:
                dc_criteria.setdefault(current_dc, []).append({"percent": percent, "text": text})
            buffer_lines = []
            i += 2
            continue

        if current_formula:
            if line.endswith("|"):
                buffer_lines.append(line.strip().strip("|").strip())
                i += 1
                continue
            if line:
                buffer_lines.append(line.strip())

        i += 1

    flush_formula()

    # override: eating disorders use their own formula, not mental disorders
    for dc, names in list(formula_map.items()):
        has_eating = any("eating" in n.lower() for n in names)
        if has_eating:
            formula_map[dc] = set([n for n in names if "eating" in n.lower()])

    return {
        "section": section,
        "url": url,
        "codes": codes,
        "formulas": formulas,
        "formula_map": {k: sorted(list(v)) for k, v in formula_map.items()},
        "dc_criteria": dc_criteria,
    }


def build_chart(sections_override: list[str] | None = None):
    if sections_override:
        sections = sections_override
    else:
        resp = requests.get(BASE_PART_URL, timeout=30)
        resp.raise_for_status()
        lines = html_to_text(resp.text)
        sections = []
        for line in lines:
            m = SECTION_RE.search(line)
            if m:
                sec = m.group(0).split()[-1].strip()
                if sec not in sections:
                    sections.append(sec)

    section_map = {}
    global_formulas = []
    dc_to_section = {}
    for section in sections:
        parsed = parse_section(section)
        section_map[section] = {
            "parsed": parsed,
            "formula_by_name": {f["name"]: f["criteria"] for f in parsed["formulas"]},
        }
        for f in parsed.get("formulas", []):
            if f.get("criteria"):
                global_formulas.append((f.get("name") or "", f.get("criteria")))
        for code in parsed.get("codes", []):
            dc = code.get("dc")
            if dc and dc not in dc_to_section:
                dc_to_section[dc] = section

    def _extract_section(text: str) -> str:
        m = re.search(r"4\.\d+[a-z]*", (text or "").lower())
        return m.group(0) if m else ""

    def _criteria_from_lines(lines: list[str]) -> list[dict]:
        out = []
        for line in lines or []:
            m = re.search(r"(100|90|80|70|60|50|40|30|20|10|0)\s*%?", str(line))
            if not m:
                continue
            pct = int(m.group(1))
            out.append({"percent": pct, "text": str(line).strip()})
        return out

    conditions = []
    if os.path.exists(CFR_JSON):
        with open(CFR_JSON, "r", encoding="utf-8") as f:
            cfr = json.load(f)
        for r in cfr.get("rules", []):
            dc = str(r.get("diagnostic_code") or "").strip()
            if not dc.isdigit():
                continue
            name = str(r.get("title") or r.get("key") or "").strip()
            section = dc_to_section.get(dc) or _extract_section(r.get("cfr") or "")
            criteria = []
            formula_names = []
            source_url = SECTION_URL.format(section) if section else ""

            if section in section_map:
                parsed = section_map[section]["parsed"]
                formula_by_name = section_map[section]["formula_by_name"]
                formula_names = parsed["formula_map"].get(dc, [])
                if formula_names:
                    for fname in formula_names:
                        criteria.extend(formula_by_name.get(fname, []))
                if not criteria:
                    # keyword match between formula name and condition name
                    name_l = (name or "").lower()
                    best = None
                    best_score = 0
                    for fname, fcriteria in formula_by_name.items():
                        if not fcriteria:
                            continue
                        base = fname.lower().replace("general rating formula for", "").replace("rating formula for", "")
                        words = [w for w in re.split(r"[^a-z]+", base) if len(w) >= 4]
                        if not words:
                            continue
                        score = sum(1 for w in words if w in name_l)
                        if score > best_score:
                            best_score = score
                            best = fcriteria
                    if best and best_score > 0:
                        criteria.extend(best)
                if not criteria:
                    # fallback: apply the only formula if the section has one
                    formulas_with_criteria = [f for f in parsed["formulas"] if f.get("criteria")]
                    if len(formulas_with_criteria) == 1:
                        criteria.extend(formulas_with_criteria[0].get("criteria", []))

            if not criteria:
                criteria = _criteria_from_lines(r.get("rating_criteria", []) or [])
            if not criteria and global_formulas:
                # global keyword match as last resort
                name_l = (name or "").lower()
                best = None
                best_score = 0
                for fname, fcriteria in global_formulas:
                    base = (fname or "").lower().replace("general rating formula for", "").replace("rating formula for", "")
                    words = [w for w in re.split(r"[^a-z]+", base) if len(w) >= 4]
                    if not words:
                        continue
                    score = sum(1 for w in words if w in name_l)
                    if score > best_score:
                        best_score = score
                        best = fcriteria
                if best and best_score > 0:
                    criteria = best
            if not criteria and global_formulas:
                # fallback: use the most complete formula in the entire Part 4
                best = max(global_formulas, key=lambda x: len(x[1]))
                criteria = best[1]

            conditions.append({
                "condition": name,
                "diagnostic_code": dc,
                "cfr_section": section,
                "criteria": criteria,
                "formula_names": formula_names,
                "source_url": source_url,
            })
    else:
        for section in sections:
            parsed = section_map[section]["parsed"]
            formula_by_name = section_map[section]["formula_by_name"]
            for code in parsed["codes"]:
                dc = code["dc"]
                name = code["name"]
                criteria = parsed["dc_criteria"].get(dc, [])
                formula_names = parsed["formula_map"].get(dc, [])
                if not criteria and formula_names:
                    for fname in formula_names:
                        criteria.extend(formula_by_name.get(fname, []))
                if not criteria:
                    formulas_with_criteria = [f for f in parsed["formulas"] if f.get("criteria")]
                    if formulas_with_criteria:
                        best_formula = max(formulas_with_criteria, key=lambda f: len(f.get("criteria", [])))
                        criteria.extend(best_formula.get("criteria", []))
                conditions.append({
                    "condition": name,
                    "diagnostic_code": dc,
                    "cfr_section": section,
                    "criteria": criteria,
                    "formula_names": formula_names,
                    "source_url": parsed["url"],
                })
    # optional name cleanup from local CFR JSON
    name_map = {}
    if os.path.exists(CFR_JSON):
        with open(CFR_JSON, "r", encoding="utf-8") as f:
            cfr = json.load(f)
        for r in cfr.get("rules", []):
            dc = str(r.get("diagnostic_code") or "").strip()
            title = str(r.get("title") or "").strip()
            if dc and title and "[removed]" not in title.lower():
                name_map[dc] = title

    def _bad_name(title: str) -> bool:
        t = title.strip()
        if not t:
            return True
        if "[removed]" in t.lower():
            return True
        if "added" in t.lower() or "criterion" in t.lower() or "note" in t.lower():
            return True
        if t.lower() in {"complete", "partial", "total"}:
            return True
        if "." in t and DOT_LEADER_RE.sub("", t).strip() != t.strip():
            return True
        if len(t) < 6:
            return True
        if t.endswith((" and", " or", " of", ",")):
            return True
        if not re.search(r"[A-Za-z]", t):
            return True
        return False

    cleaned = []
    for c in conditions:
        if _bad_name(c.get("condition", "")):
            alt = name_map.get(c.get("diagnostic_code", ""))
            if alt:
                c["condition"] = alt
        if _bad_name(c.get("condition", "")):
            continue
        cleaned.append(c)

    payload = {
        "source": "ecfr.io (mirror of eCFR)",
        "source_part_url": BASE_PART_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conditions": cleaned,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return OUT_PATH, len(conditions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sections", help="Comma-separated list of sections (e.g., 4.130,4.71a)")
    args = parser.parse_args()
    override = [s.strip() for s in (args.sections or "").split(",") if s.strip()] or None
    path, count = build_chart(override)
    print(f"Wrote {count} conditions to {path}")
import argparse
