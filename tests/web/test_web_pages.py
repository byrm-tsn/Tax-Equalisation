"""The results page, the reference example and the unsupported-route page."""

from __future__ import annotations

import base64
import json
import random
import re
import zlib
from collections.abc import Callable
from datetime import date
from html import unescape
from typing import Any

import pytest
from django.test import Client
from django.utils.http import urlencode

from teq_engine import ENGINE_VERSION, REFERENCE_RATES_AS_OF, ScenarioInput
from teq_engine.reference import reference_example_data
from teq_web.scenarios.services import (
    MAX_ENCODED_LENGTH,
    ScenarioTooLargeError,
    decode_scenario,
    encode_scenario,
)

Rows = Callable[[str], dict[str, list[str]]]
Section = Callable[[str, str, str], str]

REFERENCE_FIGURES = (
    "188,676",
    "180,676",
    "369,352",
    "127,762",
    "57,196",
    "4,566",
    "18,414",
    "4,500",
)


def _token(**changes: object) -> str:
    data = reference_example_data()
    data.update(changes)
    return encode_scenario(ScenarioInput.model_validate(data), REFERENCE_RATES_AS_OF)


def _text(html: str) -> str:
    """Visible text with tags removed and whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _results_from_form(
    client: Client, form_data: dict[str, str], tab: str = "", **changes: str
) -> str:
    """Post the form with ``changes`` and return the results page it redirects to.

    The form redirects to the Tax tab; ``tab`` asks for another tab of the same result.
    """
    response = client.post("/", form_data | changes)
    assert response.status_code == 303, response.content.decode()[:2000]
    location = response["Location"]
    page = client.get(location + (f"&tab={tab}" if tab else ""))
    assert page.status_code == 200
    return str(page.content.decode())


def _crafted_link(payload: dict[str, Any]) -> str:
    """A link built by hand from a payload (as an older or a tampered tool might)."""
    raw = zlib.compress(json.dumps(payload).encode("utf-8"), 9)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _class_1_claims(text: str) -> list[str]:
    """Sentences that mention Class 1 or Class 1A without negating it."""
    sentences = re.split(r"(?<=[.;:])\s+", text)
    return [
        sentence
        for sentence in sentences
        if "Class 1" in sentence
        and not re.search(r"\b(no|not|none|without)\b", sentence, flags=re.IGNORECASE)
    ]


def test_example_redirects_to_the_reference_results(client: Client, reference_token: str) -> None:
    response = client.get("/example")
    assert response.status_code == 302
    assert response["Location"] == f"/estimate?s={reference_token}"


def test_reference_results_reproduce_the_pack(
    tax_html: str, rows_of: Rows, section_of: Section
) -> None:
    for figure in REFERENCE_FIGURES:
        assert figure in tax_html, figure
    assert "£188,676" in tax_html
    assert "2.10 times" in tax_html
    assert "Two-year total" in tax_html
    # The pack's figures sit on the right lines of the year-by-year table, not just anywhere.
    rows = rows_of(section_of(tax_html, "per-year", "assumptions"))
    assert rows["Gross cash pay (grossed up)"][0] == "£127,762"
    assert rows["Income tax"][0] == "£57,196"
    assert rows["Employee National Insurance"][0] == "£4,566"
    assert rows["Employer National Insurance"][0] == "£18,414"
    assert rows["Class 1A National Insurance on benefits"][0] == "£4,500"
    assert rows["Total employer cost"] == ["£188,676", "£180,676", "£369,352"]
    assert rows["Total (second decomposition)"] == ["£188,676", "£180,676", "£369,352"]


TAB_SECTIONS = {
    "tax": [
        "in-short",
        "flags",
        "net-guarantee",
        "treatment",
        "gross-up",
        "employer-charges",
        "per-year",
        "assumptions",
    ],
    "immigration": ["immigration"],
    "explain": ["narrative", "immigration-narrative", "trace", "provenance"],
}


@pytest.mark.parametrize("tab", sorted(TAB_SECTIONS))
def test_each_tab_shows_its_own_sections_in_the_pack_order(
    client: Client, reference_token: str, tab: str
) -> None:
    html = client.get("/estimate", {"s": reference_token, "tab": tab}).content.decode()
    ids = TAB_SECTIONS[tab]
    positions = [html.index(f'<section id="{section_id}"') for section_id in ids]
    assert positions == sorted(positions)
    assert positions[0] > html.index('class="tabs')
    assert html.index('id="disclaimer"') > positions[-1]
    for other, other_ids in TAB_SECTIONS.items():
        if other != tab:
            for section_id in other_ids:
                assert f'<section id="{section_id}"' not in html, (tab, section_id)


def test_flags_put_warnings_before_information(
    client: Client, form_data: dict[str, str], section_of: Section
) -> None:
    # The reference example has no warning-severity flags; relocation above the cap does.
    html = _results_from_form(client, form_data, relocation_amount="10000")
    flags = section_of(html, "flags", "net-guarantee")
    # Warnings are cards; information is a compact list of things to check after them.
    assert flags.index("flag--warning") < flags.index('class="check"')
    assert "flag--info" not in flags
    assert 'title="RELOCATION_EXCESS_TAXABLE"' in flags
    assert "Social security agreement may apply" in flags


def test_treatment_table_labels_each_row(tax_html: str, rows_of: Rows, section_of: Section) -> None:
    table = section_of(tax_html, "treatment", "gross-up")
    rows = rows_of(table)
    # Columns: amount, paid in, treatment (label and part), National Insurance, meaning.
    assert rows["Base salary"][2:4] == ["Taxable cash", "Class 1"]
    assert rows["Cost-of-living allowance"][2:4] == ["Taxable cash", "Class 1"]
    assert rows["Housing (rent paid by the employer)"][2:4] == [
        "Taxable benefit in kind",
        "Class 1A",
    ]
    assert rows["Relocation"][:4] == ["£8,000 one-off", "year 1", "Exempt £8,000", "None"]
    assert rows["Relocation"][4] == "Within the £8,000 cap per move, so all of it is exempt."
    assert "Relocation (above the cap)" not in rows
    # The treatment codes stay in the page, as each cell's title only.
    for code in ("GROSS_EQUALISED", "NET_CASH", "TAXABLE_BIK", "EXEMPT_CAPPED"):
        assert f'<td title="{code}">' in table, code
    assert "_" not in _text(table)


def test_relocation_excess_is_split_into_its_two_treatments(
    client: Client, form_data: dict[str, str], rows_of: Rows, section_of: Section
) -> None:
    html = _results_from_form(client, form_data, relocation_amount="10000")
    table = section_of(html, "treatment", "gross-up")
    rows = rows_of(table)
    assert rows["Relocation"][:4] == ["£10,000 one-off", "year 1", "Exempt £8,000", "None"]
    excess = rows["Relocation (above the cap)"]
    assert excess[1:4] == ["year 1", "Taxable benefit in kind £2,000", "Class 1A on the excess"]
    assert "Class 1A" in excess[4]
    assert table.count('<td title="EXEMPT_CAPPED">') == 1
    assert table.count('<td title="TAXABLE_BIK">') == 2  # the housing and the excess


def test_home_scheme_claims_no_uk_national_insurance(
    client: Client, form_data: dict[str, str], rows_of: Rows, section_of: Section
) -> None:
    changes = {
        "social_security": "HOME_SCHEME_AGREEMENT",
        "fx_rate": "55.25",
        "fx_date": "2026-10-01",
        "relocation_amount": "10000",
    }
    html = _results_from_form(client, form_data, **changes)
    explain = _results_from_form(client, form_data, tab="explain", **changes)
    table = section_of(html, "treatment", "gross-up")
    narrative = _text(section_of(explain, "narrative", "trace"))
    no_nic = "No UK National Insurance: the employee stays in the Turkish scheme"
    rows = rows_of(table)
    for name in (
        "Base salary",
        "Cost-of-living allowance",
        "Housing (rent paid by the employer)",
        "Relocation (above the cap)",
    ):
        assert rows[name][3] == no_nic, name
    assert rows["Relocation"][3] == "None"
    assert _class_1_claims(_text(table)) == []
    assert "Class 1" not in _text(table)
    assert _class_1_claims(narrative) == []
    assert "no UK National Insurance or Class 1A is due" in narrative
    assert "Turkish employer contributions continue" in narrative
    assert "£13,882 is Turkish employer social security" in narrative
    assert "£0" not in narrative


def test_gross_up_shows_segment_rate_and_exact_figures(tax_html: str, section_of: Section) -> None:
    section = _text(section_of(tax_html, "gross-up", "employer-charges"))
    assert "IT 45% + NIC 2%" in section
    assert "47%" in section
    assert "£127,761.51" in section
    assert "£127,762" in section
    assert "£66,000.26" in section


def test_per_year_table_has_both_decompositions_and_the_total(
    tax_html: str, section_of: Section
) -> None:
    section = _text(section_of(tax_html, "per-year", "assumptions"))
    assert "What the employer pays" in section
    assert "Where the money goes" in section
    assert "£369,352" in section
    assert "Both decompositions give the same total in every year." in section


def test_immigration_block_is_separate_with_payers_and_subtotals(
    immigration_html: str, section_of: Section
) -> None:
    panel = section_of(immigration_html, "immigration", "disclaimer")
    assert "never added to it" in panel
    assert '<th scope="col">Payer</th>' in panel
    assert "£3,165" in panel
    assert "£2,889" in panel
    assert "Applicant in law; employer by policy" in panel
    assert "about 6 to 16 weeks" in panel
    assert 'action="/estimate"' in panel
    assert 'method="get"' in panel
    assert 'type="hidden" name="s"' in panel


def test_tailoring_by_get_keeps_the_scenario(client: Client, reference_token: str) -> None:
    answers = {"s": reference_token, "sponsor_licence_held": "false", "visa_length_years": "2"}
    response = client.get("/estimate", answers | {"tab": "immigration"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "£1,682" in html  # the licence fee appears in the immigration costs
    assert "Apply for a sponsor licence" in html
    # The employment cost is unchanged, and the Tax tab keeps the answers in its tab links.
    tax = client.get("/estimate", answers).content.decode()
    assert "188,676" in tax
    assert "sponsor_licence_held=false" in tax


def test_invalid_tailoring_does_not_break_the_page(client: Client, reference_token: str) -> None:
    params = {"s": reference_token, "visa_length_years": "lots"}
    response = client.get("/estimate", params | {"tab": "immigration"})
    assert response.status_code == 200
    assert "Some answers could not be used" in response.content.decode()
    assert client.get("/estimate", params).status_code == 200


def test_narrative_and_trace_drawer(explain_html: str, section_of: Section) -> None:
    narrative = section_of(explain_html, "narrative", "trace")
    assert narrative.count("<p>") >= 5
    assert "2.10 times the salary" in narrative
    trace = section_of(explain_html, "trace", "provenance")
    assert "<details>" in trace
    assert 'id="t6"' in trace
    assert "PAYE81740" in trace


def test_the_gross_up_links_to_its_trace_step_on_the_explain_tab(
    tax_html: str, reference_token: str, section_of: Section
) -> None:
    gross_up = section_of(tax_html, "gross-up", "employer-charges")
    assert f'href="/estimate?s={reference_token}&amp;tab=explain#t6"' in gross_up


def test_provenance_disclaimer_and_links(
    explain_html: str, tax_html: str, reference_token: str, reference_result
) -> None:
    # Provenance stays visible text: the inputs hash and the rate-set ids.
    assert reference_result.inputs_hash in _text(explain_html)
    assert "UK_INCOME_TAX:2026-27:v1" in _text(explain_html)
    for html in (tax_html, explain_html):
        assert "8 October 2026" in html
        assert "An illustration, not tax or immigration advice" in html
        assert f'href="/?s={reference_token}"' in html
        assert f"/api/v1/estimates?s={reference_token}" in html


def test_results_url_carries_the_rates_date(client: Client) -> None:
    token = encode_scenario(
        ScenarioInput.model_validate(reference_example_data()),
        REFERENCE_RATES_AS_OF.replace(day=20),
    )
    html = client.get("/estimate", {"s": token}).content.decode()
    assert "rates as at 20 October 2026" in html


def test_home_scheme_shows_the_turkish_employer_line(client: Client) -> None:
    token = _token(
        assumptions={"social_security": "HOME_SCHEME_AGREEMENT"},
        fx={"rate": "65.7", "as_of": "2026-10-01"},
    )
    html = client.get("/estimate", {"s": token}).content.decode()
    assert "Turkish employer social security (estimated)" in html
    assert 'title="HOME_SCHEME_CERTIFICATE_REQUIRED"' in html
    assert "HOME_SCHEME_CERTIFICATE_REQUIRED" not in _text(html)
    assert "requires a certificate of coverage" in html
    assert "Turkish employer contributions continue" in html


@pytest.mark.parametrize(
    ("route", "title"),
    [
        ({"home": "TR", "host": "GB", "region": "SCT"}, "This region is not supported yet"),
        ({"home": "TR", "host": "DE", "region": None}, "This route is not supported yet"),
    ],
)
def test_unsupported_route_page(client: Client, route: dict[str, object], title: str) -> None:
    response = client.get("/estimate", {"s": _token(route=route)})
    html = response.content.decode()
    assert response.status_code == 200
    assert title in html
    assert "Turkey to the United Kingdom (England)" in html
    assert "What adding it would require" in html
    assert "188,676" not in html


def test_damaged_link_is_a_400_page(client: Client) -> None:
    response = client.get("/estimate", {"s": "not-a-scenario"})
    assert response.status_code == 400
    assert "This link cannot be read" in response.content.decode()


def test_missing_scenario_redirects_to_the_form(client: Client) -> None:
    response = client.get("/estimate")
    assert response.status_code == 302
    assert response["Location"] == "/"


def test_compare_needs_an_exchange_rate_for_the_home_scheme(
    client: Client, reference_token: str
) -> None:
    html = client.get("/compare", {"s": reference_token}).content.decode()
    assert "188,676" in html
    assert "needs an exchange rate" in html


def test_compare_shows_both_social_security_options(client: Client) -> None:
    token = _token(fx={"rate": "65.7", "as_of": "2026-10-01"})
    html = client.get("/compare", {"s": token}).content.decode()
    assert "UK National Insurance applies" in html
    assert "Stays in the Turkish scheme" in html
    assert "Turkish employer social security (estimated)" in html
    assert "188,676" in html


def test_security_headers(client: Client, reference_token: str) -> None:
    response = client.get("/estimate", {"s": reference_token})
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'none'" in response["Content-Security-Policy"]
    assert "Set-Cookie" not in response or "sessionid" not in response["Set-Cookie"]
    assert response["Cache-Control"] == "private, no-store"
    assert response["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )


@pytest.mark.parametrize(
    ("method", "path", "query", "status"),
    [
        ("get", "/", "", 200),
        ("get", "/estimate", "s={token}", 200),
        ("get", "/compare", "s={token}", 200),
        ("get", "/example", "", 302),
        ("get", "/estimate", "s=damaged", 400),
        ("get", "/no-such-page", "", 404),
        ("post", "/", "", 200),
        ("get", "/api/v1/estimates", "s={token}", 200),
        ("get", "/api/v1/estimates", "s=damaged", 400),
        ("get", "/api/v1/routes", "", 200),
        ("get", "/api/v1/warnings", "", 200),
        ("get", "/api/v1/reference-example", "", 200),
        ("get", "/api/v1/openapi.json", "", 200),
        ("get", "/api/v1/docs", "", 200),
        ("get", "/api/v1/no-such-thing", "", 404),
        ("put", "/api/v1/estimates", "", 405),
    ],
)
def test_every_page_and_api_response_is_private_and_denies_device_features(
    client: Client, reference_token: str, method: str, path: str, query: str, status: int
) -> None:
    url = path + ("?" + query.format(token=reference_token) if query else "")
    response = getattr(client, method)(url)
    assert response.status_code == status
    assert response["Cache-Control"] == "private, no-store"
    assert response["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )


def test_operations_endpoints_keep_their_own_no_store(client: Client) -> None:
    response = client.get("/healthz")
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]
    assert "payment=()" in response["Permissions-Policy"]


def test_an_oversized_form_post_is_a_plain_413_page(
    client: Client, form_data: dict[str, str]
) -> None:
    response = client.post("/", form_data | {"fx_source": "x" * 300_000})
    assert response.status_code == 413
    assert response["Content-Type"].startswith("text/html")
    html = response.content.decode()
    assert "Too much data was sent" in html
    assert "the limit is 256 KB" in html


@pytest.mark.parametrize("rates_as_of", [date(1, 1, 1), date(9999, 12, 31)])
def test_a_link_with_an_extreme_rates_date_is_a_400_page(client: Client, rates_as_of: date) -> None:
    token = encode_scenario(ScenarioInput.model_validate(reference_example_data()), rates_as_of)
    response = client.get("/estimate", {"s": token})
    assert response.status_code == 400
    html = response.content.decode()
    assert "This link cannot be read" in html
    assert "between 1 January 2000 and 31 December 2100" in html
    assert "Traceback" not in html


def test_a_tampered_link_is_refused_not_reflected(client: Client, reference_token: str) -> None:
    attack = '"><svg/onload=alert(1)>'
    response = client.get("/estimate?" + urlencode({"s": reference_token + attack}))
    assert response.status_code == 400
    html = response.content.decode()
    assert "svg" not in html
    assert "onload" not in html


@pytest.mark.parametrize("tab", ["tax", "immigration", "explain"])
def test_a_valid_link_is_echoed_unchanged(client: Client, reference_token: str, tab: str) -> None:
    html = client.get("/estimate", {"s": reference_token, "tab": tab}).content.decode()
    if tab == "immigration":  # the tailoring form carries the scenario
        assert f'<input type="hidden" name="s" value="{reference_token}">' in html
    assert f'href="/?s={reference_token}"' in html
    assert f'href="/compare?s={reference_token}"' in html
    echoed = set(re.findall(r"[?&;]s=([A-Za-z0-9_-]+)", html))
    echoed |= set(re.findall(r'name="s" value="([^"]*)"', html))
    # The only other link is the calculated variant the Tax tab makes itself.
    others = echoed - {reference_token}
    assert len(others) == (1 if tab == "tax" else 0)
    for calculated in others:
        variant, _ = decode_scenario(calculated)
        assert variant.hypothetical_tax.method.value == "CALCULATED"


def test_a_hand_made_link_is_echoed_in_canonical_form(client: Client) -> None:
    payload = {
        "inputs": reference_example_data(),
        "rates_as_of": REFERENCE_RATES_AS_OF.isoformat(),
        "v": 1,
        "engine_version": ENGINE_VERSION,
    }
    crafted = _crafted_link(payload)
    canonical = encode_scenario(
        ScenarioInput.model_validate(reference_example_data()), REFERENCE_RATES_AS_OF
    )
    assert crafted != canonical
    pages = {
        tab: client.get("/estimate", {"s": crafted, "tab": tab}).content.decode()
        for tab in ("tax", "immigration", "explain")
    }
    for html in pages.values():
        assert crafted not in html
        assert f'href="/?s={canonical}"' in html
    assert f'name="s" value="{canonical}"' in pages["immigration"]  # the tailoring form


def test_a_link_from_another_engine_version_is_recalculated_with_a_notice(
    client: Client,
) -> None:
    payload = {
        "v": 1,
        "rates_as_of": REFERENCE_RATES_AS_OF.isoformat(),
        "engine_version": "0.0.1",
        "inputs": reference_example_data(),
    }
    html = client.get("/estimate", {"s": _crafted_link(payload)}).content.decode()
    notice = f"Recalculated under engine {ENGINE_VERSION}; the link was created under engine 0.0.1."
    assert notice in html
    assert "£188,676" in html


def test_a_link_from_this_engine_version_has_no_notice(tax_html: str) -> None:
    assert "the link was created under engine" not in tax_html


def _noise(seed: int, count: int) -> list[str]:
    """Deterministic, incompressible labels (CJK characters chosen pseudo-randomly)."""
    generator = random.Random(seed)
    return [
        "".join(chr(0x4E00 + generator.randrange(20000)) for _ in range(80)) for _ in range(count)
    ]


def _near_cap_link() -> str:
    """A link just under the size cap whose home-scheme variant is just over it."""
    labels = _noise(3, 45)
    base = reference_example_data() | {"fx": {"rate": "55.25", "as_of": "2026-10-01"}}

    def scenario(chars: int, social: str) -> ScenarioInput:
        whole, rest = divmod(chars, 80)
        texts = labels[:whole] + ([labels[whole][:rest]] if rest else [])
        items = [
            {"id": f"b{n}", "kind": "BONUS", "label": text, "amount": "1000.00", "years": "ALL"}
            for n, text in enumerate(texts)
        ]
        data = base | {"items": items, "assumptions": {"social_security": social}}
        return ScenarioInput.model_validate(data)

    def link(chars: int, social: str) -> str | None:
        try:
            return encode_scenario(scenario(chars, social), REFERENCE_RATES_AS_OF)
        except ScenarioTooLargeError:
            return None

    low, high = 0, 45 * 80
    while low < high:  # the most label text whose UK_NIC link fits
        middle = (low + high + 1) // 2
        low, high = (middle, high) if link(middle, "UK_NIC") else (low, middle - 1)
    for chars in range(low, low - 40, -1):
        token = link(chars, "UK_NIC")
        if token and link(chars, "HOME_SCHEME_AGREEMENT") is None:
            return token
    raise AssertionError("no scenario sits between the two sizes")  # pragma: no cover


def test_compare_near_the_size_cap_shows_the_column_without_a_link(client: Client) -> None:
    token = _near_cap_link()
    assert MAX_ENCODED_LENGTH - 100 < len(token) <= MAX_ENCODED_LENGTH
    response = client.get("/compare", {"s": token})
    assert response.status_code == 200
    html = response.content.decode()
    assert "too large to carry in a link" in html
    assert ">UK National Insurance applies</a>" in html
    assert ">Stays in the Turkish scheme</a>" not in html
    assert "Turkish employer social security (estimated)" in html


def test_flags_name_their_years_once_per_jurisdiction(client: Client, section_of: Section) -> None:
    token = _token(assignment={"length_years": 10})
    html = client.get("/estimate", {"s": token}).content.decode()
    flags = section_of(html, "flags", "net-guarantee")
    # The override means the Turkish rates are not used, so only the UK line appears.
    assert flags.count('title="RATES_NOT_PUBLISHED_FOR_YEAR"') == 1
    assert "RATES_NOT_PUBLISHED_FOR_YEAR" not in _text(flags)
    text = _text(flags)
    assert text.count("Rates not yet published (years 2 to 10)") == 1
    assert (
        "UK rates for tax years 2027-28 to 2035-36 are not published; the 2026-27 rates "
        "have been carried forward."
    ) in text
    assert "Turkish rates" not in text
    assert "Assignment year" not in text


def test_identical_relocation_flags_collapse_to_one(client: Client, section_of: Section) -> None:
    extra = [
        {"id": f"move-{n}", "kind": "RELOCATION", "amount": "9000.00", "frequency": "ONE_OFF"}
        | {"years": [1]}
        for n in range(3)
    ]
    token = _token(items=[*reference_example_data()["items"], *extra])  # type: ignore[misc]
    html = client.get("/estimate", {"s": token}).content.decode()
    flags = section_of(html, "flags", "net-guarantee")
    assert flags.count('title="RELOCATION_EXCESS_TAXABLE"') == 1
    assert "RELOCATION_EXCESS_TAXABLE" not in _text(flags)
    assert "Relocation above the exemption (year 1)" in _text(flags)


def test_the_exchange_rate_shows_as_entered(client: Client) -> None:
    token = _token(
        hypothetical_tax={"method": "CALCULATED", "includes_social_security": True},
        fx={"rate": "55.25", "as_of": "2026-10-01"},
    )
    html = client.get("/estimate", {"s": token}).content.decode()
    assert "at 55.25 lira to the pound" in html
    assert "55.250000" not in html


def test_labels_are_escaped_everywhere(client: Client, rows_of: Rows, section_of: Section) -> None:
    label = "<img src=x onerror=alert(1)>"
    items = [
        {"id": "flat", "kind": "HOUSING", "label": label, "amount": "1000.00", "years": "ALL"},
    ]
    token = _token(items=items)
    for url in ("/estimate", "/", "/compare"):
        html = client.get(url, {"s": token}).content.decode()
        assert "<img" not in html, url
    html = client.get("/estimate", {"s": token}).content.decode()
    assert "&lt;img src=x onerror=alert(1)&gt;" in section_of(html, "treatment", "gross-up")
    assert label in rows_of(section_of(html, "treatment", "gross-up"))


def test_tailoring_values_are_escaped(client: Client, reference_token: str) -> None:
    params = {
        "s": reference_token,
        "visa_length_years": "<script>x</script>",
        "sponsor_licence_held": '"><b>bold</b>',
    }
    response = client.get("/estimate", params | {"tab": "immigration"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "Some answers could not be used" in html
    assert "<script>" not in html
    assert "<b>bold" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    # The tab links carry the answers URL-encoded, never as markup.
    for tab in ("tax", "explain"):
        other = client.get("/estimate", params | {"tab": tab}).content.decode()
        assert "<script>" not in other
        assert "<b>bold" not in other
        assert "visa_length_years=%3Cscript%3Ex%3C%2Fscript%3E" in other


def test_unknown_page_is_a_friendly_404(client: Client) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "Page not found" in response.content.decode()


def test_scenario_refused_as_given_is_a_422_page(client: Client) -> None:
    token = _token(fx={"rate": "55.25", "as_of": "2026-11-01"})
    response = client.get("/estimate", {"s": token})
    assert response.status_code == 422
    assert "This scenario cannot be calculated as given" in response.content.decode()


# --------------------------------------------------------------------------- the Tax tab


def test_the_lead_line_names_the_route_in_words(tax_html: str) -> None:
    lead = re.search(r'<p class="lead">(.*?)</p>', tax_html, flags=re.S)
    assert lead is not None
    assert lead.group(1) == (
        "Turkey to England, 2 years, rates as at 8 October 2026. All amounts in pounds."
    )
    assert "TR to GB" not in tax_html


def test_in_short_is_the_first_three_narrative_paragraphs(
    tax_html: str, reference_result, section_of: Section
) -> None:
    from teq_web.narration.figures import unknown_numbers
    from teq_web.narration.narrator import TemplateNarrator

    summary = section_of(tax_html, "in-short", "flags")
    paragraphs = re.findall(r"<p>(.*?)</p>", summary, flags=re.S)
    expected = TemplateNarrator().narrate(reference_result)[:3]
    assert [unescape(p) for p in paragraphs[:3]] == expected
    assert "In short" in summary
    assert "£369,352" in summary
    assert unknown_numbers(expected, reference_result) == set()
    assert "The full explanation" in summary


def test_the_reference_example_has_no_warning_cards_and_four_checks(
    tax_html: str, section_of: Section
) -> None:
    flags = section_of(tax_html, "flags", "net-guarantee")
    assert "flag--warning" not in flags
    assert "flag--error" not in flags
    checks = re.findall(r'<li class="check" title="([A-Z_]+)">(.*?)</li>', flags, flags=re.S)
    assert [code for code, _ in checks] == [
        "RATES_NOT_PUBLISHED_FOR_YEAR",
        "SOCIAL_SECURITY_AGREEMENT_MAY_APPLY",
        "AUTO_ENROLMENT_MAY_APPLY",
        "APPRENTICESHIP_LEVY_MAY_APPLY",
    ]
    texts = [_text(text) for _, text in checks]
    rates = [text for text in texts if "Rates not yet published" in text]
    assert len(rates) == 1
    assert "UK rates for tax year 2027-28" in rates[0]
    assert "Turkish rates" not in " ".join(texts)
    for code, _ in checks:
        assert code not in _text(flags)


def test_assumptions_follow_the_pack_then_the_rest(
    tax_html: str, rows_of: Rows, section_of: Section
) -> None:
    section = section_of(tax_html, "assumptions", "disclaimer")
    pack = section[: section.index('id="further-assumptions"')]
    further = section[section.index('id="further-assumptions"') :]
    assert "The assumptions the reference pack asks for" in pack
    assert re.findall(r'<th scope="row">([^<]*)</th>', pack) == [
        "UK residence",
        "Which National Insurance applies",
        "Overseas Workday Relief",
        "England or Scotland",
        "Tax year",
    ]
    tax_year = rows_of(pack)["Tax year"]
    assert tax_year[0] == (
        "Year 1 is UK tax year 2026-27; the assignment runs to 2027-28. Rates not yet "
        "published are carried forward unchanged from the latest published year, which "
        "affects year 2."
    )
    for code in (
        "UK_RESIDENT_FULL_YEAR",
        "UK_NIC_APPLIES",
        "OWR_NOT_MODELLED",
        "ENGLAND_RATES",
        "RATES_UNCHANGED_LATER_YEARS",
    ):
        assert f'title="{code}"' in pack, code
        assert f'title="{code}"' not in further, code
    assert 'title="MODE_ILLUSTRATIVE_WHOLE_YEAR"' in further
    assert 'title="HYPO_TAX_OVERRIDE"' in further
    assert "_" not in _text(section)


def test_home_scheme_assumptions_name_the_certificate(
    client: Client, rows_of: Rows, section_of: Section
) -> None:
    token = _token(
        assumptions={"social_security": "HOME_SCHEME_AGREEMENT"},
        fx={"rate": "65.7", "as_of": "2026-10-01"},
        assignment={"length_years": 1},
    )
    html = client.get("/estimate", {"s": token}).content.decode()
    rows = rows_of(section_of(html, "assumptions", "disclaimer"))
    assert "requires a certificate of coverage" in rows["Which National Insurance applies"][0]
    assert rows["Tax year"][0] == "The assignment is UK tax year 2026-27."
    assert rows["Tax year"][1] == "Does the assignment start in UK tax year 2026-27?"


def test_the_calculate_it_link_recalculates_from_the_turkish_rules(
    client: Client, tax_html: str, section_of: Section
) -> None:
    guarantee = section_of(tax_html, "net-guarantee", "treatment")
    link = re.search(
        r'<a href="(/estimate\?s=[A-Za-z0-9_-]+)">Calculate it from the 2026', guarantee
    )
    assert link is not None
    assert "an indicative mid-September 2026 rate of 65.7" in _text(guarantee)
    further = section_of(tax_html, "further-assumptions", "disclaimer")
    assert link.group(1) in further
    inputs, rates_as_of = decode_scenario(link.group(1).split("s=", 1)[1])
    assert inputs.hypothetical_tax.method.value == "CALCULATED"
    assert inputs.fx is not None
    assert inputs.fx.source == "indicative"
    assert rates_as_of == REFERENCE_RATES_AS_OF
    html = client.get(link.group(1)).content.decode()
    assert "£34,212.20" in html
    assert "calculated under the Turkish rules at 65.7 lira to the pound" in html
    assert "Calculate it from the 2026 Turkish rules instead" not in html


def test_the_calculate_it_link_needs_a_rate_on_the_rates_date(client: Client) -> None:
    # Before the indicative rate's date, and with no rate of its own: no link.
    early = encode_scenario(
        ScenarioInput.model_validate(reference_example_data()), date(2026, 9, 14)
    )
    html = client.get("/estimate", {"s": early}).content.decode()
    assert "Calculate it from the 2026 Turkish rules instead" not in html
    # With a rate of its own the scenario's rate is used, whatever the date.
    own = encode_scenario(
        ScenarioInput.model_validate(
            reference_example_data() | {"fx": {"rate": "55.25", "as_of": "2026-09-01"}}
        ),
        date(2026, 9, 14),
    )
    html = client.get("/estimate", {"s": own}).content.decode()
    link = re.search(r'href="/estimate\?s=([A-Za-z0-9_-]+)">Calculate it', html)
    assert link is not None
    inputs, _ = decode_scenario(link.group(1))
    assert inputs.fx is not None
    assert inputs.fx.rate == 55.25
    assert "indicative mid-September" not in html


def test_no_calculate_it_link_when_the_tax_is_already_calculated(client: Client) -> None:
    token = _token(
        hypothetical_tax={"method": "CALCULATED"}, fx={"rate": "65.7", "as_of": "2026-10-01"}
    )
    html = client.get("/estimate", {"s": token}).content.decode()
    assert "Calculate it from the 2026 Turkish rules instead" not in html


def test_no_calculate_it_link_when_the_variant_is_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from teq_web.web import views

    def too_large(*args: object, **kwargs: object) -> str:
        raise ScenarioTooLargeError("too large", reason="too_large")

    monkeypatch.setattr(views, "encode_scenario", too_large)
    inputs = ScenarioInput.model_validate(reference_example_data())
    assert views._calculate_url(inputs, REFERENCE_RATES_AS_OF) is None


def test_the_marginal_cost_line_is_in_plain_words(
    tax_html: str, rows_of: Rows, section_of: Section
) -> None:
    rows = rows_of(section_of(tax_html, "per-year", "assumptions"))
    assert rows["Cost to the employer of £1 more net pay"][:2] == ["2.17", "2.17"]
    assert "one more net pound" not in tax_html


def test_the_refusal_page_keeps_codes_in_titles(client: Client) -> None:
    html = client.get(
        "/estimate", {"s": _token(route={"home": "TR", "host": "GB", "region": "SCT"})}
    ).content.decode()
    assert 'title="REGION_NOT_SUPPORTED"' in html
    assert 'title="TR to GB (ENG)"' in html
    assert "REGION_NOT_SUPPORTED" not in _text(html)
    assert "TR to GB" not in _text(html)


# --------------------------------------------------------------------------- tabs


def _tab_bar(html: str) -> list[tuple[str, str, bool]]:
    """The tab bar as (label, href, is current), in order."""
    bar = html[html.index('<nav class="tabs') : html.index("</nav>", html.index('class="tabs'))]
    return [
        (label, unescape(href), bool(current))
        for href, current, label in re.findall(
            r'<a href="([^"]*)"( aria-current="page")?>([^<]*)</a>', bar
        )
    ]


@pytest.mark.parametrize(
    ("tab", "current"),
    [("tax", "Tax"), ("immigration", "Immigration"), ("explain", "Explain"), ("", "Tax")],
)
def test_the_tab_bar_marks_the_current_tab(
    client: Client, reference_token: str, tab: str, current: str
) -> None:
    params = {"s": reference_token} | ({"tab": tab} if tab else {})
    html = client.get("/estimate", params).content.decode()
    bar = _tab_bar(html)
    assert [label for label, _, _ in bar] == ["Inputs", "Tax", "Immigration", "Explain"]
    assert [label for label, _, is_current in bar if is_current] == [current]
    assert bar[0][1] == f"/?s={reference_token}"
    for label, href, _ in bar[1:]:
        assert href == f"/estimate?s={reference_token}&tab={label.lower()}"


def test_an_unknown_tab_shows_the_tax_tab(client: Client, reference_token: str) -> None:
    html = client.get("/estimate", {"s": reference_token, "tab": "nonsense"}).content.decode()
    assert '<section id="net-guarantee"' in html
    assert [label for label, _, current in _tab_bar(html) if current] == ["Tax"]
    assert "nonsense" not in html


def test_the_tab_bar_keeps_the_tailoring_answers(client: Client, reference_token: str) -> None:
    params = {
        "s": reference_token,
        "tab": "explain",
        "sponsor_licence_held": "false",
        "dependants_adults": "1",
        "unrelated": "dropped",
    }
    html = client.get("/estimate", params).content.decode()
    for label, href, _ in _tab_bar(html)[1:]:
        assert href == (
            f"/estimate?s={reference_token}&tab={label.lower()}"
            "&sponsor_licence_held=false&dependants_adults=1"
        )
    assert "unrelated" not in html
    # Following the Immigration tab link keeps the tailored guidance.
    immigration = client.get(_tab_bar(html)[2][1]).content.decode()
    assert "£1,682" in immigration  # the licence fee, as the answers say no licence is held


def test_the_tailoring_form_lands_on_the_immigration_tab(immigration_html: str) -> None:
    form = immigration_html[immigration_html.index('action="/estimate"') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="tab" value="immigration">' in form


def test_entry_points_still_land_on_tab_less_results_urls(
    client: Client, form_data: dict[str, str], reference_token: str
) -> None:
    assert client.get("/example")["Location"] == f"/estimate?s={reference_token}"
    assert client.post("/", form_data)["Location"] == f"/estimate?s={reference_token}"
    token = _token(fx={"rate": "65.7", "as_of": "2026-10-01"})
    compare = client.get("/compare", {"s": token}).content.decode()
    for href in re.findall(r'href="(/estimate\?[^"]*)"', compare):
        assert "tab=" not in href


# --------------------------------------------------------------------------- the Immigration tab


def test_the_immigration_tab_leads_with_the_route_and_the_verified_date(
    immigration_html: str,
) -> None:
    text = _text(immigration_html)
    assert "Immigration: Turkey to England, Skilled Worker visa" in text
    assert "Guidance verified 8 October 2026." in text
    assert "GUIDANCE:TR-GB" not in immigration_html
    assert "reference pack" not in text.lower()
    assert "What we would need to tailor this further" not in text


def test_the_immigration_tab_puts_answers_before_questions(immigration_html: str) -> None:
    headings = re.findall(r'<h3 id="(imm-[a-z]+)">', immigration_html)
    assert headings == [
        "imm-process",
        "imm-documents",
        "imm-costs",
        "imm-timeline",
        "imm-family",
        "imm-employer",
    ]
    tailoring = immigration_html.index('<details class="tailoring"')
    assert immigration_html.index('id="imm-employer"') < tailoring
    assert immigration_html.index("Not answered") > tailoring
    assert tailoring < immigration_html.index("Sources and how each item was verified")


def test_the_steps_are_numbered_with_who_does_each(
    immigration_html: str, section_of: Section
) -> None:
    process = section_of(immigration_html, "imm-process", "imm-documents")
    steps = re.findall(r"<li><strong>([^<]+)</strong> <span class=\"muted\">\(([^)]+)\)", process)
    assert steps[0] == (
        "Define the job: occupation code and salary check",
        "Employer; typically 3 to 10 days",
    )
    assert steps[1][0] == "Assign the Certificate of Sponsorship"
    assert (
        "Home Office decision (outside the UK)",
        "Home Office; typically 15 to 21 days",
    ) in steps
    assert '<ol class="stages">' in process


def test_costs_and_timeline_are_unchanged(immigration_html: str, section_of: Section) -> None:
    costs = _text(section_of(immigration_html, "imm-costs", "imm-timeline"))
    assert "£3,165" in costs
    assert "£2,889" in costs
    assert "Applicant in law; employer by policy" in costs
    timeline = section_of(immigration_html, "imm-timeline", "imm-family")
    assert "about 6 to 16 weeks (43 to 112 days)" in timeline
    assert '<th scope="col">Typical time (an assumption)</th>' in timeline


def test_the_immigration_tab_shows_how_each_item_was_verified(immigration_html: str) -> None:
    text = _text(immigration_html)
    assert "(Official source)" in text
    assert "Third-party report, Skilled Worker visa: how much it costs (GOV.UK)" in text
    assert "Not re-verified." in text
    assert (
        "Third-party report: Reported by third parties and not yet confirmed on the official "
        "page; check it before relying on the figure."
    ) in text


def test_the_family_block_names_partner_and_children(
    client: Client, reference_token: str, immigration_html: str, section_of: Section
) -> None:
    family = _text(section_of(immigration_html, "imm-family", "imm-employer"))
    assert "Partner as a dependant." in family
    assert "Children as dependants." in family
    assert "No partner or children are applying as dependants on these answers" in family
    assert "Funds to show on these answers: £1,270" in family
    # Family entries are not repeated in the application-process table.
    process = section_of(immigration_html, "imm-process", "imm-documents")
    assert "Partner as a dependant" not in process
    tailored = client.get(
        "/estimate",
        {
            "s": reference_token,
            "tab": "immigration",
            "dependants_adults": "1",
            "dependants_children": "1",
        },
    ).content.decode()
    family = _text(section_of(tailored, "imm-family", "imm-employer"))
    assert "Visa application fee (partner)" in family
    assert "Immigration Health Surcharge (children)" in family
    assert "£1,552" in family
    assert "£1,270 main applicant + £285 partner + £315 first child" in family


def test_the_tailoring_form_is_collapsed_until_answered(
    client: Client, reference_token: str, immigration_html: str
) -> None:
    closed = re.search(
        r'<details class="tailoring"( open)?>\s*<summary>(.*?)</summary>', immigration_html
    )
    assert closed is not None
    assert closed.group(1) is None
    assert closed.group(2) == (
        "Tailor this guidance (assumed: applying from outside the UK, sponsor licence held, "
        "2-year visa, no dependants, lives in a TB-test country such as Turkey)"
    )
    answered = client.get(
        "/estimate",
        {"s": reference_token, "tab": "immigration", "sponsor_licence_held": "false"},
    ).content.decode()
    opened = re.search(r'<details class="tailoring"( open)?>\s*<summary>(.*?)</summary>', answered)
    assert opened is not None
    assert opened.group(1) == " open"
    assert opened.group(2).startswith("Tailor this guidance (answered: applying from outside")
    assert "no sponsor licence yet" in opened.group(2)


def test_immigration_warnings_keep_their_codes_in_titles(
    client: Client, reference_token: str
) -> None:
    html = client.get(
        "/estimate", {"s": reference_token, "tab": "immigration", "visa_length_years": "6"}
    ).content.decode()
    assert 'title="VISA_LENGTH_EXCEEDS_SINGLE_GRANT"' in html
    assert "VISA_LENGTH_EXCEEDS_SINGLE_GRANT" not in _text(html)
    assert "longer than a single Skilled Worker grant" in html


def _visible_outside(html: str, *skipped: tuple[str, str]) -> str:
    """Visible text of ``html`` without the sections between each pair of ids."""
    for start, end in skipped:
        html = html[: html.index(f'id="{start}"')] + html[html.index(f'id="{end}"') :]
    return _text(html)


@pytest.mark.parametrize("tab", ["tax", "immigration", "explain"])
def test_no_codes_in_the_visible_text_of_any_tab(
    client: Client, reference_token: str, tab: str
) -> None:
    from teq_engine import Code, Treatment

    html = client.get("/estimate", {"s": reference_token, "tab": tab}).content.decode()
    # The trace and provenance on the Explain tab are where codes and identifiers belong.
    text = _visible_outside(html, ("trace", "disclaimer")) if tab == "explain" else _text(html)
    codes = {code.value for code in Code} | {treatment.value for treatment in Treatment}
    codes |= {"VISA_LENGTH_EXCEEDS_SINGLE_GRANT", "IMMIGRATION_CONTENT_STALE"}
    assert [code for code in sorted(codes) if code in text] == []
    assert "TR to GB" not in text
    assert "GUIDANCE:TR-GB" not in text


# --------------------------------------------------------------------------- the Explain tab


def test_the_explain_tab_tells_both_stories(
    explain_html: str, reference_result, section_of: Section
) -> None:
    from teq_engine import reference_example
    from teq_guidance import build_panel
    from teq_web.narration.figures import unknown_numbers, unknown_panel_numbers
    from teq_web.scenarios.services import default_tailoring

    def paragraphs(start: str, end: str) -> list[str]:
        found = re.findall(r"<p>(.*?)</p>", section_of(explain_html, start, end), flags=re.S)
        return [unescape(paragraph) for paragraph in found]

    tax = paragraphs("narrative", "immigration-narrative")
    immigration = paragraphs("immigration-narrative", "trace")
    assert 5 <= len(tax) <= 8
    assert len(immigration) == 4
    assert immigration[0].startswith("The employer moves first.")
    assert "£3,165" in immigration[2]
    assert unknown_numbers(tax, reference_result) == set()
    panel = build_panel(default_tailoring(reference_example()), as_of=date(2026, 10, 8))
    assert unknown_panel_numbers(immigration, panel) == set()
    assert "trial and error" not in explain_html
    assert "the step-by-step iteration the reference pack describes" in explain_html
