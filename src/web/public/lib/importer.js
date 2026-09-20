// Acquisition import: CSV parsing, dataset detection, field mapping,
// deterministic normalisation and exception detection. Front-end only for
// the demo; the same steps map onto ImportJob / FieldMapping /
// ImportException server-side later.
import { COMPANY_SPECS, generateCompany } from "./seed.js";

// ---- CSV ----------------------------------------------------------------
export function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += ch;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  const [header = [], ...body] = rows.filter((r) => r.some((c) => c !== ""));
  return {
    columns: header.map((h) => h.trim()),
    rows: body.map((r) => Object.fromEntries(header.map((h, i) => [h.trim(), (r[i] ?? "").trim()]))),
  };
}
export function toCsv(columns, rows) {
  const esc = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => columns.map((c) => esc(r[c])).join(","))].join("\n");
}

// ---- Canonical schema ----------------------------------------------------
export const DATASETS = {
  customers: {
    label: "Customers",
    fields: {
      customer_name: { label: "Customer name", required: true, aliases: ["client", "customer", "name", "account name", "company", "customer name", "bill to"] },
      contact: { label: "Contact", aliases: ["contact", "attn", "primary contact"] },
      email: { label: "Email", aliases: ["email", "e-mail", "contact email"] },
      phone: { label: "Phone", aliases: ["phone", "tel", "telephone", "phone #", "mobile"] },
      address: { label: "Address", aliases: ["address", "street", "addr", "service address"] },
      city: { label: "City", aliases: ["city", "town", "city/state", "city, state", "location"] },
      state: { label: "State", aliases: ["state", "st", "province"] },
      zip: { label: "ZIP", aliases: ["zip", "zip code", "postal", "postal code"] },
      service_type: { label: "Service type", aliases: ["service", "service type", "type", "segment", "line"] },
      status: { label: "Status", aliases: ["status", "active", "active?"] },
      source_customer_id: { label: "Source customer ID", aliases: ["customer id", "cust id", "id", "customer #"] },
    },
  },
  invoices: {
    label: "Invoices / Accounts Receivable",
    fields: {
      source_invoice_number: { label: "Source invoice number", required: true, aliases: ["inv #", "invoice", "invoice #", "invoice number", "inv no", "number", "ref"] },
      customer_name: { label: "Customer name", required: true, aliases: ["client", "customer", "customer name", "bill to", "account"] },
      issue_date: { label: "Issue date", aliases: ["date", "invoice date", "issued", "inv date"] },
      due_date: { label: "Due date", aliases: ["due", "due date", "pay by", "payable by"] },
      amount: { label: "Amount", aliases: ["amount", "total", "invoice total", "amt", "billed"] },
      outstanding_balance: { label: "Outstanding balance", aliases: ["balance", "amt due", "amount due", "open", "outstanding", "remaining"] },
      status: { label: "Status", aliases: ["status", "paid?", "state"] },
    },
  },
  vendors: {
    label: "Vendors / Purchases",
    fields: {
      vendor_name: { label: "Vendor", required: true, aliases: ["vendor", "supplier", "payee", "vendor name"] },
      sku: { label: "SKU / item", aliases: ["sku", "item", "item #", "part", "part #", "product code"] },
      description: { label: "Description", aliases: ["description", "desc", "item description", "memo"] },
      quantity: { label: "Quantity", aliases: ["qty", "quantity", "units"] },
      unit: { label: "Unit", aliases: ["unit", "uom", "u/m"] },
      unit_price: { label: "Unit price", aliases: ["price", "unit price", "rate", "cost", "unit cost"] },
      date: { label: "Date", aliases: ["date", "po date", "purchase date", "ordered"] },
      total: { label: "Total", aliases: ["total", "ext price", "extended", "line total", "amount"] },
    },
  },
  subscriptions: {
    label: "Software subscriptions",
    fields: {
      product: { label: "Vendor / product", required: true, aliases: ["product", "software", "vendor", "application", "tool", "service"] },
      category: { label: "Category", aliases: ["category", "type", "function"] },
      monthly_cost: { label: "Monthly cost", aliases: ["monthly $", "monthly", "cost", "monthly cost", "price", "mrr"] },
      seats: { label: "Seats", aliases: ["seats", "users", "licenses", "licences"] },
      renewal_date: { label: "Renewal date", aliases: ["renewal", "renews", "renewal date", "term end", "expires"] },
      notes: { label: "Contract notes", aliases: ["notes", "terms", "contract", "restrictions"] },
    },
  },
  profile: { label: "Company profile", fields: {} },
  other: { label: "Other", fields: {} },
};

const norm = (s) =>
  String(s)
    .toLowerCase()
    .replace(/[^a-z0-9#$/?]+/g, " ")
    .trim();

// Which dataset does a table look like? Scores each dataset by how many of
// its aliases the header hits; confidence is the share of columns matched.
export function detectDataset(columns, filename = "") {
  const heads = columns.map(norm);
  const scores = Object.entries(DATASETS)
    .filter(([key]) => DATASETS[key].fields && Object.keys(DATASETS[key].fields).length)
    .map(([key, def]) => {
      let hits = 0;
      for (const head of heads) {
        if (Object.values(def.fields).some((f) => f.aliases.includes(head))) hits++;
      }
      let bonus = 0;
      const name = filename.toLowerCase();
      if (key === "invoices" && /ar|invoice|receivable/.test(name)) bonus = 0.15;
      if (key === "customers" && /customer|client|account/.test(name)) bonus = 0.15;
      if (key === "vendors" && /vendor|purchase|po|supplier/.test(name)) bonus = 0.15;
      if (key === "subscriptions" && /software|subscription|saas|license/.test(name)) bonus = 0.15;
      const shared = heads.filter((h) => ["date", "total", "amount", "status", "type", "id"].includes(h)).length;
      const score = heads.length ? (hits - shared * 0.4) / heads.length + bonus : 0;
      return { key, label: def.label, score };
    })
    .sort((a, b) => b.score - a.score);
  const best = scores[0];
  const runnerUp = scores[1];
  const margin = best ? best.score - (runnerUp?.score ?? 0) : 0;
  const confidence = Math.max(0, Math.min(0.99, best.score * 0.75 + margin * 0.6 + 0.1));
  return { dataset: best?.key ?? "other", label: DATASETS[best?.key ?? "other"].label, confidence: Number(confidence.toFixed(2)) };
}

// Propose one Vista field per source column. Application code guarantees the
// target exists in DATASETS[dataset].fields; anything below 0.9 is Review.
export function proposeMappings(dataset, columns, rows = []) {
  const fields = DATASETS[dataset]?.fields ?? {};
  const used = new Set();
  return columns.map((column) => {
    const head = norm(column);
    let target = null;
    let confidence = 0;
    for (const [key, def] of Object.entries(fields)) {
      if (used.has(key)) continue;
      const idx = def.aliases.indexOf(head);
      if (idx === 0 || head === norm(def.label)) {
        target = key;
        confidence = 0.99;
        break;
      }
      if (idx > 0 && confidence < 0.97) {
        target = key;
        confidence = 0.97;
      }
      if (idx < 0 && def.aliases.some((a) => head.includes(a) || a.includes(head)) && confidence < 0.72) {
        target = key;
        confidence = 0.72;
      }
    }
    if (!target && /^acct|^acc|account/.test(head)) confidence = 0.51;
    if (target) used.add(target);
    const example = rows.find((r) => r[column])?.[column] ?? "";
    return { source: column, example, target, confidence, status: target && confidence >= 0.9 ? "Ready" : "Review" };
  });
}

// ---- Normalisation (deterministic) --------------------------------------
const STATES = {
  alabama: "AL", alaska: "AK", arizona: "AZ", arkansas: "AR", california: "CA", colorado: "CO", connecticut: "CT", delaware: "DE", florida: "FL", georgia: "GA", hawaii: "HI", idaho: "ID", illinois: "IL", indiana: "IN", iowa: "IA", kansas: "KS", kentucky: "KY", louisiana: "LA", maine: "ME", maryland: "MD", massachusetts: "MA", mass: "MA", michigan: "MI", minnesota: "MN", mississippi: "MS", missouri: "MO", montana: "MT", nebraska: "NE", nevada: "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", ohio: "OH", oklahoma: "OK", oregon: "OR", pennsylvania: "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", tennessee: "TN", texas: "TX", utah: "UT", vermont: "VT", virginia: "VA", washington: "WA", "west virginia": "WV", wisconsin: "WI", wyoming: "WY",
};
export function normalizeState(value) {
  const v = String(value ?? "").trim().replace(/\.$/, "");
  if (/^[A-Za-z]{2}$/.test(v)) return v.toUpperCase();
  return STATES[v.toLowerCase()] ?? v;
}
export function normalizeCityState(value) {
  const v = String(value ?? "").replace(/\s+/g, " ").trim();
  const m = v.match(/^(.*?)[,\s]+([A-Za-z.]{2,14})$/);
  if (!m) return { city: v, state: "" };
  const state = normalizeState(m[2]);
  if (!/^[A-Z]{2}$/.test(state)) return { city: v, state: "" };
  return { city: m[1].trim().replace(/,$/, ""), state };
}
export function normalizePhone(value) {
  const digits = String(value ?? "").replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
  if (digits.length !== 10) return String(value ?? "").trim();
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}
export function normalizeDate(value) {
  const v = String(value ?? "").trim();
  if (!v) return "";
  if (/^\d{4}-\d{2}-\d{2}/.test(v)) return v.slice(0, 10);
  const us = v.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$/);
  if (us) {
    const year = us[3].length === 2 ? `20${us[3]}` : us[3];
    return `${year}-${us[1].padStart(2, "0")}-${us[2].padStart(2, "0")}`;
  }
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toISOString().slice(0, 10);
}
export function normalizeMoney(value) {
  const v = String(value ?? "").trim();
  if (!v) return 0;
  const negative = /^\(.*\)$/.test(v) || v.startsWith("-");
  const n = Number(v.replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) ? (negative ? -n : n) : 0;
}
export function normalizeStatus(value) {
  const v = String(value ?? "").trim().toLowerCase();
  if (["y", "yes", "true", "1", "active", "open"].includes(v)) return v === "open" ? "open" : "active";
  if (["n", "no", "false", "0", "inactive", "closed"].includes(v)) return "inactive";
  if (["paid", "pd", "settled"].includes(v)) return "paid";
  if (["overdue", "late", "past due"].includes(v)) return "overdue";
  return v;
}
export function normalizeText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}
export function normalizeName(value) {
  return normalizeText(value)
    .replace(/\b(l\.?l\.?c\.?|inc\.?|co\.?|corp\.?|ltd\.?)\b/gi, (m) => m.replace(/\./g, "").toUpperCase())
    .replace(/\s*,\s*/g, ", ");
}

// Apply mappings to source rows -> canonical records with provenance.
export function transformRows(dataset, rows, mappings, { file, sheet = "Sheet1", job }) {
  const fields = DATASETS[dataset]?.fields ?? {};
  const active = mappings.filter((m) => m.target && fields[m.target]);
  return rows.map((row, i) => {
    const record = {};
    const original = {};
    for (const m of active) {
      const raw = row[m.source];
      original[m.source] = raw;
      let value = normalizeText(raw);
      switch (m.target) {
        case "phone":
          value = normalizePhone(raw);
          break;
        case "state":
          value = normalizeState(raw);
          break;
        case "city": {
          const cs = normalizeCityState(raw);
          value = cs.city;
          if (cs.state && !record.state) record.state = cs.state;
          break;
        }
        case "issue_date":
        case "due_date":
        case "date":
        case "renewal_date":
          value = normalizeDate(raw);
          break;
        case "amount":
        case "outstanding_balance":
        case "unit_price":
        case "total":
        case "monthly_cost":
          value = normalizeMoney(raw);
          break;
        case "quantity":
        case "seats":
          value = Math.round(normalizeMoney(raw));
          break;
        case "status":
          value = normalizeStatus(raw);
          break;
        case "customer_name":
        case "vendor_name":
          value = normalizeName(raw);
          break;
        default:
          break;
      }
      record[m.target] = value;
    }
    const confidence = Math.min(...active.map((m) => m.confidence), 1);
    return { ...record, provenance: { file, sheet, row: i + 2, original, normalized: { ...record }, importJob: job, confidence, review: confidence >= 0.9 ? "auto-accepted" : "reviewed" } };
  });
}

// A short before/after sample for the transformation preview step.
export function previewTransforms(dataset, rows, mappings) {
  const out = [];
  const seen = new Set();
  const sample = transformRows(dataset, rows.slice(0, 60), mappings, { file: "", job: "" });
  for (let i = 0; i < sample.length; i++) {
    for (const m of mappings) {
      if (!m.target) continue;
      const raw = rows[i][m.source];
      const value = sample[i][m.target];
      if (raw === undefined || String(raw) === String(value)) continue;
      const key = `${m.target}:${raw}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ field: m.target, source: raw, normalized: String(value) });
      if (out.length >= 10) return out;
    }
  }
  return out;
}

// ---- Exceptions ------------------------------------------------------------
const nameKey = (s) =>
  normalizeText(s)
    .toLowerCase()
    .replace(/[.,']/g, "")
    .replace(/\b(llc|inc|co|corp|ltd|the|services?|enterprises?|supply|hvac|boston|w w)\b/g, "")
    .replace(/#\d+/g, "")
    .replace(/\s+/g, " ")
    .trim();
function similarity(a, b) {
  const x = nameKey(a);
  const y = nameKey(b);
  if (!x || !y) return 0;
  if (x === y) return 0.93;
  const wa = new Set(x.split(" "));
  const wb = new Set(y.split(" "));
  const inter = [...wa].filter((w) => wb.has(w)).length;
  const union = new Set([...wa, ...wb]).size;
  const j = inter / union;
  return j >= 0.5 ? 0.6 + j * 0.3 : 0;
}
// Possible duplicates inside the upload and possible matches against the
// portfolio vendor master. Nothing is merged without a human decision.
export function detectExceptions(datasets, existingVendors = []) {
  const exceptions = [];
  let n = 1;
  const customers = datasets.customers?.records ?? [];
  for (let i = 0; i < customers.length; i++) {
    for (let j = i + 1; j < customers.length; j++) {
      const s = similarity(customers[i].customer_name, customers[j].customer_name);
      if (s >= 0.85 && customers[i].customer_name !== customers[j].customer_name) {
        exceptions.push({ id: `X-${n++}`, type: "Possible duplicate customer", left: customers[i].customer_name, right: customers[j].customer_name, leftRow: customers[i].provenance.row, rightRow: customers[j].provenance.row, confidence: Number(s.toFixed(2)), actions: ["Merge", "Keep separate", "Skip"], decision: null, dataset: "customers", recordIds: [i, j] });
        if (exceptions.length >= 8) break;
      }
    }
  }
  const vendors = [...new Set((datasets.vendors?.records ?? []).map((r) => r.vendor_name))];
  for (const v of vendors) {
    const best = existingVendors.map((e) => ({ e, s: similarity(v, e.name) })).sort((a, b) => b.s - a.s)[0];
    if (best && best.s >= 0.6 && nameKey(v) !== nameKey(best.e.name) && best.s < 0.93) {
      exceptions.push({ id: `X-${n++}`, type: "Possible vendor match", left: v, right: best.e.name, leftRow: null, rightRow: null, confidence: Number(best.s.toFixed(2)), actions: ["Match", "Keep separate", "Skip"], decision: null, dataset: "vendors", matchVendor: best.e.name });
    } else if (best && nameKey(v) === nameKey(best.e.name) && v !== best.e.name) {
      exceptions.push({ id: `X-${n++}`, type: "Vendor name variant", left: v, right: best.e.name, leftRow: null, rightRow: null, confidence: 0.9, actions: ["Match", "Keep separate", "Skip"], decision: null, dataset: "vendors", matchVendor: best.e.name });
    }
  }
  return exceptions;
}

// ---- Sample files for the demo (Cedar) ---------------------------------------
// Cedar's records come from the same generator as Harbor/Summit, serialised
// with the messy headers and formats a real export would have.
export function cedarSampleFiles() {
  const cedar = generateCompany(COMPANY_SPECS.cedar, "sample");
  const us = (iso) => {
    const [y, m, d] = iso.split("-");
    return `${Number(m)}/${Number(d)}/${y.slice(2)}`;
  };
  const customers = toCsv(
    ["Acct", "Client", "Contact", "Email", "Phone", "Address", "City", "Service", "Active?"],
    cedar.customers.map((c, i) => ({
      Acct: 91000 + i,
      Client: c.name,
      Contact: c.contact,
      Email: c.email,
      Phone: c.phone.replace(/\D/g, ""),
      Address: c.address,
      City: i % 3 === 0 ? "Cambridge Mass." : i % 3 === 1 ? "Boston, MA" : "Somerville MA",
      Service: c.serviceType,
      "Active?": c.status === "active" ? "Y" : "N",
    })),
  );
  const invoices = toCsv(
    ["Inv #", "Client", "Date", "Pay By", "Total", "Amt Due", "Status"],
    cedar.invoices.map((inv) => ({
      "Inv #": inv.number,
      Client: inv.customerName,
      Date: us(inv.issueDate),
      "Pay By": us(inv.dueDate),
      Total: `$${inv.amount.toLocaleString("en-US", { minimumFractionDigits: 2 })}`,
      "Amt Due": inv.outstanding ? `$${inv.outstanding.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "-",
      Status: inv.status === "paid" ? "PD" : inv.status === "overdue" ? "Past due" : "Open",
    })),
  );
  const purchases = toCsv(
    ["Vendor", "Item", "Description", "Qty", "UOM", "Price", "Date", "Ext Price"],
    cedar.purchases.map((p) => ({
      Vendor: cedar.vendors.find((v) => v.id === p.vendorId)?.sourceName ?? p.vendorName,
      Item: p.sku,
      Description: p.description,
      Qty: p.quantity,
      UOM: p.unit,
      Price: p.unitPrice.toFixed(2),
      Date: us(p.date),
      "Ext Price": p.total.toFixed(2),
    })),
  );
  const subs = toCsv(
    ["Software", "Category", "Monthly $", "Users", "Renewal", "Notes"],
    cedar.subscriptions.map((s) => ({ Software: s.product, Category: s.category, "Monthly $": s.monthlyCost, Users: s.seats, Renewal: us(s.renewalDate), Notes: s.notes })),
  );
  return [
    { name: "cedar_customers.csv", type: "text/csv", group: "Customers", text: customers },
    { name: "cedar_open_ar.csv", type: "text/csv", group: "Invoices", text: invoices },
    { name: "cedar_vendor_purchases.csv", type: "text/csv", group: "Vendors / purchases", text: purchases },
    { name: "cedar_software.csv", type: "text/csv", group: "Software subscriptions", text: subs },
  ];
}
export const CEDAR_PROFILE = {
  name: COMPANY_SPECS.cedar.name,
  location: COMPANY_SPECS.cedar.location,
  industry: COMPANY_SPECS.cedar.industry,
  acquired: COMPANY_SPECS.cedar.acquired,
  description: "Residential HVAC service and maintenance-plan business serving greater Boston. Runs Housecall Pro and QuickBooks Online.",
};

// Turn transformed datasets into a canonical company object the store
// understands. `decisions` are the exception decisions made by the analyst.
export function buildCompany({ id, profile, datasets, exceptions, job }) {
  const merged = new Set();
  for (const x of exceptions) {
    if (x.dataset === "customers" && x.decision === "Merge") merged.add(x.recordIds[1]);
  }
  const vendorAlias = {};
  for (const x of exceptions) {
    if (x.dataset === "vendors" && x.decision === "Match") vendorAlias[x.left] = x.matchVendor;
  }
  const customers = (datasets.customers?.records ?? [])
    .map((r, i) => ({ r, i }))
    .filter(({ i }) => !merged.has(i))
    .map(({ r, i }) => ({
      id: `${id}-cust-${String(i + 1).padStart(4, "0")}`,
      companyId: id,
      name: r.customer_name,
      contact: r.contact ?? "",
      email: r.email ?? "",
      phone: r.phone ?? "",
      address: r.address ?? "",
      city: r.city ?? "",
      state: r.state ?? "",
      zip: r.zip ?? "",
      serviceType: r.service_type ?? "",
      status: r.status || "active",
      sourceCustomerId: r.source_customer_id ?? "",
      provenance: r.provenance,
    }));
  const byName = new Map(customers.map((c) => [c.name.toLowerCase(), c.id]));
  const today = new Date("2026-09-19");
  const invoices = (datasets.invoices?.records ?? []).map((r, i) => {
    const outstanding = Number(r.outstanding_balance ?? 0);
    const overdue = outstanding > 0 && r.due_date && new Date(r.due_date) < today;
    return {
      id: `${id}-inv-${String(i + 1).padStart(4, "0")}`,
      companyId: id,
      customerId: byName.get(String(r.customer_name).toLowerCase()) ?? null,
      customerName: r.customer_name,
      number: r.source_invoice_number,
      issueDate: r.issue_date ?? "",
      dueDate: r.due_date ?? "",
      amount: Number(r.amount ?? outstanding),
      outstanding,
      status: outstanding === 0 ? "paid" : overdue ? "overdue" : "open",
      provenance: r.provenance,
    };
  });
  const vendorNames = [...new Set((datasets.vendors?.records ?? []).map((r) => r.vendor_name))];
  const vendors = vendorNames.map((source, i) => ({
    id: `${id}-ven-${i + 1}`,
    companyId: id,
    name: vendorAlias[source] ?? source,
    sourceName: source,
    contact: "",
    provenance: (datasets.vendors?.records ?? []).find((r) => r.vendor_name === source)?.provenance,
  }));
  const vendorId = Object.fromEntries(vendors.map((v) => [v.sourceName, v]));
  const purchases = (datasets.vendors?.records ?? []).map((r, i) => {
    const v = vendorId[r.vendor_name];
    const qty = Number(r.quantity ?? 0);
    const unitPrice = Number(r.unit_price ?? 0);
    return {
      id: `${id}-po-${String(i + 1).padStart(4, "0")}`,
      companyId: id,
      vendorId: v?.id,
      vendorName: v?.name ?? r.vendor_name,
      sku: r.sku ?? "",
      description: r.description ?? "",
      quantity: qty,
      unit: r.unit ?? "",
      unitPrice,
      date: r.date ?? "",
      total: Number(r.total ?? Math.round(qty * unitPrice * 100) / 100),
      provenance: r.provenance,
    };
  });
  const subscriptions = (datasets.subscriptions?.records ?? []).map((r, i) => ({
    id: `${id}-sub-${i + 1}`,
    companyId: id,
    product: r.product,
    category: r.category ?? "",
    monthlyCost: Number(r.monthly_cost ?? 0),
    seats: Number(r.seats ?? 0),
    renewalDate: r.renewal_date ?? "",
    notes: r.notes ?? "",
    provenance: r.provenance,
  }));
  const all = [...customers, ...invoices, ...purchases, ...subscriptions];
  const reviewed = exceptions.filter((x) => x.decision && x.decision !== "Skip").length + all.filter((r) => r.provenance?.review === "reviewed").length;
  return {
    id,
    name: profile.name,
    location: profile.location,
    industry: profile.industry,
    acquired: profile.acquired,
    description: profile.description ?? "",
    syntheticDemo: true,
    analysisRunAt: null,
    importJobs: [
      {
        id: job,
        createdAt: new Date().toISOString(),
        files: Object.values(datasets).map((d) => d.file),
        accepted: all.length - reviewed,
        reviewed,
        rejected: merged.size + exceptions.filter((x) => x.decision === "Skip").length,
      },
    ],
    importExceptions: exceptions.filter((x) => !x.decision).map((x) => ({ ...x, open: true })),
    customers,
    invoices,
    vendors,
    purchases,
    subscriptions,
  };
}
