# Assumptions and warning codes

Every result carries coded warnings and assumptions, so that the reader can see what the figures rest on and what to check. This page explains each code in plain English, grouped under tax, social security, immigration and data, and then lists the points where the reference pack (the assignment brief) differs from current guidance.

The codes are defined once, in the engine's catalogue (`src/teq_engine/warnings.py`), each with a text template and a tailoring question; the web page and the API render from that catalogue. The immigration guidance has its own stated assumptions, held in the guidance pack (`src/teq_guidance/data/tr_gb_skilled_worker.json`).

Kinds: an **error** stops the calculation and no figures are shown; a **warning** means a figure may be incomplete or needs checking; **info** explains something about the result; an **assumption** is a simplification the figures rest on.

Everything here is an illustration, not tax or immigration advice.

---

## Tax

| Code | Kind | What it means | What to check |
|---|---|---|---|
| `UK_RESIDENT_FULL_YEAR` | assumption | The employee is treated as UK resident for every whole UK tax year of the assignment. | Will the employee arrive or leave part-way through a tax year? Split-year treatment needs calendar-accurate mode. |
| `ENGLAND_RATES` | assumption | Income tax is calculated at the rates for England (and Wales and Northern Ireland). | Will the employee live in England? Scotland has its own income tax rates. |
| `REGION_NOT_SUPPORTED` | error | The host region is not one the tool supports. In this version only England is supported; Scotland is refused rather than calculated with the wrong rates. | Scottish rates are a later addition. National Insurance is UK-wide either way. |
| `ROUTE_UNSUPPORTED` | error | The home and host countries are not a supported route. Only Turkey to the UK (England) is supported. | The message lists the supported routes. Adding a route needs that country's rate tables and rules; the tool never guesses. |
| `MODE_ILLUSTRATIVE_WHOLE_YEAR` | assumption | Each assignment year is treated as one whole UK tax year and one whole Turkish calendar year. Actual start and end dates and part years are not modelled. | What are the real dates? Calendar-accurate mode is future work. |
| `PERSONAL_ALLOWANCE_ENTITLED` | assumption | The employee gets the UK personal allowance because they are UK resident. At the reference salary the allowance tapers to nil, so it makes no difference there. | Matters only at lower salaries. |
| `PERSONAL_ALLOWANCE_TAPER_BAND` | info | Total income falls between £100,000 and £125,140, where the personal allowance is withdrawn at £1 for every £2, so income tax has an effective marginal rate of 60%. | Nothing to do: it explains why the cost rises steeply in this band. |
| `MODIFIED_PAYE_SAME_YEAR_GROSSUP` | assumption | The employer runs PAYE on the grossed-up pay in the same tax year, as the reference pack's HMRC references describe, so no tax spills into a later year. | Will tax be settled through payroll in-year, or after the year end? Settlement after the year end can move tax into the next year. |
| `BIK_CASH_EQUIVALENT_AS_INPUT` | assumption | A benefit in kind (housing in the reference example) is taken at the cash value entered. The statutory valuation of living accommodation (the greater of annual value and rent paid, plus any additional charge) is not performed. | Is the property rented by the employer? Does the employee contribute towards it? |
| `RELOCATION_QUALIFYING_ASSUMED` | assumption | All relocation items are assumed to be qualifying removal expenses and benefits, provided within the time limit, so they are exempt up to the cap. | Are all items on HMRC's qualifying list and paid within the time limit? |
| `RELOCATION_EXCESS_TAXABLE` | warning | Relocation costs exceed the £8,000 exemption, which applies once per move and is used up across all years. The excess is a taxable benefit, with employer Class 1A National Insurance. | Can the package stay within the cap, or is the excess intended? |
| `RELOCATION_OUTSIDE_WINDOW` | warning | Relocation paid in assignment year 3 or later falls outside the exemption window (the tax year of the job start and the following tax year), so it is treated as taxable. | When does the job start? The window depends on the actual start date. |
| `EQUALISED_ITEM_NO_HYPO_SHARE` | warning | A bonus or other equalised item joins the net guarantee in full because the hypothetical-tax base is salary only, so the employer bears all UK tax on it. | Should the hypothetical tax base include all equalised items? |
| `HYPO_TAX_OVERRIDE` | assumption | The hypothetical home (Turkish) tax was supplied as a figure (the reference pack's £30,000), not calculated. This is the single most material assumption: under 2026 Turkish rules the calculated figure is about £34,200 at indicative exchange rates. | Should the hypothetical tax be calculated from the 2026 rules instead? Which base does the employer's policy use? |
| `OWR_NOT_MODELLED` | assumption | Overseas Workday Relief, which can exempt pay for workdays outside the UK under the four-year regime (subject to a cap), is not modelled. | Are any non-UK workdays expected? |
| `HOME_COUNTRY_TAX_RESIDENCE_RISK` | warning | The employee may remain Turkish tax resident in the year they leave, which can create home-country tax that equalisation must cover. | What are the departure date and the treaty position? Calendar-accurate mode would model this. |
| `PAYROLLING_REPORTING_CHANGE_2027` | assumption | Payrolling of benefits becomes mandatory from April 2027 (living accommodation excluded). This changes how benefits are reported, not what they cost. | Is the payroll provider ready? |
| `AUTO_ENROLMENT_MAY_APPLY` | info | Employer pension auto-enrolment contributions (at least 3% of qualifying earnings) may add to the cost and are not included. | Will the employee be auto-enrolled into a UK pension, or stay in a home plan? |
| `APPRENTICESHIP_LEVY_MAY_APPLY` | info | The Apprenticeship Levy (0.5% of the pay bill above a £15,000 allowance) may add to the cost for a large employer and is not included. | Is the employer's UK pay bill above £3 million a year? |
| `GROSS_UP_FALLBACK_BISECTION` | warning | The exact gross-up solve failed its own check, so a slower but safe method was used. The figures are still valid; the fault is investigated. | Nothing for the user to do. |
| `GROSS_UP_NOT_CONVERGED` | error | The gross-up could not be solved by either method. No figures are shown rather than partial ones. | Nothing for the user to do; this is an engine fault. |

Stated, uncoded conventions behind every figure:

- **Rounding.** The gross-up is solved exactly, then the gross cash is rounded **up** to the pound so the net guarantee is never under-delivered; every line is recalculated on the rounded gross and rounded half-up to the pound, and totals are sums of rounded lines so every table adds up. In the reference example the employee receives £66,000.26 net against a £66,000 guarantee.
- **Money** is held as exact decimals, never floating-point numbers.
- **Turkish withholding** is cumulative month by month; the annual calculation used here matches it only when pay is even through the year.

---

## Social security

| Code | Kind | What it means | What to check |
|---|---|---|---|
| `UK_NIC_APPLIES` | assumption | UK employee and employer National Insurance apply, because no certificate of coverage keeps the employee in the Turkish scheme. | Will the employee stay in the Turkish scheme under the social security agreement? |
| `SOCIAL_SECURITY_AGREEMENT_MAY_APPLY` | info | The UK and Turkey have a social security agreement (convention). The employee may be able to stay in the Turkish scheme for a period with a certificate of coverage, which removes UK National Insurance but keeps Turkish employer contributions. Its conditions concern the employer, the insurance and the duration; the period limit has not been verified. | Will a certificate be obtained? If so, recalculate with the home-scheme option. Note that who employs the worker bears on this (see the immigration route note). |
| `NIC_EXEMPTION_ASSUMED` | warning | The home-scheme option is on: UK employee National Insurance, employer National Insurance and Class 1A are removed. Turkish employer contributions continue and are shown as their own line, so the assignment does not look cheaper than it is. | Has a certificate of coverage been applied for, and for which dates? |
| `HOME_SCHEME_CERTIFICATE_REQUIRED` | assumption | Staying in the Turkish scheme needs a certificate of coverage, obtained before the assignment starts. | Who will obtain it, and by when? |
| `HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED` | warning | In home-scheme mode the Turkish employer contribution is estimated: the employer rate less the applicable incentive, on earnings capped at the 2026 ceiling (9 times the minimum wage), converted to pounds. Roughly £11,700 a year at the ceiling and an indicative rate. | Confirm the employer rate, incentive and ceiling with the Turkish payroll. |
| `ANNUAL_NIC_BASIS` | assumption | National Insurance is calculated on an annual basis. In practice it is calculated per pay period, which can differ slightly when pay is uneven. | Is pay spread evenly across the year? |
| `EMPLOYMENT_ALLOWANCE_NOT_APPLIED` | assumption | The Employment Allowance, which some employers can deduct from their National Insurance bill, is not applied. | Is the employer eligible? |

The employee's own Turkish contributions are already inside the hypothetical deduction when the hypothetical tax includes social security (as in the reference pack), so the net guarantee does not change with the home-scheme option.

---

## Immigration

The immigration panel is guidance, not a decision. It never decides eligibility (the occupation code, its going rate and the basic-pay-only rule decide that), and its costs are **never added to the employment-cost estimate**.

| Code | Kind | What it means | What to check |
|---|---|---|---|
| `IMMIGRATION_CONTENT_STALE` | warning | The guidance was last verified more than 180 days (configurable) before the date the panel is shown. Fees usually change each April. | Re-verify fees and timings before relying on them. |
| `VISA_LENGTH_EXCEEDS_SINGLE_GRANT` | warning (guidance panel) | A visa longer than 5 years was requested; a single Skilled Worker grant lasts at most 5 years, so costs are priced for one 60-month grant and a further application would be needed. | Is a shorter assignment or an extension planned? |

### How each point is marked

Every requirement, document, cost, stage and note in the guidance pack carries one verification level and at least one GOV.UK source, and the Immigration tab shows the level's label beside the item:

- **Official source** (`official_source`): traced to the wording of the official page; check the linked page before relying on a figure. For example the Immigration Skills Charge, the health surcharge, the salary threshold and English at B2.
- **Third-party report** (`third_party`): reported by third parties and not yet confirmed on the official page; check it on GOV.UK before relying on the figure. The 8 April 2026 fee changes (visa application fees, sponsor licence fee, licence priority service, the Certificate of Sponsorship fee for 2026) are in this group, as are the licence processing time and the TB test price.
- **Not re-verified** (`unverified`): stated from general knowledge of the rules; confirm on the official page before relying on it. For example the maintenance funds (£1,270, and £285, £315 and £200 for dependants), the reduced salary thresholds, priority visa service prices and the typical duration of each timeline stage.

[VERIFICATION.md, section 6](VERIFICATION.md#6-facts-not-re-verified-against-the-official-pages) lists every item at each level.

### Assumptions when a tailoring question is unanswered

The panel works with no answers at all. Each unanswered question uses the value below, and the panel data lists it under "what we would need to tailor this further" with the assumption it made; on the Immigration tab each question shows its assumed answer, and the main ones are named in the line above the collapsed tailoring form.

| Question | Assumed if unanswered | Effect if different |
|---|---|---|
| Visa length | 2 years (the reference assignment) | The health surcharge and the Immigration Skills Charge scale per year; the application fee is higher above 3 years; a single grant lasts up to 5 years, so anything longer is priced as one 5-year grant with a warning. The length may be given in months, in which case part years are priced: the skills charge per further six months or part, the health surcharge at half a year for a final part year of six months or less. |
| Where the application is made | Outside the UK | Inside the UK: a different fee and a decision in up to 8 weeks rather than 3. |
| Sponsor licence held | Yes | No: a licence fee of £1,682 (medium or large) or £611 (small or charitable) and about 6 to 10 weeks, usually the longest stage. |
| Sponsor size | Medium or large | Small or charitable: lower licence fee and Immigration Skills Charge (£480 a year rather than £1,320). |
| Partner applying as a dependant | 0 | Adds a fee, the health surcharge, documents and funds. |
| Children applying as dependants | 0 | Adds a fee and the child health surcharge per child, documents and funds. |
| Lived in a TB-test country | Yes (Turkey is on the list) | No: no TB test certificate, cost or stage. |
| Who employs the worker in the UK | The UK entity | If the Turkish employer posts the worker, or it is undecided, the panel notes that the Global Business Mobility Senior or Specialist Worker route may be the relevant one and that the social security position may differ. A note, never a decision. |
| How English will be shown | A Secure English Language Test | A degree taught in English (with Ecctis confirmation) or an exempt nationality removes the test, its cost and its 2 to 6 weeks. |
| Sponsor certifies maintenance | No | Yes: no funds evidence is needed. |
| Occupation sector | None of education, healthcare or social care | Listed occupations in those sectors need a criminal record certificate. |
| Occupation code (SOC 2020) | Not provided | Decides eligibility (RQF level 6) and the going rate, which can be above £41,700. |
| Nationality | Turkish | Nationals of majority English-speaking countries meet the English requirement without a test. |

### Other stated points in the guidance

- **Costs are formulas.** Each cost shows its formula (for example "£1,035 x 2 years") and its payer: the employer (and whether it may be recovered from the worker), the applicant, or "applicant in law; employer by policy" for the visa fee and health surcharge.
- **Costs the worker must not bear.** The Immigration Skills Charge can never be recovered from the worker; since 31 December 2024 the sponsor licence fee and the Certificate of Sponsorship fee cannot be passed to the worker either.
- **Subtotals add exact sterling amounts only.** The TB test (a range in Turkish lira, not converted), the English test (a range) and optional priority services are listed but not added.
- **A fee the pack does not hold** (the visa fee for more than 3 years from inside the UK) is shown as "not held" with a pointer to the source, never guessed.
- **Maintenance funds are not a cost.** They are money the applicant must show has been held for 28 days, unless the sponsor certifies maintenance.
- **Reduced salary thresholds** (new entrants from £33,400, relevant PhDs, the Immigration Salary List) are stated to exist; the tool does not decide whether one applies.
- **Timeline totals are ranges, never dates.** Typical durations in calendar days are combined along the critical path of the stages that apply; holidays, appointment availability and requests for more information can extend them. Weeks round the minimum down and the maximum up.

---

## Data

| Code | Kind | What it means | What to check |
|---|---|---|---|
| `RATES_UNAVAILABLE` | error | No rate table covers the date asked for and none can be carried forward to it. | Is the rates date inside a period the bundled tables cover? |
| `RATES_NOT_PUBLISHED_FOR_YEAR` | info | Rates for a later assignment year are not yet published, so the latest published year's rates are carried forward. This is why the reference pack's year 2 equals year 1 apart from relocation. Turkish rates are flagged only when the scenario uses them (a calculated hypothetical tax or home-scheme social security); under a supplied hypothetical tax they do not affect the figures. | Recalculate when the later rates are published. |
| `RATES_UNCHANGED_LATER_YEARS` | assumption | Rates for years not yet published are assumed unchanged from the latest published year. UK thresholds are in fact frozen to 2030/31. | As above. |
| `RATE_SET_SUPERSEDED` | info | Newer rules have been approved since a saved result was calculated (production only). | Rerun the scenario under the current rules? |
| `FX_RATE_USER_SUPPLIED` | info | The exchange rate used, its date and its source are pinned to the result, so it can be reproduced later. | Is this the rate to use? |
| `FX_RATE_STALE` | warning | The exchange rate is more than 30 days older than the rates date. | Enter a current rate. |
| `FX_RATE_IMPLAUSIBLE` | warning | The exchange rate is outside the plausible band for the pair. | Was it entered the right way round (lira per pound)? |
| `FX_RATE_IN_FUTURE` | error | The exchange-rate snapshot is dated after the rates date, so it cannot have been the rate in force; the scenario is refused. | Enter a rate dated on or before the rates date. |
| `SALARY_CONVERTED_AT_SNAPSHOT_FX` | info | A salary entered in lira was converted to pounds at the pinned rate for the net guarantee and the UK gross-up; the Turkish hypothetical tax uses the lira figure directly. | Is this the rate the net guarantee should use? |
| `DEGRADED_BUNDLED_RATES` | warning | The rates database was unavailable, so the rate tables bundled with the engine were used (production). | Retry later if the result must be saved. |
| `PERSISTENCE_UNAVAILABLE` | warning | The result could not be saved because the database was unavailable (production). | Retry later. |
| `NARRATIVE_FALLBACK` | info | The template narrative is shown because generated narration was unavailable or failed its number check (production option). | Nothing to do. The narrative never changes a figure. |

---

## Reference pack versus current guidance

As found on 7 and 8 October 2026. Where current official guidance differs from the reference pack, the tool follows the guidance and says so. Positions traced to the official page are marked "(official source)" and the reported fee changes "(third-party report)"; the two-year total and the hypothetical tax are computed by the tool, and the remaining rows are not re-verified. Check each on the official page before relying on it.

| Topic | Reference pack | Current position | Effect on the tool |
|---|---|---|---|
| UK rates and thresholds | "2026/27 rates" | Thresholds and rates frozen to 2030/31, identical to 2025/26 (official source) | None numerically; the tax year is labelled explicitly |
| HS212 edition | Linked as "2026" | The 2026 edition is the one for 2025/26 returns (understanding, not verified) | None numerically |
| Employee NICs wording | "8% up to £50,270" | 8% between £12,570 and £50,270, 2% above | Computation already correct; label clarified |
| Two-year total | £369,352 | Sum of unrounded years is £369,351.47; £369,352 is the sum of lines rounded to the pound | Rounding policy stated; the reference pack's convention reproduced |
| Hypothetical Turkish tax | £30,000 assumed; "tool should work it out" | About £34,200 under 2026 rules at indicative exchange rates | Calculated figure shown beside any override; most material assumption |
| Turkish social security ceiling | Not stated | Raised to 9 times the minimum wage from 1 January 2026 (official source) | In the TR_SGK rate set |
| Social security agreement | "May stay in the Turkish scheme; flag it" | Convention exists (1961 Order); conditions are employer, insurance and duration; period limit not verified | Toggle plus Turkish employer line plus certificate assumption |
| Visa decision time | 3 weeks outside, 8 weeks inside | Same (official source) | None |
| English requirement | Not mentioned | B2 for new applications from 8 January 2026 (official source) | Documents and timeline |
| Tuberculosis test | Not mentioned | Turkey is on the list; clinics in Istanbul and Ankara (official source) | Conditional document and timeline stage |
| Immigration Skills Charge | Not mentioned | £1,320 and £480 a year from 16 December 2025 (official source) | Cost item with employer payer, cannot be recovered from the worker |
| Visa and sponsor fees | Not mentioned | Changed on 8 April 2026 (third-party report) | Cost items with verified_at |
| Salary requirement | Not mentioned | £41,700 or the going rate, RQF 6, basic pay only (official source) | Guidance text; no eligibility inference |
| Accommodation value | Rent treated as the benefit | Statutory value is the greater of annual value and rent | Assumption `BIK_CASH_EQUIVALENT_AS_INPUT` |
| Payrolling of benefits | Not mentioned | Mandatory from April 2027, accommodation excluded | Assumption; no cost effect |
