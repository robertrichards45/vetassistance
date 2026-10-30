"""Build cfr_data/rating_explorer.json from the official eCFR pages cached in cfr_data/ecfr_cache.

Each diagnostic code gets: name, CFR section, body system, criteria (percent + text,
including Bilateral/Unilateral style sub-rows), notes, and the general rating formula it
uses (spine, mental disorders, ...). The older va_ratings_chart.json is used only to
fill in codes this parser can't read, so nothing that worked before is lost.

Run:  python scripts/build_rating_explorer_data.py
"""
from __future__ import annotations

import glob
import html
import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cfr_data", "ecfr_cache")
OLD = os.path.join(ROOT, "cfr_data", "va_ratings_chart.json")
OUT = os.path.join(ROOT, "cfr_data", "rating_explorer.json")

SYSTEMS = {
    "4.71a": "Musculoskeletal", "4.73": "Muscle injuries", "4.79": "Eye", "4.84a": "Eye", "4.85": "Hearing",
    "4.86": "Hearing", "4.87": "Ear", "4.87a": "Ear", "4.88b": "Infectious diseases", "4.97": "Respiratory",
    "4.104": "Cardiovascular", "4.114": "Digestive", "4.115a": "Genitourinary", "4.115b": "Genitourinary",
    "4.116": "Gynecological and breast", "4.117": "Blood and lymphatic", "4.118": "Skin", "4.119": "Endocrine",
    "4.124a": "Neurological", "4.130": "Mental health", "4.150": "Dental and oral",
}
DC_RE = re.compile(r"^(\d{4})\s+(.+)$", re.S)
FORMULA_RE = re.compile(r"^(General Rating Formula|Formula for Rating|General Formula)", re.I)


def clean(cell: str) -> str:
    t = re.sub(r"<sup>.*?</sup>", "", cell, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def pct(cell: str):
    t = clean(cell)
    m = re.fullmatch(r"(?:\d\s+)?(\d{1,3})", t)  # tolerate a leading footnote marker
    return int(m.group(1)) if m and 0 <= int(m.group(1)) <= 100 else None


def parse_section(path: str):
    sec = re.search(r"section_(4\.[0-9a-z]+)\.html$", path).group(1)
    raw = open(path, encoding="utf-8", errors="ignore").read()
    codes, formulas = {}, {}
    cur = None          # current dict being filled (a DC or a formula)
    context = ""        # text of a header row with no percent (e.g. "Pronounced; ...:")
    group = ""          # nerve-group heading, e.g. "Sciatic nerve" above "8520 Paralysis of:"
    prev_plain = ""     # text of the previous row if it was a short heading with no percent
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, flags=re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)
        if not cells:
            continue
        text = clean(cells[0])
        value = pct(cells[-1]) if len(cells) > 1 else None
        if not text:
            continue
        m = DC_RE.match(text)
        if m and not text.lower().startswith(("note", "1 ")):
            code, name = m.group(1), m.group(2).strip().rstrip(":").strip()
            if prev_plain:
                group = prev_plain.rstrip(".:").strip()
            low = name.lower().rstrip(".")
            if group and sec == "4.124a" and low in ("paralysis of", "neuritis", "neuralgia"):
                nerve = re.sub(r"^the\s+", "", group, flags=re.I).lower()
                name = f"Paralysis of the {nerve}" if low == "paralysis of" else f"{low.capitalize()}, {nerve}"
            prev_plain = ""
            cur = codes.setdefault(code, {"diagnostic_code": code, "condition": name, "cfr_section": sec,
                                          "criteria": [], "notes": []})
            context = ""
            if value is not None:
                cur["criteria"].append({"percent": value, "text": name})
            continue
        if FORMULA_RE.match(text):
            cur = formulas.setdefault(text.rstrip(":"), {"name": text.rstrip(":"), "cfr_section": sec, "criteria": [], "notes": []})
            context = ""
            continue
        if cur is None:
            prev_plain = text if (value is None and len(text) < 60 and not text.lower().startswith("note")) else ""
            continue
        if text.lower().startswith("note"):
            cur["notes"].append(text)
            continue
        if value is None:
            context = text.rstrip(":").strip()
            prev_plain = text if len(text) < 60 else ""
            continue
        prev_plain = ""
        label = f"{context}: {text}" if context and len(text) < 40 else text
        cur["criteria"].append({"percent": value, "text": label})
    return sec, codes, formulas


def main():
    codes, formulas = {}, {}
    for path in sorted(glob.glob(os.path.join(CACHE, "section_4.*.html"))):
        sec, c, f = parse_section(path)
        if sec in SYSTEMS:
            for k, v in c.items():
                codes.setdefault(k, v)
            formulas.update(f)

    old = json.load(open(OLD, encoding="utf-8")) if os.path.exists(OLD) else {"conditions": []}
    filled = 0
    for o in old.get("conditions", []):
        dc = o.get("diagnostic_code")
        if not dc:
            continue
        cur = codes.get(dc)
        if cur is None and o.get("criteria"):
            codes[dc] = {**o, "notes": [], "from_old_parser": True}
            filled += 1
        elif cur is not None:
            if not cur["criteria"] and o.get("criteria") and o.get("formula_names"):
                cur["criteria"] = o["criteria"]

    # Hearing loss is rated from audiometric tables (38 CFR 4.85-4.86), not criteria rows.
    codes.setdefault("6100", {
        "diagnostic_code": "6100", "condition": "Hearing loss", "cfr_section": "4.85", "criteria": [],
        "notes": ["Rated from puretone threshold averages and Maryland CNC speech discrimination scores using "
                  "Tables VI, VIa, and VII in 38 CFR 4.85. Exceptional patterns of hearing impairment are covered by 4.86."],
        "instruction": "Rate using the audiometric tables in 38 CFR 4.85 (Tables VI, VIa, VII); see 4.86 for exceptional patterns."})

    def codes_in(name):
        out = set()
        for a, b in re.findall(r"(\d{4})\s*(?:through|-|to)\s*(\d{4})", name):
            out.update(str(n) for n in range(int(a), int(b) + 1))
        out.update(re.findall(r"\b(\d{4})\b", name))
        return out
    explicit = {
        "General Rating Formula for Diseases and Injuries of the Spine": {str(n) for n in range(5235, 5244)},
        "Formula for Rating Intervertebral Disc Syndrome Based on Incapacitating Episodes": {"5243"},
        "General Rating Formula for Major and Minor Epileptic Seizures": {str(n) for n in range(8910, 8915)},
        "General Rating Formula for Diseases of the Eye": {str(n) for n in range(6000, 6010)},
    }
    for c in codes.values():
        c["formula_names"] = []
    for f in formulas.values():
        targets = set()
        for key, dcs in explicit.items():
            if f["name"].lower().startswith(key.lower()):
                targets |= dcs
        targets |= codes_in(f["name"])
        f["applies_to"] = sorted(t for t in targets if t in codes)
        for t in f["applies_to"]:
            codes[t]["formula_names"].append(f["name"])

    for k in [k for k, c in codes.items() if c["condition"].strip("[]. ").lower() == "removed"]:
        codes.pop(k)
    for c in codes.values():
        c["system"] = SYSTEMS.get(c["cfr_section"], "Other")
        if ":" in c["condition"] and len(c["condition"]) > 60:
            head, rest = c["condition"].split(":", 1)
            c["condition"], c["instruction"] = head.strip(), rest.strip()
        c.setdefault("instruction", "")
        dc = int(c["diagnostic_code"])
        if not c["criteria"] and not c["instruction"] and c["cfr_section"] == "4.124a" and 8600 <= dc < 8800:
            base = dc - 100 if dc < 8700 else dc - 200
            c["instruction"] = (f"Rated on the same scale as DC {base} (paralysis). Neuritis is capped at severe incomplete "
                                "paralysis unless there are organic changes (38 CFR 4.123)." if dc < 8700 else
                                f"Rated on the same scale as DC {base} (paralysis), up to moderate incomplete paralysis "
                                "(38 CFR 4.124).")
            c["see_also"] = str(base)
        c["source_url"] = f"https://www.ecfr.gov/current/title-38/chapter-I/part-4/section-{c['cfr_section']}"
        c.setdefault("formula_names", [])
        # Name clean-up: drop amendment notes like "Replaces 5285-5295 ..." that leak into names.
        c["condition"] = re.sub(r"^(Replaces|Title, criteria)[^.]*\.?\s*", "", c["condition"]).strip() or c["condition"]
        seen, uniq = set(), []
        for cr in c["criteria"]:
            key = (cr["percent"], cr["text"])
            if key not in seen:
                seen.add(key)
                uniq.append(cr)
        c["criteria"] = uniq

    data = {
        "source": "eCFR.gov, 38 CFR Part 4 (official text)",
        "source_part_url": "https://www.ecfr.gov/current/title-38/chapter-I/part-4",
        "effective_date": old.get("effective_date"),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conditions": sorted(codes.values(), key=lambda c: c["diagnostic_code"]),
        "formulas": sorted(formulas.values(), key=lambda f: f["name"]),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    print(f"{len(codes)} codes ({filled} from older data), {len(formulas)} formulas -> {OUT}")


if __name__ == "__main__":
    main()
