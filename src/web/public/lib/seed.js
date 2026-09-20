// Deterministic synthetic portfolio for the PE analyst demo. Every figure on
// screen is computed from these records; nothing is typed in as a total.
import { PERIOD } from "./format.js";

function prng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = (rnd, list) => list[Math.floor(rnd() * list.length)];
const between = (rnd, lo, hi) => lo + rnd() * (hi - lo);
const round2 = (n) => Math.round(n * 100) / 100;
function isoDate(rnd, start, end) {
  const a = new Date(start).getTime();
  const b = new Date(end).getTime();
  return new Date(a + rnd() * (b - a)).toISOString().slice(0, 10);
}
function addDays(iso, days) {
  const d = new Date(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

const FIRST = "James Maria Robert Linda David Karen Michael Susan Daniel Patricia Thomas Nancy Chris Angela Kevin Laura Brian Emily Mark Rachel".split(
  " ",
);
const LAST = "Nguyen Patel Sullivan Brooks Rivera Kim Murphy Costa Hansen Walsh Dubois Reyes Chen Ortiz Fletcher Baptiste Malone Kowalski Greer Lindqvist".split(
  " ",
);
const STREETS = "Main St, Oak Ave, Harbor Rd, Elm St, Maple Dr, Summit Blvd, Commerce Way, Pine St, Lakeview Ter, Industrial Pkwy".split(
  ", ",
);
const SERVICE = [
  "Residential service",
  "Residential install",
  "Commercial service",
  "Maintenance plan",
  "Light commercial",
];
const BIZ = [
  "Acme Foods",
  "Bayside Dental",
  "Pioneer Storage",
  "Northgate Church",
  "Riverbend Apartments",
  "Copper Kettle Bakery",
  "Lakeside Veterinary",
  "Meadow Ridge HOA",
  "Fairview Fitness",
  "Granite Peak Brewing",
];

export const CATALOG = [
  {
    sku: "FIL-MERV13-20X25",
    description: "Pleated air filter MERV 13, 20x25x1 (case of 12)",
    unit: "case",
  },
  {
    sku: "REF-R410A-25",
    description: "R-410A refrigerant, 25 lb cylinder",
    unit: "cylinder",
  },
  {
    sku: "CAP-45-5-440",
    description: "Dual run capacitor 45/5 MFD 440V",
    unit: "each",
  },
  {
    sku: "CONT-2P-40A",
    description: "Definite purpose contactor 2-pole 40A 24V",
    unit: "each",
  },
  {
    sku: "TSTAT-PRO-T6",
    description: "Programmable thermostat, Pro T6",
    unit: "each",
  },
  {
    sku: "MOTOR-COND-1/4HP",
    description: "Condenser fan motor 1/4 HP 208-230V",
    unit: "each",
  },
  {
    sku: "LINESET-3/8-3/4-50",
    description: "Copper line set 3/8 x 3/4, 50 ft",
    unit: "each",
  },
];

// Per-company unit prices. Differences between companies are what the
// purchasing analysis surfaces; keep them explicit so the demo is explainable.
const PRICES = {
  harbor: {
    "FIL-MERV13-20X25": 8.25,
    "REF-R410A-25": 165.0,
    "CAP-45-5-440": 12.4,
    "CONT-2P-40A": 21.9,
    "TSTAT-PRO-T6": 118.0,
    "MOTOR-COND-1/4HP": 142.5,
    "LINESET-3/8-3/4-50": 96.0,
  },
  summit: {
    "FIL-MERV13-20X25": 8.9,
    "REF-R410A-25": 142.0,
    "CAP-45-5-440": 14.75,
    "CONT-2P-40A": 21.9,
    "TSTAT-PRO-T6": 124.5,
    "MOTOR-COND-1/4HP": 139.0,
    "LINESET-3/8-3/4-50": 101.25,
  },
  cedar: {
    "FIL-MERV13-20X25": 10.1,
    "REF-R410A-25": 158.0,
    "CAP-45-5-440": 13.2,
    "CONT-2P-40A": 24.6,
    "TSTAT-PRO-T6": 118.0,
    "MOTOR-COND-1/4HP": 149.0,
    "LINESET-3/8-3/4-50": 96.0,
  },
};

export const COMPANY_SPECS = {
  harbor: {
    id: "harbor",
    name: "Harbor Heating",
    location: "Portland, ME",
    city: "Portland",
    state: "ME",
    industry: "Residential & light-commercial HVAC",
    acquired: "2025-03-14",
    seed: 11,
    customers: 212,
    invoices: 468,
    purchases: 190,
    vendors: [
      { name: "Ferguson HVAC Supply", source: "Ferguson HVAC Supply" },
      { name: "Johnstone Supply", source: "Johnstone Supply" },
      { name: "Carrier Enterprise", source: "Carrier Enterprise" },
      { name: "Grainger", source: "Grainger" },
    ],
    subscriptions: [
      ["ServiceTitan", "Field service", 1890, 14, "2026-10-10", "Auto-renews annually; 60-day notice to cancel"],
      ["QuickBooks Online Advanced", "Accounting", 200, 3, "2027-02-01", ""],
      ["Microsoft 365 Business", "Productivity", 264, 22, "2027-01-15", ""],
      ["Gusto", "Payroll", 318, 22, "2026-12-01", "Month-to-month"],
    ],
    analysisRunAt: "2026-09-17T14:20:00Z",
  },
  summit: {
    id: "summit",
    name: "Summit Mechanical",
    location: "Denver, CO",
    city: "Denver",
    state: "CO",
    industry: "Commercial HVAC & mechanical",
    acquired: "2025-11-03",
    seed: 23,
    customers: 158,
    invoices: 402,
    purchases: 165,
    overdueBoost: 7,
    vendors: [
      { name: "Johnson Controls", source: "Johnson Controls" },
      { name: "Johnstone Supply", source: "Johnstone Supply" },
      { name: "Ferguson HVAC Supply", source: "FERGUSON ENT #1188" },
      { name: "Trane Supply", source: "Trane Supply" },
    ],
    subscriptions: [
      ["ServiceTitan", "Field service", 2450, 19, "2027-03-31", "Enterprise tier; multi-year"],
      ["Sage Intacct", "Accounting", 690, 4, "2027-05-01", ""],
      ["Microsoft 365 Business", "Productivity", 336, 28, "2026-11-20", ""],
      ["Procore", "Project management", 1150, 6, "2027-01-01", "Contract value floor"],
    ],
    analysisRunAt: "2026-09-18T09:05:00Z",
  },
  cedar: {
    id: "cedar",
    name: "Cedar Climate",
    location: "Boston, MA",
    city: "Boston",
    state: "MA",
    industry: "Residential HVAC service",
    acquired: "2026-09-08",
    seed: 37,
    customers: 247,
    invoices: 391,
    purchases: 148,
    vendors: [
      { name: "Ferguson HVAC Supply", source: "Ferguson Enterprises" },
      { name: "Johnstone Supply", source: "Johnstone Supply Boston" },
      { name: "Johnson Control Services LLC", source: "Johnson Control Services LLC" },
      { name: "Grainger", source: "W.W. Grainger" },
    ],
    subscriptions: [
      ["Housecall Pro", "Field service", 549, 9, "2026-12-15", ""],
      ["QuickBooks Online Plus", "Accounting", 99, 2, "2027-04-01", ""],
      ["Google Workspace", "Productivity", 168, 14, "2027-02-10", ""],
    ],
  },
};

function provenance(file, sheet, row, original, normalized, job, confidence = 1) {
  return {
    file,
    sheet,
    row,
    original,
    normalized,
    importJob: job,
    confidence,
    review: confidence >= 0.9 ? "auto-accepted" : "reviewed",
  };
}

// Builds a full canonical dataset for one company. `job` names the import
// job so provenance can point back at it.
export function generateCompany(spec, job = `imp-${spec.id}-001`) {
  const rnd = prng(spec.seed);
  const c = spec.id;
  const files = {
    customers: `${c}_customers.csv`,
    invoices: `${c}_open_ar.xlsx`,
    purchases: `${c}_vendor_purchases.csv`,
    subs: `${c}_software.csv`,
  };
  const customers = [];
  for (let i = 0; i < spec.customers; i++) {
    const business = rnd() < 0.22;
    const name = business
      ? `${pick(rnd, BIZ)}${rnd() < 0.3 ? " LLC" : ""}`
      : `${pick(rnd, FIRST)} ${pick(rnd, LAST)}`;
    const id = `${c}-cust-${String(i + 1).padStart(4, "0")}`;
    const row = i + 2;
    const phoneRaw = `${Math.floor(between(rnd, 200, 989))}${Math.floor(between(rnd, 200, 989))}${Math.floor(between(rnd, 1000, 9999))}`;
    const phone = `(${phoneRaw.slice(0, 3)}) ${phoneRaw.slice(3, 6)}-${phoneRaw.slice(6)}`;
    const address = `${Math.floor(between(rnd, 10, 980))} ${pick(rnd, STREETS)}`;
    customers.push({
      id,
      companyId: c,
      name,
      contact: business ? `${pick(rnd, FIRST)} ${pick(rnd, LAST)}` : name,
      email: `${name.toLowerCase().replace(/[^a-z]+/g, ".").replace(/\.$/, "")}@example.com`,
      phone,
      address,
      city: spec.city,
      state: spec.state,
      zip: String(Math.floor(between(rnd, 10000, 99999))),
      serviceType: pick(rnd, SERVICE),
      status: rnd() < 0.9 ? "active" : "inactive",
      provenance: provenance(
        files.customers,
        "Sheet1",
        row,
        { Client: name, Phone: phoneRaw, City: `${spec.city} ${spec.state}` },
        { name, phone, city: spec.city, state: spec.state },
        job,
      ),
    });
  }
  // A planted near-duplicate customer for the exception queue.
  customers.push({
    id: `${c}-cust-dup-1`,
    companyId: c,
    name: "Acme Manufacturing LLC",
    contact: "Dana Whitfield",
    email: "ap@acmemfg.example.com",
    phone: "(617) 555-0142",
    address: "88 Commerce Way",
    city: spec.city,
    state: spec.state,
    zip: "02139",
    serviceType: "Commercial service",
    status: "active",
    provenance: provenance(files.customers, "Sheet1", customers.length + 2, { Client: "Acme Manufacturing LLC" }, { name: "Acme Manufacturing LLC" }, job),
  });
  customers.push({
    id: `${c}-cust-dup-2`,
    companyId: c,
    name: "ACME Manufacturing, L.L.C.",
    contact: "D. Whitfield",
    email: "accounts@acmemfg.example.com",
    phone: "(617) 555-0142",
    address: "88 Commerce Way",
    city: spec.city,
    state: spec.state,
    zip: "02139",
    serviceType: "Commercial service",
    status: "active",
    provenance: provenance(files.customers, "Sheet1", customers.length + 2, { Client: "ACME Manufacturing, L.L.C." }, { name: "ACME Manufacturing, L.L.C." }, job, 0.93),
  });

  const invoices = [];
  for (let i = 0; i < spec.invoices; i++) {
    const customer = pick(rnd, customers);
    const issue = isoDate(rnd, PERIOD.start, "2026-09-15");
    const due = addDays(issue, 30);
    const commercial = customer.serviceType.includes("ommercial");
    const amount = round2(commercial ? between(rnd, 900, 18500) : between(rnd, 180, 4200));
    const ageDays = Math.round((new Date("2026-09-19") - new Date(due)) / 86_400_000);
    let outstanding = 0;
    if (ageDays < 0) outstanding = rnd() < 0.75 ? amount : 0;
    else if (ageDays < 60) outstanding = rnd() < 0.28 ? amount : 0;
    else outstanding = rnd() < 0.08 ? amount : 0;
    const number = `${spec.name.split(" ")[0].slice(0, 3).toUpperCase()}-${10400 + i}`;
    invoices.push({
      id: `${c}-inv-${String(i + 1).padStart(4, "0")}`,
      companyId: c,
      customerId: customer.id,
      customerName: customer.name,
      number,
      issueDate: issue,
      dueDate: due,
      amount,
      outstanding,
      status: outstanding === 0 ? "paid" : ageDays > 0 ? "overdue" : "open",
      provenance: provenance(
        files.invoices,
        "Open AR",
        i + 2,
        { "Inv #": number, Client: customer.name, "Amt Due": String(outstanding), "Pay By": due },
        { number, customer_name: customer.name, outstanding_balance: outstanding, due_date: due },
        job,
      ),
    });
  }
  // Planted: a block of large invoices >90 days overdue.
  for (let i = 0; i < (spec.overdueBoost ?? 0); i++) {
    const customer = customers.filter((x) => x.serviceType.includes("ommercial"))[i * 3];
    const issue = addDays("2026-09-19", -(125 + i * 9));
    const due = addDays(issue, 30);
    const amount = round2(between(rnd, 6500, 24000));
    const number = `${spec.name.split(" ")[0].slice(0, 3).toUpperCase()}-${9800 + i}`;
    invoices.push({
      id: `${c}-inv-late-${i + 1}`,
      companyId: c,
      customerId: customer.id,
      customerName: customer.name,
      number,
      issueDate: issue,
      dueDate: due,
      amount,
      outstanding: amount,
      status: "overdue",
      provenance: provenance(files.invoices, "Open AR", invoices.length + 2, { "Inv #": number, Client: customer.name, "Amt Due": String(amount), "Pay By": due }, { number, outstanding_balance: amount, due_date: due }, job),
    });
  }

  const vendors = spec.vendors.map((v, i) => ({
    id: `${c}-ven-${i + 1}`,
    companyId: c,
    name: v.name,
    sourceName: v.source,
    contact: `ar@${v.name.toLowerCase().replace(/[^a-z]+/g, "")}.example.com`,
    provenance: provenance(files.purchases, "Sheet1", i + 2, { Vendor: v.source }, { normalized_name: v.name }, job, v.source === v.name ? 1 : 0.86),
  }));
  const purchases = [];
  for (let i = 0; i < spec.purchases; i++) {
    const item = pick(rnd, CATALOG);
    const vendor = vendors[i % vendors.length];
    const qty = Math.max(1, Math.round(between(rnd, 1, item.unit === "each" ? 24 : 8)));
    const unitPrice = PRICES[c][item.sku];
    const date = isoDate(rnd, PERIOD.start, "2026-09-12");
    purchases.push({
      id: `${c}-po-${String(i + 1).padStart(4, "0")}`,
      companyId: c,
      vendorId: vendor.id,
      vendorName: vendor.name,
      sku: item.sku,
      description: item.description,
      quantity: qty,
      unit: item.unit,
      unitPrice,
      date,
      total: round2(qty * unitPrice),
      provenance: provenance(
        files.purchases,
        "Sheet1",
        i + 2,
        { Vendor: vendor.sourceName, Item: item.sku, Qty: String(qty), Price: String(unitPrice), Date: date },
        { vendor: vendor.name, sku: item.sku, quantity: qty, unit_price: unitPrice, date },
        job,
      ),
    });
  }
  const subscriptions = spec.subscriptions.map(([product, category, cost, seats, renewal, notes], i) => ({
    id: `${c}-sub-${i + 1}`,
    companyId: c,
    product,
    category,
    monthlyCost: cost,
    seats,
    renewalDate: renewal,
    notes,
    provenance: provenance(files.subs, "Sheet1", i + 2, { Product: product, "Monthly $": String(cost), Renewal: renewal }, { product, monthly_cost: cost, renewal_date: renewal }, job),
  }));

  return {
    id: c,
    name: spec.name,
    location: spec.location,
    industry: spec.industry,
    acquired: spec.acquired,
    description: spec.description ?? "",
    syntheticDemo: true,
    analysisRunAt: spec.analysisRunAt ?? null,
    importJobs: [
      {
        id: job,
        createdAt: `${spec.acquired}T15:00:00Z`,
        files: Object.values(files),
        accepted: customers.length + invoices.length + purchases.length + subscriptions.length,
        reviewed: 2,
        rejected: 0,
      },
    ],
    importExceptions: [],
    customers,
    invoices,
    vendors,
    purchases,
    subscriptions,
  };
}

function seedTasks() {
  return [
    {
      id: "T-101",
      title: "Confirm ServiceTitan renewal terms before 60-day notice window",
      companyId: "harbor",
      description: "Harbor's ServiceTitan contract auto-renews on Oct 10, 2026. Summit is on the enterprise tier; check whether both can move to one agreement.",
      category: "Integration",
      sourceType: "subscription",
      sourceId: "harbor-sub-1",
      assignee: "Sarah Okafor",
      priority: "High",
      dueDate: "2026-09-26",
      status: "In progress",
      createdBy: "Sarah Okafor",
      createdAt: "2026-09-10T13:40:00Z",
      completedAt: null,
      outcome: null,
      outcomeNotes: "",
      realizedResult: null,
    },
    {
      id: "T-102",
      title: "Collect on 7 Summit invoices more than 90 days overdue",
      companyId: "summit",
      description: "Commercial accounts with balances past 90 days. Controller to call each account and confirm dispute status.",
      category: "Integration",
      sourceType: "finding",
      sourceId: "F-204",
      assignee: "Miguel Torres",
      priority: "High",
      dueDate: "2026-09-30",
      status: "Open",
      createdBy: "Vista (agent)",
      createdAt: "2026-09-16T08:12:00Z",
      completedAt: null,
      outcome: null,
      outcomeNotes: "",
      realizedResult: null,
    },
    {
      id: "T-103",
      title: "Map Summit chart of accounts to Northstar standard",
      companyId: "summit",
      description: "Sage Intacct export uses a 5-digit chart; Northstar reporting expects the 4-digit standard.",
      category: "Integration",
      sourceType: "import",
      sourceId: "imp-summit-001",
      assignee: "Sarah Okafor",
      priority: "Medium",
      dueDate: "2026-09-12",
      status: "Blocked",
      createdBy: "Sarah Okafor",
      createdAt: "2026-08-28T16:00:00Z",
      completedAt: null,
      outcome: null,
      outcomeNotes: "Waiting on Summit controller for account descriptions.",
      realizedResult: null,
    },
    {
      id: "T-104",
      title: "Review Harbor invoice-entry agent exception (duplicate PO reference)",
      companyId: "harbor",
      description: "The invoice reconciliation agent flagged two vendor invoices referencing the same PO.",
      category: "Agent exception",
      sourceType: "agent_run",
      sourceId: "run-harbor-recon-0043",
      assignee: "Sarah Okafor",
      priority: "Medium",
      dueDate: "2026-09-22",
      status: "Open",
      createdBy: "Vista (agent)",
      createdAt: "2026-09-18T11:30:00Z",
      completedAt: null,
      outcome: null,
      outcomeNotes: "",
      realizedResult: null,
    },
    {
      id: "T-105",
      title: "Consolidate Microsoft 365 tenants (Harbor + Summit)",
      companyId: "harbor",
      description: "Both companies pay for Microsoft 365 Business separately.",
      category: "Opportunity follow-up",
      sourceType: "opportunity",
      sourceId: "OP-011",
      assignee: "Priya Raman",
      priority: "Low",
      dueDate: "2026-08-30",
      status: "Complete",
      createdBy: "Sarah Okafor",
      createdAt: "2026-07-14T10:00:00Z",
      completedAt: "2026-08-27T15:10:00Z",
      outcome: "Implemented",
      outcomeNotes: "Moved Summit's 28 seats onto Harbor's tenant; 8 unused seats released.",
      realizedResult: 1152,
    },
  ];
}

function seedOpportunities() {
  return [
    {
      id: "OP-011",
      title: "Consolidate Microsoft 365 licensing across Harbor and Summit",
      category: "Software",
      companyIds: ["harbor", "summit"],
      confidence: 0.88,
      potentialValue: 1152,
      status: "Realized",
      foundAt: "2026-07-12T09:00:00Z",
      fact: "Harbor (22 seats, $264/mo) and Summit (28 seats, $336/mo) each hold a separate Microsoft 365 Business agreement.",
      evidence: [
        { companyId: "harbor", entity: "subscription", id: "harbor-sub-3" },
        { companyId: "summit", entity: "subscription", id: "summit-sub-3" },
      ],
      calculation: ["Summit pays $12.00/seat; 8 seats had no sign-in in 90 days.", "8 seats × $12.00 × 12 months = $1,152.00 scenario"],
      benefit: "Releasing the 8 idle Summit seats on a shared tenant would reduce annual software cost by $1,152.00.",
      assumptions: ["Seat usage from tenant admin export, not verified per user", "No migration cost included"],
      nextAction: "Move Summit onto Harbor's tenant and release idle seats.",
      realizedValue: 1152,
    },
    {
      id: "OP-014",
      title: "Summit working capital: 7 commercial invoices past 90 days",
      category: "Working capital",
      companyIds: ["summit"],
      confidence: 0.97,
      potentialValue: null,
      status: "Task created",
      foundAt: "2026-09-16T08:10:00Z",
      fact: "Seven Summit invoices with due dates more than 90 days before Sep 19, 2026 still carry their full balance.",
      evidence: [{ companyId: "summit", entity: "invoice", query: "overdue90" }],
      calculation: ["Sum of outstanding balance on the 7 invoices (see evidence)."],
      benefit: "Collecting these balances would convert outstanding AR to cash; no savings are claimed.",
      assumptions: ["Dispute status unknown", "Some balances may already be in collections"],
      nextAction: "Controller to confirm status of each account; escalate disputed balances.",
      realizedValue: null,
    },
    {
      id: "OP-016",
      title: "Automate Harbor vendor-invoice entry (observed copy/paste workflow)",
      category: "Process automation",
      companyIds: ["harbor"],
      confidence: 0.74,
      potentialValue: null,
      status: "Under review",
      foundAt: "2026-09-11T17:20:00Z",
      fact: "Recorder sessions show Harbor's AP clerk copying 6 fields per vendor invoice from email into QuickBooks, 43 times in the observed week.",
      evidence: [{ companyId: "harbor", entity: "finding", id: "F-201" }],
      calculation: ["43 invoices/week × ~4.5 min observed per invoice ≈ 3.2 hours/week of manual entry."],
      benefit: "Time saved is a scenario until the agent runs in shadow mode; no dollar value is claimed.",
      assumptions: ["Observed week is representative", "Agent accuracy not yet measured on Harbor data"],
      nextAction: "Run the invoice reconciliation agent in shadow mode for two weeks and compare.",
      realizedValue: null,
    },
  ];
}

function seedFindings() {
  return [
    {
      id: "F-201",
      companyId: "harbor",
      title: "AP clerk re-keys vendor invoice fields from email into QuickBooks",
      detail: "43 occurrences across 5 recorded sessions; the same 6 fields each time.",
      severity: "Medium",
      status: "Reviewed",
      agentId: "harbor-ap",
      runId: "run-harbor-ap-0007",
      foundAt: "2026-09-11T17:05:00Z",
    },
    {
      id: "F-202",
      companyId: "harbor",
      title: "Two vendor invoices reference the same PO (PO-2291)",
      detail: "Ferguson invoices 88213 and 88240 both cite PO-2291; totals differ by $312.40.",
      severity: "High",
      status: "Open",
      agentId: "harbor-recon",
      runId: "run-harbor-recon-0043",
      foundAt: "2026-09-18T11:28:00Z",
    },
    {
      id: "F-203",
      companyId: "harbor",
      title: "ServiceTitan renewal inside 30 days",
      detail: "Auto-renewal on Oct 10, 2026 with a 60-day cancellation notice.",
      severity: "Medium",
      status: "Actioned",
      agentId: "harbor-renewals",
      runId: "run-harbor-renewals-0012",
      foundAt: "2026-09-08T06:00:00Z",
    },
    {
      id: "F-204",
      companyId: "summit",
      title: "7 commercial invoices more than 90 days overdue",
      detail: "Full balances outstanding; all from Sage Intacct open-AR export.",
      severity: "High",
      status: "Actioned",
      agentId: "summit-ar",
      runId: "run-summit-ar-0021",
      foundAt: "2026-09-16T08:05:00Z",
    },
    {
      id: "F-205",
      companyId: "summit",
      title: "Vendor 'FERGUSON ENT #1188' matched to Ferguson HVAC Supply at 86%",
      detail: "Name match only; no tax ID in the source export.",
      severity: "Low",
      status: "Open",
      agentId: "summit-ap",
      runId: "run-summit-ap-0004",
      foundAt: "2026-09-14T12:40:00Z",
    },
  ];
}

function seedAgents() {
  const run = (id, agentId, companyId, goal, startedAt, status, steps, output, cost, review = 0) => ({
    id,
    agentId,
    companyId,
    goal,
    startedAt,
    status,
    sources: steps.sources,
    events: steps.events,
    output,
    evidence: steps.evidence ?? [],
    corrections: steps.corrections ?? [],
    modelCost: cost,
    needsReview: review,
  });
  return {
    agents: [
      { id: "harbor-ap", companyId: "harbor", name: "AP discovery", represents: "AP clerk (recorded sessions)", status: "Active", lastRunAt: "2026-09-18T15:10:00Z", cases: 61, review: 1, findings: 3, lastFailure: null, cost: 1.84 },
      { id: "harbor-recon", companyId: "harbor", name: "Invoice reconciliation", represents: "Vendor invoice → PO matching", status: "Active", lastRunAt: "2026-09-18T11:30:00Z", cases: 43, review: 1, findings: 1, lastFailure: null, cost: 3.12 },
      { id: "harbor-renewals", companyId: "harbor", name: "Renewal watch", represents: "Software & contract renewals", status: "Active", lastRunAt: "2026-09-19T06:00:00Z", cases: 4, review: 0, findings: 1, lastFailure: null, cost: 0.21 },
      { id: "summit-ar", companyId: "summit", name: "AR aging review", represents: "Controller (open-AR export)", status: "Active", lastRunAt: "2026-09-16T08:05:00Z", cases: 402, review: 0, findings: 1, lastFailure: null, cost: 0.96 },
      { id: "summit-ap", companyId: "summit", name: "AP discovery", represents: "Vendor master normalisation", status: "Paused", lastRunAt: "2026-09-14T12:40:00Z", cases: 165, review: 1, findings: 1, lastFailure: "2026-09-13T22:10:00Z · Sage export timed out", cost: 1.05 },
    ],
    runs: [
      run(
        "run-harbor-recon-0043",
        "harbor-recon",
        "harbor",
        "Match this week's 43 Ferguson and Johnstone vendor invoices to purchase orders and flag mismatches.",
        "2026-09-18T11:02:00Z",
        "Needs review",
        {
          sources: ["harbor_vendor_purchases.csv (190 rows)", "AP inbox export, Sep 12–18 (43 messages)", "Open PO list (61 rows)"],
          events: [
            ["11:02", "Loaded 43 vendor invoices from the AP inbox export."],
            ["11:03", "Matched 41 invoices to open POs by vendor + PO number + total within $1."],
            ["11:04", "Invoice 88213 and 88240 both reference PO-2291 — totals $2,114.60 and $2,427.00."],
            ["11:05", "Could not decide which invoice is the duplicate; escalated to review."],
            ["11:06", "Emitted finding F-202 with both invoice rows and the PO line attached."],
          ],
          evidence: ["Ferguson invoice 88213 · PO-2291 · $2,114.60", "Ferguson invoice 88240 · PO-2291 · $2,427.00", "PO-2291 · Ferguson HVAC Supply · $2,114.60"],
          corrections: [],
        },
        "41 of 43 invoices matched automatically. 2 invoices need a human decision (same PO referenced twice).",
        0.41,
        1,
      ),
      run(
        "run-harbor-ap-0007",
        "harbor-ap",
        "harbor",
        "Explain the recurring stretches of work in this week's recorder sessions from Harbor's AP clerk.",
        "2026-09-11T16:40:00Z",
        "Complete",
        {
          sources: ["5 recorder session reports (Sep 8–11)", "Activity evidence log (1,204 rows)"],
          events: [
            ["16:40", "Read 5 uploaded session reports; 61 sections."],
            ["16:44", "Grouped 43 sections as 'enter vendor invoice from email into QuickBooks'."],
            ["16:52", "Employee confirmed the label on 39 sections; corrected 4 to 'reply to vendor about missing PO'."],
            ["17:05", "Emitted finding F-201 with 43 linked sections."],
          ],
          evidence: ["Section 14 · Outlook → QuickBooks · 6 paste events", "Section 22 · Outlook → QuickBooks · 6 paste events"],
          corrections: ["4 sections relabelled by the employee (Sep 11)"],
        },
        "One repetitive workflow identified: vendor-invoice entry, 43 occurrences, ~4.5 minutes each.",
        0.88,
      ),
      run(
        "run-summit-ar-0021",
        "summit-ar",
        "summit",
        "Age every open Summit invoice and list balances more than 90 days past due.",
        "2026-09-16T08:00:00Z",
        "Complete",
        {
          sources: ["summit_open_ar.xlsx · sheet 'Open AR' (409 rows)"],
          events: [
            ["08:00", "Loaded 409 invoice rows; 402 parsed, 7 duplicates of earlier rows skipped."],
            ["08:03", "Computed days past due against Sep 16, 2026 (deterministic; no model call)."],
            ["08:05", "7 invoices exceed 90 days; emitted finding F-204 and opportunity OP-014."],
          ],
          evidence: ["7 invoice rows attached to F-204"],
        },
        "7 invoices more than 90 days overdue. All commercial accounts.",
        0.05,
      ),
      run(
        "run-summit-ap-0004",
        "summit-ap",
        "summit",
        "Normalise Summit's vendor names against the portfolio vendor master.",
        "2026-09-14T12:30:00Z",
        "Needs review",
        {
          sources: ["summit_vendor_purchases.csv (165 rows)", "Portfolio vendor master (4 vendors)"],
          events: [
            ["12:30", "Extracted 4 distinct vendor strings."],
            ["12:38", "'FERGUSON ENT #1188' resembles 'Ferguson HVAC Supply' (86%); not merged automatically."],
            ["12:40", "Emitted finding F-205 for confirmation."],
          ],
          evidence: ["Source string 'FERGUSON ENT #1188' · 58 purchase rows"],
        },
        "3 vendors matched exactly; 1 possible match awaits confirmation.",
        0.19,
        1,
      ),
      run(
        "run-harbor-renewals-0012",
        "harbor-renewals",
        "harbor",
        "List software renewals due within 45 days and their notice terms.",
        "2026-09-19T06:00:00Z",
        "Complete",
        {
          sources: ["harbor_software.csv (4 rows)"],
          events: [
            ["06:00", "Loaded 4 subscriptions."],
            ["06:00", "ServiceTitan renews Oct 10, 2026 (21 days); 60-day notice window already passed."],
          ],
          evidence: ["harbor_software.csv row 2"],
        },
        "1 renewal inside 45 days: ServiceTitan.",
        0.02,
      ),
    ],
  };
}

function seedActivity() {
  return [
    { at: "2026-09-19T06:00:00Z", companyId: "harbor", text: "Renewal watch agent completed a run: ServiceTitan renews in 21 days.", kind: "agent" },
    { at: "2026-09-18T11:30:00Z", companyId: "harbor", text: "Invoice reconciliation agent completed 43 runs; 2 need review.", kind: "agent" },
    { at: "2026-09-18T11:31:00Z", companyId: "harbor", text: "Vista created task T-104 from agent run run-harbor-recon-0043.", kind: "task" },
    { at: "2026-09-17T14:20:00Z", companyId: "harbor", text: "Portfolio analysis completed for Harbor Heating.", kind: "analysis" },
    { at: "2026-09-16T08:12:00Z", companyId: "summit", text: "Vista created task T-102 from finding F-204.", kind: "task" },
    { at: "2026-09-16T08:05:00Z", companyId: "summit", text: "Vista identified working-capital opportunity OP-014.", kind: "opportunity" },
    { at: "2026-09-14T12:40:00Z", companyId: "summit", text: "AP discovery agent paused after Sage export timed out; 1 finding awaiting review.", kind: "agent" },
    { at: "2026-09-11T17:20:00Z", companyId: "harbor", text: "Vista identified process-automation opportunity OP-016.", kind: "opportunity" },
    { at: "2026-08-27T15:10:00Z", companyId: "harbor", text: "Priya Raman completed T-105 — Microsoft 365 consolidation implemented ($1,152 realized).", kind: "task" },
  ];
}

export function seedState() {
  const { agents, runs } = seedAgents();
  return {
    version: 1,
    companies: [generateCompany(COMPANY_SPECS.harbor), generateCompany(COMPANY_SPECS.summit)],
    tasks: seedTasks(),
    opportunities: seedOpportunities(),
    findings: seedFindings(),
    agents,
    runs,
    activity: seedActivity(),
    nextIds: { task: 106, opportunity: 17, job: 3 },
  };
}
