# Vista — Business Course of Action

Distilled from the original business plan (full text in git history:
`BUSINESS_PLAN_AND_IDEAS.md`, removed as superseded) plus decisions made since.

## What Vista is

Financial and operating intelligence for lower-middle-market private equity.
The product direction as of 2026-09-21 is two platforms with three user views:

- **Financial platform — PE analyst:** financial performance, model inputs and
  assumptions, opportunities, and validated impact across authorized portfolio companies.
- **Financial platform — portco CFO:** the assigned company's subset of the same
  financial model and evidence, supporting company financial review and validation.
- **Automation platform — FDE (forward-deployed engineer):** workflow discovery,
  automation delivery, exceptions, and measured operational results within assigned scope.

Shared canonical records and evidence connect the platforms. An FDE's operational
result can support a financial conclusion only with a baseline, explicit assumptions,
and validation. The long-term agent vision remains bounded administrative execution
with review; dedicated CFO/FDE views and end-to-end automation are not yet shipped.

**Positioning:** "Vista connects portfolio financial analysis with the workflows
behind it. PE analysts see across authorized companies, CFOs understand their own
company's financial picture, and FDEs deliver automations with measurable results."

## Who buys it and who uses it

- **Economic buyer:** PE operating partner / platform-company executive / CFO
  responsible for post-acquisition improvement.
- **Financial users:** PE analysts and portco CFOs; the CFO's financial visibility
  is a company-scoped subset, not a separate set of numbers.
- **Implementation users:** FDEs responsible for assigned workflows and automation
  outcomes; this role does not automatically carry portfolio financial access.
- **Evidence contributors:** employees, bookkeepers, and operations staff supplying
  records and reviewing explanations through a minimal recorder/verification surface.

## Core hypotheses to validate (in order)

1. PE firms repeatedly hit fragmented operations across acquisitions and will PAY for
   a repeatable fix (one interview supports this; it is not proof).
2. Agent-discovered findings are accurate enough that sponsors act on them.
3. The second deployment takes meaningfully less custom work than the first
   (product vs consulting test).

## Course of action

### Original hackathon plan (historical)

The sequence below records the earlier HVAC prototype plan. Current implementation
is in `CURRENT_IMPLEMENTATION.md`; the current priority is the financial/FDE split
in `NEXT_STEPS.md`. The canonical demo now uses six insurance and industrial companies.

1. Build the frontend company view + findings queue on the existing API.
2. Build the hackathon demo: portfolio dashboard, three synthetic HVAC companies
   (Harbor Heating, Summit Mechanical, Cedar Climate), live Cedar onboarding via CSV
   import with agent-proposed mappings, synergy agent with evidence-backed findings →
   tasks. Keep a known answer sheet + one deliberately misleading vendor overlap to
   prove the agent flags ambiguity. If time is tight, keep purchasing analysis and
   cut the rest — preserve the import → evidence → action loop.
3. Demo checks: changing input data changes findings; ambiguous matches get flagged;
   displayed calculations agree with underlying records.

### Post-hackathon → first paid pilot
4. Find one design-partner sponsor (operating partner or platform exec) with an
   urgent, measurable workflow problem and authorized representative data.
5. Scope a paid pilot: ONE workflow (customer-to-invoice), defined implementation
   boundary, baseline measured in human-minutes-per-item, explicit success measures
   and a conversion decision date.
6. Interview the staff doing the workflow BEFORE picking the automation; deploy at
   one company; document the operational result.

### Expansion test
7. Deploy at a second company in the same portfolio. Measure how much carried over —
   this is the moment Vista proves it's a product, not a service.
8. Expand through the sponsor: additional acquisitions, adjacent workflows, referrals
   to other operating teams.

## Commercial structure (validate through the pilot)

Implementation fee (discovery/cleanup/migration) + recurring per-company subscription
+ portfolio/usage pricing for cross-company capabilities and heavy agent use. Do not
promise unlimited customization or unlimited agent usage before support costs are
known. Long-term economic test: recurring revenue covers infra + service while
implementation effort declines across similar deployments.

## Metrics that matter

- **Onboarding:** time to first usable workspace; unresolved data issues.
- **Workflow:** human minutes per item; corrections; completion time.
- **Adoption:** active operational users; share of work done in Vista.
- **Financial:** documented collection improvements / realized cost reductions —
  never equate "time saved" with realized savings, and label scenario math as such.
- **Delivery economics:** engineering hours per company; reusable vs custom;
  support + inference cost per tenant (already tracked via `usage_events`).

## Standing risks and how we hold them off

- **Scope creep** → one industry, one connected workflow set.
- **Surveillance backlash** → employee surface stays minimal; consent and retention
  policy ship before any observation feature; findings framed as questions, not
  accusations.
- **Consulting trap** → limit supported configurations; track custom engineering hours.
- **Unreliable automation** → permissions, review gates, append-only audit logs,
  recoverable actions (all already in the architecture).
- **Unsubstantiated synergies** → every claim cites source records; observed fact /
  potential benefit / realized result are kept distinct.
