// Suggested workflows: repeatable pieces of work inferred from what was on the
// screen (apps, documents, sites) and what was done (transfers, switches,
// taskmining activities). Deterministic; no model call. Also the workflow-level
// trends (steps, cases, recurring workflows) that feed the insights summary.
import { shortApp } from './sections.js';

export const WORKFLOWS_FILE = 'workflows.json';

const ROLES = [
  ['email', /outlook|mail|thunderbird|gmail/i],
  ['accounting', /quickbooks|xero|sage|netsuite|odoo.*(account|invoic|bill)|freshbooks|dynamics|sap\b/i],
  ['crm', /salesforce|hubspot|pipedrive|zoho|odoo.*(crm|pipeline|lead)|dynamics 365 sales/i],
  ['spreadsheet', /excel|sheets|numbers|libreoffice calc/i],
  ['pdf', /acrobat|preview|foxit|pdf/i],
  ['password_manager', /1password|bitwarden|keepass|lastpass|keychain|\(private\)/i],
  ['chat', /slack|teams|zoom|webex/i],
  ['browser', /chrome|safari|firefox|edge|brave|arc/i],
  ['documents', /word|docs|pages|notepad|textedit/i],
];

export function appRole(app, title = '') {
  const s = `${app} ${title}`;
  for (const [role, re] of ROLES) if (re.test(s)) return role;
  return 'other';
}

const ts = (e) => Date.parse(e.timestamp);
const r1 = (n) => Math.round(n * 10) / 10;
const pick = (roleOf, role) => [...roleOf.entries()].filter(([, r]) => r === role).map(([a]) => a);

function domain(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

// What the session ran in: apps by role, documents by type, web sites.
export function inferEnvironment({ manifest = {}, events = [], files = [] }) {
  const roleOf = new Map();
  const titles = new Map();
  for (const e of events) {
    if (!e.app) continue;
    if (!titles.has(e.app)) titles.set(e.app, new Set());
    if (e.window_title) titles.get(e.app).add(e.window_title);
  }
  const apps = (manifest.apps ?? []).map((a) => {
    const role = appRole(a.app, [...(titles.get(a.app) ?? [])].join(' '));
    roleOf.set(a.app, role);
    return { app: a.app, short: shortApp(a.app), role, seconds: a.seconds ?? 0 };
  });
  for (const app of titles.keys()) if (!roleOf.has(app)) roleOf.set(app, appRole(app, [...titles.get(app)].join(' ')));
  const sites = new Map();
  for (const e of events) {
    const d = e.url ? domain(e.url) : '';
    if (d) sites.set(d, (sites.get(d) ?? 0) + 1);
  }
  const docTitles = new Set();
  for (const set of titles.values()) for (const t of set) {
    const m = /([^\\/:*?"<>|]+\.(pdf|xlsx?|csv|docx?|pptx?|txt|json))\b/i.exec(t);
    if (m) docTitles.add(m[1]);
  }
  const documents = files.map((f) => ({ name: f.name, ext: (f.ext ?? '').toLowerCase(), app: f.intervals?.[0]?.app ?? null, edited: !!f.edited, parsed: f.review?.status === 'parsed', flags: f.review?.flags?.length ?? 0 }));
  for (const t of docTitles) if (!documents.some((d) => d.name === t)) documents.push({ name: t, ext: t.slice(t.lastIndexOf('.')).toLowerCase(), app: null, edited: false, parsed: false, flags: 0, from_title: true });
  return {
    apps,
    roles: [...new Set(roleOf.values())].filter((r) => r !== 'other'),
    documents,
    sites: [...sites.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([host, n]) => ({ host, n })),
    _roleOf: roleOf,
  };
}

function orderedSwitches(events) {
  const focus = events.filter((e) => e.event_type === 'focus' && e.app).sort((a, b) => ts(a) - ts(b));
  const pairs = new Map();
  for (let i = 1; i < focus.length; i++) {
    if (focus[i].app === focus[i - 1].app) continue;
    const k = `${focus[i - 1].app}→${focus[i].app}`;
    pairs.set(k, (pairs.get(k) ?? 0) + 1);
  }
  return { focus, pairs };
}

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

// Every workflow: { id, title, kind, apps, steps, evidence, automation, sources, why }.
export function suggestWorkflows({ manifest = {}, events = [], files = [], summary = null }) {
  const env = inferEnvironment({ manifest, events, files });
  const roleOf = env._roleOf;
  const out = [];
  const add = (w) => {
    if (out.some((x) => x.id === w.id)) return;
    out.push({ ...w, id: w.id ?? slug(w.title), automation: Math.min(1, Math.max(0, r1(w.automation ?? 0.5))) });
  };
  const roleName = (app) => ({ email: 'email', accounting: 'the accounting system', crm: 'the CRM', spreadsheet: 'the spreadsheet', pdf: 'the PDF', browser: 'the browser', chat: 'chat', documents: 'the document', password_manager: 'the password manager' }[roleOf.get(app)] ?? shortApp(app));

  // 1. Re-keying between systems (events: cross-app pastes).
  const transfers = new Map();
  for (const e of events) {
    if (e.event_type !== 'paste' || !e.payload?.source_app || e.payload.source_app === e.app) continue;
    const k = `${e.payload.source_app}→${e.app}`;
    const t = transfers.get(k) ?? { from: e.payload.source_app, to: e.app, n: 0, chars: 0, ms: 0, docs: new Set() };
    t.n += 1; t.chars += e.payload.chars ?? 0; t.ms += e.payload.transfer_ms ?? 0;
    if (e.payload.source_title) t.docs.add(e.payload.source_title);
    transfers.set(k, t);
  }
  for (const t of [...transfers.values()].sort((a, b) => b.n - a.n)) {
    const toRole = roleOf.get(t.to);
    add({
      id: `rekey-${slug(t.from)}-${slug(t.to)}`,
      title: `Re-key ${shortApp(t.from)} data into ${shortApp(t.to)}`,
      kind: 'data_transfer',
      apps: [t.from, t.to],
      steps: [`Open the source in ${shortApp(t.from)}`, 'Copy one field at a time', `Switch to ${shortApp(t.to)} and paste it`, `Repeat for each field (${t.n} paste${t.n === 1 ? '' : 's'} this session)`],
      evidence: { pastes: t.n, chars: t.chars, mean_transfer_s: r1(t.ms / t.n / 1000), sources: [...t.docs].slice(0, 3) },
      automation: toRole === 'accounting' || toRole === 'spreadsheet' || toRole === 'crm' ? 0.85 : 0.6,
      sources: ['events'],
      why: `${t.n} value${t.n === 1 ? ' was' : 's were'} copied from ${roleName(t.from)} and pasted into ${roleName(t.to)}; a document extractor or import could fill these fields directly.`,
    });
  }
  // 1b. Switching back and forth between two recognised systems without a
  // recorded paste still looks like carrying data across by hand.
  const { pairs: switchPairs } = orderedSwitches(events);
  for (const [k, n] of [...switchPairs.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4)) {
    const [from, to] = k.split('→');
    if ((roleOf.get(from) ?? 'other') === 'other' || (roleOf.get(to) ?? 'other') === 'other') continue;
    if (out.some((w) => w.kind === 'data_transfer' && w.apps.includes(from) && w.apps.includes(to))) continue;
    add({
      id: `carry-${slug(from)}-${slug(to)}`,
      title: `Carry ${shortApp(from)} data over to ${shortApp(to)}`,
      kind: 'data_transfer',
      apps: [from, to],
      steps: [`Read the value in ${shortApp(from)}`, `Switch to ${shortApp(to)}`, 'Type it in', 'Switch back for the next one'],
      evidence: { switches: n },
      automation: 0.5,
      sources: ['events'],
      why: `${n} switch${n === 1 ? '' : 'es'} from ${roleName(from)} to ${roleName(to)} with no paste in between suggests values re-typed by hand.`,
    });
  }

  // 2. Document intake (documents + focus titles): PDFs read next to an accounting/CRM system.
  const pdfs = env.documents.filter((d) => d.ext === '.pdf');
  const systems = pick(roleOf, 'accounting').concat(pick(roleOf, 'crm'));
  if (pdfs.length) {
    const target = systems[0] ?? pick(roleOf, 'spreadsheet')[0] ?? null;
    const invoice = /invoice|inv-|bill/i.test(pdfs.map((d) => d.name).join(' '));
    add({
      id: target ? `intake-${slug(target)}` : 'intake-pdf',
      title: target ? `${invoice ? 'Invoice' : 'Document'} intake into ${shortApp(target)}` : `Read ${invoice ? 'invoice' : 'document'} PDFs`,
      kind: 'document_processing',
      apps: [...new Set([...pdfs.map((d) => d.app).filter(Boolean), ...pick(roleOf, 'pdf'), ...(target ? [target] : [])])],
      steps: ['Open the PDF', 'Read vendor, amount and reference', ...(target ? [`Enter it in ${shortApp(target)}`] : ['Act on it']), 'File or close the PDF'],
      evidence: { documents: pdfs.map((d) => d.name).slice(0, 5), count: pdfs.length },
      automation: target ? 0.8 : 0.6,
      sources: ['documents', 'events'],
      why: `${pdfs.length} PDF${pdfs.length === 1 ? ' was' : 's were'} open${target ? ` while ${roleName(target)} was used` : ''}; OCR plus a posting rule would remove the manual read-and-type step.`,
    });
  }

  // 3. Tracker upkeep (documents): spreadsheets edited during the session.
  const sheets = env.documents.filter((d) => ['.xlsx', '.xls', '.csv'].includes(d.ext));
  for (const d of sheets.slice(0, 3)) {
    add({
      id: `tracker-${slug(d.name)}`,
      title: `Update the “${d.name}” tracker`,
      kind: 'reporting',
      apps: [d.app].filter(Boolean),
      steps: ['Open the tracker', 'Add or update the row for each item handled', 'Save'],
      evidence: { document: d.name, edited: d.edited, flags: d.flags },
      automation: 0.7,
      sources: ['documents'],
      why: `${d.name} was ${d.edited ? 'edited' : 'open'} during the session; the same rows exist in the systems the data came from and could be exported automatically.`,
    });
  }
  // 3b. Any other document (Word, text, slides) handled during the session.
  const others = env.documents.filter((d) => d.ext !== '.pdf' && !['.xlsx', '.xls', '.csv'].includes(d.ext));
  for (const d of others.slice(0, 3)) {
    add({
      id: `document-${slug(d.name)}`,
      title: `${d.edited ? 'Update' : 'Work from'} the “${d.name}” document`,
      kind: 'document_processing',
      apps: [d.app].filter(Boolean),
      steps: ['Open the document', d.edited ? 'Make the changes' : 'Read what is needed', d.edited ? 'Save and share' : 'Carry it into the next step'],
      evidence: { document: d.name, edited: d.edited, flags: d.flags },
      automation: d.edited ? 0.5 : 0.4,
      sources: ['documents'],
      why: `${d.name} was ${d.edited ? 'edited' : 'open'} during the session; documents handled every time are a candidate for a template or extractor.`,
    });
  }

  // 4. Inbox handling (taskmining activities + email focus).
  const acts = summary?.top_activities ?? [];
  const emailApps = pick(roleOf, 'email');
  const emailActs = acts.filter((a) => /email|mail|reply/i.test(a.activity));
  if (emailApps.length) {
    const lookups = [...switchPairs.entries()].filter(([k]) => emailApps.some((a) => k.startsWith(`${a}→`))).map(([k, n]) => ({ to: k.split('→')[1], n })).sort((a, b) => b.n - a.n);
    add({
      id: 'inbox-triage',
      title: `Answer ${shortApp(emailApps[0])} requests with lookups in ${lookups[0] ? shortApp(lookups[0].to) : 'other systems'}`,
      kind: 'communication',
      apps: [emailApps[0], ...lookups.slice(0, 2).map((l) => l.to)],
      steps: ['Read the request', ...(lookups[0] ? [`Look it up in ${shortApp(lookups[0].to)}`] : []), 'Write the reply', 'Move on to the next message'],
      evidence: { activities: emailActs.map((a) => ({ activity: a.activity, count: a.count, total_s: a.total_s })), switches: lookups.slice(0, 3) },
      automation: 0.5,
      sources: ['taskmining', 'events'],
      why: emailActs.length ? `The task-mining pass found ${emailActs.map((a) => a.activity.toLowerCase()).join(' and ')}${lookups[0] ? `, each followed by a switch to ${roleName(lookups[0].to)}` : ''}; a drafted reply with the looked-up figures would save the round trip.` : 'Email was used alongside other systems; replies that need figures from those systems can be pre-drafted.',
    });
  }

  // 5. Web lookups (events: URLs / browser focus).
  const browsers = pick(roleOf, 'browser');
  if (browsers.length) {
    add({
      id: 'web-lookup',
      title: env.sites[0] ? `Look up ${env.sites[0].host} during the work` : `Look things up in ${shortApp(browsers[0])} mid-task`,
      kind: 'lookup',
      apps: [browsers[0]],
      steps: ['Switch to the browser', env.sites[0] ? `Open ${env.sites[0].host}` : 'Search or open a bookmarked site', 'Find the value', 'Switch back and continue'],
      evidence: { sites: env.sites.slice(0, 5) },
      automation: 0.45,
      sources: ['events'],
      why: 'A browser lookup in the middle of another task usually means a value (rate, status, address) that could be fetched into the main system.',
    });
  }

  // 6. Sign-in step (events: password manager / private window).
  const pw = pick(roleOf, 'password_manager');
  if (pw.length) {
    add({
      id: 'sign-in',
      title: 'Sign in through the password manager',
      kind: 'access',
      apps: pw,
      steps: ['Open the password manager', 'Find the credential', 'Return and sign in'],
      evidence: { seconds: (manifest.apps ?? []).filter((a) => pw.includes(a.app)).reduce((s, a) => s + (a.seconds ?? 0), 0) },
      automation: 0.4,
      sources: ['events'],
      why: 'Time in a password manager mid-session points at a system without single sign-on.',
    });
  }

  // 7. Remaining taskmining automation candidates not covered above.
  for (const a of (summary?.automation ?? []).filter((a) => a.activity)) {
    if (out.some((w) => w.apps.some((app) => a.activity.includes(shortApp(app))) || w.title.toLowerCase().includes(a.activity.toLowerCase()))) continue;
    add({
      id: `activity-${slug(a.activity)}`,
      title: `Automate “${a.activity}”`,
      kind: 'activity',
      apps: (manifest.apps ?? []).filter((x) => a.activity.includes(shortApp(x.app))).map((x) => x.app),
      steps: (summary?.top_variant?.activities ?? []).filter((x) => x === a.activity).length ? ['Part of the main case path'] : [],
      evidence: { score: a.score, hours_total: a.hours_total },
      automation: a.score ?? 0.3,
      sources: ['taskmining'],
      why: `Task mining scored this activity ${Math.round((a.score ?? 0.3) * 100)}% automatable from its repetition and structure.`,
    });
  }

  // 8. Nothing matched: every app that was actually used is still a piece of
  // work worth naming, so the card is never empty for a real session.
  if (!out.length) {
    for (const a of [...(manifest.apps ?? [])].sort((x, y) => (y.seconds ?? 0) - (x.seconds ?? 0)).slice(0, 3)) {
      add({
        id: `work-${slug(a.app)}`,
        title: `Work in ${shortApp(a.app)}`,
        kind: 'activity',
        apps: [a.app],
        steps: [`Open ${shortApp(a.app)}`, 'Do the task', 'Move on'],
        evidence: { seconds: a.seconds ?? 0 },
        automation: 0.3,
        sources: ['events'],
        why: `${shortApp(a.app)} took ${Math.round((a.seconds ?? 0) / 60)} min this session; time spent in one place is where a repeatable step usually hides.`,
      });
    }
  }

  out.sort((a, b) => b.automation - a.automation);
  const { _roleOf, ...environment } = env;
  return { version: 1, generated_at: new Date().toISOString(), environment, workflows: out.slice(0, 12) };
}

// Compact form kept on the manifest so later sessions can compare.
export function workflowsStub(wf) {
  return { count: wf.workflows.length, ids: wf.workflows.map((w) => w.id), kinds: [...new Set(wf.workflows.map((w) => w.kind))] };
}

// Workflow-level comparison with earlier recordings (their manifest summary +
// workflows stub). Also marks which suggested workflows recur.
export function workflowTrends({ summary = null, workflows = null }, previous = []) {
  const prev = previous.filter((m) => m?.summary && m.active_seconds > 0);
  const recurring = (workflows?.workflows ?? []).map((w) => ({ id: w.id, title: w.title, seen_before: previous.filter((m) => m?.workflows?.ids?.includes(w.id)).length }));
  if (!prev.length) return { baseline: 0, metrics: [], recurring };
  const avg = (f) => prev.reduce((s, m) => s + (f(m) || 0), 0) / prev.length;
  const mk = (key, label, now, base) => ({ key, label, now: r1(now), baseline: r1(base), delta_pct: base ? Math.round(((now - base) / base) * 100) : null });
  const stepsPerCase = (s) => (s?.cases ? (s.steps ?? 0) / s.cases : s?.steps ?? 0);
  return {
    baseline: prev.length,
    metrics: [
      mk('steps_per_case', 'Steps per case', stepsPerCase(summary), avg((m) => stepsPerCase(m.summary))),
      mk('cases', 'Cases followed', summary?.cases ?? 0, avg((m) => m.summary.cases)),
      mk('open_questions', 'Open questions', summary?.open_questions ?? 0, avg((m) => m.summary.open_questions)),
      mk('workflows', 'Workflows suggested', workflows?.workflows?.length ?? 0, avg((m) => m.workflows?.count)),
    ],
    recurring,
  };
}
