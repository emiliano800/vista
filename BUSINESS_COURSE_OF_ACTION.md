# Vista — Business Course of Action

Distilled from the original business plan (full text in git history:
`BUSINESS_PLAN_AND_IDEAS.md`, removed as superseded) plus decisions made since.

## What Vista is

An adaptive operating platform for lower-middle-market private equity. PE firms
onboard acquired businesses whose operations live in spreadsheets, paper, and
employee heads. Each company gets its own workspace; AI agents — one per back-office
employee — discover how work actually happens, surface evidence-backed
inefficiencies, and (eventually) execute the fixes. Findings roll up into company
summaries, then portfolio-level opportunities (purchasing, software overlap,
cross-sell). Initial industry: HVAC roll-ups.

**Positioning:** "Vista turns acquired businesses' scattered records into an adaptive
operating platform. Each business gets the tools it needs, agents carry out
administrative work, and PE owners get evidence-backed opportunities to improve
operations across their portfolio."

## Who buys it

- **Economic buyer:** PE operating partner / platform-company exec / CFO responsible
  for post-acquisition improvement.
- **Implementation sponsor:** whoever owns integrating acquisitions.
- **Daily users:** office managers, bookkeepers, ops staff — who must barely notice
  Vista exists (the employee experience is a few onboarding questions and occasional
  one-tap verification questions, not a monitoring tool).

## Core hypotheses to validate (in order)

1. PE firms repeatedly hit fragmented operations across acquisitions and will PAY for
   a repeatable fix (one interview supports this; it is not proof).
2. Agent-discovered findings are accurate enough that sponsors act on them.
3. The second deployment takes meaningfully less custom work than the first
   (product vs consulting test).

## Course of action

### Now → hackathon (product exists: see IMPLEMENTATION_SO_FAR.md)
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
