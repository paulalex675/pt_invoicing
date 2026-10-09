/* PT Invoices - single-file front end. No build step, no dependencies. */
'use strict';

const TYPES = ['PT session', 'Coaching session', 'Small group', 'Online coaching', 'Other'];
const $ = (s) => document.querySelector(s);
const appEl = $('#app');

let cfg;
const state = {
  tab: 'new',
  customers: [],
  invoices: [],
  custForm: null, // {mode:'new'|'edit', name, email, address}
  sending: false,
  draft: loadDraft(),
};

/* ---------- small helpers ---------- */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pounds = (p) => '£' + (p / 100).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const store = {
  get: (k) => localStorage.getItem('pt.' + k),
  set: (k, v) => localStorage.setItem('pt.' + k, v),
  del: (k) => localStorage.removeItem('pt.' + k),
};
function newLine(prev) {
  return { date: prev?.date || today(), type: prev?.type || TYPES[0], durationMins: prev?.durationMins || '', amount: prev?.amount || '', notes: '' };
}
function loadDraft() {
  try {
    const d = JSON.parse(localStorage.getItem('pt.draft'));
    if (d && Array.isArray(d.lines) && d.lines.length) return d;
  } catch { /* ignore */ }
  return { customerId: '', issueDate: today(), lines: [newLine()] };
}
const saveDraft = () => localStorage.setItem('pt.draft', JSON.stringify(state.draft));
function toast(msg, bad = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, bad ? 6000 : 3500);
}
const totalPence = () =>
  state.draft.lines.reduce((sum, l) => {
    const n = parseFloat(l.amount);
    return sum + (n > 0 ? Math.round(n * 100) : 0);
  }, 0);

/* ---------- auth: Cognito hosted UI, authorization code + PKCE ---------- */
const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

async function login() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  const st = b64url(crypto.getRandomValues(new Uint8Array(16)));
  store.set('verifier', verifier);
  store.set('state', st);
  const q = new URLSearchParams({
    response_type: 'code', client_id: cfg.clientId, redirect_uri: cfg.redirectUri,
    scope: 'openid email', code_challenge: challenge, code_challenge_method: 'S256', state: st,
  });
  location.assign(`https://${cfg.cognitoDomain}/oauth2/authorize?${q}`);
}

async function tokenRequest(params) {
  const r = await fetch(`https://${cfg.cognitoDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: cfg.clientId, ...params }),
  });
  if (!r.ok) throw new Error('Sign-in failed. Please try again.');
  const t = await r.json();
  store.set('id', t.id_token);
  store.set('exp', String(Date.now() + (t.expires_in - 60) * 1000));
  if (t.refresh_token) store.set('refresh', t.refresh_token);
}

async function handleRedirect() {
  const p = new URLSearchParams(location.search);
  if (p.has('error')) {
    history.replaceState({}, '', location.pathname);
    throw new Error(p.get('error_description') || 'Sign-in was cancelled.');
  }
  if (!p.has('code')) return;
  const code = p.get('code');
  const ok = p.get('state') === store.get('state');
  history.replaceState({}, '', location.pathname);
  if (!ok) throw new Error('Sign-in was interrupted. Please try again.');
  await tokenRequest({ grant_type: 'authorization_code', code, redirect_uri: cfg.redirectUri, code_verifier: store.get('verifier') });
  store.del('verifier');
  store.del('state');
}

async function getToken() {
  if (store.get('id') && Date.now() < Number(store.get('exp'))) return store.get('id');
  const rt = store.get('refresh');
  if (rt) {
    try {
      await tokenRequest({ grant_type: 'refresh_token', refresh_token: rt });
      return store.get('id');
    } catch { /* fall through */ }
  }
  return null;
}

function clearSession() { ['id', 'exp', 'refresh'].forEach(store.del); }
function signOut() {
  clearSession();
  const q = new URLSearchParams({ client_id: cfg.clientId, logout_uri: cfg.redirectUri });
  location.assign(`https://${cfg.cognitoDomain}/logout?${q}`);
}

async function api(method, path, body) {
  const token = await getToken();
  if (!token) { showLogin('Your session has ended. Please sign in again.'); throw new Error('Signed out'); }
  const r = await fetch(cfg.apiUrl.replace(/\/$/, '') + path, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { clearSession(); showLogin('Your session has ended. Please sign in again.'); throw new Error('Signed out'); }
  if (!r.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

/* ---------- views ---------- */
function showLogin(message) {
  $('#tabs').hidden = true;
  $('#signOut').hidden = true;
  appEl.innerHTML = `
    <div class="login">
      <h2>Sign in</h2>
      <p class="${message ? 'error' : ''}">${esc(message || 'Sign in to create and send invoices.')}</p>
      <button class="primary" data-action="login">Sign in</button>
    </div>`;
}

function customerForm() {
  const f = state.custForm;
  return `
    <div class="subpanel">
      <label class="field"><span>Name</span><input data-cust="name" value="${esc(f.name)}" autocomplete="off" autocapitalize="words"></label>
      <label class="field"><span>Email</span><input data-cust="email" type="email" inputmode="email" autocapitalize="off" value="${esc(f.email)}"></label>
      <label class="field"><span>Address (optional)</span><textarea data-cust="address">${esc(f.address)}</textarea></label>
      <div class="formbar">
        <button class="secondary" data-action="cancel-customer">Cancel</button>
        <button class="primary" data-action="save-customer">${f.mode === 'edit' ? 'Save changes' : 'Add customer'}</button>
      </div>
    </div>`;
}

function lineCard(l, i) {
  const many = state.draft.lines.length > 1;
  return `
    <section class="session">
      <div class="row-end">
        <span class="session-title">Session ${i + 1}</span>
        ${many ? `<button class="remove" data-action="remove-line" data-i="${i}">Remove</button>` : ''}
      </div>
      <div class="grid2">
        <label class="field"><span>Date</span><input type="date" data-line="${i}" data-field="date" value="${esc(l.date)}"></label>
        <label class="field"><span>Type</span>
          <select data-line="${i}" data-field="type">${TYPES.map((t) => `<option ${t === l.type ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
        </label>
        <label class="field"><span>Length (minutes)</span><input data-line="${i}" data-field="durationMins" inputmode="numeric" placeholder="60" value="${esc(l.durationMins)}"></label>
        <label class="field"><span>Price (£)</span><input data-line="${i}" data-field="amount" inputmode="decimal" placeholder="0.00" value="${esc(l.amount)}"></label>
      </div>
      <label class="field"><span>Notes (optional)</span><input data-line="${i}" data-field="notes" maxlength="300" value="${esc(l.notes)}" placeholder="e.g. strength block, week 3"></label>
    </section>`;
}

function renderNew() {
  const d = state.draft;
  const cust = state.customers.find((c) => c.id === d.customerId);
  return `
    <section class="panel">
      <label class="field"><span>Customer</span>
        <select data-bind="customerId">
          <option value="">Choose a customer</option>
          ${state.customers.map((c) => `<option value="${esc(c.id)}" ${c.id === d.customerId ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
          <option value="__new">+ New customer</option>
        </select>
      </label>
      ${cust && !state.custForm ? `<button class="link" data-action="edit-customer">Edit ${esc(cust.name)}'s details</button>` : ''}
      ${state.custForm ? customerForm() : ''}
      <label class="field"><span>Invoice date</span><input type="date" data-bind="issueDate" value="${esc(d.issueDate)}"></label>
    </section>
    <h2>Sessions</h2>
    ${d.lines.map(lineCard).join('')}
    <button class="secondary wide" data-action="add-line">Add another session</button>
    <div class="bar">
      <div><small>Total</small><strong id="total">${pounds(totalPence())}</strong></div>
      <button class="primary" data-action="send" ${state.sending ? 'disabled' : ''}>${state.sending ? 'Sending…' : 'Send invoice'}</button>
    </div>`;
}

function renderInvoices() {
  if (!state.invoices.length) return '<p class="empty">No invoices yet. Create one from the New invoice tab.</p>';
  return state.invoices.map((inv) => `
    <article class="inv">
      <div class="inv-top"><strong>${esc(inv.number)}</strong>
        <span class="chip ${inv.status === 'paid' ? 'paid' : ''}">${inv.status === 'paid' ? 'Paid' : 'Awaiting payment'}</span></div>
      <div class="inv-mid"><span>${esc(inv.customer.name)}</span><span class="amt">${pounds(inv.total)}</span></div>
      <div class="inv-sub">Issued ${fmtDate(inv.issueDate)}, due ${fmtDate(inv.dueDate)}</div>
      <div class="inv-actions">
        <button data-action="pdf" data-n="${esc(inv.number)}">View PDF</button>
        <button data-action="resend" data-n="${esc(inv.number)}">Resend email</button>
        <button data-action="toggle-paid" data-n="${esc(inv.number)}">${inv.status === 'paid' ? 'Mark unpaid' : 'Mark paid'}</button>
      </div>
    </article>`).join('');
}

function render() {
  $('#tabs').hidden = false;
  $('#signOut').hidden = false;
  document.querySelectorAll('#tabs button').forEach((b) => {
    if (b.dataset.tab === state.tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  appEl.innerHTML = state.tab === 'new' ? renderNew() : renderInvoices();
}

/* ---------- actions ---------- */
async function loadAll() {
  [state.customers, state.invoices] = await Promise.all([api('GET', '/customers'), api('GET', '/invoices')]);
  if (state.draft.customerId && !state.customers.some((c) => c.id === state.draft.customerId)) state.draft.customerId = '';
}

async function saveCustomer() {
  const f = state.custForm;
  try {
    const saved = f.mode === 'edit'
      ? await api('PUT', `/customers/${f.id}`, { name: f.name, email: f.email, address: f.address })
      : await api('POST', '/customers', { name: f.name, email: f.email, address: f.address });
    state.customers = await api('GET', '/customers');
    state.draft.customerId = saved.id;
    state.custForm = null;
    saveDraft();
    render();
    toast(f.mode === 'edit' ? 'Customer updated' : `${saved.name} added`);
  } catch (e) { if (e.message !== 'Signed out') toast(e.message, true); }
}

function validateDraft() {
  const d = state.draft;
  if (!d.customerId) return 'Choose a customer first.';
  for (const [i, l] of d.lines.entries()) {
    if (!l.date) return `Session ${i + 1} needs a date.`;
    if (!(parseFloat(l.amount) > 0)) return `Session ${i + 1} needs a price.`;
    if (l.durationMins !== '' && !/^\d{1,3}$/.test(String(l.durationMins))) return `Session ${i + 1} length must be whole minutes.`;
  }
  return null;
}

async function sendInvoice() {
  const problem = validateDraft();
  if (problem) return toast(problem, true);
  const cust = state.customers.find((c) => c.id === state.draft.customerId);
  if (!confirm(`Send an invoice for ${pounds(totalPence())} to ${cust.name} (${cust.email})?`)) return;
  state.sending = true; render();
  try {
    const d = state.draft;
    const inv = await api('POST', '/invoices', {
      customerId: d.customerId,
      issueDate: d.issueDate,
      lines: d.lines.map((l) => ({ ...l, durationMins: l.durationMins === '' ? null : Number(l.durationMins), amount: Number(l.amount) })),
    });
    const last = d.lines[d.lines.length - 1];
    state.draft = { customerId: '', issueDate: today(), lines: [newLine({ type: last.type, durationMins: last.durationMins, amount: last.amount })] };
    saveDraft();
    state.invoices = await api('GET', '/invoices');
    state.tab = 'invoices';
    state.sending = false; render();
    inv.emailQueued ? toast(`${inv.number} sent to ${cust.name}`) : toast(`${inv.number} saved, but the email didn't send. Use Resend email.`, true);
  } catch (e) {
    state.sending = false; render();
    if (e.message !== 'Signed out') toast(e.message, true);
  }
}

async function invoiceAction(action, number) {
  const inv = state.invoices.find((i) => i.number === number);
  try {
    if (action === 'pdf') {
      const w = window.open('', '_blank'); // opened synchronously so iOS doesn't block it
      try {
        const { url } = await api('GET', `/invoices/${number}/pdf`);
        w ? (w.location.href = url) : location.assign(url);
      } catch (e) { if (w) w.close(); throw e; }
    } else if (action === 'resend') {
      if (!confirm(`Resend ${number} to ${inv.customer.email}?`)) return;
      const r = await api('POST', `/invoices/${number}/resend`);
      toast(r.emailQueued ? `${number} resent` : "The email didn't send. Please try again.", !r.emailQueued);
    } else if (action === 'toggle-paid') {
      const status = inv.status === 'paid' ? 'issued' : 'paid';
      await api('POST', `/invoices/${number}/status`, { status });
      inv.status = status;
      render();
      toast(status === 'paid' ? `${number} marked paid` : `${number} marked unpaid`);
    }
  } catch (e) { if (e.message !== 'Signed out') toast(e.message, true); }
}

/* ---------- events (delegated, so re-rendering never loses handlers) ---------- */
document.addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (tab) { state.tab = tab.dataset.tab; render(); return; }
  if (e.target.id === 'signOut') return signOut();
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const a = el.dataset.action;
  if (a === 'login') login();
  else if (a === 'add-line') { state.draft.lines.push(newLine(state.draft.lines.at(-1))); saveDraft(); render(); }
  else if (a === 'remove-line') { state.draft.lines.splice(Number(el.dataset.i), 1); saveDraft(); render(); }
  else if (a === 'edit-customer') {
    const c = state.customers.find((x) => x.id === state.draft.customerId);
    state.custForm = { mode: 'edit', id: c.id, name: c.name, email: c.email, address: c.address || '' };
    render();
  }
  else if (a === 'cancel-customer') { state.custForm = null; render(); }
  else if (a === 'save-customer') saveCustomer();
  else if (a === 'send') sendInvoice();
  else invoiceAction(a, el.dataset.n);
});

document.addEventListener('input', (e) => {
  const t = e.target;
  if (t.dataset.cust) state.custForm[t.dataset.cust] = t.value;
  else if (t.dataset.line !== undefined) {
    state.draft.lines[Number(t.dataset.line)][t.dataset.field] = t.value;
    saveDraft();
    const total = $('#total');
    if (total) total.textContent = pounds(totalPence());
  } else if (t.dataset.bind === 'issueDate') { state.draft.issueDate = t.value; saveDraft(); }
});

document.addEventListener('change', (e) => {
  const t = e.target;
  if (t.dataset.bind === 'customerId') {
    if (t.value === '__new') state.custForm = { mode: 'new', name: '', email: '', address: '' };
    else { state.draft.customerId = t.value; state.custForm = null; saveDraft(); }
    render();
  } else if (t.dataset.line !== undefined && t.tagName === 'SELECT') {
    state.draft.lines[Number(t.dataset.line)][t.dataset.field] = t.value;
    saveDraft();
  }
});

/* ---------- boot ---------- */
async function boot() {
  try {
    cfg = await (await fetch('config.json', { cache: 'no-store' })).json();
  } catch {
    appEl.innerHTML = '<p class="msg error">Couldn\'t load the app settings. Check your connection and reload.</p>';
    return;
  }
  const { primary, accent } = cfg.brand || {};
  if (primary) { document.documentElement.style.setProperty('--brand', primary); document.querySelector('meta[name=theme-color]').content = primary; }
  if (accent) document.documentElement.style.setProperty('--accent', accent);
  $('#appName').textContent = cfg.brand?.app_name || 'Invoices';
  document.title = cfg.brand?.app_name || 'Invoices';
  $('#signOut').hidden = true;

  try { await handleRedirect(); } catch (e) { return showLogin(e.message); }
  if (!(await getToken())) return showLogin();
  appEl.innerHTML = '<p class="msg">Loading…</p>';
  try { await loadAll(); render(); } catch (e) { if (e.message !== 'Signed out') appEl.innerHTML = `<p class="msg error">${esc(e.message)}</p>`; }
}

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
boot();
