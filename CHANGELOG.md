# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version is the project version, which is also the engine version (`teq_engine.__version__`) that every result and golden file records.

## 0.2.0 - 2026-10-08

No figure changed: every golden case reproduces exactly as under 0.1.0.

### Changed

- The notice that a later year's rates are not yet published (`RATES_NOT_PUBLISHED_FOR_YEAR`) names only the jurisdictions whose rates the scenario uses. Turkish rates are flagged only for a calculated hypothetical tax or home-scheme social security; under a supplied hypothetical tax they do not affect the figures and are not mentioned.
- `RATES_NOT_PUBLISHED_FOR_YEAR` has severity info rather than warning, since `RATES_UNCHANGED_LATER_YEARS` already states the assumption.
- A supplied hypothetical tax whose calculated comparison uses carried-forward Turkish rates says so in its note.
- The marginal-cost line reads "Cost to the employer of £1 more net pay".
- Results are presented as tabs, one per request: Inputs, Tax, Immigration and Explain. The Tax tab leads with the headline figures and a short summary, shows warnings as cards and information as a list of things to check, and puts the assumptions the reference pack asks for first. The Immigration tab puts the process, documents, costs, timeline and family before the tailoring questions. Tailoring answers carry across the tabs.
- A new scenario calculates the hypothetical tax from the Turkish rules by default, at an indicative exchange rate labelled as one to confirm; a supplied figure gets a one-click link to the calculated alternative.
- The guidance pack's verification levels are renamed `official_source`, `third_party` and `unverified`, shown as "Official source", "Third-party report" and "Not re-verified", each with a plain explanation of what to check.

### Added

- A plain-English immigration narrative on the Explain tab and in the API response (`immigration_narrative`), in which every number is checked against the guidance panel.
- Guidance requirements may carry a `family` topic, so partner and children's requirements are grouped under Family.

## 0.1.0 - 2026-10-08

### Added

- `teq_engine`: the calculation engine. UK income tax, National Insurance and benefit rules for England 2026/27; item treatments, including the per-move relocation cap; an exact piecewise-linear gross-up solver, checked against the reference pack's fixed-point iteration and with a bisection fallback; illustrative whole-year periods with carried-forward rates; the Turkish hypothetical tax (income tax, social security and stamp tax), calculated or supplied; the home-scheme Turkish employer contribution line; the catalogue of coded warnings and assumptions; the calculation trace; versioned YAML rate sets with sources; a route registry that refuses unsupported routes and regions; the reference example, reproduced to the pound.
- `teq_guidance`: the Turkey to UK Skilled Worker guidance as data, with standard-library code only. Tailoring questions with stated assumptions; conditional requirements and documents; costs as formulas with payers, tiers, subtotals and non-recoupable flags; a dependency-graph timeline with its critical path; pack validation.
- `teq_web`: the Django web app. The input form, the results page with the trace and the immigration panel, the compare view and the refusal pages, with results carried in the URL and no database; a JSON API on Django Ninja with OpenAPI documentation; `/healthz`, `/readyz` and `/selftest/golden`; the `estimate` management command.
- The golden corpus, property tests and boundary tests, and the documentation: architecture, assumptions and verification.
