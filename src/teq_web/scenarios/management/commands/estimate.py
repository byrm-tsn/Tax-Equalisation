"""``manage.py estimate``: the same estimate as the web page and the API, in the terminal.

    python src/manage.py estimate --example             # the reference example as a table
    python src/manage.py estimate --example --json      # the full result as JSON
    python src/manage.py estimate --input scenario.json # a scenario file

A scenario file holds the inputs exactly as ``POST /api/v1/estimates`` takes them; its
``rates_as_of`` (if any) is used and ``options`` is ignored. Every refusal is a one-line
``CommandError`` (a scenario that fails validation lists each problem on its own line).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import TypeAdapter, ValidationError

from teq_engine import (
    REFERENCE_RATES_AS_OF,
    CalculationResult,
    EngineError,
    LineCode,
    ScenarioInput,
    UnsupportedRouteError,
    reference_example,
)
from teq_engine.types import IsoDate
from teq_web.formatting import gbp
from teq_web.scenarios.capability import supported_route_dicts
from teq_web.scenarios.services import estimate, rates_date_problem, today

_RATIOS = (LineCode.MULTIPLE_OF_SALARY, LineCode.MARGINAL_COST_PER_NET_POUND)

_ISO_DATE: TypeAdapter[date] = TypeAdapter(IsoDate)


def _parse_date(value: object, *, source: str) -> date:
    """A rates date, by the same rules as the API; ``source`` names it in the error."""
    try:
        parsed = _ISO_DATE.validate_python(value)
    except ValidationError as exc:
        raise CommandError(f"{source} must be a date such as 2026-10-08, not {value!r}") from exc
    problem = rates_date_problem(parsed)
    if problem is not None:
        raise CommandError(f"{source}: {problem}")
    return parsed


class Command(BaseCommand):
    help = "Calculate a tax-equalisation estimate and print it as a table or as JSON."

    def add_arguments(self, parser: CommandParser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument(
            "--example", action="store_true", help="Use the reference pack's example."
        )
        source.add_argument(
            "--input",
            metavar="PATH",
            help="A JSON scenario file (the POST /api/v1/estimates body).",
        )
        parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
        parser.add_argument(
            "--rates-as-of",
            metavar="YYYY-MM-DD",
            help="Rates date (default: the file's rates_as_of, the example's date, or today).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        inputs, rates_as_of = self._load(options)
        try:
            result = estimate(inputs, rates_as_of=rates_as_of)
        except UnsupportedRouteError as exc:
            # The engine's message may list the supported routes by code; list them once,
            # by name.
            refused = exc.message.split(" Supported:", 1)[0].rstrip()
            supported = "; ".join(str(route["description"]) for route in supported_route_dicts())
            raise CommandError(f"{refused} Supported: {supported}.") from exc
        except EngineError as exc:  # rates unavailable, invalid as given, solver failure
            raise CommandError(exc.message) from exc
        if options["json"]:
            self.stdout.write(
                json.dumps(json.loads(result.to_json()), indent=2, ensure_ascii=False)
            )
            return
        self.stdout.write(render_table(result))

    def _load(self, options: dict[str, Any]) -> tuple[ScenarioInput, date]:
        override = (
            _parse_date(options["rates_as_of"], source="--rates-as-of")
            if options.get("rates_as_of")
            else None
        )
        if options["example"]:
            return reference_example(), override or REFERENCE_RATES_AS_OF
        path = Path(options["input"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CommandError(f"Cannot read {path}: {exc.strerror}") from exc
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"{path} is not UTF-8 text (byte {exc.start} cannot be read): save the "
                "scenario as UTF-8 JSON."
            ) from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise CommandError(f"{path} must hold a JSON object.")
        data.pop("options", None)
        file_date = data.pop("rates_as_of", None)
        try:
            inputs = ScenarioInput.model_validate(data)
        except ValidationError as exc:
            problems = "\n".join(
                f"  /{'/'.join(str(p) for p in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise CommandError(f"{path} is not a valid scenario:\n{problems}") from exc
        if override is not None:
            rates_as_of = override
        elif file_date is not None:
            rates_as_of = _parse_date(file_date, source=f"{path}: rates_as_of")
        else:
            rates_as_of = today()
        return inputs, rates_as_of


def render_table(result: CalculationResult) -> str:
    """The per-year lines and totals as a plain-text table."""
    years = result.years
    totals = {line.code: line.amount for line in result.totals.lines}
    headers = ["Line"] + [f"Year {y.assignment_year}" for y in years] + ["Total"]
    codes: list[LineCode] = []
    labels: dict[LineCode, str] = {}
    for year in years:
        for line in year.lines:
            if line.code not in labels:
                codes.append(line.code)
                labels[line.code] = line.label
    rows = []
    for code in codes:
        values = []
        for year in years:
            amount = year.line(code) if year.has_line(code) else None
            values.append(
                "" if amount is None else (str(amount) if code in _RATIOS else gbp(amount))
            )
        total = totals.get(code)
        rows.append([labels[code], *values, "" if total is None else gbp(total)])
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def fmt(row: list[str]) -> str:
        cells = [row[0].ljust(widths[0])] + [
            cell.rjust(w) for cell, w in zip(row[1:], widths[1:], strict=True)
        ]
        return "  ".join(cells).rstrip()

    route = result.route
    lines = [
        f"Tax-equalisation estimate: {route.home} to {route.host} ({route.region}), "
        f"{len(years)} year(s), rates as at {result.rates_as_of.isoformat()}",
        f"Engine {result.engine_version}; inputs {result.inputs_hash}",
        "",
        fmt(headers),
        "  ".join("-" * w for w in widths),
    ]
    for code, row in zip(codes, rows, strict=True):
        if code is LineCode.TOTAL_EMPLOYER_COST:
            lines.append("  ".join("-" * w for w in widths))
        lines.append(fmt(row))
    lines += [
        "",
        f"Total employer cost over the assignment: {gbp(result.totals.total_employer_cost)}",
    ]
    if result.warnings:
        lines += ["", "Flags:"]
        lines += [f"  [{w.severity}] {w.code}: {w.text}" for w in result.warnings]
    lines += ["", "An illustration, not tax or immigration advice."]
    return "\n".join(lines) + "\n"
