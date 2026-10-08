"""``manage.py estimate``: the same service from the command line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    with pytest.raises(CommandError, match="Supported: Turkey to the United Kingdom") as caught:
        _run("--input", str(path))
    message = str(caught.value)
    assert message.count("Supported:") == 1
    assert message.startswith("The host region SCT is not supported for GB.")


def _write(tmp_path: Path, **changes: object) -> Path:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({**reference_example_data(), **changes}), encoding="utf-8")
    return path


@pytest.mark.parametrize("rates_as_of", ["0001-01-01", "9999-12-31"])
def test_an_extreme_rates_date_argument_is_one_line(rates_as_of: str) -> None:
    with pytest.raises(CommandError) as caught:
        _run("--example", "--rates-as-of", rates_as_of)
    message = str(caught.value)
    assert message == (
        "--rates-as-of: The rates date must be between 1 January 2000 and 31 December 2100."
    )


@pytest.mark.parametrize("rates_as_of", ["0001-01-01", "9999-12-31"])
def test_an_extreme_rates_date_in_the_file_names_the_field(
    tmp_path: Path, rates_as_of: str
) -> None:
    path = _write(tmp_path, rates_as_of=rates_as_of)
    with pytest.raises(CommandError) as caught:
        _run("--input", str(path))
    message = str(caught.value)
    assert "\n" not in message
    assert message.startswith(f"{path}: rates_as_of: The rates date must be between")
    assert "--rates-as-of" not in message


@pytest.mark.parametrize("rates_as_of", ["not-a-date", 20261008, "2026-13-01"])
def test_a_bad_rates_date_in_the_file_names_the_field(tmp_path: Path, rates_as_of: object) -> None:
    path = _write(tmp_path, rates_as_of=rates_as_of)
    with pytest.raises(CommandError) as caught:
        _run("--input", str(path))
    message = str(caught.value)
    assert message == (
        f"{path}: rates_as_of must be a date such as 2026-10-08, not {rates_as_of!r}"
    )


def test_a_file_that_is_not_utf8_is_one_line(tmp_path: Path) -> None:
    path = tmp_path / "binary.json"
    path.write_bytes(b"\xff\xfe{")
    with pytest.raises(CommandError) as caught:
        _run("--input", str(path))
    message = str(caught.value)
    assert "\n" not in message
    assert message.startswith(f"{path} is not UTF-8 text")


def test_the_command_line_prints_one_line_not_a_traceback(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "teq_web.settings.test"}
    manage = str(repo / "src" / "manage.py")
    completed = subprocess.run(
        [sys.executable, manage, "estimate", "--example", "--rates-as-of", "9999-12-31"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    assert completed.stderr.strip().splitlines() == [
        "CommandError: --rates-as-of: The rates date must be between 1 January 2000 and "
        "31 December 2100."
    ]


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="Cannot read"):
        _run("--input", str(tmp_path / "absent.json"))
