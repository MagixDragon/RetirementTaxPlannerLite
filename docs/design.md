# RetirementTaxPlannerLite design

Status: first slice (marginal tax graph). The forward-looking plan is
designed for here but not built yet.

## Goals

* A free, open-source desktop app (Windows, macOS, Linux) that draws the
  kinds of tax graphs retirement planners show clients: first the
  **marginal tax graph**, then the **forward-looking (year-by-year) graph**.
* Native look, keyboard and screen-reader support, not a browser app.
* Tax law lives in data files people can read and edit, with policy
  switches for things like the senior deduction's expiry.
* Reasonable defaults that hide what cannot matter for a household, while
  letting the user turn any layer back on.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Familiar to the author; `tomllib` in the standard library |
| UI | PySide6 (Qt 6) | Uses UIA / NSAccessibility / AT-SPI, standard menus and shortcuts |
| Charts | matplotlib | Stacked step areas, annotations, PNG/SVG/PDF export |
| Data | TOML (tax law, themes), JSON (profiles) | Hand-editable, comment-friendly |
| Packaging (later) | PyInstaller or Briefcase in GitHub Actions | One download per OS |

## Layout of the code

```
src/rtpl/
  model/profile.py        Profile, Person, Household (JSON)
  taxlaw/loader.py        FederalLaw, StateLaw, TaxLawLibrary (TOML + projection)
  engine/tax.py           compute_tax(): one year, one household
  engine/marginal.py      build_marginal_series(), annotations, default visibility
  charts/marginal_chart.py  render_marginal() onto any matplotlib Figure
  theme.py                light/dark palettes from TOML
  app/                    Qt main window and profile form
  cli.py                  headless chart export (rtpl-chart)
  data/federal/*.toml     per-year federal law + projection rules
  data/states/*.toml      per-state law
  data/themes/default.toml
```

The engine never imports Qt, so it is testable on its own and reusable by
the forward-looking plan and the command-line exporter.

## The three setup areas

1. **Baseline (the profile).** Tax year, filing status, state (optional),
   one or two people with birth year, a *Medicare Part B enrolled* choice
   (Automatic / Yes / No, so SSDI recipients under 65 get IRMAA), Social
   Security, wages, pension and IRA withdrawals; household interest,
   dividends, gains, other income, adjustments, itemized deductions, and
   switches for *has a taxable brokerage account* and *buys ACA coverage*.
2. **Tax law (data files).** One TOML file per federal tax year, and one per
   state. See below.
3. **Plan assumptions (forward graph, not built yet).** Inflation, returns,
   allocation, and an event list. See "Forward-looking plan" below.

## Tax law data

`data/federal/2026.toml` holds brackets, standard deductions, the
additional 65+ deduction, the OBBBA senior deduction (amount, 6% phase-out,
first and last year), capital gains bands, NIIT, Social Security
thresholds, IRMAA tiers for that premium year, and ACA poverty-line
reference points. A `[policy]` table holds switches:

* `personal_exemption` (0 under TCJA, kept by OBBBA)
* `senior_deduction` (on for 2025–2028)
* `aca_enhanced_subsidies` (true for 2025; false for 2026, which brings
  back the 400% FPL cliff)

**User overrides.** The app also reads a `taxlaw` folder in the user's
config directory; a file there replaces the shipped file for that year or
state. Nothing needs code changes.

**Projection.** Years without a file are built from the newest enacted
year by `federal/projection.toml`: indexed amounts (brackets, standard
deductions, capital gains bands, IRMAA thresholds and premiums, poverty
line) grow with inflation and round down like the IRS does; statutory
amounts (NIIT and Social Security thresholds, the senior deduction) stay
fixed; `[sunsets]` turns policies off after their last year. Projected law
is labelled as projected on the chart.

## The marginal graph

The X axis is extra income of a chosen kind, $0–$250K by default:

* **Ordinary income** (treated as an IRA withdrawal or Roth conversion, the
  common retiree case: not investment income, but exempt in states that
  exempt retirement income)
* **Qualified dividends**
* **Long-term capital gains**

For each $100 step (finer steps for wider ranges), the engine recomputes the
whole return and splits the change in tax into layers using counterfactual
recomputation, so the layers always sum exactly to the real change:

| Layer | How it is isolated |
|---|---|
| Tax on Ordinary Income | ordinary tax change with taxable SS and the senior deduction frozen |
| Social Security Taxation Phase-In | extra ordinary tax from newly taxable SS (senior deduction frozen) |
| Enhanced Senior Deduction Phase-Out | the remaining ordinary tax change |
| Capital Gains and Qualified Dividends Tax | change in preferential-rate tax, including gains pushed out of the 0% band |
| Net Investment Income Tax | change in NIIT |
| State Tax (XX) | change in state tax, labelled with the state code |

The total line is the sum of the *shown* layers, so turning off the state
layer gives a federal-only picture.

**Annotations** are cliffs in premiums, not taxes, so they are vertical
lines rather than part of the rate:

* **IRMAA.** Income in year *Y* sets Medicare premiums in *Y + 2*. The line
  sits where MAGI (AGI + tax-exempt interest) crosses each tier for the
  premium year, using projected thresholds when that year has no file. The
  label gives the added yearly premium for the people who will be enrolled.
* **ACA.** Lines at 138% FPL (Medicaid in expansion states), 250% (cost
  sharing reductions) and, when enhanced credits are off, the 400% subsidy
  cliff, using ACA MAGI (AGI + tax-exempt interest + untaxed Social
  Security).

**Default visibility** (every item is a checkbox the user can override;
*Reset to suggested* undoes overrides):

| Item | Shown by default when |
|---|---|
| Ordinary income tax | always |
| SS phase-in, senior phase-out, NIIT | the layer is non-zero somewhere on the graph |
| Capital gains / QD | brokerage account, existing gains or dividends, or a gains/dividends source |
| State | a state with an income tax is entered |
| IRMAA | someone is on Medicare by the premium year (age 63+ now, or Part B enrolled) |
| ACA | the household buys marketplace coverage and someone is under 65 and not on Medicare |

## Accessibility and CUA

* Standard menus with mnemonics and shortcuts (Ctrl+N/O/S, Ctrl+E export).
* Every form field has a label buddy and an accessible name.
* A **Data table** tab lists where the marginal rate changes and what each
  annotation means, so the chart's content is available as text.
* Layers are also told apart by legend order and labels, not color alone.

## Colors and themes

`data/themes/default.toml` defines light and dark palettes. Layer colors
come from a colorblind-checked categorical set, stepped separately for each
background. *View → Chart Colors…* edits any layer color for either mode and
saves to `theme.toml` in the user config folder. The defaults are our own
palette; if you want house colors from a planner's charts, drop their hex
values into the theme file.

## Not modelled yet

Credits (child, saver's, elderly), AMT, QBI, the 0.9% additional Medicare
tax, self-employment tax, state credits and most state retirement
exclusions, Washington's capital gains tax, ACA premium tax credit dollar
amounts (only the thresholds are drawn), widow(er) filing transitions.

## Forward-looking plan (next)

Proposed shape, to discuss before building:

* **Assumptions:** inflation, return per asset class, allocation, SS COLA,
  claiming ages, spending need.
* **Accounts:** taxable, traditional, Roth, HSA, cash, with balances and cost
  basis.
* **An event list with defaults and overrides.** A short list of rules, each
  with a year or age range, instead of a line per year. Examples:
  * "Roth conversions from age 62 to 70, fill to the top of the 22% bracket"
  * "Start Social Security at 70 (Person 1), 67 (Person 2)"
  * "RMDs per the Uniform Lifetime table" (on by default)
  * "Person 1 dies at 85" (switches to single filing after the survivor years)
  * "Sell home in 2031, $250K gain"
  Each rule has defaults; a year-specific override wins over a range rule.
* The engine runs `compute_tax()` per year with the projected law for that
  year, so policy switches and sunsets apply automatically.
