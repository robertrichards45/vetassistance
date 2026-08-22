"""Regression tests for scripts/build_va_ratings_chart_ecfr.py.

These pin down real bugs found (and fixed) while building the VA ratings
chart feature: criteria from one condition's rating table bleeding into an
unrelated condition's entry, cross-reference sentences being misread as
diagnostic-code table rows, and garbled/truncated condition titles slipping
through into the final chart. Each test reproduces the minimal input shape
that triggered the bug, so a future change to the parser that reintroduces
one of these can't pass silently.
"""
import pytest

from scripts.build_va_ratings_chart_ecfr import (
    parse_section,
    extract_section,
    criteria_from_lines,
    keyword_best_formula,
    is_bad_name,
)


# ---------------------------------------------------------------------------
# parse_section
# ---------------------------------------------------------------------------

def test_new_dc_ends_the_previous_formula():
    """Section 4.104 bug: a "general formula" (Diseases of the Heart) that
    already produced criteria rows must not keep absorbing every DC and row
    that follows it for the rest of the section (aneurysm, PAD, etc.)."""
    lines = [
        "GENERAL RATING FORMULA FOR DISEASES OF THE HEART:",
        "Workload of 3.0 METs or less results in heart failure symptoms | 100",
        "7001 Endocarditis",
        "7110 Aortic aneurysm: Ascending, thoracic, or abdominal:",
        "Evaluate at 100 percent if the aneurysm is five centimeters or larger | 100",
        "Otherwise | 0",
    ]
    parsed = parse_section("4.104", lines, "https://example.test")

    heart_formula = next(f for f in parsed["formulas"] if "DISEASES OF THE HEART" in f["name"])
    assert len(heart_formula["criteria"]) == 1

    assert "7110" in parsed["dc_criteria"]
    aneurysm_rows = parsed["dc_criteria"]["7110"]
    assert len(aneurysm_rows) == 2
    assert {r["percent"] for r in aneurysm_rows} == {100, 0}
    assert "Aortic aneurysm" not in " ".join(r["text"] for r in aneurysm_rows)


def test_cross_reference_sentence_is_not_a_two_dc_table_row():
    """A prose sentence that merely *mentions* two DC numbers must not be
    read as a genuine two-code listing row — that's how DC 7329 previously
    ended up with the name "(Intestine, large, resection of), whichever
    results in a higher evaluation." instead of its real, short title."""
    lines = [
        "7329 Intestine, large, resection of:",
        "Total colectomy with formation of ileostomy | 100",
        "Note: For colectomy or colostomy, use DC 7327 or DC 7329 "
        "(Intestine, large, resection of), whichever results in a higher evaluation.",
    ]
    parsed = parse_section("4.114", lines, "https://example.test")

    entries = [c for c in parsed["codes"] if c["dc"] == "7329"]
    assert len(entries) == 1
    assert entries[0]["name"] == "Intestine, large, resection of:"


def test_multi_dc_listing_row_still_works():
    """The line-must-start-with-a-digit guard shouldn't break genuine
    same-line multi-DC listing rows like "5013 Osteoporosis 5014
    Osteomalacia"."""
    lines = ["5013 Osteoporosis 5014 Osteomalacia"]
    parsed = parse_section("4.71a", lines, "https://example.test")

    dcs = {c["dc"] for c in parsed["codes"]}
    assert dcs == {"5013", "5014"}


def test_dc_list_then_shared_formula_associates_all_of_them():
    """Mental-disorders-style layout: several DCs are listed first, then ONE
    shared formula applies to all of them — the formula must claim every DC
    introduced since the last formula boundary, not just the last one."""
    lines = [
        "9411 Posttraumatic stress disorder",
        "9412 Other specified trauma- and stressor-related disorder",
        "General Rating Formula for Mental Disorders",
        "Total occupational and social impairment | 100",
        "Occupational and social impairment with deficiencies | 70",
    ]
    parsed = parse_section("4.130", lines, "https://example.test")

    for dc in ("9411", "9412"):
        assert any("Mental Disorders" in name for name in parsed["formula_map"].get(dc, []))


def test_bare_percent_lines_pair_with_preceding_text():
    """Musculoskeletal-style layout: criteria text on its own line(s),
    followed by a bare percent number with no pipe delimiter at all."""
    lines = [
        "5000 Osteomyelitis, acute, subacute, or chronic",
        "Of the pelvis, vertebrae, or extending into major joints",
        "100",
        "Frequent episodes, with constitutional symptoms",
        "60",
    ]
    parsed = parse_section("4.71a", lines, "https://example.test")

    rows = parsed["dc_criteria"]["5000"]
    assert [r["percent"] for r in rows] == [100, 60]
    assert "pelvis" in rows[0]["text"]
    assert "Frequent episodes" in rows[1]["text"]


# ---------------------------------------------------------------------------
# is_bad_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Posttraumatic stress disorder",
    "Aortic aneurysm: Ascending, thoracic, or abdominal",
    "Intestine, large, resection of",
    "Knee, other impairment of",
    "Fibromyalgia (fibrositis, primary fibromyalgia syndrome)",
])
def test_good_names_are_not_flagged(name):
    assert is_bad_name(name) is False


@pytest.mark.parametrize("name", [
    "",
    "Evaluation November 1, 1962; evaluation, criterion, and note May 19, 2024.",
    "Removed February 7, 2021.",
    "Knee, other impairment of: 5258 Cartilage, semilunar, dislocated, with frequent",
    "(Intestine, large, resection of), whichever results in a higher evaluation.",
    "Fibromyalgia (fibrositis, primary fibromyalgia syndrome",  # unclosed paren
    "Complete",
    "Some condition and",
])
def test_bad_names_are_flagged(name):
    assert is_bad_name(name) is True


# ---------------------------------------------------------------------------
# keyword_best_formula / criteria_from_lines / extract_section
# ---------------------------------------------------------------------------

def test_keyword_best_formula_picks_matching_candidate_not_first():
    candidates = [
        ("Rating Formula for Eating Disorders", [{"percent": 100, "text": "x"}]),
        ("General Rating Formula for Mental Disorders", [{"percent": 100, "text": "y"}]),
    ]
    best = keyword_best_formula(candidates, "bulimia nervosa eating disorder")
    assert best == [{"percent": 100, "text": "x"}]


def test_keyword_best_formula_returns_none_when_nothing_matches():
    candidates = [("General Rating Formula for Mental Disorders", [{"percent": 100, "text": "y"}])]
    assert keyword_best_formula(candidates, "aortic aneurysm") is None


def test_criteria_from_lines_extracts_percent_and_text():
    out = criteria_from_lines(["Total colectomy with ileostomy: 100 percent"])
    assert out == [{"percent": 100, "text": "Total colectomy with ileostomy: 100 percent"}]


def test_extract_section_pulls_cfr_section_number():
    assert extract_section("§4.104") == "4.104"
    assert extract_section("nothing here") == ""
