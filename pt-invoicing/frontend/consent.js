/* Parent consent page. Public: no login. The single-use token arrives in the
   link after '#', so it is never sent to a server in a URL or Referer header;
   it is read once, removed from the address bar and posted in a request body.

   Wording version must match CURRENT_CONSENT_VERSION in pt_app/juniors/consent.py.
   If you change the wording below, bump both. */
'use strict';

const WORDING_VERSION = '2026-10-v1';

const TYPES = {
  training: {
    title: 'Taking part in coached training sessions',
    body: 'Personal training and conditioning sessions coached by me, planned for {name}\'s age and stage of development.',
  },
  growth_measurements: {
    title: 'Monthly growth measurements',
    body: 'Height, sitting height and weight, measured about once a month. I use them to estimate where {name} is in their growth spurt so I can adjust training load. You\'ll get a summary by email.',
  },
  hr_data: {
    title: 'Heart-rate recording',
    body: '{name} wears a chest strap during some sessions so I can see heart rate live and track fitness over time.',
  },
  photos: {
    title: 'Photos and video for marketing',
    body: 'Photos or short clips of {name} training, used on my website or social media. Leave this unticked and I won\'t use any.',
  },
};

const $ = (s) => document.querySelector(s);
const page = $('#page');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let cfg, token, info, sending = false;

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast bad'; t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 6000);
}

async function post(path, body) {
  const r = await fetch(cfg.juniorsApiUrl.replace(/\/$/, '') + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || 'Something went wrong. Please try again.'); e.status = r.status; throw e; }
  return data;
}

function message(title, text) {
  page.innerHTML = `<div class="done"><h2>${esc(title)}</h2><p class="small">${esc(text)}</p></div>`;
}

function render() {
  const name = info.child_first_name || 'your child';
  const biz = cfg.businessName || 'me';
  const types = info.consent_types.filter((k) => TYPES[k]);
  page.innerHTML = `
    <p class="lede">Hi${info.guardian_name ? ' ' + esc(info.guardian_name.split(' ')[0]) : ''}. Please choose what you're happy for me to do while coaching <strong>${esc(name)}</strong>. You can change your mind at any time.</p>

    <section class="panel">
      ${types.map((k) => `
        <label class="consent-type">
          <input type="checkbox" name="type" value="${k}">
          <div><strong>${esc(TYPES[k].title)}</strong><span>${esc(TYPES[k].body.replaceAll('{name}', name))}</span></div>
        </label>`).join('')}
    </section>

    <section class="panel" id="heights" hidden>
      <p style="margin:0 0 10px"><strong>Optional: parents' heights</strong></p>
      <p class="small" style="margin:0 0 12px">These improve the growth estimate. Only the average of the two is stored; the individual heights are not saved. Leave blank if you'd rather not, or if either is unknown.</p>
      <div class="grid2">
        <label class="field"><span>Birth parent 1 (cm)</span><input id="pa" inputmode="decimal" placeholder="e.g. 178"></label>
        <label class="field"><span>Birth parent 2 (cm)</span><input id="pb" inputmode="decimal" placeholder="e.g. 165"></label>
      </div>
    </section>

    <section class="panel info">
      <p><strong>About ${esc(name)}'s information</strong></p>
      <p>Only I (${esc(biz)}) can see it. It's stored securely on UK-based servers and is never sold or shared with anyone else.</p>
      <p>I keep it while ${esc(name)} trains with me. You can ask me to stop, withdraw any part of this consent, or have the data deleted at any time by replying to my email. I keep a record of what you agreed to and when.</p>
      <p>When ${esc(name)} turns 18 they'll be asked to give their own consent.</p>
    </section>

    <section class="panel">
      <div class="grid2">
        <label class="field"><span>Your full name</span><input id="givenBy" autocomplete="name" autocapitalize="words"></label>
        <label class="field"><span>Relationship to ${esc(name)}</span>
          <select id="rel"><option value="">Choose</option><option value="mother">Mother</option><option value="father">Father</option><option value="guardian">Legal guardian</option></select>
        </label>
      </div>
      <label class="declare"><input type="checkbox" id="declare">
        <span class="small">I'm ${esc(name)}'s parent or legal guardian and have parental responsibility. I've read the information above.</span></label>
    </section>

    <button class="primary wide" id="submit">Give consent</button>
    <p class="small" style="text-align:center;margin-top:14px">Consent wording version ${esc(info.consent_version)}. Link valid until ${esc(new Date(info.expires_at * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }))}.</p>`;
}

function height(id) {
  const v = $(id).value.trim();
  if (!v) return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n < 120 || n > 230) throw new Error('Parent heights should be in centimetres, between 120 and 230.');
  return n;
}

async function submit() {
  if (sending) return;
  const granted = [...document.querySelectorAll('input[name=type]:checked')].map((c) => c.value);
  const givenBy = $('#givenBy').value.trim();
  const rel = $('#rel').value;
  if (!granted.length) return toast('Tick at least one option. If you don\'t want to give consent, you can just close this page.');
  if (!givenBy) return toast('Please enter your full name.');
  if (!rel) return toast('Please choose your relationship.');
  if (!$('#declare').checked) return toast('Please confirm you have parental responsibility.');
  let pa = null, pb = null;
  if (granted.includes('growth_measurements')) {
    try { pa = height('#pa'); pb = height('#pb'); } catch (e) { return toast(e.message); }
  }
  sending = true;
  $('#submit').disabled = true; $('#submit').textContent = 'Sending…';
  try {
    await post('/juniors/consent', {
      token, given_by: givenBy, relationship: rel, granted,
      parent_a_height_cm: pa, parent_b_height_cm: pb,
    });
    token = null;
    message('Thank you', `Your choices for ${info.child_first_name || 'your child'} have been recorded. You can close this page.`);
  } catch (e) {
    sending = false;
    if (e.status === 410) return message('This link has expired', e.message);
    $('#submit').disabled = false; $('#submit').textContent = 'Give consent';
    toast(e.message);
  }
}

page.addEventListener('change', (e) => {
  if (e.target.name === 'type' && e.target.value === 'growth_measurements') $('#heights').hidden = !e.target.checked;
});
page.addEventListener('click', (e) => { if (e.target.id === 'submit') submit(); });

async function boot() {
  token = new URLSearchParams(location.hash.slice(1)).get('t');
  history.replaceState(null, '', location.pathname); // keep the token out of history and screenshots
  try {
    cfg = await (await fetch('config.json', { cache: 'no-store' })).json();
  } catch {
    return message('Something went wrong', 'Couldn\'t load this page. Check your connection and open the link again.');
  }
  const { primary, accent } = cfg.brand || {};
  if (primary) { document.documentElement.style.setProperty('--brand', primary); document.querySelector('meta[name=theme-color]').content = primary; }
  if (accent) document.documentElement.style.setProperty('--accent', accent);
  if (cfg.businessName) $('#bizName').textContent = cfg.businessName;
  if (!cfg.juniorsApiUrl) return message('Not available yet', 'Consent isn\'t set up yet. Please reply to the email you received.');
  if (!token) return message('Link not recognised', 'Please open the link from your email again, or reply to that email for a new one.');
  try {
    info = await post('/juniors/consent/lookup', { token });
  } catch (e) {
    return message(e.status === 410 ? 'This link has expired' : 'Something went wrong', e.message);
  }
  if (info.consent_version !== WORDING_VERSION) {
    return message('This page is being updated', 'Please reply to the email you received and I\'ll send you a fresh link.');
  }
  render();
}

boot();
