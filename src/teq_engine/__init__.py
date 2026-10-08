"""Tax-equalisation calculation engine.

Pure functions over versioned rate data: no Django, no I/O during a calculation, no
clock. The public entry point is :func:`calculate`::

    from teq_engine import calculate, default_provider, reference_example
    from teq_engine.reference import REFERENCE_RATES_AS_OF

    result = calculate(reference_example(), default_provider(), rates_as_of=REFERENCE_RATES_AS_OF)
    result.year(1).line("TOTAL_EMPLOYER_COST")  # Decimal('188676')
"""

from teq_engine._version import __version__
from teq_engine.calculator import DISCLAIMER_VERSION, ENGINE_VERSION, SCHEMA_VERSION, calculate
from teq_engine.errors import (
    EngineError,
    EngineInvariantError,
    RateSetError,
    RatesUnavailableError,
    ScenarioValidationError,
    SolverError,
    SupportedRoute,
    UnsupportedRegionError,
    UnsupportedRouteError,
)
from teq_engine.money import RoundingPolicy
from teq_engine.ratesets.provider import BundledProvider, RateSetProvider, default_provider
from teq_engine.ratesets.schemas import RateCategory, RateSet
from teq_engine.reference import REFERENCE_RATES_AS_OF, reference_example
from teq_engine.routes.registry import resolve_route, supported_routes
from teq_engine.treatments import ItemKind, Treatment
from teq_engine.types import (
    Assumption,
    Assumptions,
    CalculationResult,
    CompensationItem,
    FxSnapshot,
    GrossUpResult,
    HypoTaxResult,
    HypotheticalTaxSpec,
    Line,
    LineCode,
    ScenarioInput,
    TraceStep,
    Warning,
    YearResult,
)
from teq_engine.warnings import CATALOGUE, Code, catalogue_as_dicts

__all__ = [
    "CATALOGUE",
    "DISCLAIMER_VERSION",
    "ENGINE_VERSION",
    "REFERENCE_RATES_AS_OF",
    "SCHEMA_VERSION",
    "Assumption",
    "Assumptions",
    "BundledProvider",
    "CalculationResult",
    "Code",
    "CompensationItem",
    "EngineError",
    "EngineInvariantError",
    "FxSnapshot",
    "GrossUpResult",
    "HypoTaxResult",
    "HypotheticalTaxSpec",
    "ItemKind",
    "Line",
    "LineCode",
    "RateCategory",
    "RateSet",
    "RateSetError",
    "RateSetProvider",
    "RatesUnavailableError",
    "RoundingPolicy",
    "ScenarioInput",
    "ScenarioValidationError",
    "SolverError",
    "SupportedRoute",
    "TraceStep",
    "Treatment",
    "UnsupportedRegionError",
    "UnsupportedRouteError",
    "Warning",
    "YearResult",
    "__version__",
    "calculate",
    "catalogue_as_dicts",
    "default_provider",
    "reference_example",
    "resolve_route",
    "supported_routes",
]
