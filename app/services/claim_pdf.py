"""Fill the official VA Form 21-526EZ and 21-4138 PDFs from the claim builder's answers.

Every answer goes into its named AcroForm field (mapped from the November 2022 revision of the
526EZ and the current 21-4138). Appearance streams are generated so the forms print correctly in
any viewer, and the forms' XFA layer is removed so Adobe shows the filled AcroForm.
Text that doesn't fit a box is carried onto clearly labeled continuation pages.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[2]
F526 = ROOT / "va_forms" / "21-526EZ.pdf"
F4138 = ROOT / "va_forms" / "21-4138.pdf"
MAIL_TO = "Department of Veterans Affairs, Claims Intake Center, PO Box 4444, Janesville, WI 53547-4444"

P9, P10, P11, P12, P13, P15 = ("F[0].Page_10[0].", "F[0].#subform[9].", "F[0].#subform[10].",
                               "F[0].#subform[11].", "F[0].#subform[12].", "F[0].#subform[14].")
YES, NO = "0", "1"
BRANCH_19A = {"Army": "0", "Air Force": "1", "NOAA": "2", "Navy": "3", "Coast Guard": "4", "USPHS": "5",
              "Marine Corps": "6", "Space Force": "7"}
COMPONENT_19B = {"Active": "0", "Reserves": "1", "National Guard": "2"}


# ---------------- helpers ----------------

def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _date_parts(s: str) -> tuple[str, str, str]:
    """'MM/DD/YYYY', 'MM/YYYY', 'YYYY-MM-DD' or 'YYYY' -> (MM, DD, YYYY); blanks where unknown."""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return m.group(2).zfill(2), m.group(3).zfill(2), m.group(1)
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
    m = re.fullmatch(r"(\d{1,2})[/-](\d{4})", s)
    if m:
        return m.group(1).zfill(2), "", m.group(2)
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return "", "", m.group(1)
    return "", "", ""


def _yn(v: str) -> str | None:
    return YES if v == "yes" else NO if v == "no" else None


def _wrap(text: str, width: float, size: float, font: str = "helv") -> list[str]:
    """Word-wrap to a pixel width using the real Helvetica metrics."""
    out = []
    for para in (text or "").replace("\r", "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            cand = (line + " " + word) if line else word
            if fitz.get_text_length(cand, fontname=font, fontsize=size) <= width:
                line = cand
            else:
                if line:
                    out.append(line)
                while fitz.get_text_length(word, fontname=font, fontsize=size) > width and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and fitz.get_text_length(word[:cut], fontname=font, fontsize=size) > width:
                        cut -= 1
                    out.append(word[:cut])
                    word = word[cut:]
                line = word
        out.append(line)
    return out


class _Filler:
    def __init__(self, path: Path):
        self.doc = fitz.open(str(path))
        self.text: dict[str, str] = {}
        self.size: dict[str, float] = {}
        self.radio: dict[str, str] = {}
        self.check: set[str] = set()

    def t(self, name: str, value, size: float | None = None):
        if value not in (None, ""):
            self.text[name] = str(value)
            if size:
                self.size[name] = size

    def r(self, name: str, state: str | None):
        if state is not None:
            self.radio[name] = state

    def c(self, name: str, on: bool):
        if on:
            self.check.add(name)

    def ssn_everywhere(self, ssn: str):
        d = _digits(ssn)
        if len(d) != 9:
            return
        for page in self.doc:
            for w in page.widgets():
                n = w.field_name
                if "SocialSecurityNumber_FirstThreeNumbers" in n:
                    self.text[n] = d[:3]
                elif "SocialSecurityNumber_SecondTwoNumbers" in n:
                    self.text[n] = d[3:5]
                elif "SocialSecurityNumber_LastFourNumbers" in n:
                    self.text[n] = d[5:]

    def apply(self) -> fitz.Document:
        for page in self.doc:
            for w in page.widgets():
                n = w.field_name
                try:
                    if w.field_type == fitz.PDF_WIDGET_TYPE_TEXT and n in self.text:
                        if n in self.size:
                            w.text_font = "Helv"
                            w.text_fontsize = self.size[n]
                        w.field_value = self.text[n]
                        w.update()
                    elif w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON and n in self.radio:
                        if str(w.on_state()) == self.radio[n]:
                            w.field_value = w.on_state()
                            w.update()
                    elif w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX and n in self.check:
                        w.field_value = w.on_state() or True
                        w.update()
                except Exception:
                    continue
        _drop_xfa(self.doc)
        return self.doc


def _drop_xfa(doc: fitz.Document) -> None:
    try:
        cat = doc.pdf_catalog()
        kind, val = doc.xref_get_key(cat, "AcroForm")
        if kind == "xref":
            doc.xref_set_key(int(val.split()[0]), "XFA", "null")
            doc.xref_set_key(int(val.split()[0]), "NeedAppearances", "true")
        elif kind == "dict":
            doc.xref_set_key(cat, "AcroForm/XFA", "null")
    except Exception:
        pass


def _continuation(doc: fitz.Document, title: str, who: str, lines: list[str], size: float = 10.5) -> None:
    """Append letter-size pages carrying overflow text, labeled the way VA asks for attachments."""
    width, height, margin = 612, 792, 54
    lh = size * 1.32
    i = 0
    while i < len(lines):
        page = doc.new_page(width=width, height=height)
        page.insert_text((margin, margin), title, fontname="hebo", fontsize=11)
        page.insert_text((margin, margin + 15), who, fontname="helv", fontsize=9)
        y = margin + 40
        while i < len(lines) and y < height - margin:
            page.insert_text((margin, y), lines[i], fontname="helv", fontsize=size)
            y += lh
            i += 1


# ---------------- 21-526EZ ----------------

def _cell(text: str, width: float, max_lines: int, sizes=(8, 7, 6)) -> tuple[str, float, bool]:
    """Fit text in a small table cell: shrink the font first, then truncate (flagged)."""
    for s in sizes:
        lines = _wrap(text, width - 4, s)
        if len(lines) <= max_lines + (1 if s <= 6 else 0):
            return "\n".join(lines), s, False
    lines = _wrap(text, width - 4, sizes[-1])[: max_lines + 1]
    return "\n".join(lines), sizes[-1], True


def fill_526(form: dict, ssn: str = "") -> tuple[bytes, list[str]]:
    """Returns (pdf_bytes, notes). Notes list anything that needed an addendum or was left for the veteran."""
    f = _Filler(F526)
    notes: list[str] = []
    i, s, e = form.get("id") or {}, form.get("service") or {}, form.get("exposure") or {}

    # Item 1: claim process
    f.c(P9 + ("FULLY_DEVELOPED_CLAIM_FDC_PROGRAM[0]" if form.get("claim_process") == "FDC" else "Standard_Claim_Process[0]"), True)
    # Section I
    f.t(P9 + "Veteran_Service_Member_First_Name[0]", (i.get("first") or "")[:12])
    f.t(P9 + "Veteran_Service_Member_Middle_Initial[0]", (i.get("mi") or "")[:1])
    f.t(P9 + "Veteran_Service_Member_Last_Name[0]", (i.get("last") or "")[:18])
    f.ssn_everywhere(ssn)
    f.r(P9 + "RadioButtonList2[0]", _yn(i.get("filed_before")))
    f.t(P9 + "VA_File_Number[0]", _digits(i.get("va_file"))[:9])
    mm, dd, yy = _date_parts(i.get("dob"))
    f.t(P9 + "Date_Of_Birth_Month[0]", mm); f.t(P9 + "Date_Of_Birth_Day[0]", dd); f.t(P9 + "Date_Of_Birth_Year[0]", yy)
    f.t(P9 + "Veterans_Service_Number_If_Applicable[0]", (i.get("service_number") or "")[:10])
    ph = _digits(i.get("phone"))[-10:]
    if len(ph) == 10:
        f.t(P9 + "Daytime_Phone_Number_Area_Code[0]", ph[:3]); f.t(P9 + "Telephone_Middle_Three_Numbers[0]", ph[3:6])
        f.t(P9 + "Telephone_Last_Four_Numbers[0]", ph[6:])
    f.t(P9 + "CurrentMailingAddress_NumberAndStreet[0]", (i.get("street") or "")[:30])
    f.t(P9 + "CurrentMailingAddress_ApartmentOrUnitNumber[0]", (i.get("apt") or "")[:5])
    f.t(P9 + "CurrentMailingAddress_City[0]", (i.get("city") or "")[:18])
    f.t(P9 + "CurrentMailingAddress_StateOrProvince[0]", (i.get("state") or "")[:2].upper())
    f.t(P9 + "CurrentMailingAddress_Country[0]", (i.get("country") or "US")[:2].upper())
    z = _digits(i.get("zip"))
    f.t(P9 + "CurrentMailingAddress_ZIPOrPostalCode_FirstFiveNumbers[0]", z[:5])
    f.t(P9 + "CurrentMailingAddress_ZIPOrPostalCode_LastFourNumbers[0]", z[5:9])
    em = i.get("email") or ""
    f.t(P9 + "Email_Address_Optional[0]", em[:25]); f.t(P9 + "Email_Address_Optional[1]", em[25:50])
    f.c(P9 + "CheckBox1[0]", bool(i.get("electronic")) and bool(em))

    # Section IV: exposures
    f.r(P10 + "RadioButtonList2[2]", _yn(e.get("claiming_toxic")))
    if e.get("claiming_toxic") == "yes":
        f.r(P10 + "RadioButtonList2[3]", _yn(e.get("gulf_war")))
        for idx, key in ((0, "gw"), (1, "herb"), (2, "exp")):
            fm, _, fy = _date_parts(e.get(f"{key}_from"))
            tm, _, ty = _date_parts(e.get(f"{key}_to"))
            f.t(P10 + f"From_Date_Month[{idx}]", fm); f.t(P10 + f"From_Date_Year[{idx}]", fy)
            f.t(P10 + f"To_Date_Month[{idx}]", tm); f.t(P10 + f"To_Date_Year[{idx}]", ty)
        f.r(P10 + "RadioButtonList2[4]", _yn(e.get("herbicide")))
        f.t(P10 + "List_Other_Locations_Where_You_Served[0]", e.get("herb_locations"))
        f.c(P10 + "ASBESTOS[0]", bool(e.get("asbestos")))
        f.c(P10 + "SHAD_Shipboard_Hazard_And_Defense[0]", bool(e.get("shad")))
        f.c(P10 + "Contaminated_Water_At_Camp_Lejeune[0]", bool(e.get("camp_lejeune")))
        f.c(P10 + "Mustard_Gas[0]", bool(e.get("mustard_gas")))
        f.c(P10 + "Military_Occupational_Specialty_MOS_Related_Toxin[0]", bool(e.get("mos_toxin")))
        f.c(P10 + "Radiation[0]", bool(e.get("radiation")))
        f.c(P10 + "OTHER_Specify[2]", bool(e.get("other")))
        f.t(P10 + "Other_Specify[0]", e.get("other"))
        f.t(P10 + "Provide_Additional_Dates_And_Locations_Of_Potential_Exposure[0]", e.get("additional"))

    # Section V: claims. Rows 1-15 on page 11, rows 16-35 on the Section XIII addendum (page 15).
    claims = [c for c in (form.get("claims") or []) if (c.get("condition") or "").strip()]
    if len(claims) > 35:
        notes.append(f"Only 35 conditions fit on the form; {len(claims) - 35} more need a separate 21-4138 list.")
    for n, c in enumerate(claims[:35]):
        if n < 15:
            pre, k = P11, n
            date_name = P11 + ("Date_Disability_Began_Or_Worsened[0]" if n == 0 else f"Date12[{n - 1}]")
        else:
            pre, k = P15, n
            date_name = P15 + ("Date_Disability_Began_Or_Worsened[1]" if n == 15 else f"Date12[{n - 2}]")
        for field, width, key in (("CURRENTDISABILITY", 163, "condition"), ("Specify_Type_Of_Exposure_Event_Or_Injury", 117, "event"),
                                  ("ExplainHowDisabilityRelatesToEvent_Exposure_Injury", 151, "explanation")):
            txt, size, cut = _cell(c.get(key) or "", width, 2)
            f.t(f"{pre}{field}[{k}]", txt, size)
            if cut:
                notes.append(f"Row {n + 1} {key} was shortened to fit; the full text is in the attached 21-4138.")
        f.t(date_name, (c.get("date") or "")[:10], 8)
    if len(claims) > 15:
        notes.append("Conditions 16 and up are on Section XIII (page 15). Keep that page in the packet.")

    # Item 17: treatment facilities
    # The PDF numbers these boxes out of visual order; map each printed row to its real field indexes.
    rows = [(2, 0, 0, 0), (1, 2, 1, 1), (0, 1, 2, 2)]  # (facility, month, year, no-date checkbox)
    for (fac, mon, yr, chk), tr in zip(rows, (form.get("treatment") or [])[:3]):
        f.t(P11 + f"Enter_Disability_Treated_And_Name_And_Location_Of_Treatment_Facility[{fac}]", tr.get("text"), 8)
        tm, _, ty = _date_parts(tr.get("date"))
        f.t(P11 + f"Date_Of_Treatment_Month[{mon}]", tm); f.t(P11 + f"Date_Of_Treatment_Year[{yr}]", ty)
        f.c(P11 + f"Check_Box_Do_Not_Have_Date_s[{chk}]", bool(tr.get("no_date")) and not ty)

    # Section VI: service
    other = (s.get("other_names") or "").strip()
    f.r(P12 + "RadioButtonList[0]", YES if other else (NO if s.get("other_names_no") else None))
    f.t(P12 + "List_Other_Name_You_Served_Under[0]", other)
    f.r(P12 + "RadioButtonList[10]", BRANCH_19A.get(s.get("branch") or ""))
    f.r(P12 + "RadioButtonList[12]", COMPONENT_19B.get(s.get("component") or ""))
    for prefix, key in (("Entry", "entry"), ("Exit", "exit")):
        mm, dd, yy = _date_parts(s.get(key))
        f.t(P12 + f"{prefix}Date_Month[0]", mm); f.t(P12 + f"{prefix}Date_Year[0]", yy)
        f.t(P12 + ("MostRecentActiveServiceEntryDate_Day[0]" if prefix == "Entry" else "ExitDate_Day[0]"), dd)
    place = s.get("place_sep") or ""
    f.t(P12 + "PlaceOfLastOrAnticipatedSeparation[0]", place[:15]); f.t(P12 + "PlaceOfLastOrAnticipatedSeparation[1]", place[15:30])
    for n, key in ((0, "addl_from"), (1, "addl_to")):
        mm, dd, yy = _date_parts(s.get(key))
        f.t(P12 + f"AdditionalPeriodsOfService_Month[{n}]", mm); f.t(P12 + f"AdditionalPeriodsOfService_Day[{n}]", dd)
        f.t(P12 + f"AdditionalPeriodsOfService_Year[{n}]", yy)
    f.r(P12 + "RadioButtonList[1]", _yn(s.get("combat_911")))
    f.r(P12 + "RadioButtonList[2]", _yn(s.get("reserves")))
    f.r(P12 + "RadioButtonList[6]", _yn(s.get("pow")))
    f.r(P12 + "RadioButtonList[7]", _yn(s.get("retired_pay")))
    f.r(P13 + "RadioButtonList[13]", _yn(s.get("sep_pay")))

    doc = f.apply()
    # Keep only the application itself (pages 9-15); pages 1-8 are VA's instructions.
    doc.select(list(range(8, doc.page_count)))
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, notes


# ---------------- 21-4138 ----------------

def fill_4138(form: dict, statement: str, ssn: str = "") -> tuple[bytes, int]:
    """Returns (pdf_bytes, continuation_page_count)."""
    f = _Filler(F4138)
    i = form.get("id") or {}
    pre = "form1[0].#subform[0]."
    f.t(pre + "Veterans_Beneficiary_First_Name[0]", (i.get("first") or "")[:12])
    f.t(pre + "Middle_Initial1[0]", (i.get("mi") or "")[:1])
    f.t(pre + "Last_Name[0]", (i.get("last") or "")[:18])
    f.ssn_everywhere(ssn)
    f.t(pre + "VA_File_Number_If_Applicable[0]", _digits(i.get("va_file"))[:9])
    mm, dd, yy = _date_parts(i.get("dob"))
    f.t(pre + "Veterans_DOB_Month[0]", mm); f.t(pre + "DOB_Day[0]", dd); f.t(pre + "DOB_Year[0]", yy)
    f.t(pre + "Veterans_Service_Number_If_Applicable[0]", (i.get("service_number") or "")[:10])
    ph = _digits(i.get("phone"))[-10:]
    if len(ph) == 10:
        f.t(pre + "TelephoneNumber_FirstThreeNumbers[0]", ph[:3]); f.t(pre + "TelephoneNumber_SecondThreeNumbers[0]", ph[3:6])
        f.t(pre + "TelephoneNumber_LastFourNumbers[0]", ph[6:])
    em = i.get("email") or ""
    f.t(pre + "EMAIL_ADDRESS[0]", em[:20]); f.t(pre + "EMAIL_ADDRESS[1]", em[20:40])
    f.t(pre + "MailingAddress_NumberAndStreet[0]", (i.get("street") or "")[:30])
    f.t(pre + "MailingAddress_ApartmentOrUnitNumber[0]", (i.get("apt") or "")[:5])
    f.t(pre + "MailingAddress_City[0]", (i.get("city") or "")[:18])
    f.t(pre + "MailingAddress_StateOrProvince[0]", (i.get("state") or "")[:2].upper())
    f.t(pre + "MailingAddress_Country[0]", (i.get("country") or "US")[:2].upper())
    z = _digits(i.get("zip"))
    f.t(pre + "MailingAddress_ZIPOrPostalCode_FirstFiveNumbers[0]", z[:5])
    f.t(pre + "MailingAddress_ZIPOrPostalCode_LastFourNumbers[0]", z[5:9])

    # Remarks: the form wraps text itself (Arial 10). Measure with the same metrics to decide how much
    # fits in each box, then hand each box whole source lines so nothing is wrapped twice.
    size, lh = 10.0, 10.0 * 1.18
    boxes = []
    for page in f.doc:
        for w in page.widgets():
            if w.field_name.endswith("REMARKS[0]") or w.field_name.endswith("REMARKS[1]"):
                boxes.append((w.field_name, w.rect))
    boxes.sort(key=lambda b: b[0])
    src = (statement or "").replace("\r", "").split("\n")
    measured = []  # (source line, wrapped line count)
    for line in src:
        width = (boxes[0][1].width if boxes else 552) * 0.94
        measured.append((line, max(1, len(_wrap(line, width, size)))))
    idx = 0
    rest_lines: list[str] = []
    for bi, (name, rect) in enumerate(boxes):
        cap = max(1, int((rect.height - 8) // lh)) - (1 if bi == len(boxes) - 1 else 0)
        used, chunk = 0, []
        while idx < len(measured) and used + measured[idx][1] <= cap:
            chunk.append(measured[idx][0]); used += measured[idx][1]; idx += 1
        if bi == len(boxes) - 1 and idx < len(measured):
            chunk.append("(Continued on the attached page.)")
        if chunk:
            f.t(name, "\n".join(chunk))
    rest = [ln for ln, _ in measured[idx:]]
    doc = f.apply()
    extra = 0
    if rest:
        who = f"{i.get('first', '')} {i.get('last', '')}".strip()
        d = _digits(ssn)
        who += f"   SSN: XXX-XX-{d[-4:]}" if len(d) == 9 else ""
        before = doc.page_count
        wrapped = []
        for ln in rest:
            wrapped.extend(_wrap(ln, 504, 10.5) if ln.strip() else [""])
        _continuation(doc, "VA Form 21-4138, Section II Remarks (continued)", who, wrapped)
        extra = doc.page_count - before
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, extra


# ---------------- packet ----------------

def cover_sheet(form: dict, claim_names: list[str], statements: int, notes: list[str]) -> bytes:
    i = form.get("id") or {}
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    x, y = 54, 64
    page.insert_text((x, y), "Claim packet checklist", fontname="hebo", fontsize=18); y += 26
    page.insert_text((x, y), f"Veteran: {i.get('first', '')} {i.get('last', '')}", fontname="helv", fontsize=11); y += 28
    blocks = [
        ("Before mailing, the veteran must:", [
            "Read every page. Correct anything that is wrong. Fill in every [VETERAN TO CONFIRM: ...] item.",
            "Sign and date VA Form 21-526EZ, item 33A and 33B (page 13 of the form).",
            "Sign and date each VA Form 21-4138, items 9 and 10 (page 2 of each statement).",
            "Add copies (never originals) of the evidence listed below, with name and SSN on each page.",
        ]),
        ("What's in this packet:", [
            "VA Form 21-526EZ, Application for Disability Compensation (form pages 9 to 15).",
            f"{statements} VA Form 21-4138 statement{'s' if statements != 1 else ''} in support of claim.",
        ] + [f"Claimed: {n}" for n in claim_names]),
        ("Mail to:", [MAIL_TO,
                      "Keep a full copy. Send by certified mail with return receipt, or upload at VA.gov or through an accredited VSO."]),
    ]
    if notes:
        blocks.append(("Notes from preparation:", notes))
    for title, items in blocks:
        page.insert_text((x, y), title, fontname="hebo", fontsize=12); y += 18
        for it in items:
            for ln in _wrap("- " + it, 504, 10.5):
                page.insert_text((x + 8, y), ln, fontname="helv", fontsize=10.5); y += 14
        y += 10
    page.insert_text((x, 750), "Prepared with the veteran's records. Not legal advice. VA decides every claim.", fontname="helv", fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def merge(parts: list[bytes]) -> bytes:
    out = fitz.open()
    for p in parts:
        src = fitz.open(stream=p, filetype="pdf")
        out.insert_pdf(src)
        src.close()
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data
