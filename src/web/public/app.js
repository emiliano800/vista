const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => new Date(value).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
const duration = seconds => seconds < 60 ? `${Math.round(seconds)} sec` : `${(seconds / 60).toFixed(1)} min`;
let reportOffset = 0, evidenceOffset = 0, current = null, requestGeneration = 0;
function message(text = '') { $('message').textContent = text; }
function view(name) { for (const id of ['login','workspace','detail']) $(id).hidden = id !== name; }
async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {credentials:'same-origin', cache:'no-store', ...options, headers:{'Content-Type':'application/json','X-Vista-Request':'1',...options.headers}});
  if (!response.ok) {
    let body; try { body = await response.json(); } catch { /* upstream unavailable */ }
    if (response.status === 401) { ++requestGeneration; $('signout').hidden = true; view('login'); }
    throw new Error(typeof body?.detail === 'string' ? body.detail : response.status === 422 ? 'The request could not be accepted. Check your input.' : 'The workspace is unavailable. Please try again.');
  }
  return response.status === 204 ? null : response.json();
}
function handle(fn) { return async event => { try { message(); await fn(event); } catch (error) { message(error.message); } }; }
async function enter() {
  const me = await api('/auth/me');
  $('identity').textContent = me.email; $('signout').hidden = false;
  const companies = await api('/deals');
  $('company').replaceChildren(...companies.map(c => new Option(c.name, c.id)));
  view('workspace'); reportOffset = 0;
  await reports();
}
async function reports() {
  const generation = ++requestGeneration;
  const company = $('company').value;
  $('company-id').textContent = company || 'No company assigned';
  $('copy-company').disabled = !company;
  $('prev-reports').disabled = true; $('next-reports').disabled = true;
  $('reports').innerHTML = '<p class="empty">Loading reports…</p>';
  const rows = company ? await api(`/deals/${company}/recordings?offset=${reportOffset}&limit=20`) : [];
  if (generation !== requestGeneration) return;
  $('reports').innerHTML = rows.length ? rows.map(r => `<article class="report"><div><h2>${esc(date(r.started_at))}</h2><small>${esc(duration(r.active_seconds))} active · ${r.summary.n_steps} steps · ${r.summary.n_cases} cases</small></div><button data-report="${esc(r.id)}">View report →</button></article>`).join('') : `<div class="empty">${company ? 'No uploaded reports yet. Connect the desktop recorder above to share your first session.' : 'No companies are assigned to your account. Ask your administrator for access.'}</div>`;
  $('report-page').textContent = rows.length ? `Reports ${reportOffset + 1}–${reportOffset + rows.length}` : '';
  $('prev-reports').disabled = reportOffset === 0; $('next-reports').disabled = rows.length < 20;
  $('reports').querySelectorAll('[data-report]').forEach(button => button.onclick = handle(() => detail(button.dataset.report)));
}
async function detail(id) {
  const generation = ++requestGeneration;
  const r = await api(`/recordings/${id}`);
  if (generation !== requestGeneration) return;
  current = r;
  $('detail-title').textContent = date(r.started_at);
  $('detail-sub').textContent = `Analyzed on device · Uploaded ${date(r.uploaded_at)} · ${r.source_id}`;
  const s = r.summary;
  $('metrics').innerHTML = [['Active time',duration(r.active_seconds)],['Source steps',s.n_steps],['Cases',s.n_cases],['Uncorrelated steps',s.n_uncorrelated_steps]].map(([label,value]) => `<div class="metric"><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('');
  $('download-json').href = `/api/recordings/${id}/download`;
  $('download-csv').href = `/api/recordings/${id}/download?format=csv`;
  $('activity').replaceChildren(new Option('All activities',''), ...s.activities.map(a => new Option(a.activity,a.activity)));
  $('candidates').innerHTML = s.automation_potential.length ? s.automation_potential.map((a,i) => `<div class="candidate"><div><strong>${esc(a.activity)}</strong><small>${Math.round(a.score*100)}% candidate score · ${a.hours_total.toFixed(2)} observed hours</small></div><button data-candidate="${i}">View evidence</button></div>`).join('') : '<p class="muted">No automation candidates in this session.</p>';
  $('candidates').querySelectorAll('[data-candidate]').forEach(button => button.onclick = handle(async () => {
    const activity = s.automation_potential[Number(button.dataset.candidate)].activity;
    if (![...$('activity').options].some(o => o.value === activity)) $('activity').add(new Option(activity,activity));
    $('activity').value = activity; evidenceOffset = 0; await evidence(); $('activity').scrollIntoView({behavior:'smooth'});
  }));
  view('detail'); evidenceOffset = 0; await evidence();
}
async function evidence() {
  const generation = ++requestGeneration;
  $('evidence').innerHTML = '<tr><td colspan="6">Loading evidence…</td></tr>';
  $('prev-evidence').disabled = true; $('next-evidence').disabled = true;
  const query = new URLSearchParams({offset:evidenceOffset,limit:50});
  if ($('activity').value) query.set('activity',$('activity').value);
  const result = await api(`/recordings/${current.id}/evidence?${query}`);
  if (generation !== requestGeneration) return;
  $('evidence').innerHTML = result.rows.length ? result.rows.map(r => `<tr><td>#${r.row}</td><td>${esc(r.activity)}${r.note ? `<small>${esc(r.note)}</small>` : ''}</td><td>${esc(date(r.start))}<small>${esc(duration(Number(r.duration_s)))}</small></td><td>${esc(r.app)}</td><td>${esc(r.case_id || 'Unassigned')}</td><td>${esc(r.activity_source)}<small>${esc(r.case_source || 'Unassigned')}</small></td></tr>`).join('') : '<tr><td colspan="6">No evidence rows for this selection.</td></tr>';
  $('evidence-page').textContent = `${result.rows.length ? evidenceOffset + 1 : 0}–${evidenceOffset + result.rows.length} of ${result.total} steps`;
  $('prev-evidence').disabled = evidenceOffset === 0; $('next-evidence').disabled = evidenceOffset + 50 >= result.total;
}
$('login-form').onsubmit = handle(async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  const token = $('key').value.trim(); $('key').value = '';
  try { await api('/auth/session',{method:'POST',body:JSON.stringify({token})}); await enter(); }
  finally { button.disabled = false; }
});
$('signout').onclick = handle(async () => { await api('/auth/session',{method:'DELETE'}); ++requestGeneration; current = null; $('reports').replaceChildren(); $('evidence').replaceChildren(); $('signout').hidden = true; view('login'); });
$('company').onchange = handle(async () => { reportOffset = 0; await reports(); });
$('copy-company').onclick = handle(async () => { await navigator.clipboard.writeText($('company').value); message('Company ID copied.'); });
$('back').onclick = handle(async () => { view('workspace'); await reports(); });
$('prev-reports').onclick = handle(async () => { reportOffset = Math.max(0,reportOffset-20); await reports(); });
$('next-reports').onclick = handle(async () => { reportOffset += 20; await reports(); });
$('activity').onchange = handle(async () => { evidenceOffset = 0; await evidence(); });
$('prev-evidence').onclick = handle(async () => { evidenceOffset = Math.max(0,evidenceOffset-50); await evidence(); });
$('next-evidence').onclick = handle(async () => { evidenceOffset += 50; await evidence(); });
try { await enter(); } catch (error) { view('login'); if (error.message !== 'Sign in to continue') message(error.message); }
