'use strict';
/* JARVIS command center v4. Every button below is wired to a real endpoint. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked: fine */ } },
};
const state = {
  sessionId: LS.get('jarvis_session_id', '') || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
  speakReplies: LS.get('jarvis_speak', '1') === '1',
  voiceLang: LS.get('jarvis_lang', ''),
  handsFree: false, busy: false, speaking: false, rec: null, aiReady: false, aiOffline: false,
  pending: [], seenPending: new Set(),
};
LS.set('jarvis_session_id', state.sessionId);

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`);
  return data;
}
function toast(text, bad = false) {
  const t = $('#toast');
  t.textContent = text; t.classList.toggle('bad', bad); t.classList.remove('hidden');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add('hidden'), 4500);
}
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const svgIcon = (id) => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('class', 'ic'); const u = document.createElementNS('http://www.w3.org/2000/svg', 'use'); u.setAttribute('href', '#' + id); s.append(u); return s; };

/* ------------------------------------------------------------------ clock */
function tick() {
  const now = new Date();
  $('#dateText').textContent = now.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  $('#timeText').textContent = now.toLocaleTimeString(undefined, { hour12: false });
}
setInterval(tick, 1000); tick();

/* ------------------------------------------------------------------ orb + voice bars */
(function buildDecor() {
  const ticks = $('#ticks'); const NS = 'http://www.w3.org/2000/svg';
  for (let i = 0; i < 60; i++) {
    const a = (i / 60) * Math.PI * 2, r1 = i % 5 === 0 ? 96 : 98, r2 = 101;
    const l = document.createElementNS(NS, 'line');
    l.setAttribute('x1', 100 + Math.cos(a) * r1); l.setAttribute('y1', 100 + Math.sin(a) * r1);
    l.setAttribute('x2', 100 + Math.cos(a) * r2); l.setAttribute('y2', 100 + Math.sin(a) * r2);
    ticks.append(l);
  }
  const wave = $('#wave');
  for (let i = 0; i < 48; i++) { const b = el('i'); b.style.setProperty('--a', `${(i / 48) * 360}deg`); b.style.animationDelay = `${(i % 8) * 0.07}s`; wave.append(b); }
  const bars = $('#voiceBars');
  for (let i = 0; i < 8; i++) { const b = el('i'); b.style.animationDelay = `${i * 0.1}s`; bars.append(b); }
})();

function refreshUI() {
  let s = 'idle', title = 'Ready', hint = 'Type a command, or press Talk and just speak.', label = 'Standby';
  if (state.busy) { s = 'thinking'; title = 'Working on it'; hint = 'One moment, Sir.'; label = 'Thinking'; }
  else if (state.speaking) { s = 'speaking'; title = 'Speaking'; hint = 'Press the stop button to interrupt.'; label = 'Speaking'; }
  else if (state.rec) { s = 'listening'; title = 'Listening'; hint = 'Go ahead, I am listening.'; label = 'Listening'; }
  else if (state.handsFree) { label = 'Hands-free'; hint = 'Hands-free is on. Speak whenever you like.'; }
  $('#orb').dataset.state = s;
  $('#stateText').textContent = title; $('#stateHint').textContent = hint; $('#voiceStatus').textContent = label;
  $('#voiceBars').classList.toggle('on', s === 'listening' || s === 'speaking');
  $('#voiceBtn').classList.toggle('live', state.handsFree);
  $('#voiceBtnText').textContent = state.handsFree ? 'Stop hands-free' : 'Talk to JARVIS';
  $('#micInline').classList.toggle('live', !!state.rec);
  $('#stopSpeak').classList.toggle('hidden', !state.speaking);
}

/* ------------------------------------------------------------------ activity log */
const activity = [];
function logActivity(text, ok = true) {
  activity.unshift({ t: new Date(), text, ok });
  if (activity.length > 60) activity.pop();
  for (const sel of ['#activityLog', '#activityLogFull']) {
    const ul = $(sel); ul.innerHTML = '';
    for (const a of activity.slice(0, sel === '#activityLog' ? 12 : 60)) {
      const li = el('li', a.ok ? '' : 'bad');
      li.append(el('time', '', a.t.toLocaleTimeString(undefined, { hour12: false })), el('span', '', a.text));
      ul.append(li);
    }
  }
}

/* ------------------------------------------------------------------ chat */
const chatLog = $('#chatLog');
function addMsg(role, text, extra = '') {
  const div = el('div', `msg ${role} ${extra}`.trim());
  div.append(el('span', 'who', role === 'user' ? 'You' : 'JARVIS'));
  const p = el('div', '', text); div.append(p);
  chatLog.append(div); chatLog.scrollTop = chatLog.scrollHeight;
  return { div, p };
}
function addChips(div, names, results) {
  if (!names.length) return;
  const row = el('div', 'chips');
  names.forEach((n, i) => row.append(el('span', results[i] === false ? 'bad' : '', n)));
  div.append(row);
}

async function sendChat(message) {
  message = (message || '').trim();
  if (!message) return;
  if (state.busy) { toast('One moment, I am still working on the last request.'); return; }
  showView('command');
  stopSpeaking();
  state.busy = true; refreshUI();
  addMsg('user', message); $('#chatInput').value = '';
  const live = addMsg('bot', 'Thinking', 'typing');
  let answer = '', isErr = false; const names = [], oks = [];
  try {
    const res = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, session_id: state.sessionId }) });
    if (!res.ok || !res.body) throw new Error(`Server error ${res.status}`);
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    const handle = (ev) => {
      if (ev.type === 'status') live.p.textContent = ev.text;
      else if (ev.type === 'tool') { live.p.textContent = `Working: ${ev.name}`; names.push(ev.name); }
      else if (ev.type === 'tool_result') { oks.push(ev.ok); logActivity(`${ev.name}: ${ev.result.replace(/^ERROR: /, '')}`, ev.ok); }
      else if (ev.type === 'error') throw new Error(ev.text);
      else if (ev.type === 'final') { answer = ev.text; isErr = !!ev.error; (ev.pending || []).forEach(noteApproval); }
    };
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line) handle(JSON.parse(line)); }
    }
    if (!answer) throw new Error('No answer came back.');
  } catch (err) {
    answer = `I could not answer: ${err.message}`; isErr = true;
  }
  live.div.classList.remove('typing');
  live.p.textContent = answer;
  if (isErr) live.div.classList.add('err');
  addChips(live.div, names, oks);
  chatLog.scrollTop = chatLog.scrollHeight;
  state.busy = false; refreshUI();
  await speak(answer);
  if (state.handsFree) listenOnce();
}

$('#chatForm').addEventListener('submit', (e) => { e.preventDefault(); sendChat($('#chatInput').value); });
$('#micInline').addEventListener('click', () => { if (state.rec) state.rec.stop(); else { state.speakReplies = true; syncSpeakBtn(); listenOnce(); } });
$('#stopSpeak').addEventListener('click', () => { stopSpeaking(); if (state.handsFree) listenOnce(); });

(function suggestions() {
  const box = $('#suggest');
  ['Open Spotify', 'Play lofi on YouTube', 'Volume up', 'Take a screenshot', 'What is the weather in Sulaymaniyah?'].forEach((t) => {
    const b = el('button', '', t); b.type = 'button'; b.addEventListener('click', () => sendChat(t)); box.append(b);
  });
})();

/* ------------------------------------------------------------------ running tools directly (no AI) */
async function runTool(name, args = {}, label) {
  try {
    const d = await api('/api/tool', { name, args });
    const text = d.result.replace(/^ERROR: /, '');
    logActivity(`${label || name}: ${text}`, d.ok);
    toast(text.slice(0, 220), !d.ok);
    (d.pending || []).forEach(noteApproval);
    return d;
  } catch (err) { toast(err.message, true); logActivity(`${name}: ${err.message}`, false); return { ok: false, result: err.message }; }
}

/* ------------------------------------------------------------------ approvals */
function noteApproval(p) {
  if (state.seenPending.has(p.id)) return;
  state.seenPending.add(p.id);
  const div = el('div', 'msg bot warn'); div.id = 'ap-' + p.id;
  div.append(el('span', 'who', 'Approval needed'));
  const code = el('code', '', `${p.tool} ${JSON.stringify(p.args)}`);
  div.append(el('div', '', 'I need your approval to run: '), code);
  const row = el('div', 'approve-row');
  const ok = el('button', 'ghost ok', 'Approve'), no = el('button', 'ghost danger', 'Deny');
  ok.type = no.type = 'button';
  ok.onclick = () => decide(p.id, true); no.onclick = () => decide(p.id, false);
  row.append(ok, no); div.append(row); chatLog.append(div); chatLog.scrollTop = chatLog.scrollHeight;
  if (!state.pending.find((x) => x.id === p.id)) state.pending.push(p);
  renderPending();
}
async function decide(id, approve) {
  try {
    const d = await api('/api/approve', { id, approve });
    $('#ap-' + id)?.remove();
    logActivity(d.result.replace(/^ERROR: /, ''), !d.result.startsWith('ERROR'));
    addMsg('bot', d.result.replace(/^ERROR: /, ''), d.result.startsWith('ERROR') ? 'err' : '');
    state.pending = d.pending; renderPending();
  } catch (err) { toast(err.message, true); }
}
function renderPending() {
  const list = state.pending, badge = $('#pendingBadge');
  badge.textContent = list.length; badge.classList.toggle('hidden', !list.length);
  const box = $('#pendingList'); box.innerHTML = '';
  if (!list.length) { box.append(el('p', 'empty', 'Nothing is waiting.')); return; }
  list.forEach((p) => {
    const row = el('div', 'msg bot warn'); row.style.maxWidth = '100%';
    row.append(el('code', '', `${p.tool} ${JSON.stringify(p.args)}`));
    const btns = el('div', 'approve-row'); const a = el('button', 'ghost ok', 'Approve'), d = el('button', 'ghost danger', 'Deny');
    a.onclick = () => decide(p.id, true); d.onclick = () => decide(p.id, false); btns.append(a, d); row.append(btns); box.append(row);
  });
}
$('#denyAll').addEventListener('click', async () => {
  try { const d = await api('/api/pending/clear', {}); state.pending = []; $$('[id^="ap-"]').forEach((n) => n.remove()); renderPending(); toast(d.cleared ? `Denied ${d.cleared} waiting action(s).` : 'Nothing was waiting.'); } catch (e) { toast(e.message, true); }
});

/* ------------------------------------------------------------------ status polling */
function setRing(id, label, value) {
  let r = $('#ring-' + id);
  if (!r) {
    r = el('div', 'ring'); r.id = 'ring-' + id;
    r.innerHTML = '<svg viewBox="0 0 96 96"><circle class="bg" cx="48" cy="48" r="40"/><circle class="fg" cx="48" cy="48" r="40" stroke-dasharray="251.3" stroke-dashoffset="251.3"/></svg><b>--</b><span></span>';
    r.querySelector('span').textContent = label; $('#rings').append(r);
  }
  const fg = r.querySelector('.fg'); fg.style.strokeDashoffset = 251.3 * (1 - Math.min(100, value) / 100);
  fg.classList.toggle('hot', value >= 85); r.querySelector('b').textContent = Math.round(value) + '%';
}
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    if (!state.voiceLang) { state.voiceLang = s.voice_lang || 'en-US'; syncLangSelect(); }
    state.aiReady = s.ai_ready;
    const chip = $('#brainChip');
    chip.classList.toggle('ok', s.ai_ready && !state.aiOffline); chip.classList.toggle('bad', state.aiOffline);
    $('#brainText').textContent = state.aiOffline ? 'AI offline, direct commands still work' : s.ai_ready ? `AI online: ${s.brain}` : s.brains ? 'AI starting up...' : 'No AI brain set up';
    setRing('cpu', 'CPU', s.cpu); setRing('ram', 'Memory', s.ram); setRing('disk', 'Disk', s.disk);
    state.pending = s.pending || []; state.pending.forEach(noteApproval); renderPending();
  } catch { $('#brainText').textContent = 'JARVIS server not reachable'; $('#brainChip').classList.add('bad'); }
}
async function refreshDiagnose() {
  const banner = $('#banner');
  try {
    const d = await api('/api/diagnose'); state.aiOffline = !d.ok;
    if (!d.issues.length) { banner.classList.add('hidden'); return; }
    banner.textContent = d.issues.map((i) => i.text).join('  ');
    banner.classList.remove('hidden');
  } catch { banner.textContent = 'The JARVIS server is not running. Run start_windows.bat again.'; banner.classList.remove('hidden'); }
}
refreshStatus(); refreshDiagnose(); setInterval(refreshStatus, 3000); setInterval(refreshDiagnose, 12000);

/* ------------------------------------------------------------------ quick action tiles */
const TILES = [
  ['Spotify', 'i-music', () => runTool('open_app', { name: 'spotify' }, 'Spotify')],
  ['YouTube', 'i-video', () => runTool('open_url', { url: 'https://www.youtube.com' }, 'YouTube')],
  ['Chrome', 'i-globe', () => runTool('open_app', { name: 'chrome' }, 'Chrome')],
  ['Notepad', 'i-doc', () => runTool('open_app', { name: 'notepad' }, 'Notepad')],
  ['Calculator', 'i-calc', () => runTool('open_app', { name: 'calculator' }, 'Calculator')],
  ['Files', 'i-folder', () => runTool('open_app', { name: 'file explorer' }, 'Files')],
  ['Full size', 'i-max', () => runTool('maximize_window', {}, 'Full size')],
  ['Minimise', 'i-min', () => runTool('minimize_window', {}, 'Minimise')],
  ['Ring phone', 'i-phone', () => runTool('ring_phone', {}, 'Ring phone')],
  ['Python', 'i-code', () => runTool('execute_python', { code: 'import sys; print(f"Python {sys.version.split()[0]} ready")' }, 'Python')],
  ['Screenshot', 'i-shot', () => runTool('take_screenshot', {}, 'Screenshot')],
  ['Volume up', 'i-vol', () => runTool('media_control', { action: 'volume_up', times: 6 }, 'Volume up')],
  ['Volume down', 'i-vol', () => runTool('media_control', { action: 'volume_down', times: 6 }, 'Volume down')],
];
TILES.forEach(([label, icon, fn]) => {
  const b = el('button', 'tile'); b.type = 'button'; b.append(svgIcon(icon), el('span', '', label));
  b.addEventListener('click', async () => { b.classList.add('busy'); await fn(); b.classList.remove('busy'); });
  $('#tiles').append(b);
});

/* ------------------------------------------------------------------ views */
const loaders = {
  ai: () => loadBrains(),
  agents: () => loadTools(),
  memory: () => loadFacts(),
  history: () => loadHistorySessions(),
  devices: () => { loadDevices(); refreshTunnelStatus(); loadWolInfo(); },
  security: () => renderPending()
};
function showView(name) {
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + name));
  $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  loaders[name]?.();
}
$$('.nav-btn').forEach((b) => b.addEventListener('click', () => showView(b.dataset.view)));

/* ------------------------------------------------------------------ AI core: brains */
let brainData = { brains: [], presets: [] };
async function loadBrains() {
  try { brainData = await api('/api/settings'); renderBrains(); } catch (e) { toast(e.message, true); }
}
function guessPreset(b) {
  const same = brainData.presets.find((p) => p.kind === b.kind && p.base_url && b.base_url && p.base_url.replace(/\/$/, '') === b.base_url.replace(/\/$/, ''));
  return same?.key || (b.kind === 'ollama' ? 'ollama' : 'custom');
}
function readBrain(card) {
  const f = (n) => $(`[name=${n}]`, card).value.trim();
  return { id: card.dataset.id, label: f('label'), kind: card.dataset.kind, base_url: f('base_url'), model: f('model'), api_key: f('api_key') };
}
function collectBrains() { return $$('.brain').map(readBrain); }
function brainCard(b, i, total) {
  const card = el('section', 'panel brain'); card.dataset.id = b.id || ''; card.dataset.kind = b.kind;
  const presetKey = guessPreset(b);
  const rank = el('div', 'rank'); const up = el('button', '', '▲'), dn = el('button', '', '▼'); up.type = dn.type = 'button';
  up.title = 'Try this brain earlier'; dn.title = 'Try this brain later'; up.disabled = i === 0; dn.disabled = i === total - 1;
  rank.append(up, el('b', '', String(i + 1)), dn);
  const body = el('div');
  const grid = el('div', 'form-grid');
  const field = (labelText, name, value, type = 'text', ph = '') => {
    const l = el('label', '', labelText); const inp = el('input'); inp.name = name; inp.type = type; inp.value = value || ''; inp.placeholder = ph; inp.autocomplete = 'off'; l.append(inp); return l;
  };
  const presetLabel = el('label', '', 'Service'); const sel = el('select'); sel.name = 'preset';
  brainData.presets.forEach((p) => sel.add(new Option(p.label, p.key))); sel.value = presetKey; presetLabel.append(sel);
  const dl = el('datalist'); dl.id = 'models-' + i;
  grid.append(presetLabel, field('Name', 'label', b.label), field('Address', 'base_url', b.base_url));
  const modelLabel = field('Model', 'model', b.model); $('input', modelLabel).setAttribute('list', dl.id); modelLabel.append(dl); grid.append(modelLabel);
  grid.append(field('API key', 'api_key', '', 'password', b.has_key ? 'Saved. Blank keeps it.' : 'Paste your free key here'));
  const foot = el('div', 'brain-foot');
  const test = el('button', 'ghost', 'Test this brain'), rm = el('button', 'ghost danger', 'Remove'); test.type = rm.type = 'button';
  const out = el('span', 'test-out'); const help = el('span', 'help');
  foot.append(test, rm, out, help); body.append(grid, foot); card.append(rank, body);
  const applyPreset = (key, overwrite) => {
    const p = brainData.presets.find((x) => x.key === key); if (!p) return;
    if (overwrite) card.dataset.kind = p.kind;
    help.textContent = p.help; dl.innerHTML = ''; p.models.forEach((m) => dl.append(new Option(m)));
    $('[name=api_key]', card).closest('label').style.display = p.needs_key ? '' : 'none';
    if (overwrite) { $('[name=base_url]', card).value = p.base_url; $('[name=model]', card).value = p.model; if (key !== 'custom') $('[name=label]', card).value = p.label.replace(/ \(.*\)/, ''); }
  };
  applyPreset(presetKey, false);
  sel.addEventListener('change', () => applyPreset(sel.value, true));
  const move = (d) => { brainData.brains = collectBrains().map((x, idx) => ({ ...brainData.brains[idx], ...x })); const j = i + d; [brainData.brains[i], brainData.brains[j]] = [brainData.brains[j], brainData.brains[i]]; renderBrains(); };
  up.onclick = () => move(-1); dn.onclick = () => move(1);
  rm.onclick = () => { brainData.brains = collectBrains().filter((_, idx) => idx !== i); renderBrains(); };
  test.onclick = async () => {
    out.className = 'test-out'; out.textContent = 'Testing...'; test.disabled = true;
    try { const r = await api('/api/brain/test', readBrain(card)); out.className = 'test-out ' + (r.ok ? 'ok' : 'bad'); out.textContent = r.ok ? `Works (${r.ms} ms): ${r.text}` : r.text; }
    catch (e) { out.className = 'test-out bad'; out.textContent = e.message; }
    test.disabled = false;
  };
  return card;
}
function renderBrains() {
  const list = $('#brainList'); list.innerHTML = '';
  if (!brainData.brains.length) list.append(el('p', 'empty', 'No brains yet. Press "Add a brain" to set up a free one.'));
  brainData.brains.forEach((b, i) => list.append(brainCard(b, i, brainData.brains.length)));
}
$('#addBrain').addEventListener('click', () => {
  brainData.brains = collectBrains().map((x, idx) => ({ ...brainData.brains[idx], ...x }));
  const p = brainData.presets.find((x) => x.key === 'groq') || brainData.presets[0];
  brainData.brains.push({ id: '', label: 'Groq', kind: p.kind, base_url: p.base_url, model: p.model, has_key: false }); renderBrains();
  $('#brainList').lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'center' });
});
$('#saveBrains').addEventListener('click', async () => {
  const brains = collectBrains();
  if (brains.some((b) => !b.model || !b.base_url)) { toast('Every brain needs an address and a model name.', true); return; }
  try {
    const d = await api('/api/settings', { brains, voice_lang: state.voiceLang });
    brainData = { ...brainData, ...d }; renderBrains(); toast('Saved. JARVIS will try these brains from top to bottom.');
    refreshStatus(); refreshDiagnose();
  } catch (e) { toast(e.message, true); }
});

/* ------------------------------------------------------------------ agents & tools */
let allTools = [];
async function loadTools() {
  try { allTools = (await api('/api/tools')).tools; } catch (e) { toast(e.message, true); return; }
  const box = $('#toolGroups'); box.innerHTML = '';
  const groups = {}; allTools.forEach((t) => (groups[t.category] ||= []).push(t));
  Object.entries(groups).forEach(([cat, items]) => {
    const g = el('div', 'group'); g.append(el('h3', '', cat)); const grid = el('div', 'tool-grid');
    items.forEach((t) => {
      const card = el('div', 'tool' + (t.risky ? ' risky' : '')); card.append(el('b', '', t.name), el('p', '', t.description.replace(' (REQUIRES USER APPROVAL)', '')));
      const b = el('button', 'ghost', t.risky ? 'Request' : 'Try'); b.type = 'button'; b.onclick = () => openToolModal(t); card.append(b); grid.append(card);
    });
    g.append(grid); box.append(g);
  });
}
let currentTool = null;
function openToolModal(t) {
  currentTool = t; $('#toolTitle').textContent = t.name; $('#toolDesc').textContent = t.description;
  const f = $('#toolFields'); f.innerHTML = ''; $('#toolResult').classList.add('hidden');
  Object.entries(t.params).forEach(([name, spec]) => {
    const isBool = spec.type === 'boolean';
    const l = el('label', isBool ? 'check' : '', isBool ? spec.description || name : `${name}${t.required.includes(name) ? ' (required)' : ''}: ${spec.description}`);
    const i = el('input'); i.name = name; i.type = isBool ? 'checkbox' : (spec.type === 'integer' || spec.type === 'number') ? 'number' : 'text'; i.autocomplete = 'off';
    isBool ? l.prepend(i) : l.append(i); f.append(l);
  });
  $('#toolModal').classList.remove('hidden'); $('input', f)?.focus();
}
$('#toolForm').addEventListener('submit', async (e) => {
  e.preventDefault(); const t = currentTool; if (!t) return;
  const args = {};
  $$('#toolFields input').forEach((i) => {
    if (i.type === 'checkbox') { if (i.checked) args[i.name] = true; }
    else if (i.value !== '') args[i.name] = i.type === 'number' ? Number(i.value) : i.value;
  });
  const missing = t.required.filter((r) => args[r] === undefined);
  if (missing.length) { toast(`Fill in: ${missing.join(', ')}`, true); return; }
  const out = $('#toolResult'); out.classList.remove('hidden'); out.textContent = 'Running...';
  const d = await runTool(t.name, args); out.textContent = d.result;
});

/* ------------------------------------------------------------------ memory */
async function loadFacts() {
  try {
    const d = await api('/api/memory'); const ul = $('#factList'); ul.innerHTML = '';
    if (!d.facts.length) ul.append(el('li', 'empty', 'Nothing yet. Say "remember that I study Flutter" or add a note above.'));
    d.facts.forEach((f) => { const li = el('li'); li.append(el('span', '', f)); const b = el('button', '', 'Forget'); b.onclick = async () => { await api('/api/memory/forget', { keyword: f }); loadFacts(); }; li.append(b); ul.append(li); });
  } catch (e) { toast(e.message, true); }
}
$('#factForm').addEventListener('submit', async (e) => {
  e.preventDefault(); const v = $('#factInput').value.trim(); if (!v) return;
  try { await api('/api/memory', { fact: v }); $('#factInput').value = ''; loadFacts(); toast('Remembered.'); } catch (err) { toast(err.message, true); }
});
$('#clearHistory').addEventListener('click', async () => {
  try { await api('/api/history/clear', { session_id: state.sessionId }); chatLog.innerHTML = ''; greet(); toast('Chat history cleared.'); } catch (e) { toast(e.message, true); }
});

/* ------------------------------------------------------------------ media */
$$('[data-media]').forEach((b) => b.addEventListener('click', () => runTool('media_control', { action: b.dataset.media }, b.title || b.textContent.trim() || b.dataset.media)));
$('#spotifyForm').addEventListener('submit', (e) => { e.preventDefault(); const q = $('#spotifyInput').value.trim(); if (q) runTool('spotify_search', { query: q }, 'Spotify'); else toast('Type a song, artist or playlist first.', true); });
$('#youtubeForm').addEventListener('submit', (e) => { e.preventDefault(); const q = $('#youtubeInput').value.trim(); if (q) runTool('youtube_play', { query: q }, 'YouTube'); else toast('Type what you want to watch first.', true); });
$('#youtubeSearchBtn').addEventListener('click', () => { const q = $('#youtubeInput').value.trim(); if (q) runTool('youtube_search', { query: q }, 'YouTube'); else toast('Type what you want to watch first.', true); });
$('#openSpotify').addEventListener('click', () => runTool('open_app', { name: 'spotify' }, 'Spotify'));
$('#openYoutube').addEventListener('click', () => runTool('open_url', { url: 'https://www.youtube.com' }, 'YouTube'));

/* ------------------------------------------------------------------ nearby devices & phone */
let devicesData = { local_ip: '', phone_url: '', password: '1234', connected_phones: [], nearby_devices: [] };

async function loadDevices() {
  try {
    devicesData = await api('/api/devices');
    const link = $('#phonePortalLink');
    if (link) {
      link.href = devicesData.phone_url;
      link.textContent = devicesData.phone_url;
    }
    const pwdInp = $('#passcodeInput');
    if (pwdInp) pwdInp.value = devicesData.password;
    renderConnectedPhones();
    renderNearbyDevices();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderConnectedPhones() {
  const box = $('#connectedPhonesList');
  if (!box) return;
  box.innerHTML = '';
  const phones = devicesData.connected_phones || [];
  const badge = $('#phoneBadge');
  if (badge) {
    badge.textContent = phones.length;
    badge.classList.toggle('hidden', !phones.length);
  }
  if (!phones.length) {
    box.append(el('p', 'empty', 'No phone currently connected. Open the portal link above on your phone and enter the passcode.'));
    return;
  }
  phones.forEach((p) => {
    const row = el('div', 'msg bot');
    row.style.maxWidth = '100%';
    const head = el('div', '', `${p.name} (${p.type || 'Phone'}) - IP: ${p.ip}`);
    const meta = el('small', 'muted', `Battery: ${p.battery !== null ? p.battery + '%' : 'unknown'} | Active: ${p.last_seen_sec}s ago`);
    const btn = el('button', 'mini', 'Ring phone');
    btn.type = 'button';
    btn.style.marginLeft = 'auto';
    btn.onclick = () => ringPhone();
    const flex = el('div', 'row');
    flex.append(head, btn);
    row.append(flex, meta);
    box.append(row);
  });
}

function renderNearbyDevices() {
  const box = $('#nearbyDevicesList');
  if (!box) return;
  box.innerHTML = '';
  const devs = devicesData.nearby_devices || [];
  if (!devs.length) {
    box.append(el('p', 'empty', 'No nearby devices found yet. Press "Scan network" above.'));
    return;
  }
  const table = el('table', 'dev-table');
  table.innerHTML = '<thead><tr><th>Device / Host</th><th>Type</th><th>IP Address</th><th>Status</th></tr></thead>';
  const tbody = el('tbody');
  devs.forEach((d) => {
    const tr = el('tr');
    tr.innerHTML = `<td><b>${d.hostname || 'Device'}</b></td><td>${d.type || 'Unknown'}</td><td><code>${d.ip}</code></td><td><span class="dot-ok">${d.status}</span></td>`;
    tbody.append(tr);
  });
  table.append(tbody);
  box.append(table);
}

async function scanDevices() {
  toast('Scanning local network for nearby devices...');
  try {
    const d = await api('/api/devices/scan', {});
    devicesData.nearby_devices = d.nearby_devices;
    devicesData.connected_phones = d.connected_phones;
    renderConnectedPhones();
    renderNearbyDevices();
    toast(`Found ${d.nearby_devices.length} nearby device(s).`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function ringPhone() {
  try {
    const d = await api('/api/devices/ring', {});
    toast(d.alerted_phones > 0 ? `Ringing ${d.alerted_phones} connected phone(s)!` : 'Alarm queued for phone.');
  } catch (e) {
    toast(e.message, true);
  }
}

$('#scanDevicesBtn')?.addEventListener('click', scanDevices);
$('#ringPhoneBtn')?.addEventListener('click', ringPhone);
$('#copyPortalLink')?.addEventListener('click', () => {
  const url = $('#phonePortalLink')?.href;
  if (url) {
    navigator.clipboard?.writeText(url);
    toast('Copied phone portal URL to clipboard!');
  }
});
$('#passcodeForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const v = $('#passcodeInput')?.value.trim();
  if (!v) return toast('Passcode cannot be empty.', true);
  try {
    const d = await api('/api/devices/password', { password: v });
    toast(`Passcode updated to "${d.password}". Use this to connect phones.`);
  } catch (err) {
    toast(err.message, true);
  }
});
$('#sendClipPhoneBtn')?.addEventListener('click', () => runTool('sync_clipboard_with_phone', { direction: 'to_phone' }, 'Clipboard to phone'));
$('#sendNotifyPhoneBtn')?.addEventListener('click', async () => {
  try {
    await api('/api/devices/send', { title: 'JARVIS Command Center', message: 'Hello from your PC! Device link is active and synchronized.' });
    toast('Sent test notification to phone!');
  } catch (e) {
    toast(e.message, true);
  }
});

/* ------------------------------------------------------------------ Chat History & Multi-Session */
let currentHistorySession = state.sessionId;

async function loadHistorySessions() {
  const list = $('#sessionList');
  if (!list) return;
  list.innerHTML = '<li class="empty">Loading chat history...</li>';
  try {
    const data = await api('/api/history/sessions');
    const sessions = data.sessions || [];
    list.innerHTML = '';
    if (!sessions.length) {
      list.innerHTML = '<li class="empty">No conversations found.</li>';
      return;
    }
    sessions.forEach((s) => {
      const li = el('li', 'history-item');
      if (s.id === currentHistorySession) li.classList.add('active');
      const title = el('div', 'history-title', s.title || `Chat ${s.id.slice(0, 8)}`);
      const meta = el('div', 'history-meta');
      const timeStr = s.updated_at ? new Date(s.updated_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
      meta.append(el('span', '', `${s.turn_count || 0} turns`), el('span', '', timeStr));
      li.append(title, meta);
      li.onclick = () => {
        $$('.history-item').forEach((item) => item.classList.remove('active'));
        li.classList.add('active');
        selectSession(s.id, s.title);
      };
      list.append(li);
    });

    if (sessions.some(s => s.id === currentHistorySession)) {
      selectSession(currentHistorySession);
    } else if (sessions.length) {
      selectSession(sessions[0].id, sessions[0].title);
    }
  } catch (err) {
    list.innerHTML = `<li class="empty">Failed to load history: ${err.message}</li>`;
  }
}

async function selectSession(sessionId, title = '') {
  currentHistorySession = sessionId;
  const msgBox = $('#sessionMessages');
  if (!msgBox) return;
  msgBox.innerHTML = '<p class="empty">Loading messages...</p>';
  try {
    const data = await api(`/api/history/session/${encodeURIComponent(sessionId)}`);
    const msgs = data.messages || [];
    $('#currentSessionTitle').textContent = title || `Chat ${sessionId.slice(0, 10)}`;
    $('#currentSessionMeta').textContent = `${msgs.length} message(s) • ${sessionId === state.sessionId ? 'Active conversation' : 'Archived'}`;
    msgBox.innerHTML = '';
    if (!msgs.length) {
      msgBox.innerHTML = '<p class="empty">No messages recorded in this session.</p>';
      return;
    }
    msgs.forEach((m) => {
      const div = el('div', `msg ${m.role}`);
      div.append(el('span', 'who', m.role === 'user' ? 'You' : 'JARVIS'));
      div.append(el('div', '', m.content));
      msgBox.append(div);
    });
    msgBox.scrollTop = msgBox.scrollHeight;
  } catch (err) {
    msgBox.innerHTML = `<p class="empty">Error: ${err.message}</p>`;
  }
}

$('#btnNewSession')?.addEventListener('click', async () => {
  try {
    const data = await api('/api/history/session/new', { title: 'New Chat' });
    state.sessionId = data.id;
    LS.set('jarvis_session_id', state.sessionId);
    chatLog.innerHTML = '';
    greet();
    toast('Started fresh chat session.');
    showView('command');
  } catch (err) {
    toast(err.message, true);
  }
});

$('#btnSwitchToSession')?.addEventListener('click', async () => {
  if (!currentHistorySession) return;
  state.sessionId = currentHistorySession;
  LS.set('jarvis_session_id', state.sessionId);
  try {
    const data = await api(`/api/history/session/${encodeURIComponent(state.sessionId)}`);
    chatLog.innerHTML = '';
    (data.messages || []).forEach(m => addMsg(m.role, m.content));
    toast('Switched active conversation!');
    showView('command');
  } catch (err) {
    toast('Switched to session ' + currentHistorySession);
  }
});

$('#btnDeleteSession')?.addEventListener('click', async () => {
  if (!currentHistorySession) return;
  if (!confirm('Are you sure you want to delete this chat session?')) return;
  try {
    await fetch(`/api/history/session/${encodeURIComponent(currentHistorySession)}`, { method: 'DELETE' });
    toast('Deleted conversation session.');
    if (currentHistorySession === state.sessionId) {
      state.sessionId = String(Date.now());
      LS.set('jarvis_session_id', state.sessionId);
      chatLog.innerHTML = '';
      greet();
    }
    loadHistorySessions();
  } catch (err) {
    toast(err.message, true);
  }
});

$('#btnHistorySearch')?.addEventListener('click', async () => {
  const q = $('#historySearchInput')?.value.trim();
  if (!q) return loadHistorySessions();
  const list = $('#sessionList');
  list.innerHTML = '<li class="empty">Searching...</li>';
  try {
    const res = await fetch(`/api/history/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    list.innerHTML = '';
    const msgs = data.messages || [];
    const facts = data.facts || [];
    if (!msgs.length && !facts.length) {
      list.innerHTML = `<li class="empty">No results found for "${q}".</li>`;
      return;
    }
    if (facts.length) {
      const header = el('li', '', `Learned Facts (${facts.length})`);
      header.style.color = 'var(--cyan)';
      header.style.fontWeight = 'bold';
      header.style.fontSize = '12px';
      list.append(header);
      facts.forEach(f => {
        const li = el('li', 'history-item');
        li.innerHTML = `<div class="history-title">${f.fact}</div><div class="history-meta"><span>Category: ${f.category}</span></div>`;
        list.append(li);
      });
    }
    if (msgs.length) {
      const header = el('li', '', `Messages (${msgs.length})`);
      header.style.color = 'var(--cyan)';
      header.style.fontWeight = 'bold';
      header.style.fontSize = '12px';
      list.append(header);
      msgs.forEach(m => {
        const li = el('li', 'history-item');
        li.innerHTML = `<div class="history-title">${m.content.slice(0, 60)}...</div><div class="history-meta"><span>${m.session_title || m.session}</span><span>${m.role}</span></div>`;
        li.onclick = () => selectSession(m.session, m.session_title);
        list.append(li);
      });
    }
  } catch (err) {
    list.innerHTML = `<li class="empty">Search error: ${err.message}</li>`;
  }
});

/* ------------------------------------------------------------------ Remote Tunnel & WoL */
let tunnelState = { active: false, url: '' };

async function refreshTunnelStatus() {
  try {
    const res = await fetch('/api/remote/status');
    const data = await res.json();
    tunnelState = data;
    const badge = $('#remoteTunnelBadge');
    const link = $('#remoteTunnelLink');
    const btn = $('#btnToggleTunnel');
    if (badge) {
      badge.className = 'tunnel-status-badge ' + (data.active ? 'active' : 'stopped');
      badge.textContent = data.active ? '● Online (HTTPS)' : '● Offline';
    }
    if (link) {
      if (data.active && data.url) {
        link.href = data.url + '/phone';
        link.textContent = data.url + '/phone';
      } else {
        link.href = '#';
        link.textContent = 'Tunnel not started';
      }
    }
    if (btn) {
      btn.textContent = data.active ? 'Stop Remote Tunnel' : 'Start Remote Tunnel';
    }
  } catch (err) {}
}

async function toggleRemoteTunnel() {
  const btn = $('#btnToggleTunnel');
  btn.disabled = true;
  try {
    if (tunnelState.active) {
      toast('Stopping remote tunnel...');
      await api('/api/remote/stop', {});
      toast('Remote tunnel stopped.');
    } else {
      toast('Starting secure reverse SSH tunnel (pinggy.io)...');
      const d = await api('/api/remote/start', { provider: 'pinggy' });
      if (d.active && d.url) {
        toast('Remote tunnel active! Open URL on your phone.');
      } else {
        toast('Tunnel launched. Waiting for URL...', false);
      }
    }
    await refreshTunnelStatus();
  } catch (err) {
    toast('Tunnel error: ' + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

$('#btnToggleTunnel')?.addEventListener('click', toggleRemoteTunnel);
$('#copyRemoteTunnelLink')?.addEventListener('click', () => {
  const url = $('#remoteTunnelLink')?.href;
  if (url && url !== '#') {
    navigator.clipboard?.writeText(url);
    toast('Copied Remote Tunnel URL to clipboard!');
  } else {
    toast('Remote tunnel is not active yet.', true);
  }
});
$('#btnOpenGitHubGuide')?.addEventListener('click', () => {
  $('#githubModal')?.classList.remove('hidden');
});

async function loadWolInfo() {
  try {
    const data = await api('/api/phone/wol_info');
    const display = $('#wolInfoDisplay');
    if (display) {
      display.innerHTML = `Host: <b>${data.pc_name}</b><br>MAC Address: <span style="color:var(--cyan)">${data.mac_address}</span><br>Broadcast IP: ${data.broadcast_ip} (Port 9)`;
    }
  } catch (err) {}
}

$('#btnTestWol')?.addEventListener('click', async () => {
  try {
    const res = await api('/api/phone/wol_wake', {});
    toast(res.message || 'Wake-on-LAN magic packet transmitted!');
  } catch (err) {
    toast(err.message, true);
  }
});

$('#btnSleepPcDash')?.addEventListener('click', async () => {
  if (!confirm('Put your PC to sleep now? You can wake it anytime from your phone using Wake-on-LAN.')) return;
  try {
    await api('/api/phone/sleep', {});
    toast('PC entering sleep mode.');
  } catch (err) {
    toast(err.message, true);
  }
});

/* ------------------------------------------------------------------ top bar: camera, location, search */
async function cameraStart() {
  $('#cameraModal').classList.remove('hidden'); $('#cameraStatus').textContent = 'Waiting for permission...';
  try {
    state.cam = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    $('#cameraVideo').srcObject = state.cam; $('#cameraStatus').textContent = 'Camera is on. Close this window to turn it off.';
  } catch (e) { $('#cameraStatus').textContent = `Camera unavailable: ${e.message}. Allow the camera in the address bar.`; }
}
function cameraStop() { state.cam?.getTracks().forEach((t) => t.stop()); state.cam = null; $('#cameraVideo').srcObject = null; }
$('#cameraBtn').addEventListener('click', cameraStart);
$('#locationBtn').addEventListener('click', () => {
  if (!navigator.geolocation) return toast('This browser has no location support.', true);
  toast('Asking for location permission...');
  navigator.geolocation.getCurrentPosition(
    (p) => { const t = `Location: ${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)} (within ${Math.round(p.coords.accuracy)} m)`; toast(t); logActivity(t); addMsg('bot', t); },
    (e) => toast(`Location unavailable: ${e.message}`, true), { enableHighAccuracy: true, timeout: 12000 });
});
$('#searchBtn').addEventListener('click', () => { $('#searchModal').classList.remove('hidden'); $('#searchInput').focus(); });
$('#searchForm').addEventListener('submit', (e) => { e.preventDefault(); const q = $('#searchInput').value.trim(); if (!q) return; closeModals(); sendChat(`Search the web for ${q} and tell me the answer briefly`); $('#searchInput').value = ''; });
$('#searchGoogle').addEventListener('click', () => { const q = $('#searchInput').value.trim(); if (!q) return toast('Type something to search for.', true); closeModals(); runTool('open_url', { url: 'https://www.google.com/search?q=' + encodeURIComponent(q) }, 'Google'); $('#searchInput').value = ''; });
function closeModals() { $$('.modal').forEach((m) => m.classList.add('hidden')); cameraStop(); }
$$('[data-close]').forEach((b) => b.addEventListener('click', closeModals));
$$('.modal').forEach((m) => m.addEventListener('mousedown', (e) => { if (e.target === m) closeModals(); }));

/* ------------------------------------------------------------------ voice out */
const LANGS = [['en-US', 'English'], ['ar-IQ', 'Arabic (Iraq)'], ['ar-SA', 'Arabic'], ['ckb-IQ', 'Kurdish (Sorani)'], ['tr-TR', 'Turkish'], ['fa-IR', 'Persian']];
function syncLangSelect() {
  const sel = $('#langSelect');
  if (!sel.options.length) { LANGS.forEach(([v, n]) => sel.add(new Option(n, v))); sel.addEventListener('change', () => { state.voiceLang = sel.value; LS.set('jarvis_lang', sel.value); }); }
  sel.value = state.voiceLang || 'en-US';
}
function pickVoice(lang) {
  const voices = speechSynthesis.getVoices(), base = lang.split('-')[0];
  const pick = (l) => voices.find((v) => v.lang === l) || voices.find((v) => v.lang.toLowerCase().startsWith(l.split('-')[0].toLowerCase()));
  return pick(lang) || (base === 'ckb' ? pick('ar-SA') : null) || null;
}
function stopSpeaking() { if (window.speechSynthesis) speechSynthesis.cancel(); state.speaking = false; refreshUI(); }
function speak(text) {
  return new Promise((resolve) => {
    if (!state.speakReplies || !window.speechSynthesis || !text) return resolve();
    speechSynthesis.cancel();
    const parts = (text.match(/[^.!?؟۔\n]+[.!?؟۔]*/g) || [text]).map((p) => p.trim()).filter(Boolean);
    const voice = pickVoice(state.voiceLang || 'en-US');
    state.speaking = true; refreshUI();
    const done = () => { state.speaking = false; refreshUI(); resolve(); };
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p); u.lang = voice?.lang || state.voiceLang || 'en-US'; if (voice) u.voice = voice; u.rate = 1.05;
      if (i === parts.length - 1) { u.onend = done; u.onerror = done; }
      speechSynthesis.speak(u);
    });
  });
}
function syncSpeakBtn() { const b = $('#speakToggle'); b.textContent = state.speakReplies ? 'Voice replies: on' : 'Voice replies: off'; b.classList.toggle('on', state.speakReplies); b.setAttribute('aria-pressed', state.speakReplies); }
$('#speakToggle').addEventListener('click', () => { state.speakReplies = !state.speakReplies; LS.set('jarvis_speak', state.speakReplies ? '1' : '0'); if (!state.speakReplies) stopSpeaking(); syncSpeakBtn(); });
$('#testVoice').addEventListener('click', () => {
  if (!window.speechSynthesis) return toast('This browser cannot speak. Use Chrome or Edge.', true);
  const was = state.speakReplies; state.speakReplies = true;
  if (!pickVoice(state.voiceLang || 'en-US')) toast('No voice found for this language, using the default voice.');
  speak('Hello Sir. I can talk to you, and I am ready.').then(() => { state.speakReplies = was; });
});

/* ------------------------------------------------------------------ voice in (hands-free conversation) */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function listenOnce() {
  if (!SR) { toast('Voice input needs Chrome or Edge. You can still type.', true); state.handsFree = false; refreshUI(); return; }
  if (state.rec || state.busy || state.speaking) return;
  const rec = new SR(); rec.lang = state.voiceLang || 'en-US'; rec.interimResults = false; let heard = false;
  rec.onstart = () => { state.rec = rec; refreshUI(); };
  rec.onresult = (e) => { heard = true; const text = e.results[0][0].transcript.trim(); if (text) sendChat(text); };
  rec.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { toast('The microphone is blocked. Click the lock icon in the address bar and allow it.', true); state.handsFree = false; }
    else if (e.error === 'language-not-supported') { toast('This browser cannot recognise that language. Pick another one.', true); state.handsFree = false; }
  };
  rec.onend = () => { state.rec = null; refreshUI(); if (state.handsFree && !heard && !state.busy && !state.speaking) setTimeout(listenOnce, 400); };
  try { rec.start(); } catch { state.rec = null; }
}
$('#voiceBtn').addEventListener('click', () => {
  state.handsFree = !state.handsFree;
  if (state.handsFree) { state.speakReplies = true; LS.set('jarvis_speak', '1'); syncSpeakBtn(); listenOnce(); }
  else { state.rec?.abort(); stopSpeaking(); }
  refreshUI();
});

/* ------------------------------------------------------------------ focus mode, shortcuts, start-up */
$('#focusToggle').addEventListener('click', (e) => {
  const on = !document.body.classList.contains('focus'); document.body.classList.toggle('focus', on);
  e.currentTarget.textContent = on ? 'On' : 'Off'; e.currentTarget.classList.toggle('on', on); e.currentTarget.setAttribute('aria-pressed', on);
  toast(on ? 'Focus mode on: side panels hidden.' : 'Focus mode off.');
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeModals(); stopSpeaking(); }
  if (e.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) { e.preventDefault(); showView('command'); $('#chatInput').focus(); }
});
function greet() { addMsg('bot', 'Good day, Sir. Tell me what to do, for example: "open Spotify", "volume up" or "play lofi on YouTube".'); }
syncLangSelect(); syncSpeakBtn(); refreshUI(); greet();
