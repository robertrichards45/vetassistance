import json
import argparse
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.ecfr_live import get_live_va_data  # noqa: E402

OUT_PATH = os.path.join(ROOT, "cfr_data", "va_ratings_chart.json")
CFR_JSON = os.path.join(ROOT, "cfr_data", "cfr38_full.json")

PERCENT_LINE_RE = re.compile(r"\|\s*(100|90|80|70|60|50|40|30|20|10|0)\s*$")
PERCENT_ONLY_RE = re.compile(r"^(100|90|80|70|60|50|40|30|20|10|0)\s*\|\s*$")
BARE_PERCENT_RE = re.compile(r"^(100|90|80|70|60|50|40|30|20|10|0)$")
DC_LINE_RE = re.compile(r"^(\d{4})\s+(.+)$")
DC_MULTI_RE = re.compile(r"(\d{4})\s+(.+?)(?=\s+\d{4}\s+|$)")
DC_ANY_RE = re.compile(r"\b\d{4}\b")
DC_INLINE_RE = re.compile(r"\bDCs?\b\s*([0-9,\-\s]+)", re.IGNORECASE)
RANGE_RE = re.compile(r"(\d{4})\s*-\s*(\d{4})")
DOT_LEADER_RE = re.compile(r"\.{2,}.*$")
FORMULA_HEAD_RE = re.compile(r"^(general\s+)?rating formula", re.IGNORECASE)


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


def parse_section(identifier: str, lines: list[str], url: str) -> dict:
    """Extracts diagnostic codes, rating formulas, and percent/text criteria
    from a section's clean text lines (already produced by
    app.services.ecfr_live from the official eCFR.gov XML — table rows come
    through as "<criteria text> | <percent>" per line, one row per line)."""
    codes = []
    formulas = []
    formula_map = {}
    current_dc = None
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
        if not line:
            i += 1
            continue

        if FORMULA_HEAD_RE.match(line.lower()):
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if "|" not in nxt and not PERCENT_ONLY_RE.match(nxt) and not nxt.strip().isdigit():
                    line = f"{line} {nxt}".strip()
                    i += 1

        if FORMULA_HEAD_RE.match(line.lower()):
            flush_formula()
            current_formula = line.strip()
            current_formula_lines = []
            buffer_lines = []
            dc_inline = DC_INLINE_RE.search(line)
            if dc_inline:
                for dc in expand_dc_list(dc_inline.group(1)):
                    formula_map.setdefault(dc, set()).add(current_formula)
            elif recent_codes:
                for dc in recent_codes:
                    formula_map.setdefault(dc, set()).add(current_formula)
            # This formula heading has now claimed whichever DCs were listed
            # since the last formula (or section start) — start a fresh
            # accumulation so a later, unrelated formula doesn't inherit them.
            recent_codes = []
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

        # Genuine diagnostic-code listing lines are short ("5000 Osteomyelitis,
        # acute, subacute, or chronic:"). Long lines are narrative <P> prose
        # that can incidentally contain a 4-digit number (e.g. section 4.27's
        # explanation that codes "extend from 5000 to a possible 9999") —
        # skip DC detection there so prose doesn't get misread as a listing.
        is_listing_length = len(line) <= 160

        # DC_MULTI_RE's non-greedy match falls back to end-of-line when there's
        # only one code on the line, so it would otherwise "match" every
        # ordinary single-DC listing line too — but unlike the DC_LINE_RE
        # branch below, it never sets current_dc. Only take this branch when
        # there are genuinely 2+ codes on the line (e.g. "5013 Osteoporosis
        # 5014 Osteomalacia"); a lone match falls through to DC_LINE_RE.
        # Also require the line to *start* with a digit: a real DC-listing
        # row always does, whereas a cross-reference sentence like "Note:
        # For colectomy or colostomy, use DC 7327 or DC 7329 (...)" merely
        # mentions codes mid-sentence and would otherwise be misread as a
        # two-code table row, capturing the trailing prose as the "name".
        multi = list(DC_MULTI_RE.finditer(line)) if is_listing_length and line[:1].isdigit() else []
        if len(multi) >= 2:
            # A formula that already has criteria rows recorded is "done" —
            # a fresh DC listing appearing now belongs to whatever comes
            # next, not to the formula we were previously inside. Without
            # this, every DC and criteria row from here to the end of the
            # section keeps getting appended to that first formula (this is
            # exactly how section 4.104's heart-disease formula ended up
            # absorbing the aneurysm, PAD, Raynaud's, and hypertension
            # tables that follow it).
            if current_formula and current_formula_lines:
                flush_formula()
            for m in multi:
                dc = m.group(1)
                name = m.group(2).strip()
                codes.append({"dc": dc, "name": name})
                # Accumulate (don't replace) — a formula heading further down
                # applies to every DC introduced since the last formula, not
                # just the ones on this one line.
                recent_codes.append(dc)
                if current_formula:
                    formula_map.setdefault(dc, set()).add(current_formula)
            buffer_lines = []
            i += 1
            continue

        dc_match = DC_LINE_RE.match(line) if is_listing_length else None
        if dc_match:
            # Same reasoning as the multi-DC branch above: a formula that
            # already produced criteria rows has finished; this DC starts a
            # new block (its own direct criteria, or a different upcoming
            # formula) rather than continuing the old one.
            if current_formula and current_formula_lines:
                flush_formula()
            current_dc = dc_match.group(1)
            current_dc_name = dc_match.group(2).strip()
            codes.append({"dc": current_dc, "name": current_dc_name})
            recent_codes.append(current_dc)
            if current_formula:
                formula_map.setdefault(current_dc, set()).add(current_formula)
            buffer_lines = []
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

        # Some sections (e.g. musculoskeletal tables) render each row as
        # "<criteria text line(s)>" followed by a bare percent number on its
        # own line, with no pipe delimiter at all — distinct from the
        # pipe-delimited layout handled above.
        if BARE_PERCENT_RE.match(line) and (current_formula or current_dc) and buffer_lines:
            percent = int(line)
            text = " ".join(buffer_lines).strip()
            if current_formula:
                current_formula_lines.append({"percent": percent, "text": text})
            elif current_dc:
                dc_criteria.setdefault(current_dc, []).append({"percent": percent, "text": text})
            buffer_lines = []
            i += 1
            continue

        if current_formula or current_dc:
            if line.lower().startswith("note") and (line[4:5] in ("", " ", "(", ":")):
                buffer_lines = []
                i += 1
                continue
            if line.endswith("|"):
                buffer_lines.append(line.strip().strip("|").strip())
                i += 1
                continue
            if line:
                buffer_lines.append(line.strip())

        i += 1

    flush_formula()

    for dc, names in list(formula_map.items()):
        has_eating = any("eating" in n.lower() for n in names)
        if has_eating:
            formula_map[dc] = set([n for n in names if "eating" in n.lower()])

    return {
        "section": identifier,
        "url": url,
        "codes": codes,
        "formulas": formulas,
        "formula_map": {k: sorted(list(v)) for k, v in formula_map.items()},
        "dc_criteria": dc_criteria,
    }


def build_chart(sections_override: list[str] | None = None):
    live = get_live_va_data()
    live_by_id = {s["identifier"]: s for s in live["sections"]}
    section_url_for = lambda ident: f"{live['source_url']}/section-{ident}"

    sections = sections_override or list(live_by_id.keys())

    section_map = {}
    global_formulas = []
    dc_to_section = {}
    live_dc_names = {}
    for identifier in sections:
        live_section = live_by_id.get(identifier)
        if not live_section:
            continue
        lines = (live_section.get("text") or "").split("\n")
        parsed = parse_section(identifier, lines, section_url_for(identifier))
        section_map[identifier] = {
            "parsed": parsed,
            "formula_by_name": {f["name"]: f["criteria"] for f in parsed["formulas"]},
        }
        for f in parsed.get("formulas", []):
            if f.get("criteria"):
                global_formulas.append((f.get("name") or "", f.get("criteria")))
        for code in parsed.get("codes", []):
            dc = code.get("dc")
            if not dc:
                continue
            if dc not in dc_to_section:
                dc_to_section[dc] = identifier
            # Prefer the longest name seen for a DC — short entries are
            # usually a section's own summary listing, the fuller ones come
            # from the row with real criteria attached.
            candidate = (code.get("name") or "").strip().rstrip(":").strip()
            if candidate and len(candidate) > len(live_dc_names.get(dc, "")):
                live_dc_names[dc] = candidate

    def _extract_section(text: str) -> str:
        m = re.search(r"4\.\d+[a-z]*", (text or "").lower())
        return m.group(0) if m else ""

    def _criteria_from_lines(rating_lines: list[str]) -> list[dict]:
        out = []
        for line in rating_lines or []:
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
            source_url = section_url_for(section) if section else ""

            if section in section_map:
                parsed = section_map[section]["parsed"]
                formula_by_name = section_map[section]["formula_by_name"]

                def _keyword_best(candidates, name_l):
                    """Picks the single best-scoring formula by keyword overlap
                    with the condition name — never concatenates multiple
                    formulas together, which is how unrelated conditions'
                    criteria end up glued onto one entry."""
                    best, best_score = None, 0
                    for fname, fcriteria in candidates:
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
                    return best if best_score > 0 else None

                # Prefer criteria extracted directly under this DC's own row
                # over a formula-name association, which can be misattributed
                # to a later, unrelated general formula for DCs whose code
                # only appears once near the top of a long section.
                criteria = list(parsed.get("dc_criteria", {}).get(dc, []))
                formula_names = parsed["formula_map"].get(dc, [])
                # Some formula_map entries are noise — e.g. a "Note" sentence
                # that happens to mention several DC numbers alongside the
                # words "rating formula" gets registered as if it were a
                # formula name, but it was never built via a real formula
                # heading, so it has no criteria. Drop those before deciding
                # whether this DC's association is actually ambiguous.
                real_formula_names = [f for f in formula_names if formula_by_name.get(f)]
                if not criteria and len(real_formula_names) == 1:
                    # A single, unambiguous association — safe to use directly.
                    criteria.extend(formula_by_name.get(real_formula_names[0], []))
                elif not criteria and len(real_formula_names) > 1:
                    # Several *real* candidate formulas got associated with
                    # this DC (a genuinely imprecise section layout) — pick
                    # the one that actually matches the condition by keyword,
                    # rather than concatenating all of them into one garbled
                    # list.
                    picked = _keyword_best([(f, formula_by_name.get(f, [])) for f in real_formula_names], (name or "").lower())
                    if picked:
                        criteria.extend(picked)
                if not criteria:
                    picked = _keyword_best(list(formula_by_name.items()), (name or "").lower())
                    if picked:
                        criteria.extend(picked)
                if not criteria:
                    formulas_with_criteria = [f for f in parsed["formulas"] if f.get("criteria")]
                    if len(formulas_with_criteria) == 1:
                        criteria.extend(formulas_with_criteria[0].get("criteria", []))

            if not criteria:
                criteria = _criteria_from_lines(r.get("rating_criteria", []) or [])
            if not criteria and global_formulas:
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
            # Deliberately no further fallback here: grabbing an unrelated
            # formula "because it's the biggest one in the document" would
            # attach a confidently wrong condition's rating criteria (e.g.
            # spine criteria under a mental-health DC). Better to show no
            # criteria — with source_url still pointing at the real
            # regulation text — than to show wrong ones, especially once
            # this data feeds AI-drafted justifications.

            conditions.append({
                "condition": name,
                "diagnostic_code": dc,
                "cfr_section": section,
                "criteria": criteria,
                "formula_names": formula_names,
                "source_url": source_url,
            })
    else:
        for identifier in sections:
            if identifier not in section_map:
                continue
            parsed = section_map[identifier]["parsed"]
            formula_by_name = section_map[identifier]["formula_by_name"]
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
                    "cfr_section": identifier,
                    "criteria": criteria,
                    "formula_names": formula_names,
                    "source_url": parsed["url"],
                })

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
        # "resection of", "malunion of", "impairment of" etc. are genuine,
        # complete VA condition titles — ending in "of" is this dataset's
        # naming convention, not a sign of truncation. "and"/"or"/a trailing
        # comma are still treated as cut-off markers.
        if t.endswith((" and", " or", ",")):
            return True
        if not re.search(r"[A-Za-z]", t):
            return True
        # A real condition title never starts mid-sentence — these are marks
        # of a stray fragment (e.g. a cross-reference sentence like "Note:
        # ...use DC 7327 or DC 7329 (Intestine, large, resection of),
        # whichever results in a higher evaluation." got misread as DC
        # 7329's own name).
        if t.startswith(("(", ")", ",", "-")):
            return True
        if "whichever results in" in t.lower() or "whichever" in t.lower():
            return True
        # A real condition title doesn't contain ANOTHER standalone 4-digit
        # diagnostic code — that's the signature of two different DCs'
        # names having been concatenated (e.g. "Knee, other impairment of:
        # 5258 Cartilage, semilunar, dislocated, with frequent").
        if re.search(r"(?<!\S)\d{4}(?!\S)", t):
            return True
        # An unclosed parenthetical means the title was cut off mid-sentence
        # (e.g. "Fibromyalgia (fibrositis, primary fibromyalgia With chronic
        # residuals consisting" — the opening "(" never closes).
        if t.count("(") != t.count(")"):
            return True
        # Same as "[removed]" but without the brackets — an amendment-history
        # entry ("Removed February 7, 2021.") standing in for a real title.
        if t.lower().startswith("removed "):
            return True
        return False

    # cfr38_full.json carries multiple historical entries for the same DC
    # (an amendment-history row alongside the real one, sometimes several).
    # Without deduplication, a fixed/clean entry and a still-garbled sibling
    # for the same DC both end up in the output. Keep one entry per DC: a
    # clean name beats a bad one; among equally-clean entries, prefer the
    # one with more extracted criteria rows.
    best_by_dc = {}
    for c in conditions:
        if _bad_name(c.get("condition", "")):
            # Prefer the name pulled straight from the live eCFR.gov text
            # over cfr38_full.json's title, since that file's titles come
            # from an older PDF-OCR pass and are sometimes amendment notes
            # ("Added February 3, 1988") or dot-leader table-of-contents
            # artifacts instead of the actual condition name.
            alt = live_dc_names.get(c.get("diagnostic_code", "")) or name_map.get(c.get("diagnostic_code", ""))
            if alt:
                c["condition"] = alt
        if _bad_name(c.get("condition", "")):
            continue
        dc_key = c.get("diagnostic_code", "")
        existing = best_by_dc.get(dc_key)
        if existing is None or len(c.get("criteria", [])) > len(existing.get("criteria", [])):
            best_by_dc[dc_key] = c
    cleaned = list(best_by_dc.values())

    payload = {
        "source": "eCFR.gov (official)",
        "source_part_url": live["source_url"],
        "effective_date": live["effective_date"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conditions": cleaned,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return OUT_PATH, len(cleaned)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sections", help="Comma-separated list of sections (e.g., 4.130,4.71a)")
    args = parser.parse_args()
    override = [s.strip() for s in (args.sections or "").split(",") if s.strip()] or None
    path, count = build_chart(override)
    print(f"Wrote {count} conditions to {path}")
