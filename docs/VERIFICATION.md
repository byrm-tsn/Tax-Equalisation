# Verification

What is verified, how, and which facts have not been re-verified against the official pages. The acceptance criteria themselves are in [ARCHITECTURE.md, section 12](ARCHITECTURE.md#12-verification-acceptance-criteria).

Run everything with `make check`, or step by step:

```bash
pytest -q                                          # engine, golden corpus, guidance and web tests
ruff check src tests                               # lint
ruff format --check src tests                      # formatting
mypy src/teq_engine src/teq_guidance src/teq_web   # strict typing
```

The same steps run in continuous integration on every push and pull request (`.github/workflows/ci.yml`).

---

**Test counts as of 8 October 2026:** 676 in total: 279 engine (including the golden corpus and the boundary cases added after the reviews in section 8), 113 guidance, 284 web. `ruff check`, `ruff format --check` and strict `mypy` on all three packages are clean.

## 1. Golden figures (engine)

The golden corpus in `tests/golden/*.json` holds inputs, expected figures and the engine version for each case; `tests/engine/test_golden.py` runs it. A numeric difference fails the tests, and a golden file computed under another engine version fails too, so changing a figure means updating the golden files and bumping the engine version together; the change is recorded in [CHANGELOG.md](../CHANGELOG.md).

**Engine 0.2.0** (8 October 2026) changed no figure: `RATES_NOT_PUBLISHED_FOR_YEAR` became info rather than warning and names Turkish rates only when the scenario uses them (a calculated hypothetical tax or home-scheme social security), a supplied hypothetical tax whose comparison uses carried-forward Turkish rates says so in its note, and the marginal-cost line reads "Cost to the employer of £1 more net pay"; the golden files moved to 0.2.0 with it.

| Case | What it pins |
|---|---|
| `reference_example` | The reference pack's example, to the pound: gross cash £127,762, income tax £57,196, employee NICs £4,566, employer NICs £18,414, Class 1A £4,500; year 1 £188,676, year 2 £180,676, two-year total £369,352 |
| `relocation_10000` | Relocation of £10,000: £8,000 exempt, £2,000 taxable benefit with £300 Class 1A |
| `home_scheme_social_security` | UK NICs and Class 1A zero; Turkish employer contributions shown as their own line |
| `taper_band` | Income inside the £100,000 to £125,140 personal allowance taper |
| `below_personal_allowance` | Income below the personal allowance |
| `calculated_hypothetical_tax` | The Turkish hypothetical tax calculated from the 2026 rules rather than supplied |
| `unsupported_region_sct`, `unsupported_route_*` | Scotland and other routes refused with the capability message, never guessed |

The same reference example is the production self-test (`/selftest/golden`), which asserts £188,676 and £180,676.

## 2. Property tests (engine)

Hypothesis-based tests state the invariants on the exact gross before rounding: the net equals the guarantee and one penny less gross falls below it; after rounding, the net delivered is at or above the guarantee; the gross-up is strictly increasing in the target and in the benefit value; the exact segment solve agrees with bisection and with the fixed-point iteration (the reference pack's method) to the penny; tax and NIC functions are continuous and non-decreasing with marginal rates below 1; cost decompositions foot; the same inputs give an identical serialised hash; year-restricted items leave other years unchanged; no float appears anywhere in the result.

## 3. Boundary tests (engine)

Thresholds are read from the rate sets and tested at the value and one penny either side: the personal allowance (£12,570), the higher-rate threshold (£50,270), the taper start and end (£100,000 and £125,140), the NIC primary threshold and upper earnings limit, the employer secondary threshold (£5,000), the relocation cap (£8,000) and the Turkish social security ceiling; plus zero salary and assignments of 1 and 10 years.

## 4. Guidance tests (`tests/guidance`)

The immigration guidance package is tested separately (113 tests as of 8 October 2026):

- **Pack loads and validates.** The bundled pack loads; its question ids are exactly the `TailoringAnswers` fields; every item cites a known GOV.UK source; every cost has a basis, a payer, a verification level and a date; every employer cost is marked as not recoverable from the worker. Validation rejects, and reports together, an unknown dependency, a dependency cycle, a condition on an unknown question (including inside a fee tier), a condition value that is not one of the question's options, a cost without a basis or payer, an unknown basis, an unknown source, an item with no source, a bare number where an amount should be a string, an unknown verification level, `computed` on a pack item, and a wrong route.
- **Reference scenario costs.** Visa 2 years, applied for outside the UK, medium or large sponsor, licence already held, no dependants, resident in Turkey: Certificate of Sponsorship £525, Immigration Skills Charge £2,640 (£1,320 for the first 12 months plus £660 x 2 further 6-month periods), visa fee £819, health surcharge £2,070 (£1,035 x 2 years). Employer-mandatory subtotal £3,165; applicant side £2,889 (of which the employer may pay by policy £2,889); the TB test is listed separately in lira (TRY 3,000 to 5,000) and is not added.
- **Variants.** Without a licence the licence fee adds £1,682 (employer-mandatory £4,847); a small sponsor pays £611 and £960 of skills charge over two years; one partner and one child add £819 + £819 in fees and £2,070 + £1,552 in surcharge; three children, five-year visas and inside-UK applications use the right tiers; a fee the pack does not hold is shown as not held and kept out of the subtotals; maintenance funds include the dependants' amounts.
- **Conditional content.** Documents, stages and costs appear and disappear with the answers (TB certificate, criminal record certificate, funds evidence, dependants' documents, English evidence variants, the licence stage and fee). The condition operators (`equals`, `in`, `gte`, `gt`, `lte`, `all`, `any`) are tested directly, including that `true` never matches `1`.
- **Critical path.** With a licence held and a TB test needed: 43 to 112 days, 6 to 16 weeks, critical path English test, application, decision, travel. Without a licence: 70 to 145 days, 10 to 21 weeks, critical path licence, CoS, application, decision, travel. Totals are checked against an independent enumeration of every path for 16 combinations of answers, every stage on the critical path is marked (and only those), consecutive critical stages are linked by a dependency, and the parallel groups and phases are as expected.
- **Panel.** The panel holds only plain data (dicts, lists, strings, ints, bools, `None`; no floats, decimals, tuples or dates) and survives a JSON round trip; the posted-by-the-Turkish-employer answer (or "undecided") adds the Global Business Mobility route note; unanswered questions are listed with the assumption used; every item carries its verification level with its label, and the three levels are explained in words; content older than the configured age carries `IMMIGRATION_CONTENT_STALE`.

## 5. Manual checks

From a fresh clone, in under ten minutes:

1. Install, migrate, run (see the README).
2. Open `/example`: the headline shows year 1 £188,676, year 2 £180,676, total £369,352.
3. Change housing (for example to £36,000) and resubmit: the gross-up, Class 1A and totals move; the per-year table still foots.
4. Flip the NIC coverage toggle to home-scheme: UK NICs and Class 1A go to zero and the Turkish employer contribution line appears with its warning.
5. Open the Immigration tab: the process in order, documents with reasons, costs with payers and formulas, subtotals, the critical-path timeline as a range, family, the verified date, and each item's verification level and source.
6. On the Explain tab, open the calculation trace: the segment ("IT 45% + NIC 2%"), marginal rate 0.47, exact gross £127,761.51, rounded £127,762, net delivered £66,000.26.
7. `python src/manage.py estimate --example` prints the same figures as the page.
8. Request Scotland or another route: the capability message, not a number.

---

## 6. Facts not re-verified against the official pages

Every requirement, document, cost, stage and note in the immigration guidance pack carries one of three verification levels and a link to its GOV.UK source, and the Immigration tab shows both beside the item. What falls under each level:

- **Official source** (`official_source`): traced to the wording of the official page. This covers the Immigration Skills Charge (£1,320 for a medium or large sponsor and £480 for a small or charitable one, for the first 12 months, from 16 December 2025), the Immigration Health Surcharge (£1,035 a year, £776 for a child), the salary threshold (£41,700 or the going rate, basic pay only), the RQF level 6 requirement, English at B2 from 8 January 2026, the TB test for residents of Turkey, and the usual decision times (3 weeks from outside the UK, 8 weeks from inside). Fees change, usually each April, so check the linked page before relying on a figure.
- **Third-party report** (`third_party`): reported by third parties and not yet confirmed on the official page. **These need checking on GOV.UK before the figures are relied on.** They are the visa application fees from 8 April 2026 (£819 up to 3 years and £1,618 over 3 years from outside the UK, £943 up to 3 years from inside), for the main applicant and for each dependant; the sponsor licence fee from 8 April 2026 (£1,682 medium or large, £611 small or charitable); the licence priority service (about £750); the Certificate of Sponsorship fee for 2026 (£525: the same fee from 9 April 2025 is traced to the official page, but not that it is unchanged for 2026); the licence processing time (about 8 weeks); and the TB test price in Turkey (TRY 3,000 to 5,000).
- **Not re-verified** (`unverified`): stated from general knowledge of the rules and not re-verified against the official page; confirm each before relying on it. This covers the maintenance funds (£1,270, and £285, £315 and £200 for dependants), the reduced salary thresholds (new entrants from £33,400), the application window (up to 3 months before the CoS start date), the CoS validity (3 months), the TB certificate validity (6 months), the priority and super priority visa prices (about £500 and £1,000), the English test price (about £150 to £200), the rule since 31 December 2024 that the licence and CoS fees cannot be passed to the worker, the end of licence renewal (April 2024), the sponsor's duties, the Temporary Shortage List dependants rule, and the typical duration of each timeline stage.
- **Not held at all**: the visa fee for more than 3 years from inside the UK after 8 April 2026. The panel shows "not held" and points to the source rather than guessing.
- **A source link not checked**: the link to the Global Business Mobility Senior or Specialist Worker page (`https://www.gov.uk/senior-specialist-worker-visa`) follows GOV.UK's address pattern and has not been checked.
- **Tax side**: that the 2026/27 UK thresholds are frozen, and that the Turkish social security ceiling rose to 9 times the minimum wage from 1 January 2026, are traced to official sources. The HS212 edition reading, the Turkish 2026 wage brackets, the minimum wage, the ceiling amount, the employer incentive and the social security convention's period limit have not been re-verified against gib.gov.tr or legislation.gov.uk.

Before relying on the figures, open each source linked from the Immigration tab and from the rate-set provenance on the Explain tab. In the production design the pack's `verified_at` date and the rate sets are updated through the rules workflow ([ARCHITECTURE.md, section 6](ARCHITECTURE.md#6-data-model-and-database)).

---

## 7. Hand recomputation of the reference example

Inputs from the reference pack: salary £90,000, hypothetical tax £30,000 (supplied), cost-of-living allowance £6,000 net, housing £30,000 a year as a taxable benefit, relocation £8,000 in year 1 (exempt within the £8,000 cap). England rates for 2026/27: personal allowance £12,570, withdrawn by £1 for every £2 above £100,000 (nil from £125,140); 20% on the first £37,700 of taxable income, 40% to £125,140, 45% above; employee NICs 8% between £12,570 and £50,270 and 2% above; employer NICs 15% above £5,000; Class 1A 15% on benefits in kind.

**Net guarantee.** N = £90,000 - £30,000 + £6,000 = **£66,000**.

**Segment.** At the answer, total income is G + £30,000, about £157,762, above £125,140, so the personal allowance is nil and the top segment applies: 45% income tax plus 2% NICs, a marginal deduction of 47%.

**Net as a function of gross** on that segment:

- Income tax(G) = £37,700 x 20% + (£125,140 - £37,700) x 40% + (G + £30,000 - £125,140) x 45% = £7,540 + £34,976 + 0.45 x (G - £95,140)
- Employee NICs(G) = £37,700 x 8% + (G - £50,270) x 2% = £3,016 + 0.02 x (G - £50,270)
- net(G) = G - income tax - NICs = 0.53 x G - £1,713.60

**Solve.** 0.53 x G - £1,713.60 = £66,000, so G = £67,713.60 / 0.53 = **£127,761.51** (exactly £127,761.5094...). The segment check holds: G + £30,000 > £125,140 and G > £50,270.

**Lines on the exact gross:**

| Line | Exact | Rounded (on the rounded gross) |
|---|---|---|
| Gross cash | £127,761.51 | **£127,762** (rounded up so the guarantee is never under-delivered) |
| Taxable pay | £157,761.51 | £157,762 |
| Income tax | £42,516 + 45% x £32,621.51 = £57,195.68 | 45% x £32,622 + £42,516 = £57,195.90, so **£57,196** |
| Employee NICs | £3,016 + 2% x £77,491.51 = £4,565.83 | £3,016 + 2% x £77,492 = £4,565.84, so **£4,566** |
| Net cash | £66,000.00 | £127,762 - £57,195.90 - £4,565.84 = £66,000.26 delivered, shown as £66,000 |
| Employer NICs | 15% x £122,761.51 = £18,414.23 | 15% x £122,762 = £18,414.30, so **£18,414** |
| Class 1A | 15% x £30,000 = £4,500 | **£4,500** |

**Totals by the rounding convention** (sum of rounded lines):

- Year 1: £127,762 + £18,414 + £4,500 + £30,000 (housing) + £8,000 (relocation) = **£188,676**
- Year 2: £127,762 + £18,414 + £4,500 + £30,000 = **£180,676** (2026/27 rates carried forward; UK thresholds are frozen to 2030/31 in any case)
- Two years: **£369,352**, as printed in the reference pack

The unrounded years are £188,675.74 and £180,675.74, which sum to £369,351.47; the reference pack's £369,352 is the sum of lines rounded to the pound, which is the convention the tool reproduces and states.

Other figures in the trace: multiple of salary £188,676 / £90,000 = **2.10**; marginal employer cost of one more pound of net allowance on this segment 1 / 0.53 x 1.15 = **£2.17**; the fixed-point iteration G <- G + (N - net(G)) shrinks the error by a factor of 0.47 each step and agrees with the exact solve to the penny in about 21 steps.

**Indicative Turkish hypothetical tax.** Under the 2026 Turkish rules held in the engine's rate sets, at an indicative 65.7 TRY per GBP: gross TRY 5,913,000; employee SGK TRY 535,086.00 (15% on earnings capped at the ceiling); income tax TRY 1,728,665.60 less the minimum-wage credit TRY 57,881.20 = TRY 1,670,784.40; stamp tax TRY 41,871.30; total TRY 2,247,741.70 = **about £34,200** (£34,212.20), against the reference pack's assumed **£30,000**. The figure moves with the exchange rate and has not been checked against gib.gov.tr; it is shown beside the supplied figure as the most material assumption (`HYPO_TAX_OVERRIDE`).

**Immigration costs for the reference scenario** (kept separate, never added to the figures above): employer-mandatory £3,165 (CoS £525 + Immigration Skills Charge £2,640); applicant side £2,889 (visa fee £819 + health surcharge £2,070), which many employers pay by policy; TB test TRY 3,000 to 5,000 listed separately. Timeline with the licence already held: about 6 to 16 weeks (43 to 112 days); without a licence: about 10 to 21 weeks (70 to 145 days).

## 8. Independent reviews and the fixes they produced

Two independent reviews were run on the finished code, each by a reviewer who had not written it and who was asked to look for wrong figures first and style last.

**Engine and guidance review.** Found no wrong figure from the UK tax or National Insurance functions, the breakpoint list or the solver, and re-derived the golden values by hand. It found fourteen defects, all fixed with regression tests:

- the rate-set fingerprint ignored rate-set content and the rates date, so a cache could have served stale results (fingerprint now covers content checksums, and a `cache_key` includes the rates date);
- a bonus or other equalised item joined the net guarantee with no hypothetical-tax share (a share now applies when the base is all equalised items, and a warning says so when the base is salary only);
- the per-move relocation cap could be bypassed by a plain "exempt" treatment (refused);
- currency conversions used banker's rounding (now half-up);
- a future-dated exchange rate was accepted and a tiny rate could escape the size cap (both refused);
- the inputs hash was not canonical (region default, negative zero and year order normalised);
- guidance costs could not price part years or cap a single grant (months supported, half-year surcharge, 60-month cap);
- guidance arithmetic depended on the host's decimal context (now local);
- the relocation split was not traced as two lines (it is);
- a documented worst-case slope was wrong (corrected to 0.32);
- a gap between rate sets raised instead of carrying forward (carries forward);
- timeline dependencies were dropped through inapplicable stages (passed through);
- boundary tests at the relocation cap and the Turkish contribution ceiling were missing (added);
- lira components were not consistently two-decimal (they are).

**Web layer review.** Confirmed that templates never mark strings safe, that the content security policy, frame denial and CSRF protection hold, that decompression of the results link is bounded, that the API never leaks a traceback and that immigration costs are never added to the employment cost. It found fourteen presentation and robustness defects, fixed with tests: a relocation excess shown as exempt; home-scheme pages and narrative still mentioning UK National Insurance; the narrative misplacing items by year; extreme rates dates and oversize bodies causing server errors; missing `Cache-Control` and `Permissions-Policy` headers; tampered results links being reflected; the compare view failing near the link size cap; the readiness endpoint exposing error text; duplicated flags; API contract gaps; results links that did not record the engine version; two narrative claims stated unconditionally; and small form and command-line defects.

Both review reports are summarised here rather than reproduced; the tests they led to are in `tests/engine/test_cap_and_ceiling_boundaries.py`, the review cases in `tests/engine/test_calculator.py`, `tests/engine/test_types.py`, `tests/guidance/test_guidance_costs.py`, and `tests/web`.
