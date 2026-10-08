# Architecture: Tax Equalisation Cost Estimator

## Context

The estimator tells an employer what it costs, and how long it takes, to move an employee abroad under tax equalisation. The worked example in the assignment brief (called "the reference pack" throughout) is Turkey to England for two years.

This document describes two things: what the system would look like if it were real and shipped to The Cozm's employer customers, and the smallest honest vertical slice of that design, which is what this repository implements. The production design assumes a **multi-tenant SaaS** used by many employer customers. AI narration is an **optional, feature-flagged adapter** behind a template default; the slice ships the template narrator only. The slice runs single-tenant, with a default tenant and no login.

Two horizons, one design:

- **Horizon A, the slice.** Django forms and templates, SQLite, one tenant, one route, no login. An engine with tests that reproduce the reference pack to the pound.
- **Horizon B, production.** Multi-tenant SaaS with versioned rules, auditability, an API that Cozm Unity could call, and a rules-maintenance workflow.

Rule for the slice: every module in A is the same module in B with infrastructure stubbed, never a throwaway. The engine, the domain types, the result schema and the warning codes are identical in both.

Package names: `teq_engine` (the pure calculation engine), `teq_guidance` (the pure immigration guidance package) and `teq_web` (the Django project).

Related documents: [ASSUMPTIONS.md](ASSUMPTIONS.md) (every assumption and warning code), [VERIFICATION.md](VERIFICATION.md) (what is verified and how), [DEMO.md](DEMO.md) (demo script and review questions).

---

## 1. What kind of system this is

Not a transactional, high-write system. It is:

1. A **deterministic rules engine** over **versioned reference data** (UK PAYE and NIC tables, Turkish tax and social security tables, benefit rules).
2. A **knowledge product** (immigration guidance) that must carry verification dates and sources.
3. A **record of decisions** (scenarios, versions, results) that finance and tax advisers must be able to reproduce months later.

The hard problems are correctness, versioning, auditability, explainability and keeping rules current. Throughput is trivial: a calculation takes well under a millisecond. Design effort goes to the first four.

---

## 2. Functional requirements and user flows

### Actors

| Actor | Needs | Horizon |
|---|---|---|
| Mobility analyst (HR) | Create scenarios, read results, share with finance | A and B |
| Finance approver | Reproducible cost, assumptions, export, budget sign-off | B |
| Tax or immigration specialist | Calculation trace with rule references, override assumptions | B |
| Rules maintainer and approver (Cozm staff) | Draft, approve, publish rate tables and immigration content with effective dates and sources | B |
| Tenant admin | Users, roles, API keys, tenant policies | B |
| Cozm support staff | Cross-tenant access with audit trail | B |
| Machine client (Cozm Unity) | Same calculation over an API | B |
| Assignee (employee) | Out of scope; later a read-only package view | Not planned |

### Flows

- **F1 Create scenario.** Route, assignment length (and start date in calendar-accurate mode), salary and currency, hypothetical-tax mode (calculated or override), compensation items, assumptions, FX snapshot.
- **F2 Validate.** Structural (types, ranges, bounded lists), semantic (hypothetical tax below salary, route supported, rate sets available), cross-field (currency matches jurisdiction, relocation in year 1).
- **F3 Calculate.** Per-year breakdown in the reference pack's order, totals, multiple of salary, coded warnings and assumptions, calculation trace, pinned rate-set versions and engine version.
- **F4 Explain.** Plain-language narrative generated from the result; assumptions list; flags near the top.
- **F5 Immigration guidance.** Stages, documents, costs with payer, timeline ranges, tailoring questions, last-verified date, sources. Never added to the employment-cost benchmark.
- **F6 Persist and share.** Immutable scenario versions and result snapshots, share links, version comparison, export. Horizon B.
- **F7 Reference data management.** Draft, approve (four eyes), supersede, retire rate sets; rerun golden scenarios on approval; rerun affected tenant scenarios as new runs. Horizon B.
- **F8 Audit.** Append-only, hash-chained log; every result records which rules produced it. Horizon B.
- **F9 Admin and API.** Tenants, roles, API keys, tenant policies; REST v1 with OpenAPI. Horizon B.

### User journey (both horizons)

1. Land on the input page, optionally click "Load reference example".
2. Submit. Validation errors appear inline with field names and plain explanations.
3. Results page: headline (year 1, year 2, total, multiple of salary), flags, then net guarantee, item treatment table, gross-up, employer charges, per-year table, assumptions, immigration panel, narrative. A "calculation trace" drawer for specialists.
4. Horizon B adds: save as version, share, compare, export, rerun under newer rates.
5. Unsupported route: an explicit page naming the supported routes and what would be required to add this one. Never a guess.

### Non-goals

Running payroll, filing anything, giving tax or immigration advice, routes beyond Turkey to England in v1, employee self-service.

---

## 3. Domain model

### 3.1 Core concepts

- **Scenario inputs** are a frozen, validated value object (pydantic v2, `frozen=True`, `strict=True`, `extra="forbid"`): route (home, host, region), assignment (length 1 to 10 years, optional start date, period mode), salary (amount, currency, frequency), items, hypothetical-tax spec (method CALCULATED or OVERRIDE, override amount, includes social security, base policy), FX snapshot (pair, rate, as-of date, source), assumptions (UK resident, social security UK_NIC or HOME_SCHEME_AGREEMENT, OWR claimed). Serialised with sorted keys and decimals as strings; hashed for caching and idempotency.
- **Compensation item.** id, kind (SALARY, BONUS, COLA, HOUSING, RELOCATION, SCHOOL_FEES, HOME_LEAVE, PENSION_EMPLOYER, OTHER), label, amount, currency, frequency (ANNUAL, MONTHLY, ONE_OFF), years ("ALL" or a list), treatment, optional `employee_contribution` (reduces a benefit's cash equivalent). Defaults derive from kind and can be overridden. Validation: MONTHLY amounts are annualised (times 12) in whole-year mode; every listed year must be within the assignment length; in v1 all UK-side items are in GBP (a non-GBP item is a validation error naming the rule). A relocation item may use only `EXEMPT_CAPPED` or `TAXABLE_BIK`, so the per-move cap cannot be bypassed; a cash relocation allowance is entered as kind OTHER. The size cap (`MAX_AMOUNT`, one billion) applies to annualised and converted amounts, not only to the figures typed in.
- **Treatment taxonomy** (closed set; the UI shows the reference pack's three labels, taxable cash, taxable benefit in kind, exempt, mapped from this finer set):
  - `GROSS_EQUALISED` (salary, bonus): contributes gross minus its hypothetical-tax share to the net guarantee. With base `ALL_EQUALISED` the share is the item times the override's effective rate (override divided by salary) or, in calculated mode, the increase in Turkish tax from adding the item to the base, split pro rata across items. With base `SALARY_ONLY` no share applies and the warning `EQUALISED_ITEM_NO_HYPO_SHARE` says so.
  - `NET_CASH` (cost-of-living allowance in the reference pack): full amount joins the net guarantee and is grossed up; Class 1 NICs.
  - `TAXABLE_BIK` (housing): cash equivalent enters the income-tax base inside the gross-up, no employee NICs, Class 1A for the employer. The cash equivalent is an input; no living-accommodation valuation is performed (stated assumption).
  - `EXEMPT_CAPPED` (relocation): cap read from the benefit rules rate set (£8,000) with `cap_scope: PER_MOVE`, so the cap is consumed cumulatively across all years of the assignment, not reset each year; excess automatically becomes `TAXABLE_BIK`, traced as two lines. The exemption window is the tax year of the job start plus the following tax year, so in whole-year mode years 1 and 2 are inside the window and year 3 or later emits `RELOCATION_OUTSIDE_WINDOW`.
  - `EXEMPT` and `EMPLOYER_ONLY` (for example employer pension): cost without a tax effect on the employee.
  - Later: `GROSS_NOT_EQUALISED` for items where the employee bears UK tax.
- **Period model.** `PeriodPlan` is a list of `Period{assignment_year, slices: [FiscalSlice{uk_tax_year, tr_calendar_year, fraction}]}`. The calculator computes per fiscal slice and allocates to assignment years; tax functions never see dates. v1 **illustrative whole-year mode** yields one slice per year with fraction 1 and assumption `MODE_ILLUSTRATIVE_WHOLE_YEAR`. Mapping rule: assignment year 1 is the UK tax year and the Turkish calendar year containing `rates_as_of` (or the start date when given); later assignment years increment both, using the carried-forward rate set where none is published. **Calendar-accurate mode** (later, behind a flag) splits assignment dates at 6 April and 1 January, prorates thresholds and follows the relocation exemption's job-start timing. No rewrite needed.
- **Rate-set resolution** lives in the engine behind a `RateSetProvider` protocol (`get(jurisdiction, category, as_of)`, `latest(...)`). A missing future year, or a gap between two published sets, carries forward the latest earlier set and emits `RATES_NOT_PUBLISHED_FOR_YEAR`; only a date before every set is refused (`RATES_UNAVAILABLE`). Providers may implement the optional `latest_on_or_before` lookup, which the bundled provider does and the Django provider should. This is exactly why the reference pack's year 2 equals £180,676 and the result says so. `BundledProvider` reads packaged YAML; the Django provider reads the database.
- **Money.** `decimal.Decimal` everywhere via a `Money` newtype that refuses floats; engine precision 6 dp; YAML and JSON carry amounts as strings; columns are `numeric(14,2)`.
- **Rounding policy** (`RoundingPolicy`): solve the gross-up exactly; **ceil gross cash to the pound** so the net guarantee is never under-delivered; recompute every line on the rounded gross; round lines half-up to the pound; totals are sums of rounded lines so every table foots. The net cash line is derived as rounded gross minus rounded tax minus rounded employee NICs, with `net_delivered_exact` in the trace. Under this policy the reference example reproduces exactly: exact gross £127,761.51 rounds to £127,762 and delivers £66,000.26 net; the two-year total is £369,352 as printed.
- **FX.** A dated rate is an input stored with the scenario, never fetched inside the engine. Warnings: `FX_RATE_STALE` over 30 days old, `FX_RATE_IMPLAUSIBLE` outside the rate set's sanity band. A feed may prefill it; the snapshot is what the result is pinned to. A snapshot dated after `rates_as_of` is refused with `FX_RATE_IN_FUTURE` through a typed `ScenarioValidationError` that carries the field pointer.
- **Salary currency.** The reference pack states salary in GBP; a real Turkish assignee is usually paid in lira. Salary may be GBP or TRY. A TRY salary is converted to GBP at the FX snapshot for the net guarantee and the UK gross-up, and used directly in lira for the Turkish hypothetical tax, with `SALARY_CONVERTED_AT_SNAPSHOT_FX`. The multiple of salary always uses the GBP figure. A GBP salary is converted to lira only inside the Turkish module.
- **Tenant policies**, not per-scenario choices: hypothetical-tax base (salary only or all equalised items), whether Apprenticeship Levy applies, auto-enrolment defaults, retention days.
- **Home-scheme social security mode** (`social_security: HOME_SCHEME_AGREEMENT`). UK employee NICs, employer NICs and Class 1A are zero. The employee's Turkish contributions continue and are already inside the hypothetical deduction, so the net guarantee is unchanged. The employer's Turkish contributions also continue, so the engine computes them from the TR_SGK rate set (employer share less the applicable incentive, on earnings capped at the ceiling) and shows them as a separate GBP-converted line `HOME_EMPLOYER_SOCIAL_SECURITY` with `HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED` and the assumption `HOME_SCHEME_CERTIFICATE_REQUIRED`. Without this line the toggle would make the assignment look cheaper than it is; at the 2026 ceiling and an indicative rate the line is roughly £11,700 a year.

### 3.2 The gross-up solver

- Problem: find G with `G - IncomeTax(G + BIK) - EmployeeNICs(G) = N`.
- `net(G)` is continuous, piecewise linear and strictly increasing because every marginal rate is below 100% (the worst segment is the personal-allowance taper band, a 60% effective income-tax rate, combined with 8% employee NICs when cash pay is still below the upper earnings limit: a 68% marginal rate, slope 0.32). The solution is unique.
- **Primary method: exact segment solve.** Collect every breakpoint in gross-cash space: personal allowance threshold, taper start and end (£100,000 and £125,140 in total-income space, so shifted left by the benefit value), each band edge mapped back through the taxable-income relationship, and the NIC primary threshold and upper earnings limit. Evaluate `net` at each sorted breakpoint, find the segment containing N, solve the linear equation. Iteration-free, deterministic, and the trace can show the segment and the marginal rate ("47% = 45% income tax + 2% NICs" for the reference case).
- **Post-condition** `|net(G) - N| <= 0.0001`. On failure, fall back to bracketed bisection on `[0, N / (1 - max_marginal)]` with at most 200 iterations and emit `GROSS_UP_FALLBACK_BISECTION`; if that fails too, raise `SolverError` so the run is `FAILED` with code `GROSS_UP_NOT_CONVERGED`. No partial numbers are shown. Both paths are alerted on because the fallback hides a breakpoint bug.
- **Rate-set validation** rejects any table with a marginal rate at or above 100% at approval time, which guarantees monotonicity before the solver ever runs.
- **Test oracle and explanation.** The reference pack describes iteration. The fixed-point iteration `G <- G + (N - net(G))` is kept as a test oracle (it must agree with the exact solve to the penny; it converges in about 21 steps on the reference example) and as the simplest way to explain the idea.
- `GrossUpResult` records `gross_exact, gross_rounded, segment, marginal_rate, method, evaluations, net_delivered`.
- The marginal employer cost of one extra pound of net allowance on the final segment is 1 / 0.53 x 1.15 = £2.17 in the reference case.
- Invariants enforced by assertions and property tests, stated on the exact gross before rounding: the net check holds; one penny less gross delivers less than the guarantee; higher N gives higher G; adding a taxable BIK never lowers G; items restricted to year 1 leave other years unchanged. After rounding, the only guarantee is net delivered at or above the target.
- Rounding the gross up to the pound can move it across a breakpoint (for example exactly onto the upper earnings limit). That is harmless because every line is recomputed from the rounded gross by the full tax and NIC functions, never from the segment's slope.
- All engine arithmetic runs inside `decimal.localcontext()` with a fixed precision and rounding mode, so the global Decimal context of the host process cannot change results.

### 3.3 Turkish hypothetical tax module

- Inputs: annual salary in GBP, dated GBP to TRY rate, calendar year, employment type (private sector), whether to include social security, base policy.
- Rules as data for 2026 (YAML with sources): wage-income brackets in lira; employee SGK 14% plus 1% unemployment on gross capped at the monthly ceiling times 12; tax base is gross minus SGK; the minimum-wage exemption applied as a credit equal to the tax on the minimum-wage base, and the stamp-tax exemption on the minimum-wage portion; stamp tax 0.759%.
- `hypo = income_tax + stamp_tax (+ SGK if includes_social_security)`, computed in TRY, converted at the supplied dated rate.
- Output `HypoTaxResult` with each component in TRY and GBP, the FX rate used, the exemption applied, and `mode` CALCULATED or OVERRIDE. The label is part of the data, so the UI cannot mislabel it. Override skips the calculation and emits assumption `HYPO_TAX_OVERRIDE`.
- Stated limitation: Turkish withholding is cumulative per month; the annual computation is equivalent only for even pay.

### 3.4 Immigration guidance model

Implemented in the `teq_guidance` package (standard library only, no Django, no I/O beyond reading its bundled file, no clock).

- **Content is data, not code**: a versioned JSON pack per route (`teq_guidance/data/tr_gb_skilled_worker.json`) holds the route note, requirements (general, or circumstance-dependent), documents (general, or conditional with the condition as a predicate on tailoring answers), costs, timeline stages and notes, employer responsibilities, sources, and the reference-pack comparison table. Tailoring questions cover the occupation code, nationality, application location, whether a sponsor licence is held, sponsor size, the number of adult and child dependants, residence in a TB-test country, how English will be shown, whether the sponsor certifies maintenance, the occupation sector, and whether the UK entity will employ the worker or the Turkish employer posts them.
- **Conditions are data.** A condition is a test such as `{"question": "tb_listed_resident", "equals": true}` or `{"question": "dependants_adults", "gte": 1}` (operators `equals`, `in`, `gte`, `gt`, `lte`), combined with `all` and `any`. An unanswered question uses the pack's stated `assumed_if_unanswered` value, and the panel lists it under "what we would need to tailor this further" together with the assumption made.
- **Costs are formulas, not fixed numbers.** Each cost item has `basis`: FIXED, PER_VISA_YEAR (health surcharge), FIRST_YEAR_THEN_PER_6_MONTHS (skills charge), PER_ADULT_DEPENDANT, PER_CHILD_DEPENDANT, PER_DEPENDANT_VISA_YEAR_ADULT, PER_DEPENDANT_VISA_YEAR_CHILD; a `tier` rule where the fee depends on visa length (up to 3 years or longer), application location (outside or inside the UK) or sponsor size; `payer` (EMPLOYER, APPLICANT, EITHER_BY_POLICY); a `cannot_be_recouped_from_worker` flag; `verification` (search_confirmed, third_party_reported, from_knowledge); `source`; `verified_at`. The panel computes the amounts for the scenario's visa length and dependants and shows the formula next to each figure (for example "£1,035 x 2 years"), with subtotals for the employer's mandatory costs, the applicant-side costs an employer may pay by policy, and the applicant's own costs. These totals never enter the employment-cost benchmark. Visa length may be given in months: the skills charge is the first 12 months plus one period for each further six months or part, the health surcharge charges half a year for a final part year of six months or less, and anything over 60 months is priced as one 60-month grant with the panel warning `VISA_LENGTH_EXCEEDS_SINGLE_GRANT`.
- **Timeline is a small dependency graph.** Each stage has `depends_on`, a typical duration range, an actor and a condition (for example "sponsor licence application" only when no licence is held; "TB test" only for residents of a listed country). The panel computes the critical path with forward and backward passes at the minimum and maximum durations and shows the total as a range in days and weeks, never as a date, and marks which stages can run in parallel (English test and TB test alongside the licence application). A stage that does not apply hands its dependencies to the stages after it, so a conditional stage in the middle of a chain never breaks the path.
- A small decision table maps tailoring answers to which conditional items apply. Nothing infers eligibility from salary; the output states that the occupation code, the going rate and basic-pay-only rules decide it. When the answer is "posted by the Turkish employer" (or not yet decided), the panel notes that a different route (Global Business Mobility, Senior or Specialist Worker) may be the relevant one and that this also bears on the social security position, without deciding either.
- Every rendered panel shows `verified_at`, each item's verification level and its source links; content older than a configurable age (180 days by default, relative to a date the caller passes in) shows `IMMIGRATION_CONTENT_STALE`.
- The loader validates the pack at start-up: every `depends_on` names an existing stage and the graph has no cycle, every condition names a known question with a value that fits it, every cost has a basis and a payer, every item cites at least one known source, and amounts are strings.

---

## 4. Architecture and service boundaries

**Modular monolith.** One Django project, several apps, plus two pure-Python packages (the engine and the guidance package) that are separately installable. Boundaries are enforced by import-linter contracts, not by network calls. Microservices would add latency, operational burden and distributed-failure modes to a system whose unit of work is a sub-millisecond calculation. The engine package keeps the door open to a calculation service later without committing to one now.

**UI stays server-rendered** (Django templates plus htmx where interactivity helps). The slice's forms and templates are therefore the real UI, not a placeholder before a single-page app.

| Module | Responsibility | Depends on |
|---|---|---|
| `teq_engine` (package) | Pure functions: money and rounding policy, types, treatments, UK income tax, NICs and benefit rules, Turkish tax, SGK and stamp tax, breakpoints and segment solver, period plan, calculator, warnings and assumptions, trace, route registry, bundled rate sets. No Django, no I/O, no clock. | stdlib, pydantic, PyYAML |
| `teq_guidance` (package) | Pure functions over the bundled immigration guidance pack: loader and validation, tailoring decision table, cost formulas and subtotals, critical-path timeline, panel builder. No Django, no I/O, no clock. | stdlib |
| `refdata` app | `RateSet` and `GuidancePack` models with effective ranges and approval workflow; loader implementing `RateSetProvider` from the database with the bundled provider as fallback; version-keyed in-memory cache; `load_rate_sets` command idempotent by checksum. | engine |
| `immigration` app | Tailoring answers from the form and rendering of the panel over the published guidance pack. | refdata, `teq_guidance` |
| `scenarios` app | Forms, validation, orchestration service `estimate(inputs, as_of) -> CalculationResult`, persistence of versions and runs, comparison, exports. | engine, refdata, immigration |
| `narration` app | `Narrator` protocol; template implementation (default); LLM adapter (flagged) that never computes. | engine result types |
| `sharing` app | Share links: hashed tokens, scope, expiry, revocation, view limits. | scenarios |
| `accounts` app | Tenants, users, memberships, global roles, API keys, SSO, tenant policies. | none |
| `audit` app | Append-only, hash-chained events; allow-listed metadata keys. | all |
| `api` app | REST v1 on Django Ninja; schemas are the engine's pydantic types; throttling; problem-details errors; OpenAPI snapshot. | scenarios, refdata, immigration |
| `web` app | HTML views and templates. | scenarios |
| `ops` app | Health, readiness, golden self-test, metrics, feature flags. | engine, refdata |

**Request path.** View or API -> `scenarios.services.estimate` -> `refdata.provider.rate_sets_for(route, as_of)` -> `teq_engine.calculate(inputs, provider)` -> persist `CalculationRun` (B only) -> narration -> render. The immigration panel is built alongside from `teq_guidance.build_panel(answers)`. The same service backs the HTML view, the JSON API and `manage.py estimate --example`, which is how the slice proves the separation.

**Degraded mode.** The stateless estimate endpoint needs only the engine, the bundled or cached rate sets and the bundled guidance pack. If the database is unavailable, it still answers, adds `DEGRADED_BUNDLED_RATES` and the header `X-Degraded-Mode: bundled-rate-sets`, and refuses saves with a clear message. Readiness reports degraded rather than down.

**Narration boundary.** The template narrator is the default and sufficient for the brief. The LLM adapter, if enabled per tenant, receives only the structured result (no free-text user fields, so no prompt-injection path), is instructed to describe and not compute, is validated so that every number in its output exists in the result's figure set or the text is discarded, falls back to the template on timeout (8 s) or validation failure with `NARRATIVE_FALLBACK`, and logs prompt and output. The narrative never feeds back into any number.

**Dependency choices.** Django 5.2 LTS, Python 3.13, psycopg3, Django Ninja (pydantic-native, OpenAPI for free; DRF acceptable if the client mandates it), pydantic v2, Procrastinate for jobs (Postgres LISTEN/NOTIFY, retries, Django integration; keeps Redis optional), django-waffle for flags, structlog, OpenTelemetry, sentry-sdk with `send_default_pii=False`, django-allauth for OIDC later, django-otp for 2FA, django-csp. Tooling: uv lockfile, ruff, mypy strict on the engine and django-stubs on the project, pytest with pytest-django and pytest-xdist, hypothesis, schemathesis, pip-audit, import-linter.

---

## 5. API design (Horizon B, with the slice exposing the stateless endpoint)

Base path `/api/v1`. JSON. Money as decimal strings (bare JSON numbers are rejected), ISO 4217 currency codes, ISO 8601 dates. Every response carries `engine_version`, `schema_version`, `rate_set_ids` and `rates_as_of`.

| Method and path | Purpose | Notes |
|---|---|---|
| `POST /estimates` | Stateless calculation | Body is scenario inputs plus `options{include_trace, include_narrative, include_immigration}`; response is the result. Deterministic; cacheable by the result's `cache_key`. The slice exposes this. |
| `GET /routes`, `GET /reference-example`, `GET /warnings`, `GET /openapi.json` | Capability matrix, the reference pack's example, code catalogue, schema | Public metadata |
| `GET /rate-sets?jurisdiction=&category=&as_of=` | Which rules apply on a date | Provenance only |
| `POST /rate-sets`, `POST /rate-sets/{id}/approve`, `POST /rate-sets/{id}/retire` | Rules workflow | Global roles; approver must differ from author |
| `POST /scenarios`, `GET /scenarios`, `GET /scenarios/{id}`, `PATCH /scenarios/{id}` | Scenario container | PATCH changes name, tags, archive only |
| `POST /scenarios/{id}/versions` | New immutable version from changed inputs | Requires `If-Match: "v<n>"`; 409 on conflict with `current_version` |
| `GET /scenarios/{id}/versions`, `GET .../versions/{n}` | History | Cursor pagination |
| `POST .../versions/{n}/runs` | Compute and store a run | Synchronous 201; de-duplicated by (version, engine, rate-set fingerprint) |
| `GET /runs/{id}`, `GET /runs/{id}/trace`, `GET /scenarios/{id}/runs` | Pinned snapshots | Never recomputed; `RATE_SET_SUPERSEDED` added at read time if newer rules exist |
| `GET /scenarios/{id}/compare?a=&b=` | Line-by-line diff of two runs | Read-only |
| `POST /runs/{id}/exports`, `GET /exports/{id}` | PDF, XLSX, JSON export | 202 then poll; signed download URL |
| `GET /immigration/guidance?route=&...` | Tailored guidance | Query params are the tailoring answers |
| `POST /runs/{id}/share-links`, `DELETE /share-links/{id}` | Sharing | Token returned once |
| `GET /share/{token}` and `/api/v1/share/{token}` | Public read of an immutable run | Constant-time hash lookup; always 404 on miss; IP-throttled; `X-Robots-Tag: noindex`; path redacted in access logs |

Conventions:

- Errors are RFC 9457 `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, plus `code`, `request_id` and `errors[{pointer, message}]`. 422 for validation and for `route-not-supported` (which lists supported routes), 400 for malformed JSON, 404 for cross-tenant objects (never 403, to avoid existence leaks), 409 for version conflicts and effective-range overlaps, 429 with `Retry-After`, 503 with `Retry-After` when the database is down on persisted endpoints.
- Idempotency: `Idempotency-Key` on every POST; insert-first with unique (tenant, key) so concurrent duplicates resolve in the database (the loser gets 409 `request-in-progress`); same key with a different body is 422 `idempotency-key-reused`; replays return the stored response; 24-hour TTL.
- Cursor pagination on (created_at, id), `limit` at most 100.
- Versioning: URL `/v1/`; additive changes only; `Deprecation` and `Sunset` headers before retirement.
- Auth: session cookie with CSRF for the browser; `Authorization: Bearer tk_live_...` API keys (hashed, shown once, scoped `estimates:read`, `scenarios:write`, `rates:publish`, `admin`, expiring) for machines; OAuth2 client credentials later for partners.
- Throttling: per tenant (for example 600 a minute) and per key (estimates 60 a minute), lower on narration, exports and share views; backed by Redis with a local-memory fallback that fails open with a metric; coarse limits at the edge proxy.

---

## 6. Data model and database

**PostgreSQL** in production, SQLite in development and tests. Reasons: JSONB for inputs and result snapshots (schema-validated in the app), `numeric` money columns, range types with an exclusion constraint so overlapping effective periods are impossible, row-level security as a second line of tenancy defence, mature Django support, point-in-time recovery. No document or key-value store is needed. Redis is cache and rate limiter only, optional. CI runs a Postgres job because SQLite cannot exercise the constraint, triggers or GIN indexes.

Cross-cutting rules:

- **IDs** are UUIDv7 (time-ordered, no volume leaks; a small library on Python 3.13).
- **Tenancy scoping.** Tenant-owned models inherit `TenantScopedModel` with a `TenantManager` that raises `TenantContextMissing` unless middleware set the tenant in a context variable; `unscoped` requires explicit opt-in and is used only by staff tooling. A test asserts every tenant-owned model uses the scoped manager. Postgres row-level security keyed on `SET LOCAL app.tenant_id` is phase 2, after confirming the connection-pooler mode supports it.
- **Immutability.** `save()` raises when the row already exists, `QuerySet.update` and `delete` are overridden to raise, admin has no change permission, and production adds `BEFORE UPDATE OR DELETE` triggers that honour `SET LOCAL app.purge = 'on'` for the retention purge command only.

| Model | Key fields | Constraints and notes |
|---|---|---|
| `Tenant` | slug, name, status, settings JSONB (hypo base policy, apprenticeship levy applies, auto-enrolment default, retention_days) | Unique slug |
| `User`, `Membership`, `APIKey` | email login; `Membership.role` in {viewer, analyst, tenant_admin}; `User.global_roles` subset of {cozm_staff, rules_admin, rules_approver}; key `prefix` plus sha256 `key_hash`, scopes, expires_at, revoked_at, last_used_at | Unique (tenant, user); unique prefix |
| `RateSet` (global) | jurisdiction, category (UK_INCOME_TAX, UK_NIC, UK_BENEFIT_RULES, TR_INCOME_TAX, TR_SGK, TR_STAMP), label, version, effective_from, effective_to (null means open), data JSONB, schema_version, status DRAFT, APPROVED, SUPERSEDED, RETIRED, checksum, sources JSONB, supersedes FK, created_by, approved_by, approved_at | Unique (jurisdiction, category, label, version); check `effective_to > effective_from`; check approved rows have approved_by; approver differs from author (service rule); **exclusion constraint** below; index (jurisdiction, category, status, effective_from) |
| `GuidancePack` (global) | route, version, status, content JSONB, verified_at, verified_by, checksum | Unique (route, version); partial unique: one PUBLISHED per route |
| `Scenario` | tenant, name, reference (pseudonymous label, never the employee's name), current_version, tags, archived_at | Unique (tenant, reference) |
| `ScenarioVersion` (immutable) | tenant (denormalised, so scoping never needs a join), scenario, version, input JSONB (canonical), input_hash, change_note, created_by | Unique (scenario, version) |
| `CalculationRun` (immutable) | tenant (denormalised), scenario_version, engine_version, rate_set_fingerprint (covers rate sets and the guidance pack), rate_set_ids array, guidance_pack_id, status, result JSONB, warning_codes array, duration_ms, error JSONB, requested_by | Unique (scenario_version, engine_version, rate_set_fingerprint) de-duplicates reruns; GIN on warning_codes; index (tenant, created_at desc) |
| `IdempotencyKey` | tenant, key, request_hash, status IN_PROGRESS, DONE, FAILED, response_status, response_body, expires_at | Unique (tenant, key); the one row that is legitimately mutated: inserted IN_PROGRESS, completed with the response, marked FAILED so a retry can proceed |
| `ShareLink` | run, token_hash, scope SUMMARY or FULL, expires_at, revoked_at, max_views, view_count, optional password_hash | Unique token_hash; check expiry at most 90 days |
| `ExportJob` | run, format, status, file_key, error, attempts | |
| `AuditEvent` (append-only) | tenant, actor user or API key, action, object_type, object_id, metadata (allow-listed keys), request_id, prev_hash, hash | Hash chain per tenant, appended under a per-tenant advisory lock (write volume is low, so contention is negligible); no update or delete rights for the application role; partition by month when large |
| `FxRateSnapshot` | base, quote, rate, as_of, source | Reference only; the scenario stores its own copy |

**Rate-set lifecycle.** DRAFT -> APPROVED (in force) -> SUPERSEDED when a newer version closes its `effective_to` (the only permitted mutation of an approved row) or RETIRED when the data was wrong. The exclusion constraint covers APPROVED and SUPERSEDED rows, so a correction retires the bad row (leaving it referenced by old runs) and a corrected row can occupy the same range. Approving version N+1 closes version N's `effective_to` to the day before the new `effective_from` in the same transaction; an open-ended row stores `effective_to = NULL`, which the range treats as unbounded, so two open-ended rows for one category can never coexist. The check constraint `effective_to > effective_from` allows NULL. The approver-differs-from-author rule has an audited break-glass for a small rules team, where a single rules administrator may exist. Rate-set data changes go through this workflow, never through migrations. The engine declares the rate-set `schema_version` range it supports and refuses to load a set outside it.

**Exclusion constraint.** Added in a migration operation guarded by `schema_editor.connection.vendor == "postgresql"` (not declared in `Meta`, so SQLite development and `makemigrations --check` stay clean); `RateSet.clean()` repeats the overlap check in Python for SQLite. SQL in Appendix D.

**Retention.** Scenarios kept for the tenant's configured period, then purged or anonymised (inputs replaced by hash, results kept); audit log retained longer.

---

## 7. Authentication, authorisation, tenancy

- **Browser.** Django sessions, Argon2 hashing, 2FA for global roles and tenant admins, SSO via OIDC or SAML for enterprise tenants, login throttling and lockout, session rotation on privilege change, `SameSite=Lax`, `Secure`, `HttpOnly`, admin behind SSO or an IP allow-list.
- **Machines.** Scoped API keys hashed at rest, rotation and expiry, per-key throttling, last-used tracking, revocation.
- **Roles.** Tenant-side: `viewer` (read), `analyst` (create and version scenarios), `tenant_admin` (users, keys, policies). Budget sign-off of a scenario version is a status workflow on the scenario, not a role. Cozm-side global roles: `rules_admin` (draft rate sets and guidance), `rules_approver` (approve; never the author of the same row), `cozm_staff` (cross-tenant support, every access audited, visible banner).
- **Object-level checks.** One policy module `can(user, action, obj)` called from views and API, no scattered conditionals.
- **Share links.** Capability tokens: 32 random bytes, sha256 stored, scope, expiry at most 90 days, revocable, optional password, rate-limited, `noindex`, `Referrer-Policy: no-referrer`, render immutable runs only, never the edit form. The public share view resolves the tenant from the link itself, not from any session, and runs with that tenant set explicitly in the scoping context.

---

## 8. Non-functionals

### Scalability and performance

- One calculation is under a millisecond; a small container serves hundreds of requests a second. The limiting resource is the database on persisted saves, not the engine.
- Stateless `POST /estimates` scales horizontally with no coordination; responses are cacheable by the result's `cache_key` (inputs hash, rate-set content fingerprint, engine version and rates date).
- Rate sets and guidance are read-mostly: loaded once per process, cached by version; an approval bumps the version so no invalidation protocol is needed. Warm-up on start plus single-flight locking prevents a stampede after a deploy.
- Heavy work is asynchronous and idempotent: exports, bulk recalculation after an approval, LLM narration.
- Targets: p95 under 300 ms for the results page, p99 under 1 s, 99.9% availability in business hours. Storage grows linearly; a run snapshot is 5 to 20 KB.

### Caching

- No shared HTTP caching of tenant pages (`Cache-Control: private, no-store`); ETags on API GETs.
- Application cache: rate sets and guidance (version-keyed), FX reference rate (short TTL, the scenario pins its own), estimate results (long TTL, deterministic key).
- Nothing tenant-scoped is cached without the tenant in the key.

### Concurrency

- Versions are append-only; two analysts only race on who creates version N+1. `select_for_update` on the scenario row plus the unique constraint resolve it; the loser gets 409 with the current version to rebase on.
- Rate-set approval locks on (jurisdiction, category) inside a transaction and the exclusion constraint makes overlaps impossible regardless of application bugs.
- A calculation resolves its rate sets once at the start and pins their IDs; an approval mid-request cannot change the result.
- Double form submissions: Post-Redirect-Get in the browser, idempotency keys on the API.
- Workers run at-least-once; every job checks for an existing run via the unique constraint before computing.

### Rate limiting

- Token buckets per tenant, per key and per user; lower limits on narration, exports and share views; login attempts throttled; 429 with `Retry-After`; fail open with a metric if Redis is unavailable. The slice uses Django's cache-based throttle or none.

### Security

- Data classification: salary, nationality and family details are personal data under UK GDPR. Minimise: no employee name or date of birth fields, pseudonymous references, per-tenant retention and purge, and a staff lookup by reference so a data-subject request can find and purge every version and run about one person. Salary never appears in logs, metrics, Sentry (allow-list log processor, `before_send` scrubber) or job payloads (IDs only).
- Tenancy isolation is the top risk: context-required manager, UUIDv7 IDs, 404 not 403 across tenants, RLS later, and tests that try every scoped endpoint with another tenant's key.
- Input hardening: 256 KB body limit, at most 50 items and 10 years, strict decimal strings, NaN and infinity rejected, FX bounds, enum validation.
- Rate-set integrity: four-eyes approval, checksums verified at load, pydantic schema per category, audit hash chain.
- Secrets from the environment via pydantic-settings or a vault, `SECRET_KEY_FALLBACKS` rotation, hashed API keys, no secrets in images or the repository; uv lock, pip-audit, Dependabot, SBOM.
- Headers: HSTS, nonce-based CSP, `X-Content-Type-Options`, `Permissions-Policy`, frame denial, CORS off by default; CSRF on all forms.
- LLM boundary as in section 4; provider data-processing agreement; UK or EU residency; no training on tenant data; kill switch per tenant.
- Legal: "an illustration, not tax or immigration advice" disclaimer rendered with, and its version stored in, every result; versioned terms.

### Cost

- Small scale (about ten tenants, a thousand calculations a day): two small web instances, one worker, managed Postgres, optional Redis, object storage, free observability tiers: roughly £50 to £150 a month; up to about £300 with larger managed instances. LLM narration adds pence per run; PDF rendering is CPU in the worker.
- The real cost is people: tax and immigration specialists keeping rules current. The design reduces it with rates as data, source links, verification dates, expiry alerts, a four-eyes workflow and automatic golden reruns on approval.

---

## 9. Reliability

### Failure modes

| Cause | Detection | Handling |
|---|---|---|
| Wrong figure in an approved rate set | Golden corpus reruns on approval; checksum against source; schema validation; four eyes | Approval blocked on golden mismatch; bad row retired and corrected row approved; affected tenants' latest versions rerun as new runs; old runs flagged `RATE_SET_SUPERSEDED` at read time |
| No approved rate set for a year the assignment reaches | Provider miss | Carry forward with `RATES_NOT_PUBLISHED_FOR_YEAR` naming the proxy year; none at all is 422 `rates-unavailable` |
| Non-monotonic or invalid rate table | Validation at load and approval | Refuse load or approval; alert; bundled fallback |
| Bundled rate sets drift from database rate sets | Golden self-test compares both providers | Alert `rateset_drift` |
| Solver post-condition fails | Assertion | Bisection fallback with warning; if that fails the run is FAILED with no partial numbers; alert (should be unreachable) |
| FX feed down | Fetch timeout | Last snapshot or manual entry; the engine never calls the feed |
| LLM slow, down or produces an unknown number | 8 s timeout; numeric validator | Template narrative with `NARRATIVE_FALLBACK`; flag off per tenant |
| Database down | `OperationalError`; `/readyz` red; circuit breaker opens after 5 failures for 30 s | Persisted endpoints 503 with `Retry-After`; `/estimates` continues on the bundled provider in degraded mode |
| Redis down | Connection error | Limiter and caches fail open; metric and log |
| Concurrent version creation | Unique (scenario, version) plus row lock | 409 `version-conflict` with `current_version` |
| Concurrent overlapping approvals | Exclusion constraint plus category lock | 409 `effective-range-overlap` |
| Duplicate reruns | Unique (version, engine, fingerprint) | Return the existing run |
| Worker crash mid bulk-recalculation | Procrastinate retries 3 times with backoff 10 s, 60 s, 300 s | Per-run idempotency by constraint; job resumable; dead-letter after retries |
| Export failure | Attempts counter | Retry with backoff; dead-letter; user notified |
| New engine version changes outputs | CI golden-diff gate | Version bump and changelog required; old runs remain valid snapshots |
| Migration fails on deploy | Release step exit code | Rollout halts; expand and contract keeps the old image valid |
| Clock or timezone mistakes | Effective ranges are dates, not datetimes; tax-year boundary tests | UK local dates throughout; `as_of` is passed explicitly, never `now()` inside the engine |
| Share-token brute force or leak | IP throttle; 256-bit tokens; view audit | Always 404; revoke; rotate |

### Domain edge cases the engine must handle and tests must cover

- Income below the personal allowance; inside the taper band £100,000 to £125,140 (60% effective marginal income tax); at exactly £12,570, £50,270, £100,000, £125,140 and £5,000, each at the value and one penny either side; zero salary.
- Zero benefits; several benefits; benefits only in later years; an employee contribution towards housing; relocation above £8,000 (excess taxable with Class 1A); relocation split across years 1 and 2 (one cap per move, both years inside the window); relocation in year 3 or later (`RELOCATION_OUTSIDE_WINDOW`); cost-of-living allowance paid gross versus net; salary in lira converted at the snapshot.
- Hypothetical tax at or above salary (reject); hypothetical tax calculated in lira with the SGK ceiling binding and not binding; minimum-wage exemption; stale or implausible FX.
- Assignments of 1 to 10 years; region SCT refused in v1 with `region-not-supported` (a Scottish rate set is a later addition, and NICs are UK-wide either way); home-scheme social security (UK NICs and Class 1A zero, Turkish employer contributions computed and shown); very large salaries (Decimal, no overflow); rounding so that every table foots.
- Immigration: no sponsor licence yet; dependants; residence in a TB-test country; English-test timing; certificate-of-sponsorship validity window; start date far in the future.

### Race conditions

Covered above: version numbering (lock plus unique constraint plus `If-Match`), rate-set approval (category lock plus exclusion constraint plus pinned IDs), double submit (PRG plus insert-first idempotency), at-least-once jobs (existence by constraint), cache stampede (warm-up plus single flight), share revocation (checked per request, short cache).

### Data consistency

- Versions and runs are immutable snapshots carrying `engine_version`, `rate_set_ids` and a rate-set fingerprint. Reproducibility is a property of the data, not of hoping nothing changed.
- Version creation and its first run are written in one transaction in the slice; in production a run may be asynchronous with a `PENDING` status, but a version is never visible without a run status.
- Approved rate sets are never deleted, only superseded or retired; foreign keys from runs keep them intact.
- Backups: managed daily snapshots plus 7 to 30 days of point-in-time recovery; quarterly restore drill; recovery objectives of at most 15 minutes of data loss and 4 hours to restore service. Migrations are forward-only, expand and contract, `makemigrations --check` and both database vendors in CI.

### Retries

- Clients may retry any POST safely because of idempotency keys.
- Server-side retries only for idempotent external calls (FX, LLM) with exponential backoff, jitter and a circuit breaker; job retries capped with a dead-letter queue.

### Observability

- Structured JSON logs (structlog) with request id, tenant id, hashed user id, scenario version id, engine version and rate-set ids. Amounts are never logged; only the inputs hash.
- Metrics: `calc_total{route,status}`, `calc_duration_seconds`, `solver_evaluations`, `solver_fallback_total`, `warnings_total{code}`, `refusals_total{reason}`, `golden_selftest_ok`, `rateset_cache_age_seconds`, `degraded_mode_total`, request latency by endpoint, job durations and failures, LLM latency and fallbacks, 4xx and 5xx rates.
- Tracing with OpenTelemetry spans: view, service, provider, engine, database, narration.
- Alerts: error rate, p95 latency, any solver fallback or failure, rate-set drift, any rate set expiring within 30 days, no approved rate set for the next UK tax year by 1 March, job backlog, golden self-test failure.
- Health: `/healthz` (process), `/readyz` (database reachable, migrations applied, bundled and database checksums equal), `/selftest/golden` (runs the reference example on both providers, asserts £188,676 and £180,676, cached 60 s, token-protected, polled by a synthetic monitor).
- Product analytics: unsupported routes requested, warnings shown, assumptions toggled. This is the roadmap signal for which country to add next.

---

## 10. Trade-offs, pitfalls and shipping to real users

### Pitfalls to avoid

- Any float: YAML and JSON must carry amounts as strings; forms post strings; `DecimalField` only.
- Rounding lines before solving, or rounding tax but not the gross: tables stop footing.
- Omitting the taper breakpoints (shifted by the benefit value) silently breaks the segment solve; the post-condition catches it and the fallback hides it, so the fallback warning must be alerted on.
- Employer NICs on benefits in kind (it is Class 1A) or employee NICs on benefits in kind (there are none).
- Confusing assignment year, UK tax year and Turkish calendar year in field names; always carry all three.
- Hard-coded rates in code; mutable results that drift when rates change; no engine or rate-set versioning.
- Adding immigration fees into the employment-cost benchmark; treating the £30,000 hypothetical tax as fixed when the reference pack says calculate it.
- Letting an LLM produce or alter numbers; caching across tenants; logging salaries; request bodies in error reports; sequential IDs; idempotency keys scoped globally rather than per tenant.
- Fetching FX or rates live during a calculation.
- Declaring Postgres-only constraints in `Meta` (breaks SQLite) or only in Python (breaks under concurrency); trusting `effective_to IS NULL` in ad-hoc queries instead of the provider.
- Building microservices, accounts, dashboards or other extras before the core number is right.
- Returning partial numbers when the solver fails.

### Design details worth drawing attention to

- `net(G)` is piecewise linear and strictly increasing, hence a unique solution and an exact segment solve, cross-checked against the reference pack's iteration.
- The rounding policy that never under-delivers the guarantee and reproduces the reference pack to the pound, with "net delivered £66,000.26" in the trace.
- The marginal cost of one extra pound of net allowance, £2.17 in the reference case, shown per year.
- Two cost decompositions (gross cash plus employer charges plus benefits; and cash to the employee plus taxes to HMRC plus benefits plus employer-only charges) with a reconciliation line.
- Results pinned to `rate_set_ids` and `engine_version`; "compare two versions" falls out of immutability for free.
- Rate-set provenance (label, version, checksum, source URLs, approved_at) and the disclaimer version inside every result.
- The Postgres exclusion constraint on effective ranges: overlapping rate periods are impossible, not merely unlikely.
- A golden self-test endpoint that runs the reference example in production against both providers.
- Coded warnings and assumptions, each with template text and a tailoring question; a trace that cites HMRC manual paragraphs per step.
- The hypothetical-tax result carrying its own CALCULATED or OVERRIDE mode, so the UI cannot mislabel it.
- The engine driven identically from the web form, the JSON API and a management command.
- The FX snapshot stored with the scenario so a result is reproducible years later.
- A route capability matrix that makes "unsupported" a first-class, honest state.
- Immigration costs carrying `payer` and `cannot_be_recouped_from_worker` with sources and verification levels, computed as formulas, and never entering the employment benchmark.

### Gaps a tax reviewer would raise, handled as assumptions or warnings

- Employer pension auto-enrolment (3% minimum on qualifying earnings) and the Apprenticeship Levy (0.5% above a £15,000 allowance for large pay bills): `AUTO_ENROLMENT_MAY_APPLY`, `APPRENTICESHIP_LEVY_MAY_APPLY`, driven by tenant settings.
- NICs are strictly per pay period; the annual basis is an assumption (`ANNUAL_NIC_BASIS`).
- Living accommodation is taken as a cash-equivalent input (`BIK_CASH_EQUIVALENT_AS_INPUT`); the statutory valuation and the owned-property charge are not modelled.
- Relocation qualifying status and timing are assumed (`RELOCATION_QUALIFYING_ASSUMED`).
- Employment Allowance not applied (`EMPLOYMENT_ALLOWANCE_NOT_APPLIED`); Overseas Workday Relief not modelled (`OWR_NOT_MODELLED`, four-year regime with a cap).
- Home-country residence risk in the departure year and UK split-year treatment (`HOME_COUNTRY_TAX_RESIDENCE_RISK`) belong to calendar-accurate mode.
- Mandatory payrolling of benefits from April 2027 changes reporting, not cost (`PAYROLLING_REPORTING_CHANGE_2027`).
- The home-scheme social security option requires a certificate of coverage and keeps home employer contributions (`HOME_SCHEME_CERTIFICATE_REQUIRED`).
- The gross-up assumes the employer operates PAYE on the grossed-up pay within the same tax year, as the reference pack's HMRC references describe; where tax is settled after year end it can fall into the next year (`MODIFIED_PAYE_SAME_YEAR_GROSSUP`).
- Personal allowance entitlement is assumed because the employee is UK resident (`PERSONAL_ALLOWANCE_ENTITLED`); it is irrelevant at the reference salary, where the allowance tapers to nil, but matters for lower salaries.

### What would matter if this shipped to real users

- The rules-maintenance process and liability framing matter more than code: a written rate-sourcing procedure (who re-verifies HMRC and GİB figures after each Budget, with a rates calendar), how corrections reach customers whose saved runs used the old table, and an SLA for landing a Budget change.
- A visible change log of rates and content, verification dates on every panel.
- Accessibility (WCAG 2.2 AA, GOV.UK-style forms) and en-GB number formatting for HR users, print-friendly results, exports that finance can file.
- Support tooling: a staff view that reproduces any run from its snapshot.
- Staged rollout with per-tenant flags; a shadow comparison when the engine changes.
- Data residency, retention policy, data-processing terms, disclaimer stored with every result.

---

## 11. The slice, and how it maps to production

Target repository layout (src layout, one Django project, neutral package names). The slice implements the subset shown in the table below; production-only parts are named here so that they have a place to go.

```
pyproject.toml                 # setuptools, ruff, mypy, pytest config
ops/{Dockerfile, compose.yml, entrypoint.sh}
.github/workflows/ci.yml       # sqlite + postgres matrix
docs/ARCHITECTURE.md docs/ASSUMPTIONS.md docs/VERIFICATION.md docs/DEMO.md docs/rate-set-sourcing.md docs/adr/
src/teq_engine/                # pure engine package
  money.py types.py treatments.py warnings.py trace.py piecewise.py solver.py periods.py calculator.py reference.py
  ratesets/{schemas.py, provider.py, data/gb/*.yaml, data/tr/*.yaml}
  jurisdictions/gb/{income_tax.py, nic.py, benefits.py}
  jurisdictions/tr/{income_tax.py, social_security.py, stamp_tax.py, hypo.py}
  routes/{registry.py, tr_gb.py}
src/teq_guidance/              # pure immigration guidance package (stdlib only)
  model.py loader.py tailoring.py costs.py timeline.py panel.py formatting.py
  data/tr_gb_skilled_worker.json
src/teq_web/                   # Django project
  settings/{base,dev,test,prod}.py urls.py wsgi.py
  {web, scenarios, api, ops, narration}/ in the slice; accounts, refdata, immigration, sharing and audit are added in Horizon B
tests/{engine, golden, guidance, api, tenancy, web, postgres}/  tests/golden/*.json
```

| Production component | Slice status |
|---|---|
| Engine: UK income tax, NICs, benefit rules, treatments, segment solver with iteration oracle, whole-year periods, trace, warnings, bundled YAML, reference example | **Must: implemented** with golden, boundary and core property tests |
| Turkish hypothetical tax, calculated and override | **Must: implemented** (2026 constants, hand-checked test) |
| Web UI: form with item rows, results page in the reference pack's order, flags, reference-example link, NIC-coverage toggle, trace drawer, narrative, immigration panel, unsupported-route page | **Must: implemented** (templates; htmx optional) |
| Immigration content for TR to GB with tailoring questions and verified date | **Must: implemented** as a bundled JSON pack in `teq_guidance`, with cost formulas, the critical-path timeline and tests |
| Template narrator behind the `Narrator` protocol | **Must: implemented** |
| `POST /api/v1/estimates`, `GET /routes`, `GET /reference-example`, problem details, `/healthz`, `/selftest/golden`, OpenAPI snapshot | **Should: implemented** (thin layer over the same service; large signal for small effort) |
| `manage.py estimate --example` | **Should: implemented** (proves engine separation) |
| `RateSet` and `GuidancePack` models, guarded exclusion-constraint migration, `load_rate_sets`, database provider with bundled fallback | **If time: implemented** without approval UI; otherwise documented with the migration SQL |
| Tenant, User, Membership, Scenario, ScenarioVersion, CalculationRun models | **Stubbed**: models and migrations with a default tenant, "save estimate" only, no auth |
| API keys, idempotency, pagination, throttling, share links, audit, triggers, jobs, exports, LLM narrator, OpenTelemetry, calendar-accurate mode, RLS, flags | **Documented only** (interfaces named, ADR stubs) |

Slice mechanics chosen so they survive into production: the form POSTs, the view validates, then redirects to a GET results URL carrying the canonical inputs as a compact URL-safe string (bounded to a few kilobytes), so a result is reloadable, shareable and reproducible with no database, and the reference example is simply one such link. The page shows the reference pack's three treatment labels, the trace drawer, the assumptions, the immigration panel with computed costs and the critical-path timeline, and the narrative. Region is ENG only; SCT is refused with the capability message.

Order of deferral if scope must shrink: RateSet models (keep bundled YAML), API and ops endpoints, Scottish sensitivity, side-by-side what-if, Turkish module reduced to override plus documented formula with the gap stated openly. Nothing in the "must" rows is deferred.

---

## 12. Verification (acceptance criteria)

The slice is accepted when every item below holds. [VERIFICATION.md](VERIFICATION.md) records how each is checked and what could not be checked from the build environment.

- **Golden corpus** in `tests/golden/*.json` (inputs, rate-set labels, engine version, expected figures, trace step count): reference example years 1 and 2 (G £127,762; income tax £57,196; employee NICs £4,566; employer NICs £18,414; Class 1A £4,500; totals £188,676, £180,676, £369,352); relocation £10,000 (£2,000 taxable BIK, £300 Class 1A); home-scheme social security (NICs zero); income inside the taper band; income below the personal allowance; Scotland and other routes refused.
- **Golden-diff gate**: CI runs the corpus; any numeric difference fails unless the change updates the golden files, bumps the engine version and adds an entry to `docs/engine-changes.md` (checked by a script); `CODEOWNERS` on the engine and golden directories.
- **Property tests** (hypothesis): on the exact gross, the net equals the guarantee and one penny less gross falls below it; after rounding, net delivered is at or above the guarantee; the relocation cap is consumed once per move across years; G strictly increasing in the target and in the benefit value; segment solve equals bisection and equals the fixed-point oracle to the penny; tax and NIC functions continuous, non-decreasing, marginal below 1; both cost decompositions foot; determinism (same inputs give an identical serialised hash); year-restricted items leave other years unchanged; no float anywhere in the result tree; TRY to GBP round trip within rounding.
- **Boundary tests** read thresholds from the rate set and test at the value and one penny either side (personal allowance, £50,270, £100,000, £125,140, primary threshold, upper earnings limit, relocation cap, SGK ceiling), zero salary, 1 and 10 years.
- **Guidance tests**: the pack loads and validates, and validation rejects broken packs; the reference scenario's immigration costs (CoS £525, Immigration Skills Charge £2,640, visa fee £819, health surcharge £2,070; employer-mandatory £3,165, applicant side £2,889); licence and dependant variants; conditional documents; critical-path ranges against an independent path enumeration; no floats in the panel.
- **Tenancy tests** (Horizon B): parametrised over every scoped endpoint, another tenant's key gets 404; lists never leak; manager without context raises; share links cross tenants correctly.
- **Contract tests**: committed `openapi.json` snapshot; schemathesis fuzz against the running app; problem-detail shape on every error path; idempotency replay and concurrent duplicate; two-thread version-conflict test (exactly one 201 and one 409).
- **Postgres-only marker**: exclusion overlap, triggers, GIN queries, RLS.
- **Catalogue completeness**: every warning and assumption code the engine can emit has template text and a tailoring question.
- **Manual rehearsal**: fresh clone, install, migrate, run; load the reference link; change housing; flip the NIC toggle; open the immigration panel and the trace; all in under ten minutes.

---

## 13. Design review: gaps found and fixed

A deliberate second reading of the design for logical and functional holes, not for style. Each item below changed the design.

| Gap | Why it mattered | Fix |
|---|---|---|
| Home-scheme toggle removed UK NICs but computed nothing in their place | The assignment would look about £23,000 a year cheaper than it is; Turkish employer contributions continue | Engine computes the Turkish employer contribution from the TR_SGK rate set and shows it as its own line with a warning (section 3.1) |
| Relocation cap applied per year | The £8,000 exemption is per move; a £6,000 plus £6,000 split would have been treated as fully exempt | `cap_scope: PER_MOVE`, consumed cumulatively; window is years 1 and 2, warning from year 3 (section 3.1) |
| Salary assumed to be in GBP | A Turkish assignee is normally paid in lira; the net guarantee and the multiple of salary need a defined conversion | Salary may be GBP or TRY with a stated conversion rule and a code (section 3.1) |
| Immigration costs stored as fixed numbers | Health surcharge, skills charge and dependant fees scale with visa length and family; fee tiers depend on length and location | Cost items carry a basis, a tier rule and a payer; the panel computes them for the scenario (section 3.4) |
| Timeline as a flat list | Stages overlap and some are conditional; a flat list overstates or understates the total | Dependency graph with critical path, shown as ranges, never as a date (section 3.4) |
| Employer of record not asked | It decides both the social security position and which visa route is relevant | Added as a tailoring question with a non-deciding note (section 3.4) |
| Results not pinned to the guidance content version | Immigration content changes; a saved result could not be reproduced | `guidance_pack_id` stored on the run and in the fingerprint (section 6) |
| No scoping column on scenario versions | Tenancy filters would need joins and are easier to get wrong | Tenant denormalised on versions as well as runs (section 6) |
| Open-ended rate sets and the exclusion constraint | Two open-ended rows for one category would collide at approval time unless the previous row is closed in the same transaction | Approval closes the previous row atomically; NULL handling stated (section 6) |
| Idempotency rows described as immutable | They must move from in-progress to done or failed, or a crashed request blocks retries forever | Explicit three-state lifecycle (section 6) |
| Property-test invariant stated on the rounded gross | After rounding up, one penny less gross can still meet the guarantee, so the test would be wrong | Invariants stated on the exact gross; rounded guarantee stated separately (sections 3.2 and 12) |
| Scotland described inconsistently | A code implying "computed with England rates" when the region was actually refused | Region SCT refused in v1 with a capability message; Scottish rate set is a later addition (sections 9 and 11) |
| No results URL design for the slice | Refreshing a POST result or sharing it would not work without a database | Post-redirect-get with the canonical inputs encoded in the URL (section 11) |
| Two unstated tax assumptions | Same-year gross-up through PAYE, and personal allowance entitlement | Added as assumption codes (section 10, Appendix B) |
| Degraded mode covered rates but not guidance | The results page would fail on the immigration panel when the database is down | Bundled guidance pack fallback (section 4) |
| Engine dependency list omitted the YAML reader | The bundled provider reads YAML | PyYAML listed (section 4) |
| No recovery objectives, no data-subject lookup, no audit-chain locking | Each is a question a compliance customer asks on day one | Added (sections 6, 8 and 9) |

Checked and left unchanged: the gross-up breakpoints, the rounding policy, the reference example reproduction, the exclusion constraint SQL, the role split, the choice of a modular monolith, and the decision to keep immigration costs out of the employment benchmark.

---

## Appendix A. Result schema (shared by the HTML view, the API and the stored snapshot)

```json
{
  "schema_version": "1",
  "engine_version": "0.1.0",
  "status": "OK",
  "rates_as_of": "2026-10-08",
  "rate_set_ids": ["UK_INCOME_TAX:2026-27:v1", "UK_NIC:2026-27:v1", "UK_BENEFIT_RULES:2026-27:v1", "TR_INCOME_TAX:2026:v1", "TR_SGK:2026:v1"],
  "rate_set_fingerprint": "sha256:…",
  "cache_key": "sha256:…",
  "period_mode": "ILLUSTRATIVE_WHOLE_YEAR",
  "inputs_hash": "sha256:…",
  "currency": "GBP",
  "rounding": {"gross_cash": "CEIL_TO_UNIT", "lines": "HALF_UP_TO_UNIT", "totals": "SUM_OF_ROUNDED_LINES"},
  "hypothetical_tax": {"mode": "OVERRIDE", "amount": "30000.00", "components": null, "fx": null},
  "net_guarantee": {"salary": "90000.00", "hypothetical_tax": "30000.00", "hypothetical_tax_on_salary": "30000.00", "hypothetical_tax_on_equalised_items": "0.00", "net_salary": "60000.00", "net_allowances": "6000.00", "net_cash_target": "66000.00"},
  "items": [
    {"id": "cola", "kind": "COLA", "treatment": "NET_CASH", "amount": "6000.00", "years": "ALL", "nic_class": "CLASS_1"},
    {"id": "housing", "kind": "HOUSING", "treatment": "TAXABLE_BIK", "amount": "30000.00", "years": "ALL", "nic_class": "CLASS_1A"},
    {"id": "relocation", "kind": "RELOCATION", "treatment": "EXEMPT_CAPPED", "amount": "8000.00", "cap": "8000.00", "excess": "0.00", "years": [1]}
  ],
  "years": [
    {"assignment_year": 1, "uk_tax_year": "2026-27", "tr_calendar_year": 2026,
     "lines": [
       {"code": "GROSS_CASH", "amount": "127762", "trace_ref": "t1"},
       {"code": "TAXABLE_PAY", "amount": "157762"},
       {"code": "PERSONAL_ALLOWANCE", "amount": "0"},
       {"code": "INCOME_TAX", "amount": "57196"},
       {"code": "EMPLOYEE_NIC", "amount": "4566"},
       {"code": "NET_CASH", "amount": "66000"},
       {"code": "EMPLOYER_NIC", "amount": "18414"},
       {"code": "CLASS_1A", "amount": "4500"},
       {"code": "BENEFIT_COST", "amount": "30000"},
       {"code": "EXEMPT_COST", "amount": "8000"},
       {"code": "TOTAL_EMPLOYER_COST", "amount": "188676"},
       {"code": "MULTIPLE_OF_SALARY", "amount": "2.10"},
       {"code": "MARGINAL_COST_PER_NET_POUND", "amount": "2.17"}
     ]}
  ],
  "totals": {"total_employer_cost": "369352"},
  "warnings": [{"code": "SOCIAL_SECURITY_AGREEMENT_MAY_APPLY", "severity": "info", "text": "…"}],
  "assumptions": [{"code": "UK_RESIDENT_FULL_YEAR"}, {"code": "ENGLAND_RATES"}, {"code": "UK_NIC_APPLIES"}, {"code": "OWR_NOT_MODELLED"}, {"code": "RATES_UNCHANGED_LATER_YEARS"}, {"code": "MODE_ILLUSTRATIVE_WHOLE_YEAR"}, {"code": "ANNUAL_NIC_BASIS"}, {"code": "BIK_CASH_EQUIVALENT_AS_INPUT"}],
  "trace": [{"id": "t1", "step": "gross_up", "method": "segment", "segment": "IT 45% + NIC 2%", "marginal_rate": "0.47", "gross_exact": "127761.51", "gross_rounded": "127762", "net_delivered_exact": "66000.26", "refs": ["PAYE81740"]}],
  "provenance": [
    {"id": "UK_INCOME_TAX:2026-27:v1", "checksum": "sha256:…", "source": "https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027", "approved_at": "…"},
    {"id": "GUIDANCE:TR-GB:v1", "checksum": "sha256:…", "verified_at": "2026-10-08"}
  ],
  "disclaimer_version": "2026-10"
}
```

Design notes: each item's `allocations[]` carry a `hypothetical_tax_share` and, for capped relocation, two `lines` (exempt and taxable); `hypothetical_tax.fx` is filled when an exchange rate fed the calculated comparison beside an override; amounts are decimal strings; every line has a stable `code` so templates, tests and the API never depend on labels; `trace_ref` links a line to its trace step; the rounding block makes the totals reproducible. `GUIDANCE:TR-GB:v1` is the `pack_id` that `teq_guidance` reports for the bundled pack.

## Appendix B. Warning and assumption code catalogue (initial)

The engine's catalogue (`teq_engine.warnings`) holds every code below, plus `UK_RESIDENT_FULL_YEAR`, `UK_NIC_APPLIES`, `RATES_UNCHANGED_LATER_YEARS` (assumptions used by the result schema), the errors `RATES_UNAVAILABLE` and `FX_RATE_IN_FUTURE`, the warning `EQUALISED_ITEM_NO_HYPO_SHARE`, and the guidance panel warning `VISA_LENGTH_EXCEEDS_SINGLE_GRANT`. Plain-English explanations of each are in [ASSUMPTIONS.md](ASSUMPTIONS.md).

| Code | Kind and severity | Meaning | Shown action or tailoring question |
|---|---|---|---|
| `ROUTE_UNSUPPORTED` | error | Route not in the capability matrix | Lists supported routes; calculation refused |
| `GROSS_UP_NOT_CONVERGED` | error | Solver failed on both methods | No figures shown; alert |
| `GROSS_UP_FALLBACK_BISECTION` | warning | Segment solve post-condition failed; bisection used | Alert; result still valid |
| `SOCIAL_SECURITY_AGREEMENT_MAY_APPLY` | info | Bilateral agreement exists for the route | Toggle UK NICs; certificate of coverage; home employer contributions |
| `NIC_EXEMPTION_ASSUMED` | warning | UK NICs and Class 1A removed | Home-country employer contributions continue and are shown as their own line |
| `HOME_SCHEME_CERTIFICATE_REQUIRED` | assumption | Home scheme chosen | Who obtains the certificate and by when |
| `PERSONAL_ALLOWANCE_TAPER_BAND` | info | Taxable pay between £100,000 and £125,140 | Explains the 60% effective marginal rate |
| `RATES_NOT_PUBLISHED_FOR_YEAR` | warning | Later year carried forward | Names the proxy year |
| `RATE_SET_SUPERSEDED` | info (read time) | Newer rules exist since this run | Offer rerun |
| `MODE_ILLUSTRATIVE_WHOLE_YEAR` | assumption | Whole aligned years | Calendar-accurate mode is future work |
| `RELOCATION_EXCESS_TAXABLE` | warning | Relocation above the cap | Excess treated as taxable benefit with Class 1A |
| `RELOCATION_OUTSIDE_WINDOW` | warning | Relocation in year 3 or later, outside the exemption window | Depends on job start date; treated as taxable |
| `SALARY_CONVERTED_AT_SNAPSHOT_FX` | info | Salary entered in lira and converted | Rate and date shown |
| `HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED` | warning | Turkish employer contributions computed in home-scheme mode | Indicative rate and ceiling; confirm with the Turkish payroll |
| `REGION_NOT_SUPPORTED` | error | Region outside the capability matrix (Scotland in v1) | Calculation refused; England supported |
| `MODIFIED_PAYE_SAME_YEAR_GROSSUP`, `PERSONAL_ALLOWANCE_ENTITLED` | assumption | Same-year PAYE on grossed-up pay; allowance available because UK resident | None at the reference salary |
| `RELOCATION_QUALIFYING_ASSUMED` | assumption | Items assumed to qualify | Are all items on the qualifying list and within the time limit? |
| `HYPO_TAX_OVERRIDE` | assumption | Home tax figure supplied | Shows the calculated figure for comparison when available |
| `FX_RATE_USER_SUPPLIED`, `FX_RATE_STALE`, `FX_RATE_IMPLAUSIBLE` | info or warning | Exchange-rate provenance | Date and source; re-enter if stale |
| `ENGLAND_RATES` | assumption | England rates used | Scotland changes income tax, not NICs; refused in v1 |
| `ANNUAL_NIC_BASIS` | assumption | NICs computed annually | Monthly payroll differs slightly |
| `BIK_CASH_EQUIVALENT_AS_INPUT` | assumption | No accommodation valuation performed | Is the property rented by the employer; any employee contribution? |
| `EMPLOYMENT_ALLOWANCE_NOT_APPLIED` | assumption | No employer NIC allowance | Tenant eligibility |
| `OWR_NOT_MODELLED` | assumption | Overseas Workday Relief ignored | Any non-UK workdays expected? |
| `AUTO_ENROLMENT_MAY_APPLY`, `APPRENTICESHIP_LEVY_MAY_APPLY` | warning | Further employer costs possible | Tenant settings |
| `HOME_COUNTRY_TAX_RESIDENCE_RISK` | warning | Departure-year residence | Treaty position; calendar-accurate mode |
| `PAYROLLING_REPORTING_CHANGE_2027` | assumption | Reporting change, not cost | None |
| `DEGRADED_BUNDLED_RATES`, `PERSISTENCE_UNAVAILABLE` | warning | Database unavailable | Result not saved; retry later |
| `NARRATIVE_FALLBACK` | info | LLM narration unavailable or rejected | Template text shown |
| `IMMIGRATION_CONTENT_STALE` | warning | Guidance verified too long ago | Re-verify fees and timings |

## Appendix C. Rate-set data format (engine YAML, later the `RateSet.data` column)

```yaml
jurisdiction: UK-ENG
category: UK_INCOME_TAX
label: "2026-27"
version: 1
effective_from: "2026-04-06"
effective_to: "2027-04-05"
sources:
  - url: https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027
    verified_at: "2026-10-08"
    verified_by: "…"
checksum: "sha256:…"
data:
  personal_allowance: "12570"
  taper_start: "100000"
  taper_rate: "0.5"
  bands:
    - {upto: "37700", rate: "0.20"}
    - {upto: "125140", rate: "0.40"}
    - {upto: null, rate: "0.45"}
```

Rules: amounts are strings; the engine contains no numbers; a rate set missing a required key or with a marginal rate at or above 100% fails loading at start-up, not at request time. The guidance pack follows the same rules in JSON (see section 3.4).

## Appendix D. Exclusion constraint for effective ranges (Postgres-only migration)

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE refdata_rateset
  ADD CONSTRAINT rateset_no_overlap
  EXCLUDE USING gist (
    jurisdiction WITH =,
    category WITH =,
    daterange(effective_from, effective_to, '[)') WITH &&
  ) WHERE (status IN ('APPROVED', 'SUPERSEDED'));
```

Applied through a migration operation guarded by `schema_editor.connection.vendor == "postgresql"` (with `BtreeGistExtension` guarded the same way), not declared in `Meta`; `RateSet.clean()` repeats the overlap check in Python for SQLite development. An open-ended row stores `effective_to = NULL`, which `daterange` treats as unbounded.

## Appendix E. `POST /api/v1/estimates` example

Request:

```json
{
  "route": {"home": "TR", "host": "GB", "region": "ENG"},
  "assignment": {"length_years": 2, "period_mode": "ILLUSTRATIVE_WHOLE_YEAR"},
  "salary": {"amount": "90000.00", "currency": "GBP", "frequency": "ANNUAL"},
  "hypothetical_tax": {"method": "OVERRIDE", "override": "30000.00", "includes_social_security": true, "base": "SALARY_ONLY"},
  "items": [
    {"id": "cola", "kind": "COLA", "amount": "6000.00", "currency": "GBP", "frequency": "ANNUAL", "years": "ALL"},
    {"id": "housing", "kind": "HOUSING", "amount": "30000.00", "currency": "GBP", "frequency": "ANNUAL", "years": "ALL"},
    {"id": "relocation", "kind": "RELOCATION", "amount": "8000.00", "currency": "GBP", "frequency": "ONE_OFF", "years": [1]}
  ],
  "assumptions": {"uk_resident": true, "social_security": "UK_NIC", "owr_claimed": false},
  "rates_as_of": "2026-10-08",
  "options": {"include_trace": true, "include_narrative": true, "include_immigration": true}
}
```

Response: `200` with the Appendix A body. Validation failure: `422` problem details with `errors: [{"pointer": "/hypothetical_tax/override", "message": "must be below salary"}]`. Unsupported route: `422` with code `route-not-supported` and `supported_routes`. Safe to cache by the result's `cache_key`, which is derived from `inputs_hash`, `rate_set_fingerprint` (sorted rate-set ids with their content checksums), `engine_version` and `rates_as_of`; the date must be in the key because the same inputs map to different tax years on different dates.

## Appendix F. Reference pack versus current guidance (as found on 7 and 8 October 2026)

The reference pack is the assignment brief; where current official guidance differs, the tool follows the guidance and says so. Items marked "search-confirmed" were confirmed from search results quoting the source, because the build environment could not open the pages directly; verify each on the page before relying on it. The same table is held in the guidance pack (`pack_vs_current_guidance`) and shown in the immigration panel.

| Topic | Reference pack | Current position | Effect on the tool |
|---|---|---|---|
| UK rates and thresholds | "2026/27 rates" | Thresholds and rates frozen to 2030/31, identical to 2025/26 (search-confirmed) | None numerically; the tax year is labelled explicitly |
| HS212 edition | Linked as "2026" | The 2026 edition is the one for 2025/26 returns (understanding, not verified) | None numerically |
| Employee NICs wording | "8% up to £50,270" | 8% between £12,570 and £50,270, 2% above | Computation already correct; label clarified |
| Two-year total | £369,352 | Sum of unrounded years is £369,351.47; £369,352 is the sum of lines rounded to the pound | Rounding policy stated; the reference pack's convention reproduced |
| Hypothetical Turkish tax | £30,000 assumed; "tool should work it out" | About £34,200 under 2026 rules at indicative exchange rates | Calculated figure shown beside any override; most material assumption |
| Turkish social security ceiling | Not stated | Raised to 9 times the minimum wage from 1 January 2026 (search-confirmed) | In the TR_SGK rate set |
| Social security agreement | "May stay in the Turkish scheme; flag it" | Convention exists (1961 Order); conditions are employer, insurance and duration; period limit not verified | Toggle plus Turkish employer line plus certificate assumption |
| Visa decision time | 3 weeks outside, 8 weeks inside | Same | None |
| English requirement | Not mentioned | B2 for new applications from 8 January 2026 (search-confirmed) | Documents and timeline |
| Tuberculosis test | Not mentioned | Turkey is on the list; clinics in Istanbul and Ankara (search-confirmed) | Conditional document and timeline stage |
| Immigration Skills Charge | Not mentioned | £1,320 and £480 a year from 16 December 2025 (search-confirmed) | Cost item with employer payer, cannot be recovered from the worker |
| Visa and sponsor fees | Not mentioned | Changed on 8 April 2026 (third-party reports) | Cost items with verified_at |
| Salary requirement | Not mentioned | £41,700 or the going rate, RQF 6, basic pay only (search-confirmed) | Guidance text; no eligibility inference |
| Accommodation value | Rent treated as the benefit | Statutory value is the greater of annual value and rent | Assumption `BIK_CASH_EQUIVALENT_AS_INPUT` |
| Payrolling of benefits | Not mentioned | Mandatory from April 2027, accommodation excluded | Assumption; no cost effect |
