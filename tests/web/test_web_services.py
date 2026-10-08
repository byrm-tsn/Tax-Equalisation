"""The estimate service and the URL encoding of scenarios."""

from __future__ import annotations

import base64
import json
import random
import zlib
from datetime import date

import pytest

from teq_engine import ENGINE_VERSION, REFERENCE_RATES_AS_OF, ScenarioInput, reference_example
from teq_engine.reference import reference_example_data
from teq_web.scenarios.services import (
    MAX_ENCODED_LENGTH,
    RATES_DATE_MAX,
    RATES_DATE_MIN,
    ScenarioLinkError,
    ScenarioTooLargeError,
    decode_scenario,
    default_tailoring,
    encode_scenario,
    estimate,
    immigration_panel,
    rates_date_problem,
    read_scenario_link,
)


def _token(payload: object) -> str:
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(zlib.compress(raw)).rstrip(b"=").decode()


def test_estimate_wraps_the_engine(reference_result) -> None:
    result = estimate(reference_example(), rates_as_of=REFERENCE_RATES_AS_OF)
    assert result.to_json() == reference_result.to_json()
    assert result.year(1).line("TOTAL_EMPLOYER_COST") == 188676


def test_round_trip_keeps_inputs_and_rates_date() -> None:
    inputs = reference_example()
    token = encode_scenario(inputs, date(2026, 11, 3))
    decoded, rates_as_of = decode_scenario(token)
    assert decoded == inputs
    assert decoded.inputs_hash() == inputs.inputs_hash()
    assert rates_as_of == date(2026, 11, 3)


def test_token_is_url_safe_and_compact(reference_token: str) -> None:
    assert set(reference_token) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert len(reference_token) < 1024


def test_encoding_is_deterministic() -> None:
    assert encode_scenario(reference_example(), REFERENCE_RATES_AS_OF) == encode_scenario(
        reference_example(), REFERENCE_RATES_AS_OF
    )


def _noise(seed: int, length: int = 80) -> str:
    """Deterministic, incompressible text (CJK characters chosen pseudo-randomly)."""
    generator = random.Random(seed)
    return "".join(chr(0x4E00 + generator.randrange(20000)) for _ in range(length))


def test_encode_refuses_a_scenario_over_the_size_cap() -> None:
    data = reference_example_data()
    # Fifty items with incompressible labels: valid inputs, too large for a link.
    data["items"] = [
        {
            "id": f"item-{i}",
            "kind": "OTHER",
            "label": _noise(i),
            "amount": f"{i + 1}.00",
            "frequency": "ANNUAL",
            "years": "ALL",
        }
        for i in range(50)
    ]
    inputs = ScenarioInput.model_validate(data)
    with pytest.raises(ScenarioTooLargeError, match="too large"):
        encode_scenario(inputs, REFERENCE_RATES_AS_OF)


def test_decode_refuses_an_oversized_token() -> None:
    with pytest.raises(ScenarioTooLargeError):
        decode_scenario("A" * (MAX_ENCODED_LENGTH + 1))


def test_decode_refuses_a_zip_bomb() -> None:
    bomb = base64.urlsafe_b64encode(zlib.compress(b" " * 5_000_000, 9)).decode()
    assert len(bomb) <= MAX_ENCODED_LENGTH
    with pytest.raises(ScenarioTooLargeError):
        decode_scenario(bomb)


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("", "empty"),
        ("not*base64!", "not_base64"),
        (base64.urlsafe_b64encode(b"plain text").decode(), "not_zlib"),
        (base64.urlsafe_b64encode(zlib.compress(b"{not json")).decode(), "not_json"),
        (_token({"v": 99, "rates_as_of": "2026-10-08", "inputs": {}}), "version"),
        (_token({"v": 1, "rates_as_of": "yesterday", "inputs": {}}), "bad_date"),
        (_token({"v": 1, "rates_as_of": "2026-10-08", "inputs": {"route": {}}}), "invalid_inputs"),
    ],
)
def test_decode_reports_damaged_links_plainly(token: str, reason: str) -> None:
    with pytest.raises(ScenarioLinkError) as caught:
        decode_scenario(token)
    assert caught.value.reason == reason
    assert caught.value.message.endswith((".", ")."))


def test_decode_refuses_characters_outside_url_safe_base64(reference_token: str) -> None:
    for suffix in ('"><svg/onload=alert(1)>', "'", " x", "+", "/", "%"):
        with pytest.raises(ScenarioLinkError) as caught:
            decode_scenario(reference_token + suffix)
        assert caught.value.reason == "not_base64", suffix


def test_decode_refuses_data_after_the_compressed_stream(reference_token: str) -> None:
    raw = base64.urlsafe_b64decode(reference_token + "=" * (-len(reference_token) % 4))
    tampered = base64.urlsafe_b64encode(raw + b"GARBAGE").rstrip(b"=").decode()
    with pytest.raises(ScenarioLinkError) as caught:
        decode_scenario(tampered)
    assert caught.value.reason == "trailing_data"


def test_decode_tolerates_base64_padding(reference_token: str) -> None:
    padded = reference_token + "=" * (-len(reference_token) % 4)
    assert decode_scenario(padded) == decode_scenario(reference_token)


@pytest.mark.parametrize(
    "rates_as_of", [date(1, 1, 1), date(1999, 12, 31), date(2101, 1, 1), date(9999, 12, 31)]
)
def test_decode_refuses_a_rates_date_out_of_range(rates_as_of: date) -> None:
    token = encode_scenario(reference_example(), rates_as_of)
    with pytest.raises(ScenarioLinkError) as caught:
        decode_scenario(token)
    assert caught.value.reason == "date_out_of_range"
    assert caught.value.message == (
        "This link cannot be used: the rates date must be between 1 January 2000 and "
        "31 December 2100."
    )


def test_rates_date_bounds_are_inclusive() -> None:
    assert rates_date_problem(RATES_DATE_MIN) is None
    assert rates_date_problem(RATES_DATE_MAX) is None
    for rates_as_of in (RATES_DATE_MIN, RATES_DATE_MAX):
        assert decode_scenario(encode_scenario(reference_example(), rates_as_of))[1] == rates_as_of


def test_a_link_records_the_engine_version(reference_token: str) -> None:
    padding = "=" * (-len(reference_token) % 4)
    raw = zlib.decompress(base64.urlsafe_b64decode(reference_token + padding))
    assert json.loads(raw)["engine_version"] == ENGINE_VERSION
    link = read_scenario_link(reference_token)
    assert link.engine_version == ENGINE_VERSION
    assert link.engine_notice is None
    assert link.token == reference_token  # round-trips unchanged


def test_a_link_from_another_engine_version_is_read_with_a_notice() -> None:
    token = _token(
        {
            "v": 1,
            "rates_as_of": "2026-10-08",
            "engine_version": "0.0.1",
            "inputs": reference_example_data(),
        }
    )
    link = read_scenario_link(token)
    assert link.inputs == reference_example()
    assert link.engine_version == "0.0.1"
    assert link.engine_notice == (
        f"Recalculated under engine {ENGINE_VERSION}; the link was created under engine 0.0.1."
    )
    # The canonical form keeps the version the link was made under.
    assert read_scenario_link(link.token).engine_version == "0.0.1"


def test_a_link_without_an_engine_version_still_works() -> None:
    token = encode_scenario(reference_example(), REFERENCE_RATES_AS_OF, engine_version=None)
    link = read_scenario_link(token)
    assert link.engine_version is None
    assert link.engine_notice is None
    assert link.token == token


@pytest.mark.parametrize("version", [7, "", "x" * 41, "1.0<script>"])
def test_a_malformed_engine_version_is_damage(version: object) -> None:
    payload = {"v": 1, "rates_as_of": "2026-10-08", "inputs": reference_example_data()}
    payload["engine_version"] = version
    with pytest.raises(ScenarioLinkError) as caught:
        read_scenario_link(_token(payload))
    assert caught.value.reason == "bad_engine_version"


def test_decode_revalidates_the_inputs() -> None:
    data = reference_example_data()
    data["hypothetical_tax"] = {"method": "OVERRIDE", "override": "95000.00"}
    token = _token({"v": 1, "rates_as_of": "2026-10-08", "inputs": data})
    with pytest.raises(ScenarioLinkError, match="below the annual salary"):
        decode_scenario(token)


def test_default_tailoring_follows_the_assignment_length() -> None:
    data = reference_example_data()
    data["assignment"] = {"length_years": 3}
    answers = default_tailoring(ScenarioInput.model_validate(data))
    assert answers.visa_length_years == 3
    assert answers.sponsor_licence_held is True


def test_invalid_tailoring_falls_back_to_the_defaults() -> None:
    panel, problems = immigration_panel(
        reference_example(), {"visa_length_years": "many"}, as_of=REFERENCE_RATES_AS_OF
    )
    assert problems
    assert panel["costs"]["subtotals_display"]["employer_mandatory"] == "£3,165"
