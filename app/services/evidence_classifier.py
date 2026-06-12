import re

CATEGORIES = [
  "Decision Letter",
  "C&P Exam",
  "DBQ",
  "Treatment Records",
  "Service Records (STR)",
  "Buddy Statement",
  "Employer Statement",
  "Imaging/Labs",
  "Other",
  "Uncategorized",
]

KEYWORDS = [
  ("Decision Letter", [r"rating decision", r"decision letter", r"award letter", r"rating", r"award", r"notification", r"codesheet", r"code sheet", r"va decision"]),
  ("C&P Exam", [r"c&p", r"compensation", r"pension", r"exam report", r"qtc", r"lhi", r"ves", r"optum"]),
  ("DBQ", [r"dbq", r"disability benefits questionnaire"]),
  ("Service Records (STR)", [r"dd214", r"dd 214", r"separation", r"service treatment", r"service record", r"military record", r"enlistment", r"discharge"]),
  ("Treatment Records", [r"treatment", r"clinic", r"hospital", r"progress note", r"medical record", r"provider note", r"diagnosis", r"evaluation"]),
  ("Buddy Statement", [r"buddy", r"lay statement", r"statement in support", r"21-4138", r"personal statement"]),
  ("Employer Statement", [r"employer", r"work", r"attendance", r"accommodation", r"hr", r"supervisor"]),
  ("Imaging/Labs", [r"mri", r"xray", r"x-ray", r"ct", r"lab", r"imaging", r"radiology", r"ultrasound", r"blood test", r"lab report"]),
]

def classify(filename: str, mime: str = "", text: str = "") -> str:
    fn = (filename or "").lower()
    content = (text or "").lower()
    haystack = " ".join([fn, content])
    for cat, pats in KEYWORDS:
        for p in pats:
            if re.search(p, haystack):
                return cat
    return "Uncategorized"
