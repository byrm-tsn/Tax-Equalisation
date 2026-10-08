"""The results page, the reference example and the unsupported-route page."""

from __future__ import annotations

import base64
import json
import random
import re
import zlib
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from django.test import Client
from django.utils.http import urlencode

from teq_engine import ENGINE_VERSION, REFERENCE_RATES_AS_OF, ScenarioInput
from teq_engine.reference import reference_example_data
from teq_web.scenarios.services import MAX_ENCODED_LENGTH, ScenarioTooLargeError, encode_scenario

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


def _results_from_form(client: Client, form_data: dict[str, str], **changes: str) -> str:
    """Post the form with ``changes`` and return the results page it redirects to."""
    response = client.post("/", form_data | changes)
    assert response.status_code == 303, response.content.decode()[:2000]
    page = client.get(response["Location"])
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
    reference_html: str, rows_of: Rows, section_of: Section
) -> None:
    for figure in REFERENCE_FIGURES:
        assert figure in reference_html, figure
    assert "£188,676" in reference_html
    assert "2.10 times" in reference_html
    assert "Two-year total" in reference_html
    # The pack's figures sit on the right lines of the year-by-year table, not just anywhere.
    rows = rows_of(section_of(reference_html, "per-year", "assumptions"))
    assert rows["Gross cash pay (grossed up)"][0] == "£127,762"
    assert rows["Income tax"][0] == "£57,196"
    assert rows["Employee National Insurance"][0] == "£4,566"
    assert rows["Employer National Insurance"][0] == "£18,414"
    assert rows["Class 1A National Insurance on benefits"][0] == "£4,500"
    assert rows["Total employer cost"] == ["£188,676", "£180,676", "£369,352"]
    assert rows["Total (second decomposition)"] == ["£188,676", "£180,676", "£369,352"]


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


def test_treatment_table_labels_each_row(
    reference_html: str, rows_of: Rows, section_of: Section
) -> None:
    rows = rows_of(section_of(reference_html, "treatment", "gross-up"))
    # Columns: amount, paid in, treatment (label, part, code), National Insurance, meaning.
    assert rows["Base salary"][2:4] == ["Taxable cash GROSS_EQUALISED", "Class 1"]
    assert rows["Cost-of-living allowance"][2:4] == ["Taxable cash NET_CASH", "Class 1"]
    assert rows["Housing (rent paid by the employer)"][2:4] == [
        "Taxable benefit in kind TAXABLE_BIK",
        "Class 1A",
    ]
    assert rows["Relocation"][:4] == [
        "£8,000 one-off",
        "year 1",
        "Exempt £8,000 EXEMPT_CAPPED",
        "None",
    ]
    assert rows["Relocation"][4] == "Within the £8,000 cap per move, so all of it is exempt."
    assert "Relocation (above the cap)" not in rows


def test_relocation_excess_is_split_into_its_two_treatments(
    client: Client, form_data: dict[str, str], rows_of: Rows, section_of: Section
) -> None:
    html = _results_from_form(client, form_data, relocation_amount="10000")
    rows = rows_of(section_of(html, "treatment", "gross-up"))
    assert rows["Relocation"][:4] == [
        "£10,000 one-off",
        "year 1",
        "Exempt £8,000 EXEMPT_CAPPED",
        "None",
    ]
    excess = rows["Relocation (above the cap)"]
    assert excess[1:4] == [
        "year 1",
        "Taxable benefit in kind £2,000 TAXABLE_BIK",
        "Class 1A on the excess",
    ]
    assert "Class 1A" in excess[4]


def test_home_scheme_claims_no_uk_national_insurance(
    client: Client, form_data: dict[str, str], rows_of: Rows, section_of: Section
) -> None:
    html = _results_from_form(
        client,
        form_data,
        social_security="HOME_SCHEME_AGREEMENT",
        fx_rate="55.25",
        fx_date="2026-10-01",
        relocation_amount="10000",
    )
    table = section_of(html, "treatment", "gross-up")
    narrative = _text(section_of(html, "narrative", "trace"))
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


def test_a_valid_link_is_echoed_unchanged(client: Client, reference_token: str) -> None:
    html = client.get("/estimate", {"s": reference_token}).content.decode()
    assert f'<input type="hidden" name="s" value="{reference_token}">' in html
    assert f'href="/?s={reference_token}"' in html
    assert f'href="/compare?s={reference_token}"' in html
    echoed = set(re.findall(r"[?&;]s=([A-Za-z0-9_-]+)", html))
    echoed |= set(re.findall(r'name="s" value="([^"]*)"', html))
    assert echoed == {reference_token}


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
    html = client.get("/estimate", {"s": crafted}).content.decode()
    assert crafted not in html
    assert f'name="s" value="{canonical}"' in html


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


def test_a_link_from_this_engine_version_has_no_notice(reference_html: str) -> None:
    assert "the link was created under engine" not in reference_html


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
    assert flags.count("RATES_NOT_PUBLISHED_FOR_YEAR</span>") == 2
    text = _text(flags)
    assert text.count("Rates not yet published (years 2 to 10)") == 2
    assert (
        "UK rates for tax years 2027-28 to 2035-36 are not published; the 2026-27 rates "
        "have been carried forward."
    ) in text
    assert (
        "Turkish rates for 2027 to 2035 are not published; the 2026 rates have been carried "
        "forward."
    ) in text
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
    assert flags.count("RELOCATION_EXCESS_TAXABLE</span>") == 1
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
    response = client.get(
        "/estimate",
        {
            "s": reference_token,
            "visa_length_years": "<script>x</script>",
            "sponsor_licence_held": '"><b>bold</b>',
        },
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Some answers could not be used" in html
    assert "<script>" not in html
    assert "<b>bold" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def test_unknown_page_is_a_friendly_404(client: Client) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "Page not found" in response.content.decode()


def test_scenario_refused_as_given_is_a_422_page(client: Client) -> None:
    token = _token(fx={"rate": "55.25", "as_of": "2026-11-01"})
    response = client.get("/estimate", {"s": token})
    assert response.status_code == 422
    assert "This scenario cannot be calculated as given" in response.content.decode()
