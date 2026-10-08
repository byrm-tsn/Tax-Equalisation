"""Shared fixtures for the immigration guidance tests."""

from __future__ import annotations

import json
from importlib import resources

import pytest

from teq_guidance import GuidancePack, TailoringAnswers, load_pack


@pytest.fixture(scope="session")
def pack() -> GuidancePack:
    return load_pack("TR-GB")


@pytest.fixture
def raw_pack() -> dict[str, object]:
    """A fresh, mutable copy of the bundled JSON for validation tests."""
    text = (
        resources.files("teq_guidance")
        .joinpath("data", "tr_gb_skilled_worker.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(text)
    assert isinstance(data, dict)
    return data


@pytest.fixture
def reference() -> TailoringAnswers:
    """Visa 2 years, outside the UK, medium or large sponsor, licence held, no
    dependants, resident in Turkey (a TB-test country)."""
    return TailoringAnswers.reference_example()
