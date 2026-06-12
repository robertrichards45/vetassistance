from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VAForm:
    key: str
    title: str
    url: str
    use: str
    category: str
    template: str | None = None
    fields: dict[str, Any] | None = None


def _load_field_map() -> dict[str, Any]:
    from pathlib import Path
    import json

    map_path = Path(__file__).resolve().parents[1] / "data" / "va_pdf_field_map.json"
    if not map_path.exists():
        return {}
    try:
        return json.loads(map_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_va_forms() -> list[VAForm]:
    field_map = _load_field_map()
    return [
        VAForm(
            key="21-526EZ",
            title="VA Form 21-526EZ",
            url="https://www.va.gov/find-forms/about-form-21-526ez/",
            use="Initial disability compensation claim.",
            category="claim",
            template=(field_map.get("21-526EZ") or {}).get("template"),
            fields=(field_map.get("21-526EZ") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-4138",
            title="VA Form 21-4138",
            url="https://www.va.gov/find-forms/about-form-21-4138/",
            use="Statement in support of claim.",
            category="evidence",
            template=(field_map.get("21-4138") or {}).get("template"),
            fields=(field_map.get("21-4138") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-0781",
            title="VA Form 21-0781",
            url="https://www.va.gov/find-forms/about-form-21-0781/",
            use="PTSD statement in support of claim.",
            category="evidence",
            template=(field_map.get("21-0781") or {}).get("template"),
            fields=(field_map.get("21-0781") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-0781a",
            title="VA Form 21-0781a",
            url="https://www.va.gov/find-forms/about-form-21-0781a/",
            use="PTSD based on personal assault.",
            category="evidence",
            template=(field_map.get("21-0781a") or {}).get("template"),
            fields=(field_map.get("21-0781a") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-8940",
            title="VA Form 21-8940",
            url="https://www.va.gov/find-forms/about-form-21-8940/",
            use="TDIU (unemployability) application.",
            category="employment",
            template=(field_map.get("21-8940") or {}).get("template"),
            fields=(field_map.get("21-8940") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-4192",
            title="VA Form 21-4192",
            url="https://www.va.gov/find-forms/about-form-21-4192/",
            use="Employment information (employer).",
            category="employment",
            template=(field_map.get("21-4192") or {}).get("template"),
            fields=(field_map.get("21-4192") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-686c",
            title="VA Form 21-686c",
            url="https://www.va.gov/find-forms/about-form-21-686c/",
            use="Declaration of status of dependents.",
            category="dependency",
            template=(field_map.get("21-686c") or {}).get("template"),
            fields=(field_map.get("21-686c") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-674",
            title="VA Form 21-674",
            url="https://www.va.gov/find-forms/about-form-21-674/",
            use="School attendance for dependent children.",
            category="dependency",
            template=(field_map.get("21-674") or {}).get("template"),
            fields=(field_map.get("21-674") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-0966",
            title="VA Form 21-0966",
            url="https://www.va.gov/find-forms/about-form-21-0966/",
            use="Intent to file a claim.",
            category="claim",
            template=(field_map.get("21-0966") or {}).get("template"),
            fields=(field_map.get("21-0966") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-4142",
            title="VA Form 21-4142",
            url="https://www.va.gov/find-forms/about-form-21-4142/",
            use="Authorization to release medical records.",
            category="records",
            template=(field_map.get("21-4142") or {}).get("template"),
            fields=(field_map.get("21-4142") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-4142a",
            title="VA Form 21-4142a",
            url="https://www.va.gov/find-forms/about-form-21-4142a/",
            use="General release for medical provider info.",
            category="records",
            template=(field_map.get("21-4142a") or {}).get("template"),
            fields=(field_map.get("21-4142a") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-2680",
            title="VA Form 21-2680",
            url="https://www.va.gov/find-forms/about-form-21-2680/",
            use="Examination for housebound or aid and attendance.",
            category="medical",
            template=(field_map.get("21-2680") or {}).get("template"),
            fields=(field_map.get("21-2680") or {}).get("fields") or {},
        ),
        VAForm(
            key="21-0845",
            title="VA Form 21-0845",
            url="https://www.va.gov/find-forms/about-form-21-0845/",
            use="Authorization to disclose personal info.",
            category="records",
            template=(field_map.get("21-0845") or {}).get("template"),
            fields=(field_map.get("21-0845") or {}).get("fields") or {},
        ),
        VAForm(
            key="40-10007",
            title="VA Form 40-10007",
            url="https://www.va.gov/find-forms/about-form-40-10007/",
            use="Pre-need eligibility determination (burial).",
            category="burial",
            template=(field_map.get("40-10007") or {}).get("template"),
            fields=(field_map.get("40-10007") or {}).get("fields") or {},
        ),
    ]
