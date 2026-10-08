"""The estimate service and the URL encoding of scenarios."""

from __future__ import annotations

import base64
import json
import random
import zlib
from datetime import date

import pytest

from teq_engine import REFERENCE_RATES_AS_OF, ScenarioInput, reference_example
from teq_engine.reference import reference_example_data
from teq_web.scenarios.services import (
    MAX_ENCODED_LENGTH,
    ScenarioLinkError,
    ScenarioTooLargeError,
    decode_scenario,
    default_tailoring,
    encode_scenario,
    estimate,
    immigration_panel,
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
