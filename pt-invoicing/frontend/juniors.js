/* Trainees tab: trainee records (shared with PT Studio), junior consent, monthly
   growth measurements and parent summaries. Talks to the juniors API in pt_app
   (cfg.juniorsApiUrl) with the same login as invoicing.

   Uses helpers from app.js at call time: api, esc, fmtDate, today, toast, state, render. */
'use strict';

const Juniors = (() => {
  const CONSENT = {
    training: 'Training',
    growth_measurements: 'Growth measurements',
    hr_data: 'Heart-rate data',
    photos: 'Photos / video',
  };
  const STATUS = {
    granted: ['Granted', 'paid'],
    missing: ['Not given', ''],
    withdrawn: ['Withdrawn', 'warn'],
    expired: ['Needs renewing', 'warn'],
  };
  const STAGE = { pre_phv: 'Pre', circa_phv: 'Spurt', post_phv: 'Post' };
  const STAGE_LONG = {
    pre_phv: ['Before the growth spurt', 'stage-pre', 'Steady growth. Good time for skills, coordination and general fitness.'],
    circa_phv: ['In the growth spurt', 'stage-spurt', 'Bones can outpace muscles and tendons. Manage load, prioritise technique, mobility and recovery.'],
    post_phv: ['Past the growth spurt', 'stage-post', 'Main spurt is over. Strength and conditioning can build progressively.'],
  };
  const DUE_DAYS = 35; // monthly measuring, with a week's grace
  const daysSince = (iso) => Math.floor((Date.now() - new Date(iso + 'T00:00:00').getTime()) / 864e5);

  const J = {
    loaded: false,
    loading: false,
    list: [],
    view: 'list',      // 'list' | 'detail' | 'form'
    detail: null,      // full trainee from GET /juniors/trainees/{id}
    form: null,        // {mode:'new'|'edit', ...fields}
    measure: null,     // {measured_on, height_cm, sitting_height_cm, weight_kg}
    note: '',
    busy: false,
    lastLink: null,    // consent link returned while ConsentPageUrl isn't set
  };

  const jget = (path) => api('GET', path, null, cfg.juniorsApiUrl);
  const jsend = (method, path, body) => api(method, path, body || {}, cfg.juniorsApiUrl);
  const num = (v) => (v === null || v === undefined ? '' : String(v));
  const fmt1 = (v, unit) => (v === null || v === undefined || v === '' ? '–' : `${Number(v).toFixed(1)}${unit}`);

  /* ---------- loading ---------- */
  async function enter() {
    if (!cfg.juniorsApiUrl) return;
    if (!J.loaded && !J.loading) await refreshList();
  }

  async function refreshList() {
    J.loading = true;
    try {
      J.list = await jget('/juniors/trainees');
      J.loaded = true;
    } catch (e) {
      if (e.message !== 'Signed out') toast(e.message, true);
    } finally {
      J.loading = false;
      if (state.tab === 'trainees') render();
    }
  }

  async function openDetail(id) {
    J.busy = true; J.view = 'detail'; J.detail = null; J.lastLink = null; J.note = '';
    J.measure = newMeasure();
    render();
    try {
      J.detail = await jget(`/juniors/trainees/${encodeURIComponent(id)}`);
    } catch (e) {
      if (e.message !== 'Signed out') { toast(e.message, true); J.view = 'list'; }
    } finally { J.busy = false; render(); }
  }

  const reloadDetail = async () => {
    J.detail = await jget(`/juniors/trainees/${encodeURIComponent(J.detail.id)}`);
    const i = J.list.findIndex((t) => t.id === J.detail.id);
    if (i >= 0) J.list[i] = { ...J.list[i], ...J.detail };
  };

  function newMeasure() {
    return { measured_on: today(), height_cm: '', sitting_height_cm: '', weight_kg: '' };
  }

  /* ---------- views ---------- */
  function render_() {
    if (!cfg.juniorsApiUrl) {
      return '<p class="msg">The Trainees tab needs <code>juniors_api_url</code> in config.json. Add it and redeploy.</p>';
    }
    if (J.view === 'form') return renderForm();
    if (J.view === 'detail') return renderDetail();
    return renderList();
  }

  function consentChips(c) {
    if (!c) return '';
    return `<div class="chips">${Object.keys(CONSENT).map((k) => {
      const [label, cls] = STATUS[c[k]] || STATUS.missing;
      return `<span class="chip ${cls}" title="${esc(CONSENT[k])}: ${esc(label)}">${esc(CONSENT[k])}</span>`;
    }).join('')}</div>`;
  }

  function growthChips(m, consent) {
    const canMeasure = (consent || {}).growth_measurements === 'granted';
    if (!m) return canMeasure ? '<div class="chips"><span class="chip warn">Not measured yet</span></div>' : '';
    const [label, cls] = STAGE_LONG[m.stage] || ['No stage', ''];
    const rapid = (m.flags || []).includes('rapid_growth');
    const due = canMeasure && daysSince(m.measured_on) > DUE_DAYS;
    return `<div class="chips">
      <span class="chip ${cls}">${esc(label)}</span>
      ${rapid ? `<span class="chip stage-spurt">${esc(fmt1(m.growth_cm_per_month, ''))} cm/month</span>` : ''}
      ${due ? '<span class="chip warn">Due for measuring</span>' : ''}
    </div>`;
  }

  function card(t) {
    const sub = [t.age !== null && t.age !== undefined ? `Age ${Math.floor(t.age)}` : null,
      t.payer ? `Paid by ${t.payer.name}` : null].filter(Boolean).join(' · ');
    return `
      <button class="inv trainee" data-action="j-open" data-id="${esc(t.id)}">
        <div class="inv-top"><strong>${esc(t.first_name)} ${esc(t.last_name)}</strong>${t.is_junior ? '<span class="chip">Junior</span>' : ''}</div>
        ${sub ? `<div class="inv-sub">${esc(sub)}</div>` : ''}
        ${t.is_junior ? growthChips(t.latest_measurement, t.consent) + consentChips(t.consent) : ''}
      </button>`;
  }

  function renderList() {
    if (!J.loaded) return '<p class="msg">Loading…</p>';
    const juniors = J.list.filter((t) => t.is_junior);
    const others = J.list.filter((t) => !t.is_junior);
    return `
      <button class="primary wide" data-action="j-new">Add trainee</button>
      ${!J.list.length ? '<p class="empty">No trainees yet.</p>' : ''}
      ${juniors.length ? `<h2>Juniors</h2>${juniors.map(card).join('')}` : ''}
      ${others.length ? `<h2>${juniors.length ? 'Adults' : 'Trainees'}</h2>${others.map(card).join('')}` : ''}
      <button class="link" data-action="j-refresh">Refresh</button>`;
  }

  function renderForm() {
    const f = J.form;
    const payers = state.customers || [];
    const sexes = [['', 'Not set'], ['male', 'Male'], ['female', 'Female'], ['other', 'Other']];
    return `
      <div class="editing"><span>${f.mode === 'edit' ? `Editing <strong>${esc(f.first_name)} ${esc(f.last_name)}</strong>` : 'New trainee'}</span>
        <button class="link" data-action="j-cancel-form">Cancel</button></div>
      <section class="panel">
        <div class="grid2">
          <label class="field"><span>First name</span><input data-jf="first_name" value="${esc(f.first_name)}" autocapitalize="words" autocomplete="off"></label>
          <label class="field"><span>Last name</span><input data-jf="last_name" value="${esc(f.last_name)}" autocapitalize="words" autocomplete="off"></label>
          <label class="field"><span>Date of birth</span><input type="date" data-jf="dob" value="${esc(f.dob)}" max="${today()}"></label>
          <label class="field"><span>Sex</span><select data-jf="sex">${sexes.map(([v, l]) => `<option value="${v}" ${v === (f.sex || '') ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
        </div>
        <label class="field"><span>Bill payer (parent or guardian for juniors)</span>
          <select data-jf="payer_id">
            <option value="">Pays for themselves</option>
            ${payers.map((p) => `<option value="${esc(p.id)}" ${p.id === f.payer_id ? 'selected' : ''}>${esc(p.name)} (${esc(p.email)})</option>`).join('')}
          </select>
        </label>
        <p class="hint">New parent? Add them as a customer on the New invoice tab first.</p>
        <div class="grid2">
          <label class="field"><span>Height (cm)</span><input data-jf="height_cm" inputmode="decimal" value="${esc(f.height_cm)}"></label>
          <label class="field"><span>Weight (kg)</span><input data-jf="weight_kg" inputmode="decimal" value="${esc(f.weight_kg)}"></label>
        </div>
        <p class="hint">Sex is used only for the growth-stage equations, which exist for male and female.</p>
      </section>
      <button class="primary wide" data-action="j-save" ${J.busy ? 'disabled' : ''}>${J.busy ? 'Saving…' : f.mode === 'edit' ? 'Save changes' : 'Add trainee'}</button>`;
  }

  function renderDetail() {
    const t = J.detail;
    if (!t) return '<p class="msg">Loading…</p>';
    const facts = [
      t.age !== null && t.age !== undefined ? `Age ${t.age}` : 'No date of birth',
      t.sex ? t.sex[0].toUpperCase() + t.sex.slice(1) : null,
      t.height_cm ? `${t.height_cm} cm` : null,
      t.weight_kg ? `${t.weight_kg} kg` : null,
    ].filter(Boolean).join(' · ');
    return `
      <button class="link" data-action="j-back">‹ All trainees</button>
      <section class="panel">
        <div class="inv-top"><strong class="big">${esc(t.first_name)} ${esc(t.last_name)}</strong>${t.is_junior ? '<span class="chip">Junior</span>' : ''}</div>
        <div class="inv-sub">${esc(facts)}</div>
        <div class="inv-sub">${t.payer ? `Bill payer: ${esc(t.payer.name)} · ${esc(t.payer.email)}` : 'Pays for themselves'}</div>
        <div class="inv-actions"><button data-action="j-edit">Edit details</button></div>
      </section>
      ${t.is_junior ? renderGrowth(t) + renderConsent(t) + renderMeasure(t) + renderHistory(t) + renderSummary(t) : ''}`;
  }

  function renderGrowth(t) {
    const m = (t.measurements || [])[0];
    if (!m) return '';
    const [label, cls, advice] = STAGE_LONG[m.stage] || ['No stage yet', '', ''];
    const flags = m.flags || [];
    const rapid = flags.includes('rapid_growth');
    const off = m.moore_offset;
    let peak = '';
    if (off !== null && off !== undefined) {
      const yrs = Math.abs(off) < 0.25 ? null : Math.abs(off).toFixed(1);
      peak = yrs === null ? 'Around peak growth now'
        : off < 0 ? `About ${yrs} years before peak growth` : `About ${yrs} years after peak growth`;
      if (m.predicted_aphv) peak += ` (peak at about age ${Number(m.predicted_aphv).toFixed(1)})`;
    }
    const age = daysSince(m.measured_on);
    const canMeasure = (t.consent || {}).growth_measurements === 'granted';
    return `
      <h2>Growth</h2>
      <section class="panel growth ${cls}">
        <div class="growth-stage">${esc(label)}</div>
        ${advice ? `<p class="growth-advice">${esc(advice)}</p>` : ''}
        <div class="growth-stats">
          <div><small>Growth rate</small><strong class="${rapid ? 'hot' : ''}">${m.growth_cm_per_month === null || m.growth_cm_per_month === undefined ? '–' : esc(fmt1(m.growth_cm_per_month, ''))}</strong><small>cm/month</small></div>
          <div><small>Height</small><strong>${esc(fmt1(m.height_cm, ''))}</strong><small>cm</small></div>
          <div><small>Measured</small><strong>${age === 0 ? 'Today' : esc(String(age))}</strong><small>${age === 0 ? esc(fmtDate(m.measured_on)) : 'days ago'}</small></div>
        </div>
        ${peak ? `<p class="hint">${esc(peak)}</p>` : ''}
        ${rapid ? '<p class="hint hot">Growing faster than 0.6 cm/month: treat as in the growth spurt, whatever the estimate says.</p>' : ''}
        ${flags.includes('no_velocity_yet') ? '<p class="hint">Growth rate appears once there are two measurements at least 4 weeks apart.</p>' : ''}
        ${flags.includes('offset_unreliable_for_age') ? '<p class="hint">The peak-growth estimate is less reliable at this age, so lean on the growth rate.</p>' : ''}
        ${canMeasure && age > DUE_DAYS ? '<p class="hint error">Due for measuring.</p>' : ''}
      </section>`;
  }

  function renderConsent(t) {
    const c = t.consent || {};
    const pending = (t.pending_requests || [])[0];
    const allGranted = Object.keys(CONSENT).every((k) => c[k] === 'granted');
    const rows = Object.keys(CONSENT).map((k) => {
      const [label, cls] = STATUS[c[k]] || STATUS.missing;
      return `<div class="crow"><span>${esc(CONSENT[k])}</span><span class="crow-end">
        <span class="chip ${cls}">${esc(label)}</span>
        ${c[k] === 'granted' ? `<button class="mini" data-action="j-withdraw" data-type="${k}">Withdraw</button>` : ''}
      </span></div>`;
    }).join('');
    const last = (t.consent_log || [])[0];
    return `
      <h2>Consent</h2>
      <section class="panel">
        ${rows}
        ${last ? `<p class="hint">Last given by ${esc(last.given_by)} (${esc(last.relationship)}) on ${esc(fmtDate(last.granted_at.slice(0, 10)))}.</p>` : ''}
        ${pending ? `<p class="hint">Request sent to ${esc(pending.sent_to)} on ${esc(fmtDate(pending.sent_at.slice(0, 10)))} — link open until ${esc(new Date(pending.expires_at * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }))}.</p>` : ''}
        ${J.lastLink ? `<div class="linkbox"><p class="hint">Consent page isn't live yet, so nothing was emailed. Test link:</p>
          <input readonly value="${esc(J.lastLink)}" data-select><button class="mini" data-action="j-copy">Copy</button></div>` : ''}
        ${!t.payer ? '<p class="hint error">Link a bill payer with an email to send a consent request.</p>' : ''}
        <button class="${allGranted ? 'secondary' : 'primary'} wide" data-action="j-request" ${!t.payer || J.busy ? 'disabled' : ''}>
          ${pending ? 'Send a new consent request' : allGranted ? 'Ask for consent again' : 'Send consent request'}
        </button>
      </section>`;
  }

  function renderMeasure(t) {
    const ok = (t.consent || {}).growth_measurements === 'granted';
    if (!ok) return `<h2>Measurements</h2><p class="hint panel">Growth measurement consent is needed before you can record measurements.</p>`;
    const m = J.measure || newMeasure();
    const needsSex = !['male', 'female'].includes(t.sex);
    return `
      <h2>Record measurement</h2>
      <section class="panel">
        ${needsSex ? '<p class="hint error">Set sex to male or female (Edit details) to record measurements.</p>' : ''}
        <div class="grid2">
          <label class="field"><span>Date</span><input type="date" data-jm="measured_on" value="${esc(m.measured_on)}" max="${today()}"></label>
          <label class="field"><span>Height (cm)</span><input data-jm="height_cm" inputmode="decimal" placeholder="e.g. 152.4" value="${esc(m.height_cm)}"></label>
          <label class="field"><span>Sitting height (cm)</span><input data-jm="sitting_height_cm" inputmode="decimal" placeholder="optional" value="${esc(m.sitting_height_cm)}"></label>
          <label class="field"><span>Weight (kg)</span><input data-jm="weight_kg" inputmode="decimal" placeholder="optional" value="${esc(m.weight_kg)}"></label>
        </div>
        <button class="primary wide" data-action="j-measure" ${J.busy || needsSex ? 'disabled' : ''}>${J.busy ? 'Saving…' : 'Save measurement'}</button>
      </section>`;
  }

  function renderHistory(t) {
    const ms = t.measurements || [];
    if (!ms.length) return '';
    return `
      <h2>History</h2>
      <section class="panel tablewrap">
        <table class="mtable">
          <thead><tr><th>Date</th><th>Height</th><th>Weight</th><th>cm/mo</th><th>Stage</th></tr></thead>
          <tbody>${ms.map((m) => `
            <tr>
              <td>${esc(fmtDate(m.measured_on))}</td>
              <td>${fmt1(m.height_cm, '')}</td>
              <td>${fmt1(m.weight_kg, '')}</td>
              <td class="${(m.flags || []).includes('rapid_growth') ? 'hot' : ''}">${fmt1(m.growth_cm_per_month, '')}</td>
              <td>${esc(STAGE[m.stage] || '–')}</td>
            </tr>`).join('')}</tbody>
        </table>
        <p class="hint">Height in cm, weight in kg. Growth over 0.6 cm/month is highlighted. Stage: before, during or after the growth spurt. Re-measuring on the same date replaces that day's entry.</p>
      </section>`;
  }

  function renderSummary(t) {
    const ok = (t.consent || {}).growth_measurements === 'granted';
    if (!ok || !(t.measurements || []).length || !t.payer) return '';
    return `
      <h2>Email growth summary</h2>
      <section class="panel">
        <label class="field"><span>Note for ${esc(t.payer.name.split(' ')[0])} (optional)</span>
          <textarea data-jnote maxlength="1000" placeholder="e.g. Great month - technique is really coming on.">${esc(J.note)}</textarea></label>
        <button class="primary wide" data-action="j-summary" ${J.busy ? 'disabled' : ''}>Email summary to ${esc(t.payer.email)}</button>
      </section>`;
  }

  /* ---------- actions ---------- */
  function startForm(mode) {
    const t = mode === 'edit' ? J.detail : {};
    J.form = {
      mode, id: t.id,
      first_name: t.first_name || '', last_name: t.last_name || '',
      dob: t.dob || '', sex: t.sex || '', payer_id: t.payer_id || '',
      height_cm: num(t.height_cm), weight_kg: num(t.weight_kg),
    };
    J.view = 'form';
    render();
  }

  async function saveForm() {
    const f = J.form;
    if (!f.first_name.trim() || !f.last_name.trim()) return toast('Enter a first and last name', true);
    const body = {
      first_name: f.first_name, last_name: f.last_name, dob: f.dob, sex: f.sex,
      payer_id: f.payer_id, height_cm: f.height_cm, weight_kg: f.weight_kg,
    };
    J.busy = true; render();
    try {
      const saved = f.mode === 'edit'
        ? await jsend('PUT', `/juniors/trainees/${encodeURIComponent(f.id)}`, body)
        : await jsend('POST', '/juniors/trainees', body);
      toast(f.mode === 'edit' ? 'Saved' : `${saved.first_name} added`);
      J.form = null;
      J.view = 'list';
      J.busy = false;
      await refreshList();
      await openDetail(saved.id);
    } catch (e) {
      J.busy = false; render();
      if (e.message !== 'Signed out') toast(e.message, true);
    }
  }

  async function requestConsent() {
    const t = J.detail;
    const pending = (t.pending_requests || []).length;
    if (pending && !confirm('A consent link is already open. Send a new one? The old link will still work until it expires.')) return;
    J.busy = true; render();
    try {
      const r = await jsend('POST', `/juniors/trainees/${encodeURIComponent(t.id)}/consent-request`);
      if (r.email_queued) { J.lastLink = null; toast(`Consent request emailed to ${r.sent_to}`); }
      else {
        J.lastLink = r.link && r.link.startsWith('#') ? `${location.origin}/consent.html${r.link}` : r.link;
        toast('Link created (not emailed yet)');
      }
      await reloadDetail();
    } catch (e) { if (e.message !== 'Signed out') toast(e.message, true); }
    J.busy = false; render();
  }

  async function withdraw(type) {
    const t = J.detail;
    if (!confirm(`Withdraw ${CONSENT[type].toLowerCase()} consent for ${t.first_name}? The record is kept, marked as withdrawn.`)) return;
    J.busy = true; render();
    try {
      await jsend('POST', '/juniors/consent/withdraw', { trainee_id: t.id, consent_type: type });
      toast('Consent withdrawn');
      await reloadDetail();
    } catch (e) { if (e.message !== 'Signed out') toast(e.message, true); }
    J.busy = false; render();
  }

  async function saveMeasurement() {
    const m = J.measure;
    if (!m.height_cm) return toast('Enter a height', true);
    J.busy = true; render();
    try {
      const r = await jsend('POST', '/juniors/measurements', {
        trainee_id: J.detail.id, measured_on: m.measured_on,
        height_cm: m.height_cm, sitting_height_cm: m.sitting_height_cm || null, weight_kg: m.weight_kg || null,
      });
      const g = r.growth_cm_per_month;
      toast(g === null || g === undefined ? 'Saved. Growth rate shows from the next monthly measurement.'
        : `Saved: ${g.toFixed(1)} cm/month${(r.flags || []).includes('rapid_growth') ? ' — rapid growth' : ''}`);
      J.measure = newMeasure();
      await reloadDetail();
    } catch (e) { if (e.message !== 'Signed out') toast(e.message, true); }
    J.busy = false; render();
  }

  async function sendSummary() {
    const t = J.detail;
    if (!confirm(`Email ${t.first_name}'s growth summary to ${t.payer.email}?`)) return;
    J.busy = true; render();
    try {
      const r = await jsend('POST', `/juniors/trainees/${encodeURIComponent(t.id)}/growth-summary`, { note: J.note });
      toast(r.email_queued ? `Summary emailed to ${r.sent_to}` : 'Couldn\'t queue the email - try again', !r.email_queued);
      if (r.email_queued) J.note = '';
    } catch (e) { if (e.message !== 'Signed out') toast(e.message, true); }
    J.busy = false; render();
  }

  /* ---------- events (called from app.js) ---------- */
  function onClick(e) {
    const el = e.target.closest('[data-action^="j-"]');
    if (!el) return false;
    const a = el.dataset.action;
    if (a === 'j-open') openDetail(el.dataset.id);
    else if (a === 'j-back') { J.view = 'list'; J.detail = null; J.lastLink = null; render(); }
    else if (a === 'j-refresh') { J.loaded = false; render(); refreshList(); }
    else if (a === 'j-new') startForm('new');
    else if (a === 'j-edit') startForm('edit');
    else if (a === 'j-cancel-form') { J.view = J.form.mode === 'edit' ? 'detail' : 'list'; J.form = null; render(); }
    else if (a === 'j-save') saveForm();
    else if (a === 'j-request') requestConsent();
    else if (a === 'j-withdraw') withdraw(el.dataset.type);
    else if (a === 'j-measure') saveMeasurement();
    else if (a === 'j-summary') sendSummary();
    else if (a === 'j-copy') {
      navigator.clipboard?.writeText(J.lastLink).then(() => toast('Link copied'), () => toast('Copy failed - select the link instead', true));
    }
    return true;
  }

  function onInput(e) {
    const t = e.target;
    if (t.dataset.jf !== undefined && J.form) { J.form[t.dataset.jf] = t.value; return true; }
    if (t.dataset.jm !== undefined && J.measure) { J.measure[t.dataset.jm] = t.value; return true; }
    if (t.dataset.jnote !== undefined) { J.note = t.value; return true; }
    return false;
  }

  return { render: render_, enter, onClick, onInput };
})();
