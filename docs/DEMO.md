# Demo script and review notes

A ten-minute walk through the slice, the questions a reviewer is likely to ask with short answers, what is implemented, verified, simplified and left for later, and the questions to take to Ben or Octavian.

Before the demo: start the app (see the README), open `/example` in one tab and keep a terminal ready in the repository root.

---

## Ten-minute demo

### 0:00 to 1:00: what the tool answers

- "What does it cost the employer, and how long does it take, to move this employee from Turkey to England for two years under tax equalisation?"
- The engine is deterministic and pure: the same inputs and rate tables always give the same figures, and every figure carries its rule version, assumptions and a trace.
- Everything shown is an illustration, not tax or immigration advice.

### 1:00 to 3:30: the reference example, in the reference pack's order

Open `/example` (the reference scenario: salary £90,000, hypothetical tax £30,000, cost-of-living allowance £6,000 net, housing £30,000, relocation £8,000 in year 1).

1. **Headline.** Year 1 **£188,676**, year 2 **£180,676**, total **£369,352**, 2.10 times salary. These match the reference pack to the pound.
2. **Flags.** The social security agreement may apply; the hypothetical tax was supplied rather than calculated (under the 2026 Turkish rules it would be about £34,200); 2027/28 rates are not yet published, so 2026/27 rates are carried forward.
3. **Net guarantee.** £90,000 less £30,000 hypothetical tax, plus the £6,000 net allowance: £66,000 in the employee's pocket each year.
4. **Item treatment.** The reference pack's three labels: taxable cash (the allowance, grossed up), taxable benefit in kind (housing: income tax and Class 1A, no employee NICs), exempt (relocation, within the £8,000 cap per move).
5. **Gross-up.** Gross cash £127,762. Every extra pound here is taxed at 45% plus 2% NICs, so delivering £66,000 net takes £127,762 gross; the guarantee is never under-delivered (the employee actually gets £66,000.26).
6. **Employer charges.** Employer NICs £18,414 and Class 1A £4,500.
7. **Per-year table and assumptions.** Every table adds up; each assumption has a code and a question.

### 3:30 to 4:30: change housing

Change housing from £30,000 to £36,000 and resubmit. Year 1 becomes **£201,434** (up £12,758), year 2 **£193,434**, total **£394,868**. Point: £6,000 more housing costs about £12,800 a year, because the benefit is taxed at 45%, that tax is grossed up at 47%, and Class 1A and employer NICs follow.

### 4:30 to 5:30: flip the NIC toggle

Switch social security to the home scheme (staying in the Turkish scheme under the agreement). UK employee NICs, employer NICs and Class 1A drop to zero, and the gross-up falls because employee NICs no longer need grossing up. A new line shows the Turkish employer contributions that continue, with a warning that they are estimated and an assumption that a certificate of coverage is needed. Point: without that line the toggle would make the assignment look about £23,000 a year cheaper than it is.

### 5:30 to 7:30: the immigration panel

Return to the reference example and open the immigration panel.

- **Route note.** Skilled Worker, assuming the UK entity employs the worker.
- **Eligibility.** General requirements (licensed sponsor, CoS, RQF level 6 job, £41,700 or the going rate on basic pay only, English at B2 from 8 January 2026) versus circumstance-dependent ones (TB test: applies, because the applicant lives in Turkey). The tool never decides eligibility.
- **Costs, with payers and formulas.** Employer, required: CoS £525 and Immigration Skills Charge £2,640 (£1,320 then £660 per further six months), **£3,165**, none of which can be passed to the worker. Applicant side: visa fee £819 and health surcharge £2,070 (£1,035 x 2 years), **£2,889**, which many employers pay by policy. TB test TRY 3,000 to 5,000, listed separately. None of this is added to the employment cost.
- **Documents** with a reason for each conditional one.
- **Timeline.** About 6 to 16 weeks as a range, never a date; the critical path runs through the English test. Answer "no licence held" and it becomes about 10 to 21 weeks with the licence on the critical path, and £1,682 is added to the employer's costs. Answer "the Turkish employer posts the worker" and a note says the Global Business Mobility route may be the relevant one, and that this bears on social security.
- **Verified 8 October 2026**, each item marked search-confirmed, third-party reported or from knowledge, with GOV.UK links; unanswered questions listed under "what we would need to tailor this further".

### 7:30 to 9:00: the calculation trace

Open the trace drawer: the segment ("IT 45% + NIC 2%"), marginal rate 0.47, exact gross £127,761.51, rounded £127,762, net delivered £66,000.26, with HMRC manual references. One more pound of net allowance costs the employer £2.17 (1 / 0.53 x 1.15). The reference pack's iteration is kept as a test oracle and agrees with the exact solve to the penny.

### 9:00 to 10:00: same engine, three front doors, and honest refusals

```bash
python src/manage.py estimate --example          # same figures in the terminal
curl -s http://127.0.0.1:8000/api/v1/reference-example
curl -s http://127.0.0.1:8000/selftest/golden    # production self-test of the reference example
```

Request Scotland or another route: the tool refuses with the supported routes rather than guessing. Close with the four lists below.

---

## Likely review questions

| Question | Short answer |
|---|---|
| Why not iterate like the reference pack? | Net pay is piecewise linear and strictly increasing in gross pay, so there is exactly one answer and it can be solved exactly on the right segment. The iteration is kept as a test oracle and agrees to the penny. |
| Why £369,352 and not £369,351.47? | The gross is rounded up to the pound so the guarantee is never short, every line is rounded to the pound, and totals are sums of rounded lines so tables add up. That reproduces the reference pack exactly and is stated. |
| Why is year 2 the same as year 1 without relocation? | 2027/28 rates are not published, so 2026/27 rates are carried forward with a warning. UK thresholds are frozen to 2030/31 in any case. |
| Is £30,000 the right hypothetical tax? | It is the reference pack's figure, used as a supplied override. Under 2026 Turkish rules it is about £34,200 at indicative exchange rates. It is the most material assumption and is flagged as such. |
| Why are immigration costs not in the total? | They are a different kind of cost with different payers and some are optional; the benchmark is the cost of employment. They are shown in full beside it, never added to it. |
| Are the visa fees right? | Each fee says how it was verified. The Immigration Skills Charge and health surcharge are search-confirmed; the April 2026 visa and sponsor fees are third-party reported and need checking on GOV.UK. |
| What about Scotland? | Refused in this version rather than calculated at the wrong rates. A Scottish income tax rate set is a later addition; National Insurance is UK-wide. |
| What if the employee stays in Turkish social security? | Flip the toggle: UK National Insurance goes, the continuing Turkish employer contributions appear as their own line, and a certificate of coverage is flagged as required. |
| How are rates kept current? | Rates are data with effective dates, sources and checksums. In production a rules maintainer drafts, a second person approves, the golden scenarios rerun on approval, and saved results are pinned to the rules they used. |
| Why a monolith, not microservices? | A calculation takes under a millisecond. A modular monolith with a separately installable engine keeps boundaries without network failure modes. |
| How would multi-tenancy work? | Tenant-scoped managers that refuse to run without a tenant, the tenant stored on every owned row, 404 rather than 403 across tenants, row-level security later. |
| Could an AI change the numbers? | No. The slice uses a template narrator. An optional AI narrator would only describe the result, and any text containing a number not in the result is discarded. |
| Is housing valued correctly? | It is taken at the cash value entered. The statutory accommodation valuation is not performed, and that is a stated assumption. |
| Is the immigration timeline a date? | No. It is a range from the critical path of the stages that apply, because holidays and appointment availability vary. |
| What happens with an unsupported route? | An explicit message naming the supported routes and what adding one requires. Never a guess. |

---

## Implemented

The slice as scoped in [ARCHITECTURE.md, section 11](ARCHITECTURE.md#11-the-slice-and-how-it-maps-to-production):

- **Engine (`teq_engine`)**: UK income tax, NICs and benefit rules for England 2026/27; item treatments including the per-move relocation cap; the exact segment gross-up solver with the iteration oracle and a bisection fallback; illustrative whole-year periods with carried-forward rates; the warnings and assumptions catalogue; the trace; bundled YAML rate sets with sources; the reference example; the Turkish hypothetical tax, calculated or supplied; the home-scheme Turkish employer contribution line; the route registry (Turkey to England only).
- **Guidance (`teq_guidance`)**: the Turkey to UK Skilled Worker pack as versioned data with sources and verification levels; tailoring questions with stated assumptions; conditional documents and requirements; costs as formulas with payers, tiers and subtotals; the critical-path timeline; the panel builder; pack validation.
- **Web, API and operations (`teq_web`)**: the input form with item rows, the results page in the reference pack's order with flags, the NIC coverage toggle, trace drawer, narrative and immigration panel, the unsupported-route page; `POST /api/v1/estimates` and the metadata endpoints with OpenAPI docs; `/healthz`, `/readyz`, `/selftest/golden`; `manage.py estimate --example`.
- **Documentation**: architecture, assumptions, verification, this demo script and the README.

## Verified

- The reference example reproduces the reference pack to the pound (golden test, the self-test endpoint, and the hand recomputation in [VERIFICATION.md](VERIFICATION.md#7-hand-recomputation-of-the-reference-example)).
- The engine's golden corpus, property tests and boundary tests; the guidance package's tests (pack validation, reference immigration costs, variants, conditional content, critical path against path enumeration, plain-data panel).
- Lint (ruff) and strict typing (mypy) on the engine and the guidance package.
- Not verified from the build environment: the official pages themselves (GOV.UK, legislation.gov.uk, gib.gov.tr, thecozm.com). Third-party reported fees need checking on GOV.UK; see [VERIFICATION.md, section 6](VERIFICATION.md#6-what-could-not-be-verified-from-this-environment).

## Simplified

- Whole tax years, not actual dates; England only; one route (Turkey to the UK).
- NICs calculated annually; housing taken at the value entered; no Overseas Workday Relief; no Employment Allowance; auto-enrolment and the Apprenticeship Levy flagged, not costed.
- Hypothetical tax base is salary only; Turkish tax computed annually rather than month by month.
- Single tenant, no login; results carried in the URL rather than saved; rate sets read from bundled files rather than a database with an approval workflow; template narrative only.
- Immigration: Skilled Worker route only, guidance not a decision, fees and timings as at 8 October 2026, the TB test price left in lira, typical stage durations rather than live processing times.

## Future work

- Calendar-accurate mode (split years, prorated thresholds, departure-year residence), Scotland, more routes and countries.
- Multi-tenancy with authentication, roles, API keys, idempotency, pagination and throttling; saved scenario versions, comparison, share links, exports and an audit log.
- Rate sets and guidance packs in the database with four-eyes approval, the Postgres exclusion constraint, golden reruns on approval and `RATE_SET_SUPERSEDED` on old runs.
- Optional AI narration behind a feature flag, with number validation and template fallback.
- Overseas Workday Relief, statutory accommodation valuation, auto-enrolment and the Apprenticeship Levy driven by tenant settings.
- Immigration: the Global Business Mobility route, live fee updates through the content workflow, currency conversion for the TB test, more home countries.
- Observability: structured logs without salaries, metrics, tracing and alerts.

---

## Questions to take to Ben or Octavian

1. **Employer of record.** Will the UK entity employ the assignee, or will the Turkish employer keep the employment and post them? This decides the visa route (Skilled Worker or Global Business Mobility) and bears on the social security position.
2. **Country breadth.** Which routes matter next, and how soon? The design adds a country as rate sets plus a guidance pack, but each needs a specialist to source and maintain the rules.
3. **Hypothetical-tax policy.** Should the tool calculate the Turkish hypothetical tax from the rules (about £34,200 here) or use a policy figure such as £30,000? On which base (salary only, or all equalised items), and including social security or not?
4. **Tax year and Scotland.** Are whole tax years acceptable for an illustration, or are actual start and end dates needed? Is Scotland in scope?
5. **Immigration costs.** Should immigration costs stay separate from the employment cost (as now) or be combined into one figure? By policy, who pays the visa fee and health surcharge, and the dependants' fees?
