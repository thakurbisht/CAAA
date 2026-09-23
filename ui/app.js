/* Access assurance console.
 *
 * No framework. The console renders account names and reviewer notes, both of
 * which can contain anything, so every interpolated value goes through esc()
 * and the page loads no inline script — a name containing markup cannot
 * execute.
 *
 * The adjudication queue is built to be worked with the keyboard. Fifty-five
 * accounts reached by mouse is a chore; j/k to move and 1/2/3 to decide is a
 * few minutes. That is the difference between a queue that gets cleared and
 * one that does not.
 */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const api = {
  async get(p) { const r = await fetch(p); return r.json(); },
  async post(p, b) {
    const r = await fetch(p, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
    return {ok: r.ok, data: await r.json()};
  }
};

let VERDICTS = {finding: {}, link: {}};
let findings = [], queue = [], coverage = [];
let fFilter = 'open', qFilter = 'open';
let qIndex = 0, qVisible = [];
let pending = null, chosenVerdict = null;

// ---------------------------------------------------------------- overview

function tile(v, label, cls = '') {
  return `<div class="tile ${cls}"><span class="v">${v}</span>
          <span class="l">${esc(label)}</span></div>`;
}

async function renderOverview() {
  const s = await api.get('/api/summary');
  $('#rail-estate').textContent = s.estate.split(/[\\/]/).slice(-2).join('/');

  const sev = s.open_by_severity || {};
  $('#tiles').innerHTML =
    tile(sev.CRITICAL || 0, 'critical, undecided', 'crit') +
    tile(s.findings_open, `open of ${s.findings_total} findings`) +
    tile(s.queue_open, 'accounts awaiting adjudication') +
    tile(s.findings_decided, 'decisions recorded',
         s.findings_decided ? 'good' : '');

  $('#n-findings').textContent = s.findings_open;
  $('#n-queue').textContent = s.queue_open;

  const scored = $('#scored-state');
  scored.querySelector('span').textContent = s.scored ? 'scored estate'
                                                      : 'no answer key';
  const run = s.run || {};
  $('#run-meta').textContent = run.snapshot
    ? `snapshot ${run.snapshot} · ${run.snapshots} extracts`
    : '';

  let alerts = '';
  if (!s.scored) {
    alerts += `<div class="alert info"><span><b>Precision cannot be measured
      on this estate.</b> There is no answer key, so coverage is reported
      instead: what each rule examined, and what it could not.</span></div>`;
  }
  if (s.expiring_soon && s.expiring_soon.length) {
    const list = s.expiring_soon.map(d =>
      `${esc(d.subject || d.item_id)} (${d.days}d)`).join(', ');
    alerts += `<div class="alert"><span><b>Accepted risks returning for
      review.</b> ${list}</span></div>`;
  }
  $('#alerts').innerHTML = alerts;

  coverage = await api.get('/api/coverage');
  $('#coverage').innerHTML = coverage.map(r => {
    const pct = r.total ? r.examined / r.total : 0;
    const excl = Object.entries(r.excluded || {}).filter(([, n]) => n)
      .map(([k, n]) => `<span class="x"><em>${n}</em>&nbsp;${esc(
        k.replace(/_/g, ' '))}</span>`).join('');
    return `<div class="cov">
      <div class="cov-head">
        <span class="nm">${esc(r.name)}</span>
        <span class="rt"><em>${(pct * 100).toFixed(1)}%</em> ·
          ${r.findings} findings</span>
      </div>
      <div class="track"><i data-w="${(pct * 100).toFixed(2)}"></i></div>
      <p class="cov-legend"><span><em>${r.examined.toLocaleString()}</em>
        of ${r.total.toLocaleString()} examined</span>${excl}</p>
    </div>`;
  }).join('');

  // Grow the bars to their measured width on the next frame, so the motion
  // reads as the measurement arriving rather than as decoration.
  requestAnimationFrame(() => $$('#coverage .track i')
    .forEach(el => el.style.width = el.dataset.w + '%'));
}

// ---------------------------------------------------------------- findings

function findingVisible(f) {
  const q = $('#fsearch').value.trim().toLowerCase();
  if (q && !`${f.subject} ${f.rule_id} ${f.summary}`.toLowerCase().includes(q))
    return false;
  if (fFilter === 'open') return !f.decision;
  if (fFilter === 'all') return true;
  if (fFilter === 'PEER_DISTRIBUTION') return f.basis === 'PEER_DISTRIBUTION';
  return f.severity === fFilter;
}

function renderFindings(flashId) {
  const rows = findings.filter(findingVisible);
  $('#findings-empty').hidden = rows.length > 0;

  $('#findings-body').innerHTML = rows.map(f => {
    const d = f.decision;
    const chips =
      (f.basis === 'PEER_DISTRIBUTION' ? '<span class="chip stat">statistical</span>' : '') +
      (f.platform_visibility === 'no' ? '<span class="chip blind">platform blind</span>' : '');
    const verdict = d
      ? `<span class="verdict ${esc(d.verdict)}"><b>${esc(
          d.verdict.replace(/_/g, ' '))}</b>
         <small>${esc(d.by)}${d.expires_on ? ' · to ' + esc(d.expires_on) : ''}</small></span>`
      : `<button class="row-act" data-decide="${esc(f.id)}"
           data-kind="finding">Decide</button>`;
    return `<tr class="${d ? 'decided' : ''}${f.id === flashId ? ' flash' : ''}"
              data-row="${esc(f.id)}">
      <td><span class="sev ${esc(f.severity)}">${esc(
        f.severity[0] + f.severity.slice(1).toLowerCase())}</span></td>
      <td><span class="acct">${esc(f.subject)}</span></td>
      <td>${esc(f.summary)}${chips}</td>
      <td><span class="rule-tag">${esc(f.rule_id.split('_')[0])}</span></td>
      <td>${verdict}</td>
    </tr>`;
  }).join('');

  $('#findings-count').textContent =
    `${rows.length} shown · ${findings.filter(f => !f.decision).length} open ·
     ${findings.length} total`;
}

// ------------------------------------------------------------ adjudication

function renderQueue(keepIndex) {
  qVisible = queue.filter(q => qFilter === 'all' || !q.decision);
  if (!keepIndex) qIndex = 0;
  qIndex = Math.max(0, Math.min(qIndex, qVisible.length - 1));

  $('#queue-list').innerHTML = qVisible.map((q, i) => `
    <div class="qrow ${q.decision ? 'done' : ''}" role="option"
         aria-selected="${i === qIndex}" data-i="${i}">
      ${q.decision ? `<span class="badge">${esc(q.decision.verdict)}</span>` : ''}
      <span class="nm">${esc(q.ad_sam || q.cluster_id)}</span>
      <span class="sub">${esc(q.why)}</span>
    </div>`).join('');

  $('#queue-count').textContent =
    `${qVisible.length} shown · ${queue.filter(q => !q.decision).length} open ·
     ${queue.length} total`;
  renderQueueDetail();

  const sel = $('.qrow[aria-selected="true"]');
  if (sel) sel.scrollIntoView({block: 'nearest'});
}

function renderQueueDetail() {
  const q = qVisible[qIndex];
  const el = $('#queue-detail');
  if (!q) { el.innerHTML = '<p class="none">Nothing selected.</p>'; return; }

  const p = q.candidate_detail || {};
  const conf = Math.round(q.confidence * 100);
  const name = [p.first_name, p.last_name].filter(Boolean).join(' ');

  el.innerHTML = `
    <h2>${esc(q.ad_sam || q.cluster_id)}</h2>
    <p class="why">${esc(q.why)}</p>

    <dl class="facts">
      <div><dt>Account type</dt><dd>${esc(q.account_type)}</dd></div>
      <div><dt>Correlation confidence</dt>
        <dd class="meter"><span class="m"><i data-w="${conf}"></i></span>
          <span>${q.confidence.toFixed(2)}</span></dd></div>
      <div><dt>Status</dt><dd>${esc(q.status)}</dd></div>
      <div><dt>Entra</dt><dd class="mono">${esc(q.entra_upn || '—')}</dd></div>
    </dl>

    <dl class="facts">
      <div><dt>Candidate person</dt>
        <dd class="mono">${esc(q.candidate || 'none above threshold')}</dd></div>
      <div><dt>Name</dt><dd>${esc(name || '—')}</dd></div>
      <div><dt>Department</dt><dd>${esc(p.department || '—')}</dd></div>
      <div><dt>HR status</dt><dd>${esc(p.assignment_status || '—')}</dd></div>
      <div><dt>Job code</dt><dd>${esc(p.job_code || '—')}</dd></div>
      <div><dt>Termination</dt><dd>${esc(p.termination_date || '—')}</dd></div>
    </dl>

    ${q.decision ? `<div class="alert"><span><b>${esc(
        q.decision.verdict)}</b> — ${esc(q.decision.by)}${
        q.decision.note ? ': ' + esc(q.decision.note) : ''}</span></div>` : ''}

    <button class="primary" data-decide="${esc(q.id)}" data-kind="link">
      ${q.decision ? 'Change decision' : 'Record decision'}</button>`;

  requestAnimationFrame(() => $$('.meter i', el)
    .forEach(i => i.style.width = i.dataset.w + '%'));
}

function moveQueue(delta) {
  if (!qVisible.length) return;
  qIndex = Math.max(0, Math.min(qIndex + delta, qVisible.length - 1));
  $$('.qrow').forEach((r, i) =>
    r.setAttribute('aria-selected', String(i === qIndex)));
  renderQueueDetail();
  const sel = $('.qrow[aria-selected="true"]');
  if (sel) sel.scrollIntoView({block: 'nearest'});
}

// Deciding straight from the keyboard still records a name and a reason: the
// drawer opens pre-set rather than the decision being written silently, since
// a decision with nobody against it is not evidence.
function quickDecide(verdict) {
  const q = qVisible[qIndex];
  if (!q) return;
  openDrawer(q.id, 'link', q.ad_sam, '', verdict);
}

// ------------------------------------------------------------------ drawer

async function openDrawer(id, kind, subject, ruleId, preset) {
  pending = {item_id: id, item_kind: kind, subject: subject || '',
             rule_id: ruleId || ''};
  const set = kind === 'finding' ? VERDICTS.finding : VERDICTS.link;
  const keys = Object.keys(set);

  $('#d-title').textContent = kind === 'finding'
    ? 'Record a decision' : 'Does this account belong to that person?';
  $('#d-subject').textContent = subject || id;
  $('#d-choices').innerHTML = keys.map((k, i) =>
    `<button data-v="${esc(k)}" aria-pressed="false">${esc(
      k.replace(/_/g, ' '))}<kbd>${i + 1}</kbd></button>`).join('');
  $('#d-by').value = localStorage.getItem('caaa-reviewer') || '';
  $('#d-note').value = '';
  $('#d-err').textContent = '';
  $('#d-history').innerHTML = '';

  chosenVerdict = null;
  pickVerdict(preset && set[preset] ? preset : keys[0]);

  $('#scrim').hidden = false;
  $('#drawer').hidden = false;
  setTimeout(() => $('#d-by').value ? $('#d-note').focus() : $('#d-by').focus(), 60);

  const hist = await api.get('/api/history?id=' + encodeURIComponent(id));
  if (hist.length) {
    $('#d-history').innerHTML = `<div class="history"><h3>Earlier decisions</h3>
      <ul>${hist.map(h => `<li><b>${esc(h.verdict.replace(/_/g, ' '))}</b> —
        ${esc(h.by)}, ${esc(h.at.slice(0, 10))}${
        h.note ? '<br>' + esc(h.note) : ''}</li>`).join('')}</ul></div>`;
  }
}

function pickVerdict(v) {
  chosenVerdict = v;
  const set = pending.item_kind === 'finding' ? VERDICTS.finding : VERDICTS.link;
  $$('#d-choices button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.v === v)));
  $('#d-hint').textContent = set[v] || '';
  const needs = v === 'accepted_risk';
  $('#d-expiry-wrap').hidden = !needs;
  if (needs && !$('#d-expires').value) {
    const d = new Date(); d.setMonth(d.getMonth() + 3);
    $('#d-expires').value = d.toISOString().slice(0, 10);
  }
}

function closeDrawer() {
  $('#drawer').hidden = true;
  $('#scrim').hidden = true;
  pending = null;
}

async function saveDecision() {
  const by = $('#d-by').value.trim();
  if (!by) {
    $('#d-err').textContent =
      'Your name is needed. A decision with nobody against it is not evidence.';
    $('#d-by').focus();
    return;
  }
  localStorage.setItem('caaa-reviewer', by);

  const body = {...pending, verdict: chosenVerdict, note: $('#d-note').value,
                decided_by: by};
  if (chosenVerdict === 'accepted_risk') body.expires_on = $('#d-expires').value;

  const r = await api.post('/api/decide', body);
  if (!r.ok) { $('#d-err').textContent = r.data.error || 'Could not save.'; return; }

  const flashed = pending.item_id, kind = pending.item_kind;
  closeDrawer();
  await refresh(kind === 'finding' ? flashed : null, true);
  if (kind === 'link') moveQueue(0);
}

// --------------------------------------------------------------------- run

let poll = null;

function markStages(log) {
  const text = log.join('\n');
  $$('#stages li').forEach(li => {
    const s = li.dataset.stage;
    li.classList.toggle('done', text.includes(`$ ${s}`) &&
      text.indexOf(`$ ${s}`) < text.lastIndexOf('$'));
    li.classList.toggle('active',
      text.lastIndexOf(`$ ${s}`) === text.lastIndexOf('$') &&
      text.includes(`$ ${s}`) && !text.includes('Done.'));
    li.classList.toggle('failed', text.includes(`${s} failed`));
  });
  if (text.includes('Done.'))
    $$('#stages li').forEach(li => {
      li.classList.remove('active'); li.classList.add('done');
    });
}

async function pollRun() {
  const s = await api.get('/api/run-log');
  $('#run-log').textContent = s.log.join('\n') || 'Starting…';
  $('#run-log').scrollTop = $('#run-log').scrollHeight;
  markStages(s.log);

  $('#run-btn').disabled = s.running;
  $('#run-btn').textContent = s.running ? 'Running…' : 'Run pipeline';
  $('#run-state').classList.toggle('live', s.running);
  $('#run-state').querySelector('span').textContent = s.running ? 'running' : 'idle';

  if (!s.running && poll) { clearInterval(poll); poll = null; await refresh(); }
}

// ------------------------------------------------------------------ wiring

$$('.rail nav button').forEach(b => b.addEventListener('click', () => {
  $$('.rail nav button').forEach(x =>
    x.setAttribute('aria-current', String(x === b)));
  $$('.work > section').forEach(s => s.hidden = s.id !== b.dataset.tab);
  if (b.dataset.tab === 'queue') $('#queue-list').focus();
}));

$$('#f-filters button').forEach(b => b.addEventListener('click', () => {
  fFilter = b.dataset.f;
  $$('#f-filters button').forEach(x =>
    x.setAttribute('aria-pressed', String(x === b)));
  renderFindings();
}));

$$('#q-filters button').forEach(b => b.addEventListener('click', () => {
  qFilter = b.dataset.q;
  $$('#q-filters button').forEach(x =>
    x.setAttribute('aria-pressed', String(x === b)));
  renderQueue();
}));

$('#fsearch').addEventListener('input', () => renderFindings());

$('#queue-list').addEventListener('click', e => {
  const row = e.target.closest('.qrow');
  if (!row) return;
  qIndex = Number(row.dataset.i);
  moveQueue(0);
});

document.addEventListener('click', e => {
  const b = e.target.closest('[data-decide]');
  if (!b) return;
  const id = b.dataset.decide, kind = b.dataset.kind;
  const item = kind === 'finding'
    ? findings.find(f => f.id === id) : queue.find(q => q.id === id);
  openDrawer(id, kind, item ? (item.subject || item.ad_sam) : '',
             item ? item.rule_id : '');
});

$('#d-choices').addEventListener('click', e => {
  const b = e.target.closest('[data-v]');
  if (b) pickVerdict(b.dataset.v);
});
$('#d-save').addEventListener('click', saveDecision);
$('#d-cancel').addEventListener('click', closeDrawer);
$('#d-close').addEventListener('click', closeDrawer);
$('#scrim').addEventListener('click', closeDrawer);

$('#run-btn').addEventListener('click', async () => {
  $('#run-btn').disabled = true;
  $$('#stages li').forEach(li =>
    li.classList.remove('done', 'active', 'failed'));
  await api.post('/api/run', {});
  if (!poll) poll = setInterval(pollRun, 700);
  pollRun();
});

document.addEventListener('keydown', e => {
  if (!$('#drawer').hidden) {
    if (e.key === 'Escape') closeDrawer();
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') saveDecision();
    const n = Number(e.key);
    if (n >= 1 && n <= 9 && document.activeElement.tagName !== 'INPUT'
        && document.activeElement.tagName !== 'TEXTAREA') {
      const b = $$('#d-choices button')[n - 1];
      if (b) { e.preventDefault(); pickVerdict(b.dataset.v); }
    }
    return;
  }
  if ($('#queue').hidden) return;
  if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;

  if (e.key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); moveQueue(1); }
  if (e.key === 'k' || e.key === 'ArrowUp')   { e.preventDefault(); moveQueue(-1); }
  if (e.key === 'Enter') {
    const q = qVisible[qIndex];
    if (q) openDrawer(q.id, 'link', q.ad_sam, '');
  }
  const keys = Object.keys(VERDICTS.link);
  const n = Number(e.key);
  if (n >= 1 && n <= keys.length) { e.preventDefault(); quickDecide(keys[n - 1]); }
});

// ------------------------------------------------------------------- start

async function refresh(flashId, keepIndex) {
  [VERDICTS, findings, queue] = await Promise.all([
    api.get('/api/verdicts'), api.get('/api/findings'), api.get('/api/queue')]);
  await renderOverview();
  renderFindings(flashId);
  renderQueue(keepIndex);
}

refresh();
pollRun();
