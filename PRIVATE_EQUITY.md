# Private equity, briefly

Vista is built for lower-middle-market private equity. This is a short primer on
what that world looks like and why the product is shaped the way it is.

## What a PE firm does

A private equity firm raises a fund from outside investors (pensions, endowments,
family offices — the *limited partners*, or LPs) and uses that capital, plus debt,
to buy controlling stakes in private companies. The firm (the *general partner*, or
GP) then owns and operates those companies for roughly three to seven years before
selling them — to a strategic buyer, another fund, or the public markets.

Returns come from three levers:

| Lever | What it means |
| --- | --- |
| **Multiple expansion** | Sell the company at a higher earnings multiple than it was bought for |
| **Deleveraging** | Pay down the acquisition debt out of the company's cash flow |
| **Operational improvement** | Grow revenue and margins — the lever the firm controls most directly |

The first two depend on markets and financing. The third is where operating partners,
analysts, and portfolio-company management spend their time, and it is the one Vista
targets.

## Lower-middle-market specifics

"Lower-middle-market" usually means companies with roughly $5M–$50M of EBITDA
(or $10M–$100M+ of revenue). Compared with large-cap buyouts, these deals have a few
defining traits:

- **Thin data infrastructure.** Companies often run on QuickBooks, spreadsheets, a
  point-of-sale system, and email. There is rarely a data warehouse or a BI team.
- **Founder-led operations.** Process knowledge lives in a handful of people's heads
  and habits rather than in documentation.
- **Many small companies per fund.** A firm may own 10–30 companies at once, each
  with its own systems and reporting cadence, so the analyst team is stretched thin.
- **Buy-and-build.** Firms frequently acquire several companies in one sector and
  merge them ("platform + add-ons"), which makes cross-company comparison and
  consolidation a recurring problem.

## The recurring jobs

The work that fills a portfolio team's week tends to be:

1. **Monthly reporting** — collecting financials and KPIs from every company,
   normalizing them, and rolling them up for the investment committee and LPs.
2. **Finding value-creation opportunities** — spotting margin leakage, pricing
   inconsistencies, vendor overlap, or working-capital drag across companies.
3. **Tracking initiatives** — turning those opportunities into tasks for management
   teams and following up.
4. **Diligence and integration** — understanding a new company's processes quickly
   and folding add-ons into the platform.

Almost all of this is manual today: exports, spreadsheets, and calls.

## How Vista maps onto this

- The **PE analyst portfolio** is the firm-scoped view: attention feed, opportunities
  with evidence and calculations, tasks, imports, and agent runs across all companies.
- Each **company workspace** is isolated (schema-per-tenant) so that a portfolio
  company's data never bleeds into another's, while the firm can still see across them.
- **Agents** (File Reviewer, Sector Merger, Report Generator, Recording Reviewer)
  take on the recurring jobs above, with every number traced back to a source row and
  every model call metered — because in PE, a figure that cannot be sourced cannot
  be put in front of an investment committee.
- The **desktop recorder** addresses the founder-led-operations problem: it captures
  how work is actually done at a company, with employees reviewing every explanation
  before anything leaves the machine.

## Glossary

| Term | Meaning |
| --- | --- |
| **LP / GP** | Limited partners fund the vehicle; the general partner manages it |
| **EBITDA** | Earnings before interest, taxes, depreciation, and amortization — the standard earnings measure for valuing private companies |
| **Multiple** | Enterprise value divided by EBITDA (e.g. "bought at 7x") |
| **Platform / add-on** | The initial company in a sector and the smaller ones acquired to combine with it |
| **Hold period** | Time between buying and exiting a company |
| **Value-creation plan** | The operating roadmap for a company during the hold period |
| **Portfolio company** | Any company owned by the fund |

See [README.md](README.md) for the product overview and
[BUSINESS_COURSE_OF_ACTION.md](BUSINESS_COURSE_OF_ACTION.md) for the go-to-market plan.
