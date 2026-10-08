"""Shared fixtures for the engine tests."""

from __future__ import annotations

import copy
import os
from datetime import date
from typing import Any

import pytest
from hypothesis import HealthCheck, settings

from teq_engine import BundledProvider, default_provider
from teq_engine.ratesets.schemas import (
    TrIncomeTaxData,
    TrSgkData,
    TrStampData,
    UkBenefitRulesData,
    UkIncomeTaxData,
    UkNicData,
)
from teq_engine.reference import REFERENCE_RATES_AS_OF, reference_example_data

settings.register_profile(
    "default",
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("ci", max_examples=400, deadline=None)
settings.register_profile("quick", max_examples=25, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

UK_DATE = date(2026, 4, 6)
TR_DATE = date(2026, 1, 1)


@pytest.fixture(scope="session")
def provider() -> BundledProvider:
    return default_provider()


@pytest.fixture(scope="session")
def it_rates(provider: BundledProvider) -> UkIncomeTaxData:
    rs = provider.get("UK-ENG", "UK_INCOME_TAX", UK_DATE)
    assert rs is not None
    return rs.data_as(UkIncomeTaxData)


@pytest.fixture(scope="session")
def nic_rates(provider: BundledProvider) -> UkNicData:
    rs = provider.get("UK", "UK_NIC", UK_DATE)
    assert rs is not None
    return rs.data_as(UkNicData)


@pytest.fixture(scope="session")
def benefit_rules(provider: BundledProvider) -> UkBenefitRulesData:
    rs = provider.get("UK", "UK_BENEFIT_RULES", UK_DATE)
    assert rs is not None
    return rs.data_as(UkBenefitRulesData)


@pytest.fixture(scope="session")
def tr_it(provider: BundledProvider) -> TrIncomeTaxData:
    rs = provider.get("TR", "TR_INCOME_TAX", TR_DATE)
    assert rs is not None
    return rs.data_as(TrIncomeTaxData)


@pytest.fixture(scope="session")
def sgk(provider: BundledProvider) -> TrSgkData:
    rs = provider.get("TR", "TR_SGK", TR_DATE)
    assert rs is not None
    return rs.data_as(TrSgkData)


@pytest.fixture(scope="session")
def stamp(provider: BundledProvider) -> TrStampData:
    rs = provider.get("TR", "TR_STAMP", TR_DATE)
    assert rs is not None
    return rs.data_as(TrStampData)


@pytest.fixture
def ref_data() -> dict[str, Any]:
    """A fresh, mutable copy of the reference inputs as plain data."""
    return copy.deepcopy(reference_example_data())


@pytest.fixture
def rates_as_of() -> date:
    return REFERENCE_RATES_AS_OF
