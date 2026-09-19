# Business Plan / Ideas — Vista

## Business Plan — Vista

### An Adaptive Operating Platform for Lower-Middle-Market Private Equity

## Executive summary

Vista helps private equity firms onboard and improve acquired businesses whose operations depend on spreadsheets, paper records, disconnected software, and employee knowledge.

The platform provides company-specific workspaces with CRM, invoice management, tasks, and configurable workflows. AI agents analyze available information, reconstruct operational records, recommend configurations, and assist with recurring work. A portfolio view identifies opportunities to coordinate operations across companies.

Vista can begin during acquisition diligence by organizing authorized virtual data room information. After closing, it expands through operational imports, integrations, and direct employee use.

The long-term ambition is a common operating platform for PE roll-ups. The initial product focuses on customer and invoice operations in one industry, with an evidence-backed portfolio analysis.

## 1. Customer insight and market hypothesis

Our initial conversation with a lower-middle-market PE employee suggested that technology maturity varies significantly across potential acquisitions.

The interviewee described businesses using rudimentary spreadsheets, handwritten information, and knowledge held by owners or employees. They reported that many companies their team examined—including some above $50 million in revenue—lacked an ERP or a developed CRM database.

Other companies had established tools or specialized industry platforms. HubSpot, NetSuite, and PartsBase were mentioned as examples.

This is qualitative evidence from one interview. It supports further investigation; it does not establish market-wide adoption rates, willingness to pay, or demand for Vista specifically.

Our hypothesis: PE firms repeatedly encounter fragmented operations across acquisitions and would pay for a faster, repeatable way to organize records, deploy missing capabilities, and improve selected workflows.

## 2. Target customer

- **Economic buyer:** a PE operating partner, platform-company executive, or CFO responsible for post-acquisition improvement.
- **Implementation sponsor:** the person accountable for integrating acquisitions or improving back-office operations.
- **Daily users:** office managers, bookkeepers, customer-service staff, and operations employees.
- **Initial target:** a PE-backed platform pursuing repeat acquisitions within one industry, with manual customer and invoice processes and a willing implementation sponsor.

HVAC is the proposed initial industry for the prototype. Commercial selection should depend on access to design partners, recurring workflows, and measurable problems.

Starting within one industry increases the likelihood that integrations, terminology, and workflow configurations can be reused.

## 3. The problem

Acquisitions create several connected operational challenges:

- **Fragmented records:** information is distributed across files, inboxes, paper, and software.
- **Dependence on individuals:** employees know how processes work, but that knowledge is poorly documented.
- **Missing capabilities:** some businesses lack a practical way to manage customer relationships, invoices, or follow-ups.
- **Inconsistent processes:** acquired companies use different definitions, formats, and approval rules.
- **Limited portfolio visibility:** owners struggle to compare operations or substantiate potential synergies.
- **Repeated implementation work:** each acquisition can require another round of cleanup, migration, and configuration.

Vista's value proposition is to reduce the effort between acquiring a business and operating it through reliable, understandable workflows.

## 4. Product vision

Vista has two connected interfaces.

| Interface | Purpose |
| --- | --- |
| Company workspace | Run customer, invoice, task, and administrative workflows for an individual business |
| Portfolio workspace | Monitor approved cross-company metrics, track integration progress, and investigate opportunities |

Each company has separated records, permissions, and settings. Authorized portfolio users can view selected information across companies.

Vista provides its own modules while supporting three deployment paths:

| Company's starting point | Vista's approach |
| --- | --- |
| Little structured software | Import and organize records, then activate needed modules |
| Useful existing CRM or ERP | Integrate with it and define which system owns each type of record |
| Specialized industry software | Preserve its specialized functions and connect relevant data |

Over time, customers may move more work into Vista. Replacing every existing system is not a prerequisite for delivering value.

## 5. How the agents work

Agents support four stages.

- **Discover:** extract facts from documents, interpret spreadsheet structures, identify existing systems, and collect missing information through guided questions.
- **Propose:** recommend field mappings, modules, payment terms, approval rules, and workflow changes, with supporting evidence.
- **Execute:** carry out supported administrative steps within defined permissions and route exceptions for review.
- **Analyze:** compare authorized portfolio information and suggest specific improvements.

The platform distinguishes observed facts from recommendations. It also separates the company's current process from a proposed future process.

Initially, agents configure supported application features. Application code validates changes, enforces permissions, and performs financial calculations. Arbitrary agent-generated changes to production software are outside the initial scope.

## 6. Acquisition-to-operation lifecycle

### During diligence

Use authorized VDR material to build an initial company profile, document inventory, system map, and automation-readiness assessment.

Historical documents remain labeled as historical. Access and retention depend on the permitted purpose of the diligence process.

### After closing

Validate the initial profile with employees and live records. Import current customer, vendor, and invoice information. Resolve duplicates and conflicting values, then configure a first operational workflow.

### During ongoing operation

Keep information current through integrations, repeatable imports, or direct activity in Vista. Track exceptions and corrections to improve the workflow.

### Across subsequent acquisitions

Reuse proven templates and connectors while adapting company-specific settings. Measure how much implementation effort actually carries over.

Desktop observation is a later discovery option. It should supplement interviews and system records, particularly for processes that are difficult to explain, rather than serve as the sole source of operational knowledge.

## 7. Initial product and expansion

The first commercial product should cover a narrow customer-to-invoice workflow:

- Customer and contact records.
- Invoice imports and tracking.
- Due dates and outstanding balances.
- Follow-up tasks and message drafts.
- Basic company-specific rules.
- Portfolio reporting based on comparable definitions.

Full accounting, payment processing, payroll, legal decision-making, and broad compliance automation are later product areas with distinct requirements.

The first portfolio analysis can identify shared vendors, inconsistent terms, or overlapping administrative processes. Findings should include source records, an accountable owner, and a proposed next action.

Estimated benefits remain hypotheses until validated. Shared vendors alone do not prove that a purchasing discount is available.

## 8. Business model

A proposed commercial structure has three components:

- **Implementation fee:** covers discovery, data cleanup, migration, and configuration.
- **Recurring company subscription:** covers the deployed modules and ordinary platform use.
- **Portfolio and usage pricing:** covers cross-company capabilities and unusually intensive automation.

Pricing should be validated through paid pilots. Avoid promising unlimited customization or unlimited agent usage before support costs are understood.

A pilot should have a defined workflow, implementation boundary, success measures, and conversion decision.

The long-term economic test is whether recurring revenue can support infrastructure and service costs while implementation effort declines across similar deployments.

## 9. Go-to-market

Start with a PE operating partner or platform-company executive who can sponsor a pilot at one business.

Interview the staff performing the workflow before selecting the first automation. Obtain representative, authorized files and establish the existing process baseline.

Deploy at one company, document the operational result, and then expand to a second company within the same portfolio.

The second deployment is especially important: it tests whether Vista is becoming a repeatable product or remains a custom implementation service.

A successful initial relationship could expand through additional acquisitions, adjacent workflows, and introductions to other operating teams.

## 10. Differentiation and defensibility

Vista's proposed differentiation combines:

- Adoption paths for companies with very different technology maturity.
- Its own operational modules where capabilities are missing.
- Acquisition onboarding that continues into daily operations.
- Agent recommendations tied to inspectable evidence.
- Company-level execution connected to portfolio-level analysis.

Potential defensibility would come from tested industry workflows, reusable integrations, reliable exception handling, and faster deployments—not simply access to an underlying language model.

Over time, reviewed workflow outcomes could improve configuration and automation quality. Customer data should remain separated, and any cross-company learning or model training requires appropriate rights and permissions.

Model training is a later option. Retrieval, structured records, configuration, and evaluated examples are sufficient to test the initial product.

## 11. Success metrics

Measure both customer value and delivery economics.

| Area | Example measures |
| --- | --- |
| Onboarding | Time to first usable workspace; unresolved data issues |
| Workflow performance | Human minutes per item; corrections; completion time |
| Adoption | Active operational users; share of relevant work completed in Vista |
| Financial value | Documented collection improvements or realized cost reductions |
| Reliability | Failed actions; exception rates; successful recovery |
| Implementation economics | Engineering hours per company; reusable versus custom work |
| Customer economics | Support cost, infrastructure cost, retention, and expansion |

Do not equate time saved with realized expense reductions. Likewise, operating improvements and financial leverage are different mechanisms; Vista should make claims about results it can measure.

## 12. Key risks and responses

- **Excessive scope:** start with one industry and a small set of connected workflows.
- **Poor source data:** preserve provenance, expose ambiguity, and involve employees in resolution.
- **Implementation becoming consulting:** limit supported configurations and track custom engineering effort.
- **Low employee adoption:** design around actual daily work and introduce changes gradually.
- **Unreliable automation:** use explicit permissions, review points, logs, and recoverable actions.
- **Weak purchasing demand:** seek a paid pilot before assuming interest will translate into revenue.
- **Unsubstantiated synergies:** present evidence-backed opportunities and track whether proposed actions produce results.

## 13. The 24-hour hackathon prototype

Demonstrate two synthetic HVAC acquisitions with inconsistent customer, invoice, and vendor files.

The live sequence is:

1. Upload the company packet.
2. Propose field mappings and flag ambiguity.
3. Review company-specific settings.
4. Create a working CRM and invoice workspace.
5. Switch between the two separated companies.
6. Identify a shared-vendor opportunity with supporting records.
7. Create an assigned follow-up task.

Use a real import and configuration flow within a clearly supported format. Leave desktop recording, live ERP integrations, payment execution, and model training outside the prototype.

Suggested allocation: two hours for scope and fixtures, six for the application and database, six for ingestion and agent proposals, four for portfolio analysis, and six for testing and presentation.

## 14. Post-hackathon validation

The next milestone is one paid, narrowly scoped deployment.

Before expanding the product, establish:

- A buyer with an urgent problem and budget.
- A workflow with measurable baseline costs.
- Access to representative operational data.
- Employees willing to participate.
- Reliable performance under real exceptions.
- A second deployment that requires meaningfully less custom work.

## Positioning statement

Vista turns acquired businesses' scattered records into an adaptive operating platform. It deploys the tools each company needs, helps agents carry out administrative work, and gives PE owners evidence-backed opportunities to improve operations across their portfolio.

---

# Vista — 24-Hour Hackathon Plan

Yes—make the main portfolio dashboard the center of the demo, with synthetic businesses underneath it and an agent that finds actionable synergies between them. Each finding should lead to something the user can inspect and act on.

## What we're building

A platform where a PE firm can view its portfolio companies, inspect their operations, and use an AI agent to identify opportunities across them.

For the prototype, create three synthetic HVAC companies with different customer bases, vendors, software subscriptions, and operating practices.

The demo: a newly acquired company joins the portfolio, its information becomes a working company workspace, and the agent identifies opportunities involving the existing businesses.

## 1. Main portfolio dashboard

The landing page shows:

- Portfolio companies and their locations.
- Revenue, outstanding invoices, and vendor spending.
- Recommended synergies.
- Assigned integration tasks and their status.

Clicking a company opens its individual workspace. Clicking an opportunity opens the underlying evidence.

Keep the financial metrics explicitly labeled as synthetic demo data and use consistent reporting periods.

## 2. Three synthetic companies

Use fictional businesses with deliberately designed overlaps:

| Company | Business profile | Example opportunity |
| --- | --- | --- |
| Harbor Heating | Residential HVAC maintenance | Buys the same components as another company at different prices |
| Summit Mechanical | Commercial HVAC installation | Has commercial customers who may need ongoing maintenance |
| Cedar Climate | Newly acquired HVAC service business | Adds purchasing volume and uses overlapping software subscriptions |

Generate a small, coherent dataset for each:

- Company profile and service offerings.
- Approximately 20 customers.
- Approximately 30 invoices.
- Vendor purchases with quantities, item identifiers, and prices.
- Software subscriptions, renewal dates, and seat counts.
- A short operating-policy document.

Build a known answer sheet alongside the data so we can check whether the agent's findings are correct.

Include at least one misleading overlap—for example, similar vendor names belonging to different suppliers—to test whether the system avoids a false match.

## 3. Company workspaces

Each business gets a lightweight version of our own operating platform:

- **Overview:** company profile and operational metrics.
- **CRM:** customers, contacts, services purchased, and follow-up notes.
- **Billing:** invoices, due dates, payment status, and outstanding amounts.
- **Vendors:** purchasing records and subscriptions.
- **Tasks:** proposed actions, assignees, and completion status.

Use the same application structure for every company, with separate records and configurable settings. For this prototype, deploying a company means creating its workspace inside the shared platform.

## 4. The synergy agent

Give the agent three bounded analyses.

### Purchasing opportunities

Identify the same supplier and comparable items purchased by multiple businesses. Compare unit prices only when quantities, units, and other relevant terms are compatible.

Example output:

> Harbor and Cedar purchased the same filter SKU at different unit prices. Review whether Cedar can obtain Harbor's rate.

Show historical spending and a clearly labeled scenario calculation. A price difference is evidence for investigation, not a guaranteed discount.

### Software overlap

Find subscriptions serving similar purposes across companies.

Example output:

> Two businesses use separate scheduling products. Review whether a shared arrangement is practical before their renewal dates.

Show subscription cost, renewal date, and known restrictions. Do not assume overlapping software can automatically be canceled.

### Cross-selling opportunities

Match one company's customer needs with another company's services.

Example output:

> Summit has installation customers without recorded maintenance agreements. Harbor offers maintenance services in the same region.

Treat missing records as uncertainty. Propose a review task rather than claiming the customers lack coverage.

## 5. What every finding contains

Each opportunity should include:

- A concise title and explanation.
- Affected companies.
- Source records.
- Supporting calculations, if applicable.
- Assumptions and missing information.
- A recommended next action.
- A button to create an integration task.

Keep three categories distinct:

- **Observed fact:** supported by the records.
- **Potential benefit:** depends on stated assumptions.
- **Realized result:** recorded only after the action produces a verified outcome.

Avoid combining speculative benefits into a headline "guaranteed savings" number.

## 6. New acquisition onboarding

Seed Harbor and Summit in advance. Add Cedar live during the demo.

1. Upload Cedar's CSV files and company profile.
2. Have the agent propose field mappings.
3. Show one ambiguous record for human review.
4. Approve the import.
5. Create Cedar's CRM and billing workspace.
6. Run portfolio analysis again.
7. Surface opportunities involving Cedar.

This demonstrates both company adaptation and portfolio value in one sequence.

## 7. Technical approach

Use one web application and database.

Core records:

- Portfolio
- Company
- Customer
- Invoice
- Vendor purchase
- Subscription
- Source document
- Opportunity
- Task

Associate operational records with a company. Portfolio analysis can access the companies explicitly included in its scope.

Use application code to calculate totals, compare prices, and validate imported records. Use the language model to interpret inconsistent labels, explain patterns, and propose actions.

Require structured agent output so every finding can be rendered consistently and checked for valid source references.

Start with a single agent workflow. Multiple specialized agents can come later if they solve a demonstrated problem.

## 8. Build priorities

### Must work

- Portfolio dashboard.
- Three company workspaces.
- Customer and invoice views.
- One supported acquisition import.
- An actual agent analysis using the stored data.
- Evidence-backed opportunity details.
- "Create task" functionality.

### Add if time permits

- A question box: "What changed after acquiring Cedar?"
- Before-and-after comparison of portfolio findings.
- Company-specific invoice terms.
- A downloadable integration checklist.

### Leave out

- Desktop recording.
- Live banking or payment execution.
- Full accounting, payroll, legal, or compliance modules.
- Training a model.
- Broad ERP integrations.
- Autonomous outreach or contract changes.

## 9. Twenty-four-hour schedule

| Time | Focus |
| --- | --- |
| Hours 0–2 | Lock the demo story, schema, synthetic records, and expected findings |
| Hours 2–7 | Build portfolio and company screens with working database records |
| Hours 7–12 | Implement import, field mapping, validation, and workspace creation |
| Hours 12–17 | Implement synergy analysis, source references, and calculations |
| Hours 17–20 | Connect findings to tasks and complete the full demo sequence |
| Hours 20–24 | Test, fix failures, polish, rehearse, and record a backup demo |

With two engineers, one owns the application and data layer; the other owns ingestion and agent analysis. Agree on input and output formats in the first two hours.

If time gets tight, keep purchasing analysis and cut the other two analyses. Preserve the complete workflow from import to evidence to action.

## 10. Demo script

1. "Here are two businesses in our portfolio." — Show the main dashboard and briefly open a company's CRM and invoices.
2. "We just acquired Cedar, whose records are in spreadsheets." — Upload its packet and review the proposed mappings.
3. "Vista creates a usable workspace from those records." — Show Cedar's imported customers and invoices.
4. "Now we can ask what this acquisition changes across the portfolio." — Run the agent and open a purchasing opportunity.
5. "This recommendation is tied to actual records." — Show the matching items, prices, assumptions, and scenario calculation.
6. "Turn the finding into accountable work." — Create a task to review supplier terms and show it on the portfolio dashboard.

## 11. Definition of success

A judge can understand the business problem, watch new information enter the platform, inspect a valid agent finding, and see it become an action.

The prototype should also pass three checks:

1. Changing the input data changes the findings.
2. Unsupported or ambiguous matches are flagged.
3. Displayed calculations agree with the underlying records.

## Pitch

"Vista gives PE firms a common operating platform for their acquisitions. Each business gets its own customer and billing workspace, while an AI agent analyzes the portfolio for purchasing, software, and revenue opportunities—and turns supported findings into actionable integration tasks."
