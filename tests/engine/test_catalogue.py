"""The warning and assumption catalogue covers every code the engine can emit."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from teq_engine import ScenarioInput, calculate, default_provider
from teq_engine.warnings import (
    CATALOGUE,
    Code,
    catalogue_as_dicts,
    make_assumption,
    make_warning,
    render,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "teq_engine"

APPENDIX_B = {
    "ROUTE_UNSUPPORTED",
    "GROSS_UP_NOT_CONVERGED",
    "GROSS_UP_FALLBACK_BISECTION",
    "SOCIAL_SECURITY_AGREEMENT_MAY_APPLY",
    "NIC_EXEMPTION_ASSUMED",
    "HOME_SCHEME_CERTIFICATE_REQUIRED",
    "PERSONAL_ALLOWANCE_TAPER_BAND",
    "RATES_NOT_PUBLISHED_FOR_YEAR",
    "RATE_SET_SUPERSEDED",
    "MODE_ILLUSTRATIVE_WHOLE_YEAR",
    "RELOCATION_EXCESS_TAXABLE",
    "RELOCATION_OUTSIDE_WINDOW",
    "SALARY_CONVERTED_AT_SNAPSHOT_FX",
    "HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED",
    "REGION_NOT_SUPPORTED",
    "MODIFIED_PAYE_SAME_YEAR_GROSSUP",
    "PERSONAL_ALLOWANCE_ENTITLED",
    "RELOCATION_QUALIFYING_ASSUMED",
    "HYPO_TAX_OVERRIDE",
    "FX_RATE_USER_SUPPLIED",
    "FX_RATE_STALE",
    "FX_RATE_IMPLAUSIBLE",
    "ENGLAND_RATES",
    "ANNUAL_NIC_BASIS",
    "BIK_CASH_EQUIVALENT_AS_INPUT",
    "EMPLOYMENT_ALLOWANCE_NOT_APPLIED",
    "OWR_NOT_MODELLED",
    "AUTO_ENROLMENT_MAY_APPLY",
    "APPRENTICESHIP_LEVY_MAY_APPLY",
    "HOME_COUNTRY_TAX_RESIDENCE_RISK",
    "PAYROLLING_REPORTING_CHANGE_2027",
    "DEGRADED_BUNDLED_RATES",
    "PERSISTENCE_UNAVAILABLE",
    "NARRATIVE_FALLBACK",
    "IMMIGRATION_CONTENT_STALE",
}
SCHEMA_ASSUMPTIONS = {"UK_RESIDENT_FULL_YEAR", "UK_NIC_APPLIES", "RATES_UNCHANGED_LATER_YEARS"}
ENGINE_ERRORS = {"RATES_UNAVAILABLE", "FX_RATE_IN_FUTURE"}
REVIEW_WARNINGS = {"EQUALISED_ITEM_NO_HYPO_SHARE"}


def test_catalogue_is_appendix_b_plus_documented_extras() -> None:
    assert {
        c.value for c in CATALOGUE
    } == APPENDIX_B | SCHEMA_ASSUMPTIONS | ENGINE_ERRORS | REVIEW_WARNINGS
    assert set(CATALOGUE) == set(Code)
    assert CATALOGUE[Code.FX_RATE_IN_FUTURE].kind == "error"
    assert CATALOGUE[Code.EQUALISED_ITEM_NO_HYPO_SHARE].kind == "warning"


@pytest.mark.parametrize("code", list(Code))
def test_every_entry_has_text_and_question(code: Code) -> None:
    entry = CATALOGUE[code]
    assert entry.title.strip()
    assert entry.template.strip()
    assert entry.question.strip()
    params = dict.fromkeys(entry.fields(), "X")
    text, question = render(code, params)
    assert "{" not in text
    assert "{" not in question


def test_missing_parameters_are_an_error() -> None:
    with pytest.raises(KeyError):
        render(Code.FX_RATE_STALE, {})
    with pytest.raises(ValueError, match="assumption"):
        make_warning(Code.ENGLAND_RATES)
    with pytest.raises(ValueError, match="not an assumption"):
        make_assumption(Code.FX_RATE_STALE, as_of="x", days="1", rates_as_of="y")


def test_severities_match_the_brief() -> None:
    assert CATALOGUE[Code.AUTO_ENROLMENT_MAY_APPLY].kind == "info"
    assert CATALOGUE[Code.APPRENTICESHIP_LEVY_MAY_APPLY].kind == "info"
    assert CATALOGUE[Code.SOCIAL_SECURITY_AGREEMENT_MAY_APPLY].kind == "info"
    assert CATALOGUE[Code.ROUTE_UNSUPPORTED].kind == "error"
    assert CATALOGUE[Code.HOME_SCHEME_CERTIFICATE_REQUIRED].kind == "assumption"


def test_every_code_referenced_in_engine_source_is_catalogued() -> None:
    referenced: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        referenced |= set(re.findall(r"(?<![A-Za-z_])Code\.([A-Z0-9_]+)", text))
        referenced |= set(re.findall(r'    code = "([A-Z0-9_]+)"', text))
    referenced.discard("ENGINE_ERROR")  # the abstract base class, never raised itself
    assert len(referenced) > 30
    assert referenced <= {c.value for c in Code}


def test_catalogue_listing() -> None:
    rows = catalogue_as_dicts()
    assert len(rows) == len(CATALOGUE)
    assert {"code", "kind", "title", "template", "question"} <= set(rows[0])


def _scenarios(ref: dict[str, Any]) -> list[dict[str, Any]]:
    import copy

    fx = {"rate": "250", "as_of": "2026-01-01"}
    a = copy.deepcopy(ref)
    a["assumptions"]["social_security"] = "HOME_SCHEME_AGREEMENT"
    a["fx"] = fx
    a["items"][2]["amount"] = "12000.00"
    b = copy.deepcopy(ref)
    b["salary"] = {"amount": "6570000.00", "currency": "TRY"}
    b["hypothetical_tax"] = {"method": "CALCULATED"}
    b["fx"] = {"rate": "65.7", "as_of": "2026-10-01"}
    b["assignment"]["length_years"] = 3
    b["items"][2]["years"] = [3]
    b["assumptions"]["owr_claimed"] = True
    c = copy.deepcopy(ref)
    c["salary"]["amount"] = "100000.00"
    c["hypothetical_tax"]["override"] = "28000.00"
    c["items"] = []
    return [ref, a, b, c]


def test_every_emitted_code_is_catalogued(ref_data: dict[str, Any]) -> None:
    emitted: set[str] = set()
    for data in _scenarios(ref_data):
        result = calculate(
            ScenarioInput.model_validate(data), default_provider(), rates_as_of=date(2026, 10, 8)
        )
        for warning in result.warnings:
            entry = CATALOGUE[Code(warning.code)]
            assert warning.severity == entry.kind
            assert warning.question
            emitted.add(warning.code)
        for assumption in result.assumptions:
            assert CATALOGUE[Code(assumption.code)].kind == "assumption"
            emitted.add(assumption.code)
    assert {
        "FX_RATE_STALE",
        "FX_RATE_IMPLAUSIBLE",
        "SALARY_CONVERTED_AT_SNAPSHOT_FX",
        "RELOCATION_OUTSIDE_WINDOW",
        "RELOCATION_EXCESS_TAXABLE",
        "PERSONAL_ALLOWANCE_TAPER_BAND",
        "HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED",
        "NIC_EXEMPTION_ASSUMED",
        "HOME_SCHEME_CERTIFICATE_REQUIRED",
        "HYPO_TAX_OVERRIDE",
    } <= emitted
