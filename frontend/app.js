'use strict';
/* JARVIS command center v5. Low-latency touch controls, truthful device status, improved Sorani prompts. */

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
  wakeWordEnabled: LS.get('jarvis_wake_word', '1') === '1',
  isSleeping: false,
  handsFree: false, busy: false, speaking: false, rec: null, aiReady: false, aiOffline: false,
  pending: [], seenPending: new Set(),
  cam: null, camActive: false, camMicActive: true
};
LS.set('jarvis_session_id', state.sessionId);

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

async function api(path, body, method) {
  const opts = body === undefined && !method
    ? {}
    : { method: method || 'POST', headers: { 'Content-Type': 'application/json' }, body: body !== undefined ? JSON.stringify(body) : undefined };
  const res = await fetch(path, opts);
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
  if (state.isSleeping) {
    s = 'sleep';
    title = '💤 Sleep Mode (Standby)';
    hint = 'Say "Wake up JARVIS" or press Wake Up to bring me online.';
    label = 'Sleeping';
  } else if (state.busy) {
    s = 'thinking'; title = 'Working on it'; hint = 'One moment, Sir.'; label = 'Thinking';
  } else if (state.speaking) {
    s = 'speaking'; title = 'Speaking'; hint = 'Press the stop button to interrupt.'; label = 'Speaking';
  } else if (state.rec) {
    s = 'listening'; title = 'Listening'; hint = 'Go ahead, I am listening.'; label = 'Listening';
  } else if (state.handsFree) {
    label = 'Hands-free'; hint = 'Hands-free is on. Speak whenever you like.';
  }
  document.body.classList.toggle('jarvis-sleeping', state.isSleeping);
  $('#orb').dataset.state = s;
  $('#stateText').textContent = title; $('#stateHint').textContent = hint; $('#voiceStatus').textContent = label;
  $('#voiceBars').classList.toggle('on', s === 'listening' || s === 'speaking');
  $('#voiceBtn').classList.toggle('live', state.handsFree);
  $('#voiceBtnText').textContent = state.isSleeping ? 'Wake Up JARVIS' : (state.handsFree ? 'Stop hands-free' : 'Talk to JARVIS');
  $('#micInline').classList.toggle('live', !!state.rec);
  $('#stopSpeak').classList.toggle('hidden', !state.speaking);
  const wakeToggle = $('#wakeWordToggle');
  if (wakeToggle) {
    wakeToggle.textContent = state.wakeWordEnabled ? 'Armed' : 'Off';
    wakeToggle.classList.toggle('on', state.wakeWordEnabled);
    wakeToggle.setAttribute('aria-pressed', state.wakeWordEnabled);
  }
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

/* ------------------------------------------------------------------ Web Audio Synthesizer */
let audioCtx = null;
function getAudioContext() {
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) audioCtx = new AC();
  }
  if (audioCtx && audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

function playWakeChime() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    const freqs = [440, 659.25, 880, 1174.66];
    freqs.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.08);
      gain.gain.setValueAtTime(0, now + idx * 0.08);
      gain.gain.linearRampToValueAtTime(0.2, now + idx * 0.08 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.08 + 0.45);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + idx * 0.08);
      osc.stop(now + idx * 0.08 + 0.5);
    });
  } catch {}
}

function playSleepChime() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    const freqs = [880, 587.33, 440, 293.66];
    freqs.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.1);
      gain.gain.setValueAtTime(0, now + idx * 0.1);
      gain.gain.linearRampToValueAtTime(0.18, now + idx * 0.1 + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.1 + 0.55);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + idx * 0.1);
      osc.stop(now + idx * 0.1 + 0.6);
    });
  } catch {}
}

function playBlipSound() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(987.77, now);
    osc.frequency.exponentialRampToValueAtTime(1318.51, now + 0.07);
    gain.gain.setValueAtTime(0.15, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.09);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.1);
  } catch {}
}

function playVisionScanSound() {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(500, now);
    osc.frequency.exponentialRampToValueAtTime(1800, now + 0.22);
    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.25);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.26);
  } catch {}
}

/* ------------------------------------------------------------------ Phrase matchers */
function normalizePhrase(text) {
  return (text || '').toLowerCase().replace(/[.,!?;:ـ\-_]/g, ' ').replace(/\s+/g, ' ').trim();
}

function isWakePhrase(raw) {
  const t = normalizePhrase(raw);
  if (!t) return false;
  if (/\b(wake\s*up(\s+jarvis|\s+buddy)?|jarvis\s+wake\s*up|wake\s+jarvis|boot\s*up(\s+jarvis)?|online\s+jarvis|hey\s+jarvis|hi\s+jarvis|jarvis\s+online|turn\s+on\s+jarvis)\b/i.test(t)) return true;
  if (/(?:هەڵسە|هەستە|بەخەبەر\s*وەرە|بەخەبەر\s*بێ|وەخەبەر\s*وەرە|جاڕڤس\s*هەڵسە|جارڤیس\s*هەڵسە|هەڵسە\s*جاڕڤس)(?:\s+جارڤیس|\s+جاڕڤس)?/i.test(t)) return true;
  if (/(?:استيقظ|اصحى|استفق)(?:\s+جارفيس|\s+جارفس)?/i.test(t)) return true;
  if (/\b(wek\s*up|waik\s*up|wake\s*ap|jarves\s*wake)\b/i.test(t)) return true;
  return false;
}

function isSleepPhrase(raw) {
  const t = normalizePhrase(raw);
  if (!t) return false;
  if (/\b(good\s*bye|goodby|good\s*by|bye\s+jarvis|go\s+to\s+sleep|sleep\s+jarvis|sleep\s+mode|shut\s*down\s+jarvis|rest\s+jarvis)\b/i.test(t)) return true;
  if (/(?:خوات\s*لەگەڵ|بخەوە|دەتوانیت\s*بخەویت|تەواو\s*بخەوە)(?:\s+جارڤیس)?/i.test(t)) return true;
  if (/(?:الى\s*اللقاء|مع\s*السلامة|نم\s+جارفيس|اذهب\s+للنوم)/i.test(t)) return true;
  return false;
}

function isCamPhrase(raw) {
  const t = normalizePhrase(raw);
  if (!t) return false;
  if (/\b(open\s+camera|start\s+camera|live\s+cam|camera\s+vision|webcam|take\s+a\s+look|look\s+at\s+this|see\s+this)\b/i.test(t)) return true;
  if (/(?:کامێرا\s*بکەرەوە|کامێرا\s*پێبکە|سەیری\s*ئەمە\s*بکە|سەیری\s*کامێرا\s*بکە)/i.test(t)) return true;
  if (/(?:افتح\s*الكاميرا|شغل\s*الكاميرا|انظر\s*الى\s*هذا)/i.test(t)) return true;
  return false;
}

/* ------------------------------------------------------------------ Wake / Sleep sequence handlers */
let introTimer = null;
let introProgressInterval = null;

function triggerWakeUpSequence(opts = true) {
  const options = (typeof opts === 'boolean') ? { playVideo: opts, isStartup: false } : (opts || {});
  const playVideo = options.playVideo !== false;
  const isStartup = Boolean(options.isStartup);

  // Pop JARVIS window to the front of Windows desktop if woken from background
  if (!isStartup) {
    fetch('/api/window/pop_to_front', { method: 'POST' }).catch(() => {});
    try { window.focus(); } catch {}
  }

  state.isSleeping = false;
  state.handsFree = true;
  LS.set('jarvis_speak', '1');
  state.speakReplies = true;
  syncSpeakBtn();
  refreshUI();
  if (!playVideo) playWakeChime();

  const overlay = $('#wakeUpOverlay');
  const localVideo = $('#wakeLocalVideo');
  const iframeWrapper = $('#wakeIframeWrapper');
  const iframe = $('#wakeVideoFrame');
  const status = $('#wakeStatusText');
  const subtitle = $('#wakeSubtitle');
  const progressBar = $('#introProgressBar');
  const progressLabel = $('#introProgressLabel');

  if (introTimer) { clearTimeout(introTimer); introTimer = null; }
  if (introProgressInterval) { clearInterval(introProgressInterval); introProgressInterval = null; }

  if (overlay) {
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    overlay.style.visibility = 'visible';
    overlay.style.pointerEvents = 'auto';
    overlay.style.opacity = '1';
    overlay.style.transition = 'opacity 0.35s ease';

    if (subtitle) {
      subtitle.textContent = isStartup
        ? 'BOOT SEQUENCE // NEURAL CORES ONLINE'
        : 'WAKE SEQUENCE // AUDIO SENSORS ARMED';
    }
    if (status) {
      status.textContent = '"Welcome back, Sir. All systems initialized and online."';
    }

    // Play native 1080p local intro video (zero YouTube branding)
    if (playVideo && localVideo) {
      localVideo.style.display = 'block';
      if (iframeWrapper) iframeWrapper.classList.add('hidden');
      localVideo.currentTime = 0;

      const updateIntroAudioUI = (muted) => {
        const icon = $('#introAudioIcon');
        const text = $('#introAudioText');
        const prompt = $('#introAudioPrompt');
        if (muted) {
          if (icon) icon.textContent = '🔇';
          if (text) text.textContent = 'Audio: OFF (Click to unmute)';
          if (prompt) prompt.classList.remove('hidden');
        } else {
          if (icon) icon.textContent = '🔊';
          if (text) text.textContent = 'Audio: ON';
          if (prompt) prompt.classList.add('hidden');
        }
      };

      // Wire audio button & prompt on the overlay
      const audioBtn = $('#btnToggleIntroAudio');
      if (audioBtn) {
        audioBtn.onclick = (e) => {
          e.stopPropagation();
          localVideo.muted = !localVideo.muted;
          if (!localVideo.muted) localVideo.volume = 1.0;
          updateIntroAudioUI(localVideo.muted);
        };
      }
      const audioPrompt = $('#introAudioPrompt');
      if (audioPrompt) {
        audioPrompt.onclick = (e) => {
          e.stopPropagation();
          localVideo.muted = false;
          localVideo.volume = 1.0;
          updateIntroAudioUI(false);
        };
      }

      // When the intro video finishes, automatically enter Command Center!
      localVideo.onended = () => {
        dismissWakeOverlay();
      };

      // Attempt unmuted playback first
      localVideo.muted = false;
      localVideo.volume = 1.0;
      updateIntroAudioUI(false);

      const playPromise = localVideo.play();
      if (playPromise !== undefined) {
        playPromise.then(() => {
          updateIntroAudioUI(false);
        }).catch(() => {
          // Browser autoplay restriction: start muted and show clear prompt
          localVideo.muted = true;
          updateIntroAudioUI(true);
          localVideo.play().catch(() => {
            if (iframeWrapper && iframe) {
              localVideo.style.display = 'none';
              iframeWrapper.classList.remove('hidden');
              iframe.src = 'https://www.youtube-nocookie.com/embed/LuuOpozKbvE?autoplay=1&mute=1&controls=0&enablejsapi=1&rel=0';
            }
          });
        });
      }
    }

    // Stark HUD Progress Bar animation
    if (progressBar && progressLabel) {
      let progress = 0;
      progressBar.style.width = '0%';
      progressLabel.textContent = 'BOOT SEQUENCE: 0% INITIALIZING';

      const steps = [
        { pct: 20, text: '20% - NEURAL NETWORKS SYNCHRONIZED' },
        { pct: 45, text: '45% - QUANTUM SENSORS CALIBRATED' },
        { pct: 70, text: '70% - PERIPHERALS & ADAPTERS ARMED' },
        { pct: 88, text: '88% - SECURITY PROTOCOLS VERIFIED' },
        { pct: 100, text: '100% - J.A.R.V.I.S. MARK IV OPERATIONAL' },
      ];

      introProgressInterval = setInterval(() => {
        progress += 3;
        if (progress > 100) progress = 100;
        progressBar.style.width = progress + '%';

        const matchedStep = [...steps].reverse().find((s) => progress >= s.pct);
        if (matchedStep) {
          progressLabel.textContent = `BOOT SEQUENCE: ${matchedStep.text}`;
        }

        if (progress >= 100) {
          clearInterval(introProgressInterval);
          introProgressInterval = null;
          // Auto-transition into Command Center 1 second after 100% calibration
          if (!introTimer) {
            introTimer = setTimeout(() => {
              dismissWakeOverlay();
            }, 1000);
          }
        }
      }, 100);
    }

    // Safety timeout: automatically enter Command Center after 6.5 seconds max
    introTimer = setTimeout(() => {
      dismissWakeOverlay();
    }, 6500);
  }

  logActivity('JARVIS systems online (' + (isStartup ? 'Startup intro' : 'Wake-up sequence') + ' initiated)', true);
  toast('Welcome back, Sir. JARVIS is awake!');
  if (!playVideo) {
    speak('Welcome back, Sir. All systems are initialized and online.');
    startBackgroundVoiceListener();
  }
}

function dismissWakeOverlay() {
  if (introTimer) { clearTimeout(introTimer); introTimer = null; }
  if (introProgressInterval) { clearInterval(introProgressInterval); introProgressInterval = null; }

  const overlay = $('#wakeUpOverlay');
  const localVideo = $('#wakeLocalVideo');
  const iframeWrapper = $('#wakeIframeWrapper');
  const iframe = $('#wakeVideoFrame');

  if (localVideo) {
    try {
      localVideo.pause();
      localVideo.currentTime = 0;
    } catch {}
  }
  if (iframe) iframe.src = '';
  if (iframeWrapper) iframeWrapper.classList.add('hidden');

  if (overlay) {
    // Disable pointer events immediately so user can interact with the app right away
    overlay.style.pointerEvents = 'none';
    overlay.style.transition = 'opacity 0.3s ease, visibility 0.3s ease';
    overlay.style.opacity = '0';

    setTimeout(() => {
      overlay.classList.add('hidden');
      overlay.style.display = 'none';
      overlay.style.visibility = 'hidden';
      overlay.style.pointerEvents = 'none';
    }, 300);
  }
  $('#chatInput')?.focus();

  // Chime and voice greeting upon entering the Command Center
  playWakeChime();
  if (state.speakReplies) {
    setTimeout(() => {
      speak('Welcome back, Sir. All systems are initialized and online.');
    }, 350);
  }

  if (state.wakeWordEnabled && !state.rec && !state.busy) {
    setTimeout(() => startBackgroundVoiceListener(), 250);
  }
}

function triggerSleepMode() {
  state.isSleeping = true;
  stopSpeaking();
  if (state.camActive) closeLiveCamVision();
  playSleepChime();
  refreshUI();

  logActivity('JARVIS entered sleep mode (Standby)', true);
  addMsg('bot', '💤 Entering sleep mode, Sir. Audio sensors remain armed in the background. Say "Wake up JARVIS" anytime.');
  toast('JARVIS is now asleep. Say "Wake up JARVIS" to wake me.');
  speak('Good night, Sir. Audio sensors remain armed. Call me whenever you need me.');

  if (state.wakeWordEnabled) {
    startBackgroundVoiceListener();
  }
}

async function sendChat(message) {
  message = (message || '').trim();
  if (!message) return;

  if (isWakePhrase(message)) {
    triggerWakeUpSequence(true);
    return;
  }
  if (isSleepPhrase(message)) {
    triggerSleepMode();
    return;
  }
  if (isCamPhrase(message)) {
    openLiveCamVision();
    return;
  }
  if (state.isSleeping) {
    state.isSleeping = false;
    refreshUI();
  }

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
  if (state.handsFree || state.wakeWordEnabled) startBackgroundVoiceListener();
}

$('#chatForm').addEventListener('submit', (e) => { e.preventDefault(); sendChat($('#chatInput').value); });
$('#micInline').addEventListener('click', () => {
  if (state.isSleeping) {
    triggerWakeUpSequence(true);
    return;
  }
  if (state.rec) {
    stopVoiceListener();
  } else {
    state.speakReplies = true;
    syncSpeakBtn();
    startBackgroundVoiceListener();
  }
});
$('#stopSpeak').addEventListener('click', () => {
  stopSpeaking();
  if (state.handsFree || state.wakeWordEnabled) startBackgroundVoiceListener();
});

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
  ['Wake JARVIS', 'i-bolt', () => triggerWakeUpSequence(true)],
  ['Gaming Mode', 'i-bolt', () => sendChat('Activate gaming protocol')],
  ['Coding Mode', 'i-code', () => sendChat('Activate coding protocol')],
  ['Diagnostics', 'i-chip', () => sendChat('Run diagnostics')],
  ['Clean Slate', 'i-window', () => sendChat('Clean slate protocol')],
  ['Sleep Mode', 'i-moon', () => triggerSleepMode()],
  ['Live Cam', 'i-camera', () => openLiveCamVision()],
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
  'phone-control': () => loadPhoneControl(),
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
    box.append(el('p', 'empty', 'No nearby devices found yet. Press "Scan Subnet" above.'));
    return;
  }
  const table = el('table', 'dev-table');
  table.innerHTML = '<thead><tr><th>Device / Host</th><th>Type</th><th>IP Address</th><th>MAC Address</th><th>Status</th></tr></thead>';
  const tbody = el('tbody');
  devs.forEach((d) => {
    const tr = el('tr');
    tr.innerHTML = `<td><b>${d.hostname || 'Device'}</b>${d.vendor ? ` <small class="muted">(${d.vendor})</small>` : ''}</td><td>${d.type || 'Unknown'}</td><td><code>${d.ip}</code></td><td><code style="color:var(--cyan)">${d.mac || 'N/A'}</code></td><td><span class="dot-ok">${d.status}</span></td>`;
    tbody.append(tr);
  });
  table.append(tbody);
  box.append(table);
}

async function scanDevices() {
  toast('Scanning local Wi-Fi subnet for devices...');
  try {
    const d = await api('/api/devices/scan', {});
    devicesData.nearby_devices = d.nearby_devices;
    devicesData.connected_phones = d.connected_phones;
    renderConnectedPhones();
    renderNearbyDevices();
    toast(`Discovered ${d.nearby_devices.length} device(s) on local network.`);
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

let phoneFlashlightState = false;

$('#btnTogglePhoneFlashlight')?.addEventListener('click', async () => {
  try {
    phoneFlashlightState = !phoneFlashlightState;
    const res = await api('/api/phone/flashlight', { state: phoneFlashlightState });
    phoneFlashlightState = Boolean(res.state);
    const btn = $('#btnTogglePhoneFlashlight');
    if (btn) {
      btn.textContent = phoneFlashlightState ? '🔦 Flashlight (ON)' : '🔦 Toggle Flashlight (Off)';
      btn.classList.toggle('primary', phoneFlashlightState);
    }
    toast(`Phone flashlight ${phoneFlashlightState ? 'turned ON' : 'turned OFF'}!`);
  } catch (e) {
    toast('Flashlight error: ' + e.message, true);
  }
});

$('#btnVibratePhoneDash')?.addEventListener('click', async () => {
  try {
    await api('/api/phone/vibrate', { duration: 600 });
    toast('Vibration signal dispatched to phone!');
  } catch (e) {
    toast(e.message, true);
  }
});

$('#btnRequestPhonePhoto')?.addEventListener('click', async () => {
  try {
    await api('/api/phone/photo_request', {});
    toast('Photo snapshot requested from phone camera.');
  } catch (e) {
    toast(e.message, true);
  }
});

$('#phoneSpeakForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('#phoneSpeakInput')?.value.trim();
  if (!text) return toast('Type text to speak on phone first.', true);
  try {
    await api('/api/phone/speak', { text });
    $('#phoneSpeakInput').value = '';
    toast(`Sent speech to phone: "${text}"`);
  } catch (err) {
    toast(err.message, true);
  }
});

$('#btnRefreshDevices')?.addEventListener('click', scanDevices);
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

/* ------------------------------------------------------------------ Dedicated Phone Control & Open Phone */
let phoneScreenRefreshTimer = null;

async function loadPhoneControl() {
  try {
    const data = await api('/api/phone/overview');
    const connected = data.connected_phones || [];
    const isConnected = connected.length > 0;

    // Status Badge
    const badge = $('#phoneLinkBadge');
    if (badge) {
      badge.className = 'tunnel-status-badge ' + (isConnected ? 'active' : 'stopped');
      badge.textContent = isConnected ? `● Connected (${connected.length})` : '● Disconnected';
    }

    // Nav Badge
    const ctrlBadge = $('#phoneControlBadge');
    if (ctrlBadge) {
      ctrlBadge.textContent = connected.length;
      ctrlBadge.classList.toggle('hidden', !connected.length);
    }
    const devBadge = $('#phoneBadge');
    if (devBadge) {
      devBadge.textContent = connected.length;
      devBadge.classList.toggle('hidden', !connected.length);
    }

    // Phone Info Details
    const primary = connected[0] || {};
    if ($('#phoneModelName')) $('#phoneModelName').textContent = primary.device_name || (isConnected ? 'Connected Phone' : 'No Phone Connected');
    if ($('#phoneIpAddress')) $('#phoneIpAddress').textContent = primary.ip || (isConnected ? 'Wi-Fi' : '--');

    const bat = primary.battery !== undefined && primary.battery !== null ? primary.battery : '--';
    if ($('#phoneBatteryText')) $('#phoneBatteryText').textContent = bat !== '--' ? `${bat}%` : '--%';
    const batBar = $('#phoneBatteryBar');
    if (batBar) {
      if (bat !== '--') {
        const pct = Math.max(0, Math.min(100, bat));
        batBar.style.width = pct + '%';
        batBar.style.background = pct > 40 ? 'var(--green)' : pct > 15 ? 'var(--amber)' : 'var(--red)';
      } else {
        batBar.style.width = '0%';
      }
    }

    // ADB Status
    const adbList = data.adb_devices || [];
    const adbEl = $('#phoneAdbStatus');
    if (adbEl) {
      adbEl.textContent = adbList.length > 0
        ? `✓ ADB connected (${adbList[0].serial})`
        : (data.adb_installed ? 'ADB installed · no phone connected' : 'ADB not found · install Platform-Tools');
      const adbStatus = $('#adbPairStatus');
      if (adbStatus && !adbStatus.dataset.userMessage) {
        adbStatus.textContent = adbList.length ? `Connected to ${adbList.map(d => d.serial).join(', ')}. Phone system controls should be available.`
          : (data.adb_installed ? 'ADB is installed, but no Android device is paired/connected yet.' : 'ADB is missing. Install Android SDK Platform-Tools, then restart JARVIS.');
      }
    }

    // Companion Link
    const linkEl = $('#phoneCtrlPortalLink');
    const guideLinkEl = $('#guideCompanionLink');
    if (data.portal_url) {
      if (linkEl) {
        linkEl.href = data.portal_url;
        linkEl.textContent = data.portal_url;
      }
      if (guideLinkEl) {
        guideLinkEl.href = data.portal_url;
        guideLinkEl.textContent = data.portal_url;
      }
    }

    // Torch button state
    const torchBtn = $('#btnCtrlPhoneTorch');
    if (torchBtn) {
      const on = Boolean(data.torch_state);
      torchBtn.textContent = on ? '🔦 Flashlight (ON)' : '🔦 Flashlight (Off)';
      torchBtn.classList.toggle('primary', on);
    }

    // Screenshot viewport
    const imgEl = $('#phoneScreenImg');
    const placeEl = $('#phoneScreenPlaceholder');
    if (data.has_screenshot && data.screenshot_url) {
      if (imgEl) {
        imgEl.src = data.screenshot_url;
        imgEl.style.display = 'block';
      }
      if (placeEl) placeEl.style.display = 'none';
    } else {
      if (imgEl) imgEl.style.display = 'none';
      if (placeEl) placeEl.style.display = 'block';
    }

    // Render Remembered Phones & Saved PIN Status
    const savedList = data.saved_phones || [];
    const savedContainer = $('#savedPhonesList');
    const savedBadge = $('#savedPinBadge');
    const autoUnlockBtn = $('#btnAutoUnlock');
    const activeAdb = data.adb_devices || [];

    let hasActivePin = false;
    if (activeAdb.length && activeAdb[0].has_saved_pin) {
      hasActivePin = true;
    } else if (savedList.some(s => s.has_pin)) {
      hasActivePin = true;
    }

    if (savedBadge) {
      if (hasActivePin) {
        savedBadge.textContent = '● Saved PIN Active';
        savedBadge.className = 'tunnel-status-badge running';
      } else {
        savedBadge.textContent = 'No Saved PIN';
        savedBadge.className = 'tunnel-status-badge stopped';
      }
    }

    if (autoUnlockBtn) {
      autoUnlockBtn.style.opacity = hasActivePin ? '1' : '0.7';
      autoUnlockBtn.title = hasActivePin ? 'Click to auto-unlock phone with remembered PIN' : 'Enter PIN once and JARVIS will remember it';
    }

    if (savedContainer) {
      if (!savedList.length) {
        savedContainer.innerHTML = '<span class="muted" style="font-size:12.5px;">No saved phones yet. Plug in your phone and unlock once with PIN to remember it forever.</span>';
      } else {
        savedContainer.innerHTML = savedList.map(p => `
          <div style="display:flex; justify-content:space-between; align-items:center; background:#04121a; border:1px solid var(--line-soft); border-radius:4px; padding:6px 10px;">
            <div>
              <b style="color:var(--cyan);">${escapeHtml(p.model || 'Android Phone')}</b>
              <span class="muted" style="margin-left:6px; font-size:12px; font-family:var(--mono);">(${escapeHtml(p.serial)})</span>
              <span style="margin-left:6px; font-size:11px; padding:1px 5px; border-radius:3px; background:${p.connection_type === 'wifi' ? '#06b6d422; color:#38bdf8;' : '#10b98122; color:#34d399;'}">${p.connection_type === 'wifi' ? '📶 Wi-Fi' : '🔌 USB'}</span>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:12px; color:${p.has_pin ? 'var(--green)' : 'var(--amber)'}; font-family:var(--mono);">
                ${p.has_pin ? '🔒 Saved PIN: ••••' : '⚠️ No PIN saved'}
              </span>
              <button class="ghost mini btn-forget-phone" data-serial="${escapeHtml(p.serial)}" type="button" style="color:var(--red); padding:2px 6px;">Forget</button>
            </div>
          </div>
        `).join('');

        savedContainer.querySelectorAll('.btn-forget-phone').forEach(btn => {
          btn.addEventListener('click', async (e) => {
            const serial = e.currentTarget.dataset.serial;
            if (!confirm(`Forget remembered settings and PIN for ${serial}?`)) return;
            try {
              await api('/api/phone/saved', { serial }, 'DELETE');
              toast('Forgot saved device PIN.');
              loadPhoneControl();
            } catch (err) { toast(err.message, true); }
          });
        });
      }
    }
  } catch (err) {
    console.warn('loadPhoneControl error:', err);
  }
}

async function refreshAdbStatus() {
  const statusEl = $('#adbPairStatus');
  if (statusEl) { statusEl.textContent = 'Checking ADB…'; delete statusEl.dataset.userMessage; }
  try {
    const data = await api('/api/phone/adb/status');
    const connected = data.adb_devices || [];
    const adbBadge = $('#phoneAdbBadge');
    if (adbBadge) {
      adbBadge.textContent = connected.length > 0 ? `● USB Connected (${connected[0].serial})` : '● Disconnected';
      adbBadge.className = `tunnel-status-badge ${connected.length > 0 ? 'running' : 'stopped'}`;
    }
    if ($('#phoneAdbStatus')) $('#phoneAdbStatus').textContent = connected.length
      ? `✓ Connected (${connected[0].serial})`
      : (data.adb_installed ? 'ADB tool ready · no phone connected' : 'ADB not found · click Download');
    if (statusEl) statusEl.textContent = data.message || (connected.length ? 'Phone connected.' : 'No phone connected yet.');
  } catch (err) { if (statusEl) statusEl.textContent = `ADB status error: ${err.message}`; }
}

$('#btnDetectUsbPhone')?.addEventListener('click', async () => {
  const statusEl = $('#adbPairStatus');
  if (statusEl) { statusEl.textContent = 'Scanning USB ports for connected Android phone…'; statusEl.dataset.userMessage = '1'; }
  toast('Scanning USB ports for connected phone...');
  try {
    const data = await api('/api/phone/adb/status');
    const devs = data.adb_devices || [];
    if (devs.length > 0) {
      toast(`Phone detected! (${devs[0].serial})`);
      if (statusEl) statusEl.textContent = `✓ Phone connected: ${devs[0].serial}. System controls active!`;
    } else if (data.adb_installed) {
      toast('No phone detected. Please connect USB cable and enable USB Debugging.', true);
      if (statusEl) statusEl.textContent = 'No phone detected. Connect USB cable and ensure "USB Debugging" is enabled on your phone.';
    } else {
      toast('ADB tool not found. Click "Download ADB Tool (1-Click)" below.', true);
      if (statusEl) statusEl.textContent = 'ADB tool not found. Please click "Download ADB Tool (1-Click)".';
    }
    await loadPhoneControl();
  } catch (err) {
    toast(`Scan error: ${err.message}`, true);
  }
});

$('#btnSwitchAdbWifi')?.addEventListener('click', async () => {
  const statusEl = $('#adbPairStatus');
  if (statusEl) { statusEl.textContent = 'Enabling TCP port 5555 and discovering phone Wi-Fi IP…'; statusEl.dataset.userMessage = '1'; }
  toast('Switching phone to Wireless Wi-Fi ADB...');
  try {
    const res = await api('/api/phone/adb/switch_wifi', {});
    if (res.ok) {
      toast(res.message || 'Switched to wireless Wi-Fi! You can now unplug the cable.');
      if (statusEl) statusEl.textContent = `✓ ${res.message}`;
    } else {
      toast(`Wireless switch failed: ${res.error}`, true);
      if (statusEl) statusEl.textContent = `Wireless switch failed: ${res.error}`;
    }
    await loadPhoneControl();
  } catch (err) {
    toast(`Wireless switch error: ${err.message}`, true);
  }
});

$('#btnInstallAdbTool')?.addEventListener('click', async () => {
  const statusEl = $('#adbPairStatus');
  const btn = $('#btnInstallAdbTool');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Downloading ADB...'; }
  if (statusEl) { statusEl.textContent = 'Downloading official Google ADB Platform-Tools (~5MB)...'; statusEl.dataset.userMessage = '1'; }
  toast('Downloading Android ADB tools from Google...');
  try {
    const data = await api('/api/phone/adb/install');
    if (data.ok) {
      toast(data.message || 'ADB installed successfully!');
      if (statusEl) statusEl.textContent = '✓ ADB installed! Connect phone with USB cable and click "Detect Connected Phone".';
      if (btn) btn.textContent = '✓ ADB Installed';
    } else {
      throw new Error(data.error || 'Download failed');
    }
    setTimeout(refreshAdbStatus, 600);
  } catch (err) {
    toast(`ADB install error: ${err.message}`, true);
    if (statusEl) statusEl.textContent = `Download failed: ${err.message}`;
    if (btn) { btn.disabled = false; btn.textContent = '📥 Download ADB Tool (1-Click)'; }
  }
});

$('#btnAdbPair')?.addEventListener('click', async () => {
  const statusEl = $('#adbPairStatus');
  const payload = { host: $('#adbHost')?.value.trim(), port: $('#adbPairPort')?.value, pairing_code: $('#adbPairCode')?.value.trim() };
  if (statusEl) { statusEl.textContent = 'Pairing with Android…'; statusEl.dataset.userMessage = '1'; }
  try {
    const data = await api('/api/phone/adb/pair', payload);
    if (!data.ok) throw new Error(data.error || data.message || 'Pairing failed. Check the temporary code and pairing port.');
    if (statusEl) statusEl.textContent = data.message || 'Pairing successful. Now enter the main Wireless debugging port and press Connect.';
    $('#adbPairCode').value = '';
    if (data.adb_devices?.length) { if (statusEl) statusEl.textContent += ` Devices: ${data.adb_devices.map(d => d.serial).join(', ')}`; }
    toast('ADB pairing successful. Now press Connect using the main Wireless debugging port.');
    setTimeout(refreshAdbStatus, 500);
  } catch (err) { if (statusEl) statusEl.textContent = `Pairing failed: ${err.message}`; toast(err.message, true); }
});

$('#btnAdbConnect')?.addEventListener('click', async () => {
  const statusEl = $('#adbPairStatus');
  const payload = { host: $('#adbHost')?.value.trim(), port: $('#adbConnectPort')?.value };
  if (statusEl) { statusEl.textContent = 'Connecting to Android…'; statusEl.dataset.userMessage = '1'; }
  try {
    const data = await api('/api/phone/adb/connect', payload);
    if (!data.ok) throw new Error(data.error || data.message || 'Connection failed. Recheck the IP/port and make sure Wireless debugging is enabled.');
    if (statusEl) statusEl.textContent = `${data.message || 'Connected.'}${data.adb_devices?.length ? ' Devices: ' + data.adb_devices.map(d => d.serial).join(', ') : ''}`;
    toast('Android ADB connected. System-control buttons are now available.');
    setTimeout(loadPhoneControl, 250); setTimeout(refreshAdbStatus, 500);
  } catch (err) { if (statusEl) statusEl.textContent = `Connection failed: ${err.message}`; toast(err.message, true); }
});

$('#btnAdbRefresh')?.addEventListener('click', async () => { await refreshAdbStatus(); await loadPhoneControl(); });

// Phone Control Event Listeners
$('#btnRefreshPhoneCtrl')?.addEventListener('click', () => {
  loadPhoneControl();
  toast('Refreshed phone status.');
});

$('#btnRefreshSavedPhones')?.addEventListener('click', () => {
  loadPhoneControl();
  toast('Refreshed saved phones & PINs.');
});

$('#btnQuickWakePhone')?.addEventListener('click', async () => {
  try {
    const res = await api('/api/phone/key', { key: 'wake' });
    if (!res.ok) throw new Error(res.error || res.message || 'ADB connection required.');
    toast(res.message || 'Sent wake signal to phone!');
  } catch (e) {
    toast('Wake error: ' + e.message, true);
  }
});

$('#btnAutoUnlock')?.addEventListener('click', async () => {
  toast('Unlocking with remembered PIN...');
  try {
    const res = await api('/api/phone/unlock', { pin: '' });
    if (res.ok) {
      toast(res.message || 'Phone auto-unlocked successfully!');
      setTimeout(loadPhoneControl, 800);
    } else {
      toast('Auto-unlock failed: ' + (res.error || 'Enter your PIN once to save it for auto-unlock'), true);
    }
  } catch (err) {
    toast('Auto-unlock error: ' + err.message, true);
  }
});

$('#phoneUnlockForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const pin = $('#phoneUnlockPin')?.value.trim();
  try {
    const res = await api('/api/phone/unlock', { pin });
    if (res.ok) {
      toast(res.message || 'Phone unlocked successfully!');
      $('#phoneUnlockPin').value = '';
      setTimeout(loadPhoneControl, 800);
    } else {
      toast('Unlock failed: ' + (res.error || 'Check connection'), true);
    }
  } catch (err) {
    toast('Unlock error: ' + err.message, true);
  }
});

// Navigation key pad buttons
$$('#view-phone-control [data-key]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const key = btn.dataset.key;
    try {
      const res = await api('/api/phone/key', { key });
      if (!res.ok) throw new Error(res.error || res.message || 'ADB connection required.');
      toast(res.message || `Sent ${key} to phone`);
    } catch (e) {
      toast(`Key error (${key}): ${e.message}`, true);
    }
  });
});

// Auto-refresh screen toggle
$('#chkAutoRefreshPhoneScreen')?.addEventListener('change', (e) => {
  if (e.target.checked) {
    if (phoneScreenRefreshTimer) clearInterval(phoneScreenRefreshTimer);
    phoneScreenRefreshTimer = setInterval(loadPhoneControl, 2500);
    toast('Screen auto-refresh enabled (every 2.5s)');
  } else {
    if (phoneScreenRefreshTimer) clearInterval(phoneScreenRefreshTimer);
    phoneScreenRefreshTimer = null;
    toast('Screen auto-refresh disabled');
  }
});

// Capture phone screen buttons
const requestPhoneScreen = async () => {
  try {
    const res = await api('/api/phone/capture_request');
    toast(res.message || 'Dispatched screen capture request');
    setTimeout(loadPhoneControl, 1200);
  } catch (e) {
    toast('Capture error: ' + e.message, true);
  }
};
$('#btnCapturePhoneScreen')?.addEventListener('click', requestPhoneScreen);
$('#btnRequestScreenFromPhone')?.addEventListener('click', requestPhoneScreen);

// Flashlight toggle
$('#btnCtrlPhoneTorch')?.addEventListener('click', async () => {
  try {
    const res = await api('/api/phone/flashlight', {});
    const on = Boolean(res.state);
    const btn = $('#btnCtrlPhoneTorch');
    if (btn) {
      btn.textContent = on ? '🔦 Flashlight (ON)' : '🔦 Flashlight (Off)';
      btn.classList.toggle('primary', on);
    }
    toast(`Phone flashlight turned ${on ? 'ON' : 'OFF'}!`);
  } catch (e) {
    toast('Flashlight error: ' + e.message, true);
  }
});

// Vibration
$('#btnCtrlPhoneVibrate')?.addEventListener('click', async () => {
  try {
    await api('/api/phone/vibrate', { duration: 600 });
    toast('Vibration dispatched to phone!');
  } catch (e) {
    toast(e.message, true);
  }
});

// Ring Alarm
$('#btnCtrlPhoneRing')?.addEventListener('click', ringPhone);

// Photo request
$('#btnCtrlPhonePhoto')?.addEventListener('click', async () => {
  try {
    await api('/api/phone/photo_request', {});
    toast('Requested photo snapshot from phone camera.');
  } catch (e) {
    toast(e.message, true);
  }
});

// Speak on phone (TTS)
$('#phoneCtrlSpeakForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('#phoneCtrlSpeakInput')?.value.trim();
  if (!text) return toast('Type text to speak on phone first.', true);
  try {
    await api('/api/phone/speak', { text });
    $('#phoneCtrlSpeakInput').value = '';
    toast(`Broadcasting to phone: "${text}"`);
  } catch (err) {
    toast(err.message, true);
  }
});

// Clipboard and Alerts
$('#btnCtrlSendClipboard')?.addEventListener('click', () => {
  runTool('sync_clipboard_with_phone', { direction: 'to_phone' }, 'Clipboard to phone');
});
$('#btnCtrlSendAlert')?.addEventListener('click', async () => {
  try {
    await api('/api/devices/send', {
      title: 'JARVIS Notification',
      message: 'Hello from your laptop! All systems synchronized.'
    });
    toast('Sent push notification to connected phone!');
  } catch (e) {
    toast(e.message, true);
  }
});

$('#btnCopyCtrlPortalLink')?.addEventListener('click', () => {
  const url = $('#phoneCtrlPortalLink')?.href;
  if (url) {
    navigator.clipboard?.writeText(url);
    toast('Copied companion URL to clipboard!');
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
let tunnelState = { active: false, url: '', provider: 'serveo' };

async function refreshTunnelStatus() {
  try {
    const res = await fetch('/api/remote/status');
    const data = await res.json();
    tunnelState = data;
    const badge = $('#remoteTunnelBadge');
    const link = $('#remoteTunnelLink');
    const btn = $('#btnToggleTunnel');
    const sel = $('#tunnelProviderSelect');

    if (badge) {
      badge.className = 'tunnel-status-badge ' + (data.active ? 'active' : 'stopped');
      badge.textContent = data.active
        ? `● Online (HTTPS) [${data.provider || 'serveo'}]`
        : '● Offline';
    }
    if (link) {
      if (data.active && data.url) {
        const fullUrl = data.url.replace(/\/+$/, '') + '/phone';
        link.href = fullUrl;
        link.textContent = fullUrl;
      } else {
        link.href = '#';
        link.textContent = 'Tunnel not started';
      }
    }
    if (btn) {
      btn.textContent = data.active ? 'Stop Remote Tunnel' : 'Start Remote Tunnel';
      btn.classList.toggle('danger', Boolean(data.active));
    }
    if (sel && data.provider && !sel.matches(':focus')) {
      sel.value = data.provider;
    }
  } catch (err) {}
}

async function toggleRemoteTunnel() {
  const btn = $('#btnToggleTunnel');
  const sel = $('#tunnelProviderSelect');
  const badge = $('#remoteTunnelBadge');
  const link = $('#remoteTunnelLink');

  if (btn) btn.disabled = true;

  try {
    if (tunnelState.active) {
      if (btn) btn.textContent = 'Stopping tunnel...';
      toast('Stopping remote tunnel...');
      await api('/api/remote/stop', {});
      tunnelState.active = false;
      tunnelState.url = '';

      if (badge) {
        badge.className = 'tunnel-status-badge stopped';
        badge.textContent = '● Offline';
      }
      if (link) {
        link.href = '#';
        link.textContent = 'Tunnel not started';
      }
      if (btn) {
        btn.textContent = 'Start Remote Tunnel';
        btn.classList.remove('danger');
      }
      toast('Remote tunnel stopped.');
      await refreshTunnelStatus();
    } else {
      const prov = (sel ? sel.value : '') || 'serveo';
      if (btn) btn.textContent = `Connecting (${prov})...`;
      toast(`Starting secure reverse tunnel via ${prov}...`);
      const d = await api('/api/remote/start', { provider: prov });

      // Actively poll until public URL is captured
      let gotUrl = Boolean(d.active && d.url);
      for (let i = 0; i < 15 && !gotUrl; i++) {
        await new Promise((r) => setTimeout(r, 800));
        await refreshTunnelStatus();
        if (tunnelState.active && tunnelState.url) {
          gotUrl = true;
          break;
        }
      }

      if (gotUrl) {
        toast('Remote tunnel active! Open URL on your phone.');
      } else {
        toast(tunnelState.last_error ? `Tunnel notice: ${tunnelState.last_error}` : 'Tunnel starting in background...', false);
      }
    }
  } catch (err) {
    toast('Tunnel error: ' + err.message, true);
  } finally {
    if (btn) btn.disabled = false;
    await refreshTunnelStatus();
  }
}

$('#btnToggleTunnel')?.addEventListener('click', toggleRemoteTunnel);
$('#tunnelProviderSelect')?.addEventListener('change', async (e) => {
  if (tunnelState.active) {
    toast('Switching tunnel provider to ' + e.target.value + '...');
    await toggleRemoteTunnel();
    await toggleRemoteTunnel();
  }
});
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

// Periodic tunnel status synchronization
setInterval(refreshTunnelStatus, 6000);

let currentPcMac = '9C:C7:D3:36:72:C6';
async function loadWolInfo() {
  try {
    const data = await api('/api/phone/wol_info');
    currentPcMac = data.mac_address || '9C:C7:D3:36:72:C6';
    const display = $('#wolInfoDisplay');
    if (display) {
      display.innerHTML = `Host: <b>${data.pc_name}</b><br>MAC Address: <span style="color:var(--cyan); font-weight:700;">${currentPcMac}</span><br>Broadcast IP: <b>${data.broadcast_ip}</b> (Port 9)`;
    }
  } catch (err) {}
}

$('#btnCopyMacDash')?.addEventListener('click', () => {
  navigator.clipboard?.writeText(currentPcMac);
  toast(`Copied PC MAC address (${currentPcMac}) to clipboard!`);
});

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

/* ------------------------------------------------------------------ Live Cam Vision & Voice Interaction */
async function openLiveCamVision() {
  if (state.isSleeping) {
    state.isSleeping = false;
    refreshUI();
  }
  const modal = $('#cameraModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  const video = $('#cameraVideo');
  const statusChip = $('#camVisionStatus');
  if (statusChip) statusChip.innerHTML = '<i class="dot"></i><span>Initializing Camera...</span>';
  try {
    if (state.cam) {
      state.cam.getTracks().forEach((t) => t.stop());
    }
    state.cam = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
      audio: false
    });
    if (video) video.srcObject = state.cam;
    state.camActive = true;
    if (statusChip) statusChip.innerHTML = '<i class="dot"></i><span>Vision Active</span>';
    playVisionScanSound();
    appendCamTranscript('bot', 'Hello Sir. I can see you clearly through the camera. Speak aloud or ask me what I see.');
    speak('Camera vision active, Sir. I am watching and listening.');
    startBackgroundVoiceListener();
  } catch (err) {
    if (statusChip) statusChip.innerHTML = '<i class="dot" style="background:var(--red);"></i><span>Camera Failed</span>';
    toast(`Camera error: ${err.message}. Please allow camera access in the address bar.`, true);
    appendCamTranscript('bot', `Could not access camera: ${err.message}`);
  }
}

function closeLiveCamVision() {
  if (state.cam) {
    state.cam.getTracks().forEach((t) => t.stop());
    state.cam = null;
  }
  const video = $('#cameraVideo');
  if (video) video.srcObject = null;
  state.camActive = false;
  $('#cameraModal')?.classList.add('hidden');
}

function captureLiveCamFrame() {
  const video = $('#cameraVideo');
  if (!video || !video.videoWidth) return null;
  const canvas = document.createElement('canvas');
  canvas.width = Math.min(video.videoWidth, 1280);
  canvas.height = Math.min(video.videoHeight, 720);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.85);
}

function appendCamTranscript(role, text) {
  const log = $('#camTranscriptLog');
  if (!log) return null;
  const bubble = el('div', `cam-bubble ${role}`, text);
  log.append(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

async function sendCamQuery(message) {
  message = (message || '').trim();
  if (!message) message = 'What do you see in the camera right now? Please describe in detail.';
  if (state.busy) {
    toast('One moment, I am analyzing the previous view.');
    return;
  }
  const frame = captureLiveCamFrame();
  playVisionScanSound();
  appendCamTranscript('user', message);
  const botBubble = appendCamTranscript('bot', 'Analyzing visual feed...');
  stopSpeaking();
  state.busy = true;
  refreshUI();

  let answer = '', isErr = false;
  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: state.sessionId, image: frame || undefined })
    });
    if (!res.ok || !res.body) throw new Error(`Server error ${res.status}`);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '';
    const handle = (ev) => {
      if (ev.type === 'status' || ev.type === 'tool') botBubble.textContent = ev.text || `Working: ${ev.name}`;
      else if (ev.type === 'error') throw new Error(ev.text);
      else if (ev.type === 'final') { answer = ev.text; isErr = !!ev.error; }
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) handle(JSON.parse(line));
      }
    }
    if (!answer) throw new Error('No answer came back.');
  } catch (err) {
    answer = `Visual analysis failed: ${err.message}`;
    isErr = true;
  }

  botBubble.textContent = answer;
  if (isErr) botBubble.classList.add('err');
  logActivity(`Vision query: ${message}`, !isErr);
  addMsg('user', `[Live Cam Vision] ${message}`);
  addMsg('bot', answer);

  state.busy = false;
  refreshUI();
  await speak(answer);
  if (state.camActive && state.camMicActive) {
    startBackgroundVoiceListener();
  }
}

/* ------------------------------------------------------------------ top bar: camera, location, search */
$('#cameraBtn')?.addEventListener('click', openLiveCamVision);
$('#wakeBtn')?.addEventListener('click', () => triggerWakeUpSequence(true));
$('#sleepBtn')?.addEventListener('click', () => triggerSleepMode());

$('#btnSkipWake')?.addEventListener('click', dismissWakeOverlay);
$('#btnWakeEnter')?.addEventListener('click', dismissWakeOverlay);
$('#btnWakeReplay')?.addEventListener('click', () => {
  triggerWakeUpSequence({ isStartup: false, playVideo: true });
});

$('#btnCamSnapshot')?.addEventListener('click', () => {
  sendCamQuery('Analyze what you see in the camera right now in detail.');
});
$('#camVoiceForm')?.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = $('#camVoiceInput')?.value.trim();
  if (!text) return;
  $('#camVoiceInput').value = '';
  sendCamQuery(text);
});
$('#btnCamToggleMic')?.addEventListener('click', () => {
  state.camMicActive = !state.camMicActive;
  const label = $('#camMicLabel');
  if (label) label.textContent = state.camMicActive ? 'Mic: Listening' : 'Mic: Muted';
  $('#btnCamToggleMic')?.classList.toggle('danger', !state.camMicActive);
  toast(state.camMicActive ? 'Camera microphone unmuted' : 'Camera microphone muted');
});

$('#wakeWordToggle')?.addEventListener('click', () => {
  state.wakeWordEnabled = !state.wakeWordEnabled;
  LS.set('jarvis_wake_word', state.wakeWordEnabled ? '1' : '0');
  refreshUI();
  if (state.wakeWordEnabled) {
    toast('Background wake-word armed: Listening for "Wake up JARVIS".');
    startBackgroundVoiceListener();
  } else {
    toast('Background wake-word disarmed.');
    if (!state.handsFree && !state.camActive) {
      stopVoiceListener();
    }
  }
});

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
function closeModals() {
  $$('.modal').forEach((m) => m.classList.add('hidden'));
  dismissWakeOverlay();
  closeLiveCamVision();
}
$$('[data-close]').forEach((b) => b.addEventListener('click', closeModals));
$$('.modal').forEach((m) => m.addEventListener('mousedown', (e) => { if (e.target === m) closeModals(); }));

/* ------------------------------------------------------------------ voice out (natural speech) */
const LANGS = [['en-US', 'English'], ['ar-IQ', 'Arabic (Iraq)'], ['ar-SA', 'Arabic'], ['ckb-IQ', 'Kurdish (Sorani)'], ['tr-TR', 'Turkish'], ['fa-IR', 'Persian']];
function syncLangSelect() {
  const sel = $('#langSelect');
  if (!sel.options.length) { LANGS.forEach(([v, n]) => sel.add(new Option(n, v))); sel.addEventListener('change', () => { state.voiceLang = sel.value; LS.set('jarvis_lang', sel.value); }); }
  sel.value = state.voiceLang || 'en-US';
}
let currentJarvisAudio = null;

function pickVoice(lang) {
  if (!window.speechSynthesis) return null;
  const voices = speechSynthesis.getVoices() || [];
  if (!voices.length) return null;
  const isKurdishOrArabic = lang.toLowerCase().startsWith('ckb') || lang.toLowerCase().startsWith('ar');
  if (isKurdishOrArabic) {
    return (
      voices.find((v) => /ckb|kurd/i.test(v.lang + ' ' + v.name)) ||
      voices.find((v) => /ar-iq|bassel|hamed|naayf/i.test(v.lang + ' ' + v.name)) ||
      voices.find((v) => v.lang.toLowerCase().startsWith('ar')) ||
      voices[0]
    );
  }
  // Prioritize British RP Male voices (like Paul Bettany's JARVIS: Ryan, George, Daniel, UK Male)
  return (
    voices.find((v) => /ryan.*natural|en-gb.*ryan/i.test(v.name + ' ' + v.lang)) ||
    voices.find((v) => /google uk english male/i.test(v.name)) ||
    voices.find((v) => /george|daniel|oliver|arthur/i.test(v.name) && /en-gb|uk|british/i.test(v.lang + ' ' + v.name)) ||
    voices.find((v) => v.lang === 'en-GB') ||
    voices.find((v) => v.lang === lang) ||
    voices.find((v) => v.lang.toLowerCase().startsWith(lang.split('-')[0].toLowerCase())) ||
    voices[0]
  );
}

function stopSpeaking() {
  if (currentJarvisAudio) {
    try {
      currentJarvisAudio.pause();
      currentJarvisAudio.currentTime = 0;
    } catch {}
    currentJarvisAudio = null;
  }
  if (window.speechSynthesis) speechSynthesis.cancel();
  state.speaking = false;
  refreshUI();
  if ((state.wakeWordEnabled || state.handsFree || state.camActive) && !state.rec && !state.busy) {
    setTimeout(startBackgroundVoiceListener, 250);
  }
}

function connectJarvisAcousticFilter(audioElement) {
  try {
    const ctx = getAudioContext();
    if (!ctx) return;
    const source = ctx.createMediaElementSource(audioElement);
    // High-pass filter (removes muddy low-end, crisp intercom clarity)
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 115;

    // Presence boost around 2.6 kHz for Paul Bettany's crisp HUD articulation
    const presence = ctx.createBiquadFilter();
    presence.type = 'peaking';
    presence.frequency.value = 2650;
    presence.Q.value = 1.2;
    presence.gain.value = 3.5;

    // Subtle metallic HUD chamber resonance (11ms delay at 14% wet mix)
    const delay = ctx.createDelay();
    delay.delayTime.value = 0.011;
    const wetGain = ctx.createGain();
    wetGain.gain.value = 0.14;

    // Dynamic compressor for steady, authoritative vocal level
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -20;
    comp.knee.value = 12;
    comp.ratio.value = 3.5;

    source.connect(hp);
    hp.connect(presence);
    presence.connect(comp);
    presence.connect(delay);
    delay.connect(wetGain);
    wetGain.connect(comp);
    comp.connect(ctx.destination);
  } catch {}
}

async function speak(text) {
  if (!state.speakReplies || !text) return;
  stopSpeaking();
  const cleanText = text.replace(/```[\s\S]*?```/g, ' Code is ready on your screen, Sir. ')
                        .replace(/[*_#`~>\[\]\(\)]/g, ' ')
                        .replace(/\s+/g, ' ')
                        .trim();
  if (!cleanText) return;

  state.speaking = true;
  refreshUI();

  const finishSpeaking = () => {
    currentJarvisAudio = null;
    state.speaking = false;
    refreshUI();
    if ((state.wakeWordEnabled || state.handsFree || state.camActive) && !state.rec && !state.busy) {
      setTimeout(startBackgroundVoiceListener, 280);
    }
  };

  // 1. Primary: Backend Neural JARVIS Voice (Paul Bettany en-GB-RyanNeural + Kurdish Sorani Phonetic Neural TTS)
  try {
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: cleanText })
    });
    if (res.ok) {
      const blob = await res.blob();
      if (blob.size > 200) {
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        currentJarvisAudio = audio;
        connectJarvisAcousticFilter(audio);
        await new Promise((resolve) => {
          audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
          audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
          audio.play().catch(() => resolve());
        });
        finishSpeaking();
        return;
      }
    }
  } catch {}

  // 2. Fallback: Browser SpeechSynthesis with British RP Male voice selection
  await new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve();
    speechSynthesis.cancel();
    const parts = (cleanText.match(/[^.!?؟۔\n]+[.!?؟۔]*/g) || [cleanText]).map((p) => p.trim()).filter(Boolean);
    const isKurdishText = /[ۆەێڕڵپچژگڤھ]/.test(cleanText);
    const selectedLang = isKurdishText ? 'ar-IQ' : (state.voiceLang || 'en-GB');
    const voice = pickVoice(selectedLang);
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p);
      u.lang = voice?.lang || selectedLang;
      if (voice) u.voice = voice;
      u.pitch = 0.92;
      u.rate = 1.04;
      if (i === parts.length - 1) {
        u.onend = resolve;
        u.onerror = resolve;
      }
      speechSynthesis.speak(u);
    });
  });
  finishSpeaking();
}
function syncSpeakBtn() { const b = $('#speakToggle'); b.textContent = state.speakReplies ? 'Voice replies: on' : 'Voice replies: off'; b.classList.toggle('on', state.speakReplies); b.setAttribute('aria-pressed', state.speakReplies); }
$('#speakToggle').addEventListener('click', () => { state.speakReplies = !state.speakReplies; LS.set('jarvis_speak', state.speakReplies ? '1' : '0'); if (!state.speakReplies) stopSpeaking(); syncSpeakBtn(); });
$('#testVoice').addEventListener('click', () => {
  if (!window.speechSynthesis) return toast('This browser cannot speak. Use Chrome or Edge.', true);
  const was = state.speakReplies; state.speakReplies = true;
  if (!pickVoice(state.voiceLang || 'en-US')) toast('No voice found for this language, using the default voice.');
  speak('Hello Sir. I can talk to you, and I am ready.').then(() => { state.speakReplies = was; });
});

/* ------------------------------------------------------------------ continuous background voice listener */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let voiceRestartTimer = null;
let voiceShouldBeRunning = false;

function startBackgroundVoiceListener() {
  if (!SR) return;
  voiceShouldBeRunning = true;
  if (state.rec || state.speaking) return;

  clearTimeout(voiceRestartTimer);
  try {
    const rec = new SR();
    rec.lang = state.voiceLang || 'en-US';
    rec.continuous = true;
    rec.interimResults = false;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      state.rec = rec;
      refreshUI();
    };

    rec.onresult = (e) => {
      const results = e.results;
      if (!results || !results.length) return;
      const last = results[results.length - 1];
      if (!last || !last[0]) return;
      const text = (last[0].transcript || '').trim();
      if (!text) return;

      playBlipSound();

      // If JARVIS is in sleep mode: ONLY wake phrases can wake him
      if (state.isSleeping) {
        if (isWakePhrase(text)) {
          triggerWakeUpSequence(true);
        }
        return;
      }

      // Awake: handle sleep, wake, camera, or normal command
      if (isSleepPhrase(text)) {
        triggerSleepMode();
        return;
      }
      if (isWakePhrase(text)) {
        triggerWakeUpSequence(true);
        return;
      }
      if (isCamPhrase(text)) {
        openLiveCamVision();
        return;
      }

      if (state.camActive) {
        if (state.camMicActive) {
          sendCamQuery(text);
        }
        return;
      }

      sendChat(text);
    };

    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        voiceShouldBeRunning = false;
        state.handsFree = false;
      }
    };

    rec.onend = () => {
      state.rec = null;
      refreshUI();
      if (voiceShouldBeRunning && (state.wakeWordEnabled || state.handsFree || state.camActive) && !state.speaking && !state.busy) {
        clearTimeout(voiceRestartTimer);
        voiceRestartTimer = setTimeout(() => {
          if (voiceShouldBeRunning && !state.rec && !state.speaking) {
            startBackgroundVoiceListener();
          }
        }, 350);
      }
    };

    rec.start();
  } catch {
    state.rec = null;
  }
}

function stopVoiceListener() {
  voiceShouldBeRunning = false;
  clearTimeout(voiceRestartTimer);
  if (state.rec) {
    try { state.rec.abort(); } catch {}
    state.rec = null;
  }
  refreshUI();
}

$('#voiceBtn').addEventListener('click', () => {
  if (state.isSleeping) {
    triggerWakeUpSequence(true);
    return;
  }
  state.handsFree = !state.handsFree;
  if (state.handsFree) {
    state.speakReplies = true;
    LS.set('jarvis_speak', '1');
    syncSpeakBtn();
    startBackgroundVoiceListener();
    toast('Hands-free mode active. Just speak whenever you like.');
  } else {
    stopSpeaking();
    if (!state.wakeWordEnabled && !state.camActive) {
      stopVoiceListener();
    }
    toast('Hands-free stopped. Background wake-word remains active.');
  }
  refreshUI();
});

/* ------------------------------------------------------------------ focus mode, shortcuts, start-up */
$('#focusToggle').addEventListener('click', (e) => {
  const on = !document.body.classList.contains('focus'); document.body.classList.toggle('focus', on);
  e.currentTarget.textContent = on ? 'On' : 'Off'; e.currentTarget.classList.toggle('on', on); e.currentTarget.setAttribute('aria-pressed', on);
  toast(on ? 'Focus mode on: side panels hidden.' : 'Focus mode off.');
});
document.addEventListener('keydown', (e) => {
  const overlay = $('#wakeUpOverlay');
  if (overlay && !overlay.classList.contains('hidden')) {
    if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      dismissWakeOverlay();
      return;
    }
  }
  if (e.key === 'Escape') { closeModals(); stopSpeaking(); }
  if (e.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
    e.preventDefault();
    showView('command');
    $('#chatInput')?.focus();
  }
});
function greet() { addMsg('bot', 'Good day, Sir. Tell me what to do, for example: "open Spotify", "volume up" or "play lofi on YouTube".'); }
syncLangSelect(); syncSpeakBtn(); refreshUI(); greet();
refreshTunnelStatus();

// Automatically play fullscreen holographic Stark HUD intro on program start
triggerWakeUpSequence({ isStartup: true, playVideo: true });

// Unlock audio policy and background listener on first user gesture
function unlockAllAudio() {
  const vid = $('#wakeLocalVideo');
  const overlay = $('#wakeUpOverlay');
  if (vid && overlay && !overlay.classList.contains('hidden') && vid.muted) {
    try {
      vid.muted = false;
      vid.volume = 1;
      const icon = $('#introAudioIcon');
      const text = $('#introAudioText');
      const prompt = $('#introAudioPrompt');
      if (icon) icon.textContent = '🔊';
      if (text) text.textContent = 'Audio: ON';
      if (prompt) prompt.classList.add('hidden');
    } catch {}
  }
  if (state.wakeWordEnabled && !state.rec && !state.busy && overlay?.classList.contains('hidden')) {
    startBackgroundVoiceListener();
  }
}

['pointerdown', 'click', 'keydown'].forEach((evt) => {
  window.addEventListener(evt, unlockAllAudio);
});

// Phone Guide copy button
$('#btnCopyGuideLink')?.addEventListener('click', () => {
  const link = $('#guideCompanionLink')?.href || (window.location.origin + '/phone');
  navigator.clipboard?.writeText(link).then(() => {
    toast('Copied companion URL to clipboard!');
  }).catch(() => {
    prompt('Copy this link for your mobile phone:', link);
  });
});


// Poll backend wake signal every 900ms (triggers HUD & Intro when woken by background daemon or Ctrl+Alt+J)
setInterval(async () => {
  try {
    const res = await fetch('/api/wake_signal');
    if (res.ok) {
      const d = await res.json();
      if (d.wake) {
        triggerWakeUpSequence({ isStartup: false, playVideo: true });
      }
    }
  } catch {}
}, 900);
