"""The results page, the reference example and the unsupported-route page."""

from __future__ import annotations

import re

import pytest
from django.test import Client

from teq_engine import REFERENCE_RATES_AS_OF, ScenarioInput
from teq_engine.reference import reference_example_data
from teq_web.scenarios.services import encode_scenario

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


def test_example_redirects_to_the_reference_results(client: Client, reference_token: str) -> None:
    response = client.get("/example")
    assert response.status_code == 302
    assert response["Location"] == f"/estimate?s={reference_token}"


def test_reference_results_reproduce_the_pack(reference_html: str) -> None:
    for figure in REFERENCE_FIGURES:
        assert figure in reference_html, figure
    assert "£188,676" in reference_html
    assert "2.10 times" in reference_html
    assert "Two-year total" in reference_html


def test_sections_follow_the_pack_order(reference_html: str) -> None:
    markers = [
        'id="flags"',
        'id="net-guarantee"',
        'id="treatment"',
        'id="gross-up"',
        'id="employer-charges"',
        'id="per-year"',
        'id="assumptions"',
        'id="immigration"',
        'id="narrative"',
        'id="trace"',
        'id="provenance"',
    ]
    positions = [reference_html.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_flags_put_warnings_before_information(reference_html: str) -> None:
    flags = reference_html[
        reference_html.index('id="flags"') : reference_html.index('id="net-guarantee"')
    ]
    assert flags.index("flag--warning") < flags.index("flag--info")
    assert "Social security agreement may apply" in flags


def test_treatment_table_uses_the_three_labels(reference_html: str) -> None:
    table = reference_html[
        reference_html.index('id="treatment"') : reference_html.index('id="gross-up"')
    ]
    for label in ("Taxable cash", "Taxable benefit in kind", "Exempt"):
        assert f"<strong>{label}</strong>" in table
    assert "Within the £8,000 cap per move, so all of it is exempt." in table


def test_gross_up_shows_segment_rate_and_exact_figures(reference_html: str) -> None:
    section = _text(
        reference_html[
            reference_html.index('id="gross-up"') : reference_html.index('id="employer-charges"')
        ]
    )
    assert "IT 45% + NIC 2%" in section
    assert "47%" in section
    assert "£127,761.51" in section
    assert "£127,762" in section
    assert "£66,000.26" in section


def test_per_year_table_has_both_decompositions_and_the_total(reference_html: str) -> None:
    section = _text(
        reference_html[
            reference_html.index('id="per-year"') : reference_html.index('id="assumptions"')
        ]
    )
    assert "What the employer pays" in section
    assert "Where the money goes" in section
    assert "£369,352" in section
    assert "Both decompositions give the same total in every year." in section


def test_immigration_block_is_separate_with_payers_and_subtotals(reference_html: str) -> None:
    panel = reference_html[
        reference_html.index('id="immigration"') : reference_html.index('id="narrative"')
    ]
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
    response = client.get(
        "/estimate",
        {"s": reference_token, "sponsor_licence_held": "false", "visa_length_years": "2"},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "188,676" in html  # the employment cost is unchanged
    assert "£1,682" in html  # the licence fee appears in the immigration costs
    assert "Apply for a sponsor licence" in html


def test_invalid_tailoring_does_not_break_the_page(client: Client, reference_token: str) -> None:
    response = client.get("/estimate", {"s": reference_token, "visa_length_years": "lots"})
    assert response.status_code == 200
    assert "Some answers could not be used" in response.content.decode()


def test_narrative_and_trace_drawer(reference_html: str) -> None:
    narrative = reference_html[
        reference_html.index('id="narrative"') : reference_html.index('id="trace"')
    ]
    assert narrative.count("<p>") >= 5
    assert "2.10 times the salary" in narrative
    trace = reference_html[
        reference_html.index('id="trace"') : reference_html.index('id="provenance"')
    ]
    assert "<details>" in trace
    assert 'id="t6"' in trace
    assert "PAYE81740" in trace


def test_provenance_disclaimer_and_links(
    reference_html: str, reference_token: str, reference_result
) -> None:
    assert reference_result.inputs_hash in reference_html
    assert "UK_INCOME_TAX:2026-27:v1" in reference_html
    assert "8 October 2026" in reference_html
    assert "An illustration, not tax or immigration advice" in reference_html
    assert f'href="/?s={reference_token}"' in reference_html
    assert f"/api/v1/estimates?s={reference_token}" in reference_html


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
    assert "HOME_SCHEME_CERTIFICATE_REQUIRED" in html
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


def test_unknown_page_is_a_friendly_404(client: Client) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "Page not found" in response.content.decode()


def test_scenario_refused_as_given_is_a_422_page(client: Client) -> None:
    token = _token(fx={"rate": "55.25", "as_of": "2026-11-01"})
    response = client.get("/estimate", {"s": token})
    assert response.status_code == 422
    assert "This scenario cannot be calculated as given" in response.content.decode()
