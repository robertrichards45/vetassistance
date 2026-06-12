import os
import re
import json
import argparse
import urllib.request
from datetime import datetime, timezone

import pdfplumber


def download_pdf(url: str, out_path: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    with open(out_path, "wb") as f:
        f.write(data)


def normalize_line(line: str) -> str:
    line = line.replace("–", "-").replace("—", "-").replace("â€”", "-")
    line = re.sub(r"\s+", " ", line).strip()
    return line


def parse_pdf(pdf_path: str) -> list[dict]:
    section_re = re.compile(r"(?:§|Sec\.)\s*4\.\d+[a-z]*", re.IGNORECASE)
    code_line_re = re.compile(r"^(\d{4})\s+(.+)$")
    percent_re = re.compile(r"\b\d{1,3}%|percent", re.IGNORECASE)
    rules = []

    current_section = ""
    current_code = ""
    current_title = ""
    current_lines = []

    def flush_block():
        nonlocal current_code, current_title, current_lines, current_section
        if not current_code:
            return
        criteria = [l for l in current_lines if percent_re.search(l)]
        if not criteria:
            criteria = current_lines[:12]
        title = current_title or f"Diagnostic Code {current_code}"
        rules.append(
            {
                "key": f"dc_{current_code}",
                "aliases": [f"dc {current_code}", f"diagnostic code {current_code}"],
                "title": title,
                "cfr": current_section or "38 CFR Part 4",
                "diagnostic_code": current_code,
                "rating_criteria": criteria,
                "evidence_tips": [],
            }
        )
        current_code = ""
        current_title = ""
        current_lines = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.splitlines():
                line = normalize_line(raw)
                if not line:
                    continue
                if len(line) < 3:
                    continue
                if line.lower().startswith("part 4"):
                    continue
                sec_match = section_re.search(line)
                if sec_match:
                    current_section = sec_match.group(0)
                match = code_line_re.match(line)
                if match:
                    code = match.group(1)
                    try:
                        code_num = int(code)
                    except ValueError:
                        code_num = 0
                    if code_num < 5000 or code_num > 9999:
                        # likely a year or non-DC number
                        continue
                    flush_block()
                    current_code = code
                    current_title = match.group(2).strip(" -:;")
                    continue
                if current_code:
                    if not current_title and not percent_re.search(line):
                        current_title = line
                    current_lines.append(line)

    flush_block()
    return rules


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--pdf-out", default=os.path.join("cfr_data", "CFR-title38-part4.pdf"))
    parser.add_argument("--out", default=os.path.join("cfr_data", "cfr38_full.json"))
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.pdf_out), exist_ok=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    download_pdf(args.url, args.pdf_out)
    rules = parse_pdf(args.pdf_out)
    payload = {"version": datetime.now(timezone.utc).strftime("%Y-%m-%d") + " (part 4 pdf)", "rules": rules}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved {len(rules)} rules to {args.out}")


if __name__ == "__main__":
    main()
