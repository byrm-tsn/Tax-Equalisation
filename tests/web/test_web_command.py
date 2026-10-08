"""``manage.py estimate``: the same service from the command line."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from teq_engine.reference import reference_example_data


def _run(*args: str) -> str:
    out = StringIO()
    call_command("estimate", *args, stdout=out)
    return out.getvalue()


def test_example_prints_a_table_with_the_reference_figures() -> None:
    output = _run("--example")
    assert "rates as at 2026-10-08" in output
    for figure in ("£127,762", "£57,196", "£4,566", "£18,414", "£4,500", "£188,676", "£180,676"):
        assert figure in output
    assert "Total employer cost over the assignment: £369,352" in output
    total_row = next(
        line for line in output.splitlines() if line.startswith("Total employer cost ")
    )
    assert total_row.split()[-3:] == ["£188,676", "£180,676", "£369,352"]


def test_example_as_json() -> None:
    data = json.loads(_run("--example", "--json"))
    assert data["totals"]["total_employer_cost"] == "369352"
    assert data["rates_as_of"] == "2026-10-08"


def test_input_file_in_the_api_request_format(tmp_path: Path) -> None:
    path = tmp_path / "scenario.json"
    body = {**reference_example_data(), "rates_as_of": "2026-10-08", "options": {}}
    body["items"] = [item for item in body["items"] if item["id"] != "relocation"]  # type: ignore[union-attr]
    path.write_text(json.dumps(body), encoding="utf-8")
    output = _run("--input", str(path))
    total_row = next(
        line for line in output.splitlines() if line.startswith("Total employer cost ")
    )
    assert total_row.split()[-3:] == ["£180,676", "£180,676", "£361,352"]


def test_invalid_file_reports_each_problem(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    body = reference_example_data()
    body["hypothetical_tax"] = {"method": "OVERRIDE", "override": "95000.00"}
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(CommandError, match="/hypothetical_tax/override"):
        _run("--input", str(path))


def test_unsupported_route_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sct.json"
    body = reference_example_data()
    body["route"] = {"home": "TR", "host": "GB", "region": "SCT"}
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(CommandError, match="Supported: Turkey to the United Kingdom"):
        _run("--input", str(path))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="Cannot read"):
        _run("--input", str(tmp_path / "absent.json"))
