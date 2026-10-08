# Tax-Equalisation

A cost estimator for international assignments under tax equalisation: it tells an employer what it costs, and how long it takes, to move an employee from Turkey to England. It grosses up the employee's net guarantee through UK income tax and National Insurance with an exact solver, adds employer charges and benefits year by year, calculates or accepts the Turkish hypothetical tax, flags every assumption with a code and a question, shows a calculation trace, and sets out the UK Skilled Worker immigration steps, documents, costs (with who pays) and a critical-path timeline, kept separate from the employment cost. The reference example reproduces the brief to the pound: year 1 £188,676, year 2 £180,676, two-year total £369,352.

**An illustration, not tax or immigration advice.** Rates, fees and processing times change; each source and verification date is shown with the result.

## Quick start

Requires Python 3.13.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
python src/manage.py migrate
python src/manage.py runserver
```

Then open <http://127.0.0.1:8000/example>.

The web paths and commands below are the intended contract; if any differ in the running app, see the docs (the API documentation page lists the live endpoints).

### Pages

| Path | What it shows |
|---|---|
| `/` | Input form: route, assignment length, salary, hypothetical tax (calculated from the Turkish rules by default, at an indicative exchange rate to confirm), compensation items, assumptions |
| `/example` | Loads the reference scenario and shows its results (the Tax tab) |
| `/estimate?s=...&tab=...` | Results for the encoded inputs (reloadable and shareable without a database), one tab per request: `tab=tax` (the default: headline, summary, things to check, the five steps, assumptions), `tab=immigration` (process, documents, costs with who pays, timeline, family, tailoring) or `tab=explain` (the plain-English explanation, the calculation trace and provenance). Tailoring answers in the query string carry across the tabs |
| `/compare?s=...` | The same scenario under UK National Insurance and under the Turkish scheme, side by side |

### API

| Method and path | Purpose |
|---|---|
| `POST /api/v1/estimates` | Stateless calculation; money as decimal strings |
| `GET /api/v1/routes` | Supported routes (capability matrix) |
| `GET /api/v1/reference-example` | The reference scenario's inputs |
| `GET /api/v1/warnings` | The warning and assumption code catalogue |
| `/api/v1/docs` | Interactive API documentation (OpenAPI) |

### Operations

| Path | Purpose |
|---|---|
| `/healthz` | Process is up |
| `/readyz` | Rate sets and the guidance pack load correctly (the production design adds database and migration checks) |
| `/selftest/golden` | Runs the reference example and checks £188,676 and £180,676 |

### Command line

```bash
python src/manage.py estimate --example     # the reference example in the terminal
```

### Tests and checks

```bash
pytest -q
ruff check
mypy src/teq_engine
```

## Using the packages directly

The engine and the immigration guidance are plain Python packages with no Django dependency:

```python
from teq_guidance import TailoringAnswers, build_panel

panel = build_panel(TailoringAnswers.reference_example())
panel["costs"]["subtotals_display"]["employer_mandatory"]   # '£3,165'
panel["timeline"]["total_text"]                              # 'about 6 to 16 weeks (43 to 112 days)'
```

## How to add a country or route

1. **Rate sets.** Add the rates as YAML under `src/teq_engine/ratesets/data/<jurisdiction>/` (as `gb/income_tax_2026_27.yaml` and `tr/sgk_2026.yaml` do), each with its effective dates, sources and verification date; `src/teq_engine/ratesets/schemas.py` validates them when the provider loads. Write jurisdiction functions under `src/teq_engine/jurisdictions/<code>/` only where the rules differ in shape, not just in numbers: a Scottish income tax set reuses the UK functions, while a new home country needs its own hypothetical-tax module like `jurisdictions/tr/hypo.py`.
2. **Route registry.** Add the route to `SUPPORTED_ROUTES` and `_SPECS` in `src/teq_engine/routes/registry.py`, mapping it to its rate-set jurisdictions. The refusal page, the form's refusal message and `GET /api/v1/routes` read the registry, so they update themselves; the names shown in prose are in `src/teq_web/scenarios/capability.py`.
3. **Guidance pack.** Add a JSON pack under `src/teq_guidance/data/` (start from `tr_gb_skilled_worker.json`: sources, tailoring questions, requirements, stages, documents, costs with their payers), list its file in `_PACK_FILES` in `src/teq_guidance/loader.py`, and select it for the route in `build_immigration` in `src/teq_web/scenarios/services.py` (with one route, the Turkey to UK pack is always used). The loader refuses an inconsistent pack and names every problem.
4. **Golden case.** Add a case under `tests/golden/` with the inputs and the figures recomputed by hand; `tests/engine/test_golden.py` runs every file there.

The templates and the API need no change; the form needs one only for a country code that is not already in its lists (`_HOME_CODES` and `_HOST_CODES` in `src/teq_web/scenarios/forms.py`).

## Project layout

```
src/teq_engine/      pure calculation engine: money and rounding, UK income tax, NICs and
                     benefit rules, Turkish hypothetical tax, gross-up solver, periods,
                     warnings catalogue, trace, bundled rate sets (YAML), reference example
src/teq_guidance/    immigration guidance (standard library only): the Turkey to UK Skilled
                     Worker pack (JSON), tailoring, cost formulas, critical-path timeline, panel
src/teq_web/         Django project: web pages, API, operations endpoints, management command
src/manage.py        Django entry point
tests/engine/        engine unit, property and boundary tests
tests/golden/        golden corpus: inputs and expected figures
tests/guidance/      guidance pack, costs, tailoring, timeline and panel tests
docs/                architecture, assumptions, verification, demo script
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md): the production design, the slice this repository implements, and how one maps to the other.
- [Assumptions](docs/ASSUMPTIONS.md): every assumption and warning code in plain English, and where the brief differs from current guidance.
- [Verification](docs/VERIFICATION.md): what is tested and how, what could not be verified, and a hand recomputation of the reference example.
- [Demo](docs/DEMO.md): a ten-minute demo script, likely review questions, and open questions.
