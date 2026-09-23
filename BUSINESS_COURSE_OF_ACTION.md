# Vista — Business Course of Action

Distilled from the original business plan (full text in git history:
`BUSINESS_PLAN_AND_IDEAS.md`, removed as superseded) plus decisions made since.

## What Vista is

The AI operating platform for private equity rollups, delivered as a service.
Direction as of 2026-09-23: **AI services on the outside, software platform
underneath.**

A PE firm acquires a business and sends it to Vista. Vista connects the existing
systems, maps and records employee workflows, builds and deploys agents to automate
those workflows, and monitors exceptions and outcomes over time. The customer buys
an outcome — faster integration, lower SG&A, fewer manual processes, unified
visibility across the rollup — not a dashboard to configure.

The same platform is used every time: recorder, workflow-learning system, connectors,
agent runtime, evaluation, permissions, portfolio data layer. The three user views
sit on top of it:

- **PE analyst** and **portco CFO:** the reporting and visibility layer the service
  delivers — financial performance, assumptions, opportunities, and validated impact
  within authorized scope. The CFO sees the company-scoped subset of the same model.
- **FDE (forward-deployed engineer):** Vista's own delivery surface — workflow
  discovery, agent configuration and testing, approvals, exceptions, and measured
  operational results within assigned scope.

Shared canonical records and evidence connect the views. An operational result can
support a financial conclusion only with a baseline, explicit assumptions, and
validation. Connectors, an agent runtime acting in customer systems, and dedicated
CFO/FDE views are not yet shipped.

**Positioning:** "Vista is the AI operating platform for PE rollups. We initially
deploy alongside the customer to consolidate systems and automate workflows. Every
deployment expands our workflow library and improves the platform, so future
portfolio companies are onboarded with progressively less human implementation."

Do not describe Vista as an AI consulting company. Services are how Vista enters the
market and collects the proprietary knowledge (system mappings, workflow
demonstrations, exception handling, agent trajectories, human corrections, outcomes)
needed to build the product. The moat is that company #101 in a category does not
start from zero.

### Why services-first fits this market

- Every acquisition has different systems, schemas, processes, terminology,
  exceptions, and accounting practices. Pure SaaS dies on "who implements this?";
  Vista answers "Vista will."
- PE economics support it: eliminating $2M of SG&A or accelerating five tuck-ins
  justifies a six-figure engagement, not a per-seat budget fight.
- Each implementation teaches repeated patterns ("every HVAC rollup does this") that
  become product.

### Services → software trajectory

Target mix, directionally: year 1 ~70% services / 30% software; year 2 ~40/60;
long term 10–20% high-value deployment / 80–90% recurring software. Revenue must
scale with portfolio companies, workflows, and agents — not with Vista headcount.
The stages are consulting → tech-enabled services → AI-native services → software;
the recorder, reusable workflow library, agent learning, standard connectors, and a
portfolio-wide ontology are what move Vista along that ladder.

**Feature test:** does this reduce the Vista human labor required to deploy the
next customer? If not, it is deprioritized.

## Who buys it and who uses it

- **Economic buyer:** PE operating partner / platform-company executive / CFO
  responsible for post-acquisition improvement.
- **Financial users:** PE analysts and portco CFOs; the CFO's financial visibility
  is a company-scoped subset, not a separate set of numbers.
- **Implementation users:** Vista FDEs responsible for assigned workflows and
  automation outcomes; this role does not automatically carry portfolio financial
  access. Target: one FDE handling 10, then 30, then most companies with near-zero
  Vista involvement.
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

Priced on economic value, not seats:

- **Initial deployment fee** — discovery, system connection, workflow recording,
  agent build and verification, data standardization. Indicative $100k–$500k+
  depending on portfolio size and systems.
- **Recurring platform fee** — indicative $5k–$30k+ per portfolio company per month
  for the agents, monitoring, reporting layer, and portfolio data layer.
- **Outcome / usage pricing (optional)** — tied to automation volume or documented
  savings once measurement is trustworthy.

A 20-company rollup should be a large contract. Do not promise unlimited
customization or unlimited agent usage before support costs are known. Long-term
economic test: recurring revenue covers infra + service while deployment effort per
company declines across similar deployments.

## Metrics that matter

- **Onboarding:** time to first usable workspace; unresolved data issues.
- **Workflow:** human minutes per item; corrections; completion time.
- **Adoption:** active operational users; share of work done in Vista.
- **Financial:** documented collection improvements / realized cost reductions —
  never equate "time saved" with realized savings, and label scenario math as such.
- **Delivery economics (the productization metric):** Vista hours per company
  deployed; reusable vs custom; share of workflows served from the library vs built
  new; support + inference cost per tenant (already tracked via `usage_events`).

## Standing risks and how we hold them off

- **Scope creep** → one industry, one connected workflow set.
- **Surveillance backlash** → employee surface stays minimal; consent and retention
  policy ship before any observation feature; findings framed as questions, not
  accusations.
- **Consulting trap** (headcount ∝ revenue) → limit supported configurations; track
  Vista hours per deployment and require them to fall; ship every custom fix into
  the workflow library or connector set rather than leaving it in one customer.
- **Unreliable automation** → permissions, review gates, append-only audit logs,
  recoverable actions (all already in the architecture).
- **Unsubstantiated synergies** → every claim cites source records; observed fact /
  potential benefit / realized result are kept distinct.
