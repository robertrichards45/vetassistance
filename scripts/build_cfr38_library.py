import os
import re
import json
import argparse
import datetime
import urllib.request
import xml.etree.ElementTree as ET


def download_xml(out_path: str, year: int) -> str:
    url = f"https://www.govinfo.gov/bulkdata/CFR/{year}/title-38/CFR-{year}-title38.xml"
    try:
        with urllib.request.urlopen(url) as resp:
            data = resp.read()
        with open(out_path, "wb") as f:
            f.write(data)
        return url
    except Exception:
        return ""


def extract_text(elem) -> str:
    parts = []
    for node in elem.iter():
        if node.text:
            parts.append(node.text.strip())
    return " ".join([p for p in parts if p])


def parse_sections(xml_path: str) -> list[dict]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rules = []

    for section in root.iter():
        if section.tag.endswith("SECTION"):
            sectno = ""
            subject = ""
            paras = []
            for child in section:
                tag = child.tag.lower()
                if tag.endswith("sectno"):
                    sectno = extract_text(child)
                elif tag.endswith("subject"):
                    subject = extract_text(child)
                elif tag.endswith("p"):
                    paras.append(extract_text(child))

            if not sectno:
                continue

            text_block = " ".join(paras)
            codes = sorted(set(re.findall(r"Diagnostic Code\\s*(\\d{4})", text_block, flags=re.IGNORECASE)))
            criteria = [p for p in paras if "%" in p or "percent" in p.lower()]
            if not criteria and paras:
                criteria = paras[:10]

            if codes:
                for dc in codes:
                    rules.append(
                        {
                            "key": f"{sectno}-{dc}",
                            "aliases": [],
                            "title": subject or sectno,
                            "cfr": sectno,
                            "diagnostic_code": dc,
                            "rating_criteria": criteria,
                            "evidence_tips": [],
                        }
                    )
            else:
                rules.append(
                    {
                        "key": sectno,
                        "aliases": [],
                        "title": subject or sectno,
                        "cfr": sectno,
                        "diagnostic_code": "",
                        "rating_criteria": criteria,
                        "evidence_tips": [],
                    }
                )

    return rules


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.datetime.now(datetime.timezone.utc).year)
    parser.add_argument("--out", default=os.path.join("cfr_data", "cfr38_full.json"))
    parser.add_argument("--xml-out", default=os.path.join("cfr_data", "CFR-title38.xml"))
    parser.add_argument("--url", default="")
    parser.add_argument("--xml-in", default="")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    os.makedirs(os.path.dirname(args.xml_out), exist_ok=True)

    url = ""
    if args.xml_in:
        if not os.path.exists(args.xml_in):
            raise RuntimeError(f"XML input file not found: {args.xml_in}")
        with open(args.xml_in, "rb") as f:
            data = f.read()
        with open(args.xml_out, "wb") as f:
            f.write(data)
        url = f"file://{args.xml_in}"
    if args.url and not url:
        try:
            with urllib.request.urlopen(args.url) as resp:
                data = resp.read()
            with open(args.xml_out, "wb") as f:
                f.write(data)
            url = args.url
        except Exception:
            url = ""
    if not url:
        for yr in [args.year, args.year - 1, args.year - 2, args.year - 3, args.year - 4]:
            url = download_xml(args.xml_out, yr)
            if url:
                args.year = yr
                break
    if not url:
        raise RuntimeError("Failed to download CFR XML. Provide --url or --xml-in for a local XML file.")

    rules = parse_sections(args.xml_out)
    payload = {"version": str(args.year), "rules": rules}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Saved {len(rules)} rules to {args.out} (source: {url})")


if __name__ == "__main__":
    main()
