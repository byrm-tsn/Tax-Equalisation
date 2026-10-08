"""The closed set of compensation-item treatments and the default treatment per kind.

A treatment says how an item reaches the employee and therefore how it is taxed:

* ``GROSS_EQUALISED``: a gross amount (salary, bonus) that joins the net guarantee
  after the hypothetical home tax is taken off it.
* ``NET_CASH``: a cash allowance promised net (the pack's cost-of-living allowance).
  The full amount joins the net cash target and is grossed up; Class 1 NICs apply.
* ``TAXABLE_BIK``: a benefit in kind (housing). Its cash equivalent enters the
  income-tax base inside the gross-up; there are no employee NICs on it and the
  employer pays Class 1A. The cash equivalent is an input, not a statutory valuation.
* ``EXEMPT_CAPPED``: qualifying relocation expenses, exempt up to a cap per move
  (HMRC: relocation expenses and benefits, £8,000 per move). The cap is consumed
  cumulatively across the assignment; any excess becomes a ``TAXABLE_BIK`` line.
* ``EXEMPT``: employer cost with no tax effect on the employee.
* ``EMPLOYER_ONLY``: employer cost outside the employee's pay (for example an
  employer pension contribution); no tax effect on the employee.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "DEFAULT_TREATMENT",
    "DISPLAY_LABEL",
    "NIC_CLASS",
    "ItemKind",
    "NicClass",
    "Treatment",
    "default_treatment",
    "display_label",
]


class Treatment(StrEnum):
    """How a compensation item is taxed and costed."""

    GROSS_EQUALISED = "GROSS_EQUALISED"
    NET_CASH = "NET_CASH"
    TAXABLE_BIK = "TAXABLE_BIK"
    EXEMPT_CAPPED = "EXEMPT_CAPPED"
    EXEMPT = "EXEMPT"
    EMPLOYER_ONLY = "EMPLOYER_ONLY"


class ItemKind(StrEnum):
    """What a compensation item is."""

    SALARY = "SALARY"
    BONUS = "BONUS"
    COLA = "COLA"
    HOUSING = "HOUSING"
    RELOCATION = "RELOCATION"
    SCHOOL_FEES = "SCHOOL_FEES"
    HOME_LEAVE = "HOME_LEAVE"
    PENSION_EMPLOYER = "PENSION_EMPLOYER"
    OTHER = "OTHER"


class NicClass(StrEnum):
    """Which class of UK National Insurance an item attracts."""

    CLASS_1 = "CLASS_1"
    CLASS_1A = "CLASS_1A"


DEFAULT_TREATMENT: Final[dict[ItemKind, Treatment]] = {
    ItemKind.SALARY: Treatment.GROSS_EQUALISED,
    ItemKind.BONUS: Treatment.GROSS_EQUALISED,
    ItemKind.COLA: Treatment.NET_CASH,
    ItemKind.HOUSING: Treatment.TAXABLE_BIK,
    ItemKind.RELOCATION: Treatment.EXEMPT_CAPPED,
    ItemKind.SCHOOL_FEES: Treatment.TAXABLE_BIK,
    ItemKind.HOME_LEAVE: Treatment.TAXABLE_BIK,
    ItemKind.PENSION_EMPLOYER: Treatment.EMPLOYER_ONLY,
    # An unclassified item is treated as a net cash allowance: the most expensive
    # reading, so an omission never understates cost. Override it when known.
    ItemKind.OTHER: Treatment.NET_CASH,
}

# The pack's three labels, mapped from the finer treatment set.
DISPLAY_LABEL: Final[dict[Treatment, str]] = {
    Treatment.GROSS_EQUALISED: "Taxable cash",
    Treatment.NET_CASH: "Taxable cash",
    Treatment.TAXABLE_BIK: "Taxable benefit in kind",
    Treatment.EXEMPT_CAPPED: "Exempt",
    Treatment.EXEMPT: "Exempt",
    Treatment.EMPLOYER_ONLY: "Exempt",
}

NIC_CLASS: Final[dict[Treatment, NicClass | None]] = {
    Treatment.GROSS_EQUALISED: NicClass.CLASS_1,
    Treatment.NET_CASH: NicClass.CLASS_1,
    Treatment.TAXABLE_BIK: NicClass.CLASS_1A,
    Treatment.EXEMPT_CAPPED: None,
    Treatment.EXEMPT: None,
    Treatment.EMPLOYER_ONLY: None,
}


def default_treatment(kind: ItemKind) -> Treatment:
    """Return the default treatment for an item kind (overridable per item)."""
    return DEFAULT_TREATMENT[kind]


def display_label(treatment: Treatment) -> str:
    """Return the pack's display label (taxable cash, benefit in kind, exempt)."""
    return DISPLAY_LABEL[treatment]
