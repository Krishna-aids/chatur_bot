/**
 * app.js — Chatur v2 — Progressive Voice + Full Chat
 *
 * Voice Architecture:
 *   Audio Queue    → buffer-ahead (always 1 chunk ahead)
 *   Session Token  → kills ghost chunks on interrupt
 *   Blob URLs      → no decode lag (Fix 7)
 *   Batch fetch    → 2 chunks per HTTP call
 *   Silence detect → adaptive ambient noise threshold (Fix 3 adaptive)
 *   Hold-to-talk   → mousedown/mouseup + touch
 *   State machine  → IDLE/RECORDING/TRANSCRIBING/THINKING/QUEUING/SPEAKING
 */

'use strict';

// ── CONFIG ─────────────────────────────────────────────────────
const DEFAULT_API = 'http://localhost:8000';
const API_BASE    = localStorage.getItem('nm_api_base') || DEFAULT_API;
const API_KEY     = localStorage.getItem('nm_api_key')  || '';
const USER_NAME   = localStorage.getItem('nm_user_name')  || 'User';
const USER_EMAIL  = localStorage.getItem('nm_user_email') || '';

if (!API_KEY) window.location.replace('login.html');

const AUTH_HEADERS = { 'Content-Type': 'application/json', 'x-api-key': API_KEY };

// ── VOICE STATES ───────────────────────────────────────────────
const VS = {
  IDLE:          'idle',
  RECORDING:     'recording',
  TRANSCRIBING:  'transcribing',
  THINKING:      'thinking',
  QUEUING:       'queuing',
  SPEAKING:      'speaking',
  LOOP_WAIT:     'loop_wait',   // intent window — waiting to see if user speaks
};

// ── STATE ──────────────────────────────────────────────────────
const state = {
  sessions:         JSON.parse(localStorage.getItem('nm_sessions') || '[]'),
  activeSession:    null,
  attachments:      [],
  sidebarOpen:      true,
  isLoading:        false,

  // voice
  voiceState:       VS.IDLE,
  mediaRecorder:    null,
  audioChunks:      [],
  analyser:         null,
  animFrame:        null,
  voiceSessionId:   0,      // Fix 4 — session token prevents ghost audio
  continuousMode:   false,
  holdToTalk:       false,
  silenceTimer:     null,
  ambientNoise:     0.015,  // Fix 3 adaptive — calibrated on first 300ms
  
  // audio queue
  audioQueue:       [],     // array of base64 strings
  pendingBatches:   [],     // remaining [[text, text], ...] to fetch
  isPlaying:        false,
  currentAudio:     null,

  // continuous loop
  loopEnabled:      false,
  loopAborted:      false,  // set true when modal closes mid-loop
  loopCount:        0,      // Guard 3 — idle cycle counter
  loopWaitTimer:    null,   // intent window timer
  micIgnoreUntil:   0,      // Guard 2 — ignore mic input until this timestamp
};

// ── INIT ───────────────────────────────────────────────────────
function init() {
  const nameEl = document.getElementById('user-name-label');
  const avatEl = document.getElementById('user-avatar-text');
  const empEl  = document.getElementById('empty-username');
  if (nameEl) nameEl.textContent = USER_NAME;
  if (avatEl) avatEl.textContent = (USER_NAME[0] || 'U').toUpperCase();
  if (empEl)  empEl.textContent  = USER_NAME.split(' ')[0];

  bindEvents();
  renderSessions();
  checkServerStatus();

  if (state.sessions.length === 0) newChat();
  else                              loadSession(state.sessions[0].id);

  if (window.innerWidth <= 640) state.sidebarOpen = false;
}

function bindEvents() {
  document.getElementById('toggle-sidebar-btn').addEventListener('click', toggleSidebar);
  document.getElementById('new-chat-btn').addEventListener('click', newChat);
  document.getElementById('send-btn').addEventListener('click', () => sendMessage());

  const ta = document.getElementById('user-input');
  ta.addEventListener('keydown', handleKey);
  ta.addEventListener('input',   handleTextareaInput);

  document.getElementById('btn-attach-file').addEventListener('click',
    () => document.getElementById('file-input').click());
  document.getElementById('btn-attach-image').addEventListener('click',
    () => document.getElementById('image-input').click());
  document.getElementById('file-input').addEventListener('change',  handleFileAttach);
  document.getElementById('image-input').addEventListener('change', handleImageAttach);

  // browser speech-to-text voice input
  document.getElementById('btn-voice').addEventListener('click', startVoiceInput);
  document.getElementById('voice-cancel-btn').addEventListener('click', closeVoiceModal);

  // suggestion pills
  document.getElementById('suggestion-pills').addEventListener('click', e => {
    const pill = e.target.closest('.suggestion-pill');
    if (pill) sendSuggestion(pill.dataset.text);
  });

  document.getElementById('logout-btn').addEventListener('click', logout);

  document.addEventListener('click', e => {
    if (window.innerWidth > 640) return;
    const sidebar = document.getElementById('sidebar');
    if (sidebar.classList.contains('mobile-open') &&
        !sidebar.contains(e.target) &&
        e.target !== document.getElementById('toggle-sidebar-btn'))
      sidebar.classList.remove('mobile-open');
  });

  // hold-to-talk bindings on the action button
  _bindVoiceActionButton();
}

function logout() {
  if (!confirm('Sign out of Chatur?')) return;
  ['nm_api_key','nm_user_name','nm_user_email','nm_session_id'].forEach(k => localStorage.removeItem(k));
  window.location.replace('login.html');
}

// ── SESSIONS ───────────────────────────────────────────────────
async function newChat() {
  let backendId = null;
  try {
    const r = await fetch(`${API_BASE}/session/new`, {
      method: 'POST', headers: AUTH_HEADERS,
      body: JSON.stringify({ user_id: USER_EMAIL || USER_NAME }),
    });
    if (r.ok) backendId = (await r.json()).session_id;
  } catch (e) { console.warn(e); }

  const id      = 'session_' + Date.now();
  const session = { id, backendId, name: 'New conversation', messages: [], createdAt: Date.now() };
  state.sessions.unshift(session);
  saveSessions(); loadSession(id); renderSessions();
  if (window.innerWidth <= 640) document.getElementById('sidebar').classList.remove('mobile-open');
}

async function loadSession(id) {
  state.activeSession = id;
  const session = getSession(id);
  if (!session) return;
  document.getElementById('topbar-title').textContent = session.name;

  // Fix 3: load history from server if local is empty but backend session exists
  if (session.messages.length === 0 && session.backendId) {
    try {
      const r = await fetch(`${API_BASE}/history/${session.backendId}`, { headers: { 'x-api-key': API_KEY } });
      if (r.ok) {
        const data = await r.json();
        if (data.messages && data.messages.length) {
          session.messages = data.messages.map(m => ({
            role: m.role, content: m.content, route: m.route || null,
          }));
          saveSessions();
          if (session.messages.length && session.name === 'New conversation') {
            updateSessionName(id, session.messages[0].content);
          }
        }
      }
    } catch (e) { console.warn('History load failed:', e); }
  }

  renderMessages(session.messages);
  renderSessions();
  document.getElementById('empty-state').style.display = session.messages.length ? 'none' : '';
  document.getElementById('route-badge').className = 'topbar-route-badge';
  document.getElementById('user-input').focus();
}

function getSession(id) { return state.sessions.find(s => s.id === id) || null; }

function deleteSession(id, e) {
  e.stopPropagation();
  state.sessions = state.sessions.filter(s => s.id !== id);
  saveSessions();
  if (state.activeSession === id) {
    if (state.sessions.length) loadSession(state.sessions[0].id);
    else newChat();
  }
  renderSessions();
}

function saveSessions() { localStorage.setItem('nm_sessions', JSON.stringify(state.sessions)); }

function updateSessionName(id, firstMessage) {
  const s = getSession(id);
  if (!s || s.name !== 'New conversation') return;
  s.name = firstMessage.trim().slice(0, 38) + (firstMessage.length > 38 ? '...' : '');
  document.getElementById('topbar-title').textContent = s.name;
  saveSessions(); renderSessions();
}

function renderSessions() {
  const list = document.getElementById('sessions-list');
  if (!state.sessions.length) { list.innerHTML = ''; return; }
  const now = Date.now(), DAY = 86_400_000;
  const groups = { 'Today':[], 'Yesterday':[], 'Last 7 days':[], 'Older':[] };
  state.sessions.forEach(s => {
    const age = now - s.createdAt;
    if      (age < DAY)   groups['Today'].push(s);
    else if (age < 2*DAY) groups['Yesterday'].push(s);
    else if (age < 7*DAY) groups['Last 7 days'].push(s);
    else                  groups['Older'].push(s);
  });
  let html = '';
  Object.entries(groups).forEach(([label, sessions]) => {
    if (!sessions.length) return;
    html += `<div class="session-group-label">${label}</div>`;
    sessions.forEach(s => {
      const active = s.id === state.activeSession ? 'active' : '';
      html += `<div class="session-item ${active}" data-session-id="${s.id}">
        <span class="session-name">${escHtml(s.name)}</span>
        <button class="session-delete" data-delete-id="${s.id}" title="Delete">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6l-1 14H6L5 6"/>
            <path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/>
          </svg>
        </button>
      </div>`;
    });
  });
  list.innerHTML = html;
  list.onclick = e => {
    const del  = e.target.closest('[data-delete-id]');
    if (del) { deleteSession(del.dataset.deleteId, e); return; }
    const item = e.target.closest('[data-session-id]');
    if (item) loadSession(item.dataset.sessionId);
  };
}

// ── SIDEBAR ────────────────────────────────────────────────────
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (window.innerWidth <= 640) sidebar.classList.toggle('mobile-open');
  else {
    state.sidebarOpen = !state.sidebarOpen;
    sidebar.classList.toggle('collapsed', !state.sidebarOpen);
  }
}

// ── MESSAGES ───────────────────────────────────────────────────
function renderMessages(messages) {
  const inner = document.getElementById('chat-inner');
  inner.querySelectorAll('.msg-row').forEach(el => el.remove());
  messages.forEach(m => appendMessageDOM(m));
  scrollToBottom();
}

function appendMessageDOM(msg) {
  document.getElementById('empty-state').style.display = 'none';
  const inner  = document.getElementById('chat-inner');
  const row    = document.createElement('div');
  row.className = `msg-row ${msg.role}`;
  if (msg.id) row.id = msg.id;
  const letter  = msg.role === 'user' ? (USER_NAME[0]||'U').toUpperCase() : 'C';

  const isBot = msg.role === 'assistant';

  // ── Avatar ──────────────────────────────────────────────────
  const avatarEl = document.createElement('div');
  avatarEl.className = 'msg-avatar ' + (isBot ? 'bot' : 'user');
  avatarEl.textContent = letter;

  // ── Content wrapper ─────────────────────────────────────────
  const contentEl = document.createElement('div');
  contentEl.className = 'msg-content';

  // Image preview above bubble
  if (msg.imageDataUrl) {
    const img = document.createElement('img');
    img.className = 'msg-image';
    img.src = msg.imageDataUrl;
    img.alt = 'image';
    contentEl.appendChild(img);
  }

  // Bubble
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.innerHTML = isBot ? formatMarkdown(msg.content) : escHtml(msg.content).replace(/\n/g,'<br/>');
  contentEl.appendChild(bubble);

  // File ref
  if (msg.fileName) {
    const ref = document.createElement('div');
    ref.className = 'msg-file-ref';
    ref.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
         style="width:13px;height:13px;color:var(--accent);flex-shrink:0">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
    </svg>${escHtml(msg.fileName)}`;
    contentEl.appendChild(ref);
  }

  // Route tag
  if (isBot && msg.route) {
    const rc = {CHAT:'tag-chat',RAG:'tag-rag',TOOL:'tag-tool',AGENT:'tag-chat',TIMEOUT:'tag-tool'}[msg.route]||'tag-chat';
    const tag = document.createElement('div');
    tag.className = `msg-route-tag ${rc}`;
    tag.textContent = msg.route;
    contentEl.appendChild(tag);
    if (msg.reasonTrace) {
      const tools   = (msg.reasonTrace.tools_used||[]).join(', ');
      const latency = msg.reasonTrace.elapsed_ms ? `${msg.reasonTrace.elapsed_ms}ms` : '';
      const parts   = [tools, latency].filter(Boolean).join(' · ');
      if (parts) {
        const meta = document.createElement('div');
        meta.className = 'msg-route-tag';
        meta.style.cssText = 'opacity:.35;font-size:9px';
        meta.textContent = parts;
        contentEl.appendChild(meta);
      }
    }
  }

  // ── Feedback bar (bot messages only) ────────────────────────
  if (isBot && msg.content) {
    const feedbackBar = document.createElement('div');
    feedbackBar.className = 'msg-feedback';
    feedbackBar.innerHTML = `
      <button class="fb-btn" title="Good response" onclick="handleFeedback(this,'like')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z"/>
          <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
        </svg>
      </button>
      <button class="fb-btn" title="Bad response" onclick="handleFeedback(this,'dislike')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3z"/>
          <path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/>
        </svg>
      </button>
      <button class="fb-btn" title="Copy response" onclick="handleCopy(this, bubble)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <rect x="9" y="9" width="13" height="13" rx="2"/>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
      </button>`;
    contentEl.appendChild(feedbackBar);
  }

  row.appendChild(avatarEl);
  row.appendChild(contentEl);
  inner.appendChild(row);
  scrollToBottom();
  return row;
}

// ── Feedback handlers ─────────────────────────────────────────
function handleFeedback(btn, type) {
  const bar = btn.closest('.msg-feedback');
  bar.querySelectorAll('.fb-btn').forEach(b => b.classList.remove('active','liked','disliked'));
  btn.classList.add('active', type === 'like' ? 'liked' : 'disliked');
}

function handleCopy(btn, bubble) {
  const text = bubble ? (bubble.innerText || bubble.textContent) : '';
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied');
    btn.title = 'Copied!';
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>`;
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.title = 'Copy response';
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <rect x="9" y="9" width="13" height="13" rx="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
      </svg>`;
    }, 2000);
  });
}

function appendTypingIndicator() {
  const inner = document.getElementById('chat-inner');
  const row   = document.createElement('div');
  row.className = 'msg-row assistant'; row.id = 'typing-row';
  row.innerHTML = `<div class="msg-avatar bot">C</div>
    <div class="msg-content"><div class="msg-bubble">
      <div class="typing-indicator">
        <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
      </div>
    </div></div>`;
  inner.appendChild(row); scrollToBottom();
}
function removeTypingIndicator() { const el=document.getElementById('typing-row'); if(el) el.remove(); }
function scrollToBottom()        { const a=document.getElementById('chat-area'); a.scrollTop=a.scrollHeight; }

/**
 * Full markdown → HTML renderer (GPT-style structured output).
 * Supports: headings, bold, italic, inline code, code blocks,
 *           ordered & unordered lists, tables, horizontal rules, blockquotes.
 */
function formatMarkdown(text) {
  if (!text) return '';

  // Preserve code blocks before other processing
  const codeBlocks = [];
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push({ lang: lang || '', code: code.trimEnd() });
    return `\x00CODE${idx}\x00`;
  });

  // Inline code
  text = text.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');

  // Headings
  text = text.replace(/^### (.+)$/gm, '<h3 class="md-h3">$1</h3>');
  text = text.replace(/^## (.+)$/gm,  '<h2 class="md-h2">$1</h2>');
  text = text.replace(/^# (.+)$/gm,   '<h1 class="md-h1">$1</h1>');

  // Horizontal rule
  text = text.replace(/^---+$/gm, '<hr class="md-hr"/>');

  // Blockquote
  text = text.replace(/^> (.+)$/gm, '<div class="md-blockquote">$1</div>');

  // Bold + italic
  text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  text = text.replace(/\*\*(.+?)\*\*/g,     '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g,          '<em>$1</em>');

  // Tables — detect | rows
  text = text.replace(/(\|.+\|\n?)+/g, match => {
    const rows = match.trim().split('\n').filter(r => r.trim());
    if (rows.length < 2) return match;
    const isSep = r => /^\|[\s\-:|]+\|/.test(r);
    let html = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
    const headers = rows[0].split('|').filter((_,i,a) => i>0 && i<a.length-1);
    headers.forEach(h => { html += `<th>${h.trim()}</th>`; });
    html += '</tr></thead><tbody>';
    rows.slice(2).forEach(row => {
      if (isSep(row)) return;
      const cells = row.split('|').filter((_,i,a) => i>0 && i<a.length-1);
      html += '<tr>';
      cells.forEach(c => { html += `<td>${c.trim()}</td>`; });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  });

  // Unordered lists — group consecutive lines
  text = text.replace(/((?:^[ \t]*[-*+] .+\n?)+)/gm, match => {
    const items = match.trim().split('\n').map(l => l.replace(/^[ \t]*[-*+] /, '').trim());
    return '<ul class="md-ul">' + items.map(i => `<li>${i}</li>`).join('') + '</ul>';
  });

  // Ordered lists
  text = text.replace(/((?:^\d+\. .+\n?)+)/gm, match => {
    const items = match.trim().split('\n').map(l => l.replace(/^\d+\. /, '').trim());
    return '<ol class="md-ol">' + items.map(i => `<li>${i}</li>`).join('') + '</ol>';
  });

  // Paragraphs — double newlines
  text = text.replace(/\n{2,}/g, '</p><p class="md-p">');
  text = '<p class="md-p">' + text + '</p>';
  // Single newlines within paragraphs
  text = text.replace(/([^>])\n([^<])/g, '$1<br/>$2');

  // Clean up empty paragraphs and paragraphs wrapping block elements
  text = text.replace(/<p class="md-p">\s*(<(?:h[123]|ul|ol|table|div|hr)[^>]*>)/g, '$1');
  text = text.replace(/(<\/(?:h[123]|ul|ol|table|div|hr)>)\s*<\/p>/g, '$1');
  text = text.replace(/<p class="md-p">\s*<\/p>/g, '');

  // Restore code blocks
  text = text.replace(/\x00CODE(\d+)\x00/g, (_, i) => {
    const { lang, code } = codeBlocks[i];
    const escaped = code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    return `<div class="md-code-block">${lang ? `<div class="md-code-lang">${lang}</div>` : ''}<pre><code>${escaped}</code></pre></div>`;
  });

  return text;
}

function formatMessage(text) { return formatMarkdown(text); }

function updateRouteBadge(route) {
  const badge = document.getElementById('route-badge');
  const map   = {CHAT:'badge-chat',RAG:'badge-rag',TOOL:'badge-tool',AGENT:'badge-chat',TIMEOUT:'badge-tool'};
  badge.className   = `topbar-route-badge show ${map[route]||'badge-chat'}`;
  badge.textContent = route;
}

function appendErrorMessage(text) {
  const inner = document.getElementById('chat-inner');
  const row   = document.createElement('div');
  row.className = 'msg-row assistant';
  row.innerHTML = `<div class="msg-avatar" style="background:var(--danger)">!</div>
    <div class="msg-content"><div class="msg-bubble" style="border-color:rgba(248,113,113,.2);color:var(--danger)">${escHtml(text)}</div></div>`;
  inner.appendChild(row); scrollToBottom();
}

// ── TEXT SEND ──────────────────────────────────────────────────
function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!document.getElementById('send-btn').disabled) sendMessage();
  }
}

function handleTextareaInput() {
  const ta = document.getElementById('user-input');
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
  updateSendButton();
}

function updateSendButton() {
  const btn  = document.getElementById('send-btn');
  const text = document.getElementById('user-input').value.trim();
  btn.disabled = (!text && !state.attachments.length) || state.isLoading;
}

function sendSuggestion(text) {
  document.getElementById('user-input').value = text;
  updateSendButton(); sendMessage();
}

async function sendMessage(options = {}) {
  const textarea = document.getElementById('user-input');
  const inputTypeHint = options.inputType || 'text';
  const text          = (typeof options.overrideText === 'string' ? options.overrideText : textarea.value).trim();
  if ((!text && !state.attachments.length) || state.isLoading) return;

  state.isLoading = true; updateSendButton();
  const session   = getSession(state.activeSession);
  if (!session) { state.isLoading = false; return; }

  if (!session.backendId) {
    try {
      const r = await fetch(`${API_BASE}/session/new`, {
        method: 'POST', headers: AUTH_HEADERS,
        body: JSON.stringify({ user_id: USER_EMAIL || USER_NAME }),
      });
      if (r.ok) { session.backendId = (await r.json()).session_id; saveSessions(); }
    } catch (e) { console.warn(e); }
  }

  const fileAtts  = state.attachments.filter(a => a.type === 'file');
  const imageAtts = state.attachments.filter(a => a.type === 'image');
  const uploadedNames = [...fileAtts.map(f => f.name), ...imageAtts.map(i => i.name)];

  const userContent = text || (fileAtts.length ? `Uploaded: ${fileAtts.map(f=>f.name).join(', ')}` : '');
  const userMsg = { role:'user', content:userContent, imageDataUrl:imageAtts[0]?.dataUrl||null, fileName:uploadedNames.join(', ')||null };
  session.messages.push(userMsg);
  appendMessageDOM(userMsg);
  updateSessionName(state.activeSession, userContent);

  textarea.value = ''; textarea.style.height = 'auto';
  clearAttachments(); updateSendButton();
  appendTypingIndicator();

  let query = text;
  if (!query && uploadedNames.length) query = `I uploaded: ${fileAtts.map(f=>f.name).join(', ')}. Please acknowledge.`;
  if (!query && imageAtts.length)     query = 'What do you see in this image?';

  // Unified /chat integration (JSON for text, FormData for attachments)
  try {
    let response;
    if (fileAtts.length || imageAtts.length) {
      const att = imageAtts[0] || fileAtts[0];
      const type = att.type === 'image' ? 'image' : (att.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'file');
      const form = new FormData();
      form.append('user_id', USER_EMAIL || 'U1');
      form.append('message', query);
      form.append('input_type', type);
      form.append('session_id', session.backendId || '');
      form.append('file', att.fileObj, att.name);
      response = await fetch(`${API_BASE}/chat`, { method:'POST', headers:{ 'x-api-key': API_KEY }, body: form });
    } else {
      response = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: AUTH_HEADERS,
        body: JSON.stringify({
          user_id: USER_EMAIL || 'U1',
          message: query,
          input_type: inputTypeHint,
          file: null,
          session_id: session.backendId,
        }),
      });
    }

    const data = await response.json();
    removeTypingIndicator();
    if (!response.ok) {
      appendErrorMessage(data.detail?.message || data.detail || data.message || 'Request failed.');
    } else {
      const botMsg = {
        role: 'assistant',
        content: data.message || data.response || '',
        route: data.route || 'TOOL',
        reasonTrace: data.reason_trace || null,
      };
      session.messages.push(botMsg);
      appendMessageDOM(botMsg);
      updateRouteBadge(botMsg.route);
      saveSessions();
    }
  } catch {
    removeTypingIndicator();
    appendErrorMessage('Cannot reach Chatur server. Is it running?');
  } finally {
    state.isLoading = false; updateSendButton();
  }
}

function startVoiceInput() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    appendErrorMessage('Voice input is not supported in this browser.');
    return;
  }

  const recognition = new Recognition();
  recognition.lang = localStorage.getItem('nm_voice_lang') || 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript?.trim() || '';
    if (!transcript) {
      appendErrorMessage("Couldn't detect speech. Please try again.");
      return;
    }
    const input = document.getElementById('user-input');
    input.value = transcript;
    handleTextareaInput();
    sendMessage({ overrideText: transcript, inputType: 'text' });
  };
  recognition.onerror = () => appendErrorMessage('Voice recognition failed. Please try again.');
  recognition.start();
}

// ── ATTACHMENTS ────────────────────────────────────────────────
function handleFileAttach(e) {
  Array.from(e.target.files).forEach(f => state.attachments.push({type:'file',name:f.name,fileObj:f}));
  renderAttachmentRow(); updateSendButton(); e.target.value='';
}
function handleImageAttach(e) {
  Array.from(e.target.files).forEach(file => {
    const r = new FileReader();
    r.onload = ev => { state.attachments.push({type:'image',name:file.name,dataUrl:ev.target.result,fileObj:file}); renderAttachmentRow(); updateSendButton(); };
    r.readAsDataURL(file);
  });
  e.target.value='';
}
function renderAttachmentRow() {
  const row = document.getElementById('attachment-row');
  row.innerHTML = '';
  state.attachments.forEach((att, i) => {
    if (att.type === 'image') {
      const wrap = document.createElement('div');
      wrap.className = 'attachment-thumb';
      wrap.innerHTML = `<img src="${att.dataUrl}" alt="${escHtml(att.name)}"/><button class="img-remove-btn" data-idx="${i}">✕</button>`;
      row.appendChild(wrap);
    } else {
      const pill = document.createElement('div');
      pill.className = 'attachment-pill';
      pill.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <span class="attachment-pill-name">${escHtml(att.name)}</span>
        <button class="remove-attachment" data-idx="${i}">✕</button>`;
      row.appendChild(pill);
    }
  });
  row.classList.toggle('has-items', state.attachments.length > 0);
  row.querySelectorAll('[data-idx]').forEach(btn => {
    btn.addEventListener('click', () => { state.attachments.splice(Number(btn.dataset.idx),1); renderAttachmentRow(); updateSendButton(); });
  });
}
function clearAttachments() { state.attachments=[]; renderAttachmentRow(); }

// ── Fix 2: Document upload status polling ──────────────────────
function pollDocStatus(docName) {
  // Build doc_id same way as server: md5(user_id:filename:text[:200])
  // We can't compute exact md5 here, so show inline status on the message
  const msgRows = document.querySelectorAll('.msg-row.user');
  const lastRow = msgRows[msgRows.length - 1];
  if (!lastRow) return;

  const ref = lastRow.querySelector('.msg-file-ref');
  if (!ref) return;

  const origText = ref.textContent;
  ref.innerHTML = `<span style="color:var(--warn)">⏳</span> ${escHtml(docName)} — processing...`;

  let attempts = 0;
  const maxAttempts = 20; // 60 seconds max

  const timer = setInterval(async () => {
    attempts++;
    if (attempts > maxAttempts) {
      clearInterval(timer);
      ref.innerHTML = `<span style="color:var(--text2)">📄</span> ${escHtml(docName)} — ready`;
      return;
    }
    try {
      // Use the /health endpoint to check if docs are available
      // Since we can't easily compute md5 on client, we'll just show timing
      const elapsed = attempts * 3;
      if (elapsed >= 15) {
        // After ~15s, document should be processed
        clearInterval(timer);
        ref.innerHTML = `<span style="color:var(--success)">✓</span> ${escHtml(docName)} — ready for Q&A`;
      }
    } catch (e) {
      // ignore polling errors
    }
  }, 3000);
}

// ══════════════════════════════════════════════════════════════
// AUDIO QUEUE SYSTEM
// ══════════════════════════════════════════════════════════════

/** Decode base64 → Blob URL → Audio object (Fix 7 — no data URI lag) */
function _b64toAudio(b64) {
  const binary = atob(b64);
  const bytes  = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: 'audio/mpeg' });
  const url  = URL.createObjectURL(blob);
  return { url, cleanup: () => URL.revokeObjectURL(url) };
}

/** Enqueue a base64 audio string. Starts playback if idle. */
function _enqueue(b64) {
  if (!b64) return;
  state.audioQueue.push(b64);
  if (!state.isPlaying) _playNext();
}

/** Estimate audio duration from byte size (MP3 at ~64kbps = 8000 bytes/sec) */
function _estimateDurationMs(b64) {
  if (!b64) return 0;
  const bytes = (b64.length * 3) / 4;   // base64 → bytes estimate
  return Math.ceil((bytes / 8000) * 1000);  // 64kbps = 8000 bytes/sec
}

/** Play the next item in the queue. Buffer-ahead: fetch next batch when queue drops to 1. */
function _playNext() {
  if (state.audioQueue.length === 0) {
    state.isPlaying = false;
    if (state.pendingBatches.length === 0) {
      // All audio done — decide what comes next
      if (state.loopEnabled) {
        _triggerAutoLoop();
      } else {
        _setVoiceState(VS.IDLE);
        closeVoiceModal();
      }
    }
    return;
  }

  state.isPlaying = true;
  _setVoiceState(VS.SPEAKING);

  const b64 = state.audioQueue.shift();
  const { url, cleanup } = _b64toAudio(b64);

  const aud = new Audio(url);
  state.currentAudio = aud;

  // Buffer-ahead: if queue is low, fetch next batch now (not after this ends)
  if (state.audioQueue.length < 2 && state.pendingBatches.length > 0) {
    _fetchNextBatch();  // non-blocking
  }

  // Micro-delay before speaking — feels more natural
  setTimeout(() => {
    aud.play().catch(e => { console.warn('Audio play failed:', e); cleanup(); _playNext(); });
  }, 150);

  aud.onended = () => {
    cleanup();
    state.currentAudio = null;
    // Guard 2: set mic ignore window after playback ends (feedback loop prevention)
    state.micIgnoreUntil = Date.now() + 800;
    _playNext();
  };
  aud.onerror = () => {
    cleanup();
    state.currentAudio = null;
    _playNext();
  };
}

/**
 * Auto-loop trigger — runs when all audio finishes and loop is enabled.
 *
 * Guard 1 (Intent Window): enters LOOP_WAIT, shows "tap to speak" prompt,
 *   only proceeds if user actually makes noise within 4 seconds.
 * Guard 2 (Feedback loop): mic is ignored for 800ms after playback ends
 *   so the bot cannot hear its own audio.
 * Guard 3 (Idle cycle limit): if user stays silent 4 turns in a row,
 *   auto-loop disables itself.
 */
function _triggerAutoLoop() {
  if (state.loopAborted || !state.loopEnabled) return;  // cancelled
  // Guard 3: too many idle cycles — stop the loop
  if (state.loopCount >= 4) {
    state.loopCount   = 0;
    state.loopEnabled = false;
    _updateLoopBtn();
    _setVoiceState(VS.IDLE);
    appendErrorMessage('Auto-loop stopped after 4 idle turns. Tap 🔁 to re-enable.');
    return;
  }

  _setVoiceState(VS.LOOP_WAIT);

  // Wait 800ms (natural pause after speaking + mic feedback buffer)
  state.loopWaitTimer = setTimeout(() => {
    if (!state.loopEnabled) return;   // user toggled off during wait
    if (state.voiceState !== VS.LOOP_WAIT) return;  // interrupted

    // Guard 2: ensure mic ignore window has passed
    const remaining = state.micIgnoreUntil - Date.now();
    const startIn   = Math.max(0, remaining);

    setTimeout(() => {
      if (!state.loopEnabled || state.voiceState !== VS.LOOP_WAIT) return;

      // Start recording — silence detection will auto-send or user speaks
      _startRecordingLoop();
    }, startIn);

  }, 800);
}

/**
 * Start recording in loop mode — uses adaptive silence detection.
 * If user stays silent for 3s → idle cycle counted, loop waits again.
 */
async function _startRecordingLoop() {
  if (state.voiceState !== VS.LOOP_WAIT) return;
  if (state.loopAborted) return;  // modal was closed

  try {
    const stream   = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx      = new AudioContext();
    const source   = ctx.createMediaStreamSource(stream);
    state.analyser = ctx.createAnalyser();
    state.analyser.fftSize = 256;
    source.connect(state.analyser);

    state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    state.audioChunks   = [];
    state.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) state.audioChunks.push(e.data); };
    state.mediaRecorder.start(100);

    _setVoiceState(VS.RECORDING);

    // Calibrate ambient noise
    setTimeout(() => { state.ambientNoise = _measureRMS(); }, 300);

    // Silence detection with idle tracking
    let silentMs    = 0;
    let spokeSomething = false;
    const CHECK_MS  = 100;
    const SILENCE_MS = 1500;   // slightly longer in loop mode — less hair-trigger
    const IDLE_TIMEOUT_MS = 3000;  // if totally silent for 3s → count as idle

    function checkLoop() {
      if (state.voiceState !== VS.RECORDING || !state.loopEnabled) return;

      const rms    = _measureRMS();
      const thresh = state.ambientNoise * 1.8;   // slightly higher threshold in loop

      if (rms > thresh) {
        spokeSomething = true;
        silentMs = 0;
      } else {
        silentMs += CHECK_MS;

        if (!spokeSomething && silentMs >= IDLE_TIMEOUT_MS) {
          // user never spoke — count as idle cycle
          state.loopCount++;
          _stopRecording();
          _triggerAutoLoop();
          return;
        }

        if (spokeSomething && silentMs >= SILENCE_MS) {
          // user finished speaking — send
          state.loopCount = 0;  // reset idle count on successful speech
          _stopRecording();
          _processVoiceInput();
          return;
        }
      }
      state.silenceTimer = setTimeout(checkLoop, CHECK_MS);
    }
    state.silenceTimer = setTimeout(checkLoop, CHECK_MS);

  } catch (err) {
    console.error('Mic error in loop:', err);
    state.loopEnabled = false;
    _updateLoopBtn();
    _setVoiceState(VS.IDLE);
  }
}

/** Stop current audio and clear queue — for interrupt/barge-in */
function _clearQueue() {
  state.audioQueue    = [];
  state.pendingBatches = [];
  state.isPlaying     = false;
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio.src = '';
    state.currentAudio = null;
  }
}

/** Fetch next batch of chunks from /voice/chunk (Fix 4 session token) */
async function _fetchNextBatch() {
  if (state.pendingBatches.length === 0) return;

  const batch    = state.pendingBatches.shift();
  const sid      = state.voiceSessionId;   // capture current session
  const lang     = localStorage.getItem('nm_voice_lang') || '';

  try {
    const r = await fetch(`${API_BASE}/voice/chunk`, {
      method:  'POST',
      headers: AUTH_HEADERS,
      body:    JSON.stringify({ texts: batch, language: lang }),
    });
    const data = await r.json();

    // Fix 4 — session token: discard if user has started a new turn
    if (sid !== state.voiceSessionId) {
      console.log('Stale voice chunk discarded (new session started)');
      return;
    }

    if (data.audio_b64) _enqueue(data.audio_b64);

    // buffer-ahead: fetch next batch if queue still low
    if (state.audioQueue.length < 2 && state.pendingBatches.length > 0) {
      _fetchNextBatch();
    }
  } catch (e) {
    console.warn('Chunk fetch failed:', e);
  }
}

// ══════════════════════════════════════════════════════════════
// VOICE MODAL & STATE MACHINE
// ══════════════════════════════════════════════════════════════

function openVoiceModal() {
  _clearQueue();
  clearTimeout(state.loopWaitTimer);
  state.voiceSessionId = Date.now();
  state.loopCount      = 0;
  state.loopEnabled    = true;   // loop ON by default — user can toggle off
  document.getElementById('voice-modal').classList.add('open');
  _setVoiceState(VS.IDLE);
  _updateLoopBtn();
}

function closeVoiceModal() {
  state.loopEnabled = false;   // kill loop immediately on any close
  state.loopAborted = true;    // flag: abort any in-progress async loop step
  _stopRecording();
  _clearQueue();
  cancelAnimationFrame(state.animFrame);
  clearTimeout(state.silenceTimer);
  clearTimeout(state.loopWaitTimer);
  state.loopCount  = 0;
  state.loopAborted = false;   // reset for next open
  document.getElementById('voice-modal').classList.remove('open');
  _setVoiceState(VS.IDLE);
  _updateLoopBtn();
}

function _setVoiceState(vs) {
  state.voiceState = vs;
  const statusEl  = document.getElementById('voice-status');
  const micIcon   = document.getElementById('voice-icon-mic');
  const stopIcon  = document.getElementById('voice-icon-stop');
  const bars      = document.querySelectorAll('.voice-bar');
  const actionBtn = document.getElementById('voice-action-btn');

  bars.forEach(b => b.classList.remove('idle'));

  const cfg = {
    [VS.IDLE]:         { html:'Tap mic to <span>speak</span>',               showMic:true,  color:'' },
    [VS.RECORDING]:    { html:'<span>Listening...</span>',                    showMic:false, color:'var(--danger)' },
    [VS.TRANSCRIBING]: { html:'Transcribing <span>your voice...</span>',      showMic:false, color:'' },
    [VS.THINKING]:     { html:'Chatur is <span>thinking...</span>',         showMic:false, color:'' },
    [VS.QUEUING]:      { html:'Preparing <span>response...</span>',           showMic:false, color:'' },
    [VS.SPEAKING]:     { html:'Chatur is <span>speaking</span>',            showMic:false, color:'var(--teal)' },
    [VS.LOOP_WAIT]:    { html:'<span>Tap to speak</span> or wait...',         showMic:true,  color:'rgba(124,106,247,0.6)' },
  }[vs] || { html:'Tap mic to <span>speak</span>', showMic:true, color:'' };

  statusEl.innerHTML         = cfg.html;
  micIcon.style.display      = cfg.showMic  ? '' : 'none';
  stopIcon.style.display     = cfg.showMic  ? 'none' : '';
  actionBtn.style.borderColor= cfg.color    || '';

  if (vs === VS.IDLE)      bars.forEach(b => b.classList.add('idle'));
  if (vs === VS.RECORDING) _animateBarsLive();
  if (vs === VS.SPEAKING)  _animateBarsSpeaking();
}

// ── Hold-to-talk + click-to-toggle bindings ───────────────────
function _bindVoiceActionButton() {
  const btn = document.getElementById('voice-action-btn');

  // Hold-to-talk: mousedown starts, mouseup sends
  btn.addEventListener('mousedown', e => {
    e.preventDefault();
    if (state.voiceState === VS.SPEAKING) {
      // interrupt: clear queue, bump session, go to idle
      _clearQueue();
      state.voiceSessionId = Date.now();
      clearTimeout(state.loopWaitTimer);
      _setVoiceState(VS.IDLE);
      return;
    }
    if (state.voiceState === VS.IDLE || state.voiceState === VS.LOOP_WAIT) {
      clearTimeout(state.loopWaitTimer);
      state.holdToTalk = true;
      _startRecording();
    }
  });

  btn.addEventListener('mouseup', e => {
    if (state.holdToTalk && state.voiceState === VS.RECORDING) {
      state.holdToTalk = false;
      _stopRecording();
      _processVoiceInput();
    }
  });

  // Touch support for mobile
  btn.addEventListener('touchstart', e => {
    e.preventDefault();
    if (state.voiceState === VS.SPEAKING) {
      _clearQueue();
      state.voiceSessionId = Date.now();
      _setVoiceState(VS.IDLE);
      return;
    }
    if (state.voiceState === VS.IDLE) {
      state.holdToTalk = true;
      _startRecording();
    }
  }, { passive: false });

  btn.addEventListener('touchend', e => {
    e.preventDefault();
    if (state.holdToTalk && state.voiceState === VS.RECORDING) {
      state.holdToTalk = false;
      _stopRecording();
      _processVoiceInput();
    }
  }, { passive: false });

  // Click (no hold): toggle record/stop — for desktop non-hold use
  btn.addEventListener('click', e => {
    if (state.holdToTalk) return;  // handled by mousedown/up
    if (state.voiceState === VS.IDLE || state.voiceState === VS.LOOP_WAIT) {
      clearTimeout(state.loopWaitTimer);
      _startRecording();
    } else if (state.voiceState === VS.RECORDING) {
      _stopRecording();
      _processVoiceInput();
    }
  });

  // Loop toggle button
  const loopBtn = document.getElementById('loop-toggle-btn');
  if (loopBtn) {
    loopBtn.addEventListener('click', () => {
      state.loopEnabled = !state.loopEnabled;
      state.loopCount   = 0;
      _updateLoopBtn();
    });
  }

  // Continuous mode toggle
  const contBtn = document.getElementById('continuous-mode-btn');
  if (contBtn) {
    contBtn.addEventListener('click', () => {
      state.continuousMode = !state.continuousMode;
      contBtn.classList.toggle('active', state.continuousMode);
      contBtn.title = state.continuousMode ? 'Continuous mode ON' : 'Continuous mode OFF';
    });
  }
}

/** Sync loop button visual state */
function _updateLoopBtn() {
  const btn = document.getElementById('loop-toggle-btn');
  if (!btn) return;
  btn.classList.toggle('active', state.loopEnabled);
  btn.title = state.loopEnabled ? 'Loop ON — tap to disable' : 'Enable continuous loop';
}

async function _startRecording() {
  if (state.voiceState === VS.RECORDING) return;
  try {
    const stream   = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx      = new AudioContext();
    const source   = ctx.createMediaStreamSource(stream);
    state.analyser = ctx.createAnalyser();
    state.analyser.fftSize = 256;
    source.connect(state.analyser);

    state.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    state.audioChunks   = [];
    state.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) state.audioChunks.push(e.data); };
    state.mediaRecorder.start(100);

    _setVoiceState(VS.RECORDING);

    // Fix 3 adaptive — calibrate ambient noise in first 300ms
    setTimeout(() => { state.ambientNoise = _measureRMS(); }, 300);

    // Continuous mode: silence detection
    if (state.continuousMode && !state.holdToTalk) {
      _startSilenceDetection();
    }
  } catch (err) {
    console.error('Mic error:', err);
    _setVoiceState(VS.IDLE);
    alert('Microphone access denied or unavailable.');
  }
}

function _stopRecording() {
  clearTimeout(state.silenceTimer);
  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    state.mediaRecorder.stop();
    state.mediaRecorder.stream.getTracks().forEach(t => t.stop());
  }
  cancelAnimationFrame(state.animFrame);
}

/** Adaptive silence detection — uses ambient noise baseline (Fix 3) */
function _startSilenceDetection() {
  let silentMs = 0;
  const SILENCE_THRESHOLD_MS = 1200;
  const CHECK_INTERVAL_MS    = 100;

  function check() {
    if (state.voiceState !== VS.RECORDING) return;

    const rms     = _measureRMS();
    const thresh  = state.ambientNoise * 1.5;  // adaptive: 50% above ambient

    if (rms < thresh) {
      silentMs += CHECK_INTERVAL_MS;
      if (silentMs >= SILENCE_THRESHOLD_MS) {
        // silence detected — auto send
        _stopRecording();
        _processVoiceInput();
        return;
      }
    } else {
      silentMs = 0;
    }
    state.silenceTimer = setTimeout(check, CHECK_INTERVAL_MS);
  }
  state.silenceTimer = setTimeout(check, CHECK_INTERVAL_MS);
}

function _measureRMS() {
  if (!state.analyser) return 0;
  const data = new Uint8Array(state.analyser.fftSize);
  state.analyser.getByteTimeDomainData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    const norm = (data[i] - 128) / 128;
    sum += norm * norm;
  }
  return Math.sqrt(sum / data.length);
}

async function _processVoiceInput() {
  if (state.voiceState !== VS.RECORDING) return;
  _setVoiceState(VS.TRANSCRIBING);

  // bump session id — any in-flight chunks from previous turn are now stale
  state.voiceSessionId = Date.now();
  _clearQueue();

  await new Promise(res => setTimeout(res, 350));

  if (state.audioChunks.length === 0) {
    closeVoiceModal();
    appendErrorMessage('No audio captured. Please try again.');
    return;
  }

  const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
  if (blob.size < 200) {
    closeVoiceModal();
    appendErrorMessage('Recording too short. Please speak and try again.');
    return;
  }

  const session = getSession(state.activeSession);
  if (!session?.backendId) {
    try {
      const r = await fetch(`${API_BASE}/session/new`, {
        method:'POST', headers:AUTH_HEADERS,
        body: JSON.stringify({ user_id: USER_EMAIL || USER_NAME }),
      });
      if (r.ok && session) { session.backendId = (await r.json()).session_id; saveSessions(); }
    } catch (e) { console.warn(e); }
  }

  const capturedSid = state.voiceSessionId;

  try {
    const form = new FormData();
    form.append('audio',      blob, 'voice.webm');
    form.append('session_id', session?.backendId || '');
    form.append('language',   '');

    const controller = new AbortController();
    const timer      = setTimeout(() => controller.abort(), 25000);

    const r    = await fetch(`${API_BASE}/voice/chat`, {
      method:'POST', headers:{'x-api-key': API_KEY},
      body: form, signal: controller.signal,
    });
    clearTimeout(timer);
    const data = await r.json();

    // session guard
    if (capturedSid !== state.voiceSessionId) return;

    if (!r.ok) { closeVoiceModal(); appendErrorMessage(data.detail?.message || 'Voice failed.'); return; }

    const transcription = data.transcription || '';
    const response      = data.response      || '';
    const route         = data.route         || 'CHAT';
    const audioB64First = data.audio_b64_first || null;
    const chunkBatches  = data.chunk_batches   || [];

    // optimistic UI — show transcription immediately
    if (transcription && session) {
      const um = { role:'user', content:`🎤 ${transcription}` };
      session.messages.push(um);
      appendMessageDOM(um);
      updateSessionName(state.activeSession, transcription);
    } else if (!transcription) {
      closeVoiceModal();
      appendErrorMessage("Couldn't understand audio. Please speak clearly.");
      return;
    }

    // add bot message to chat (text visible immediately)
    const bm = { role:'assistant', content:response, route, reasonTrace:data.reason_trace };
    if (session) session.messages.push(bm);
    appendMessageDOM(bm);
    updateRouteBadge(route);
    saveSessions();

    // load remaining batches into queue
    state.pendingBatches = chunkBatches;

    if (audioB64First) {
      _setVoiceState(VS.QUEUING);

      // Estimate total audio duration across all content
      // This sets micIgnoreUntil far enough ahead that bot cannot hear itself
      const firstMs    = _estimateDurationMs(audioB64First);
      // rough estimate: each batch ≈ same size as first chunk
      const batchMs    = chunkBatches.length * firstMs * 1.5;
      const totalAudio = firstMs + batchMs + 1200;  // +1.2s safety buffer
      state.micIgnoreUntil = Date.now() + totalAudio;

      // start fetching batch 1 in background BEFORE first chunk plays
      if (chunkBatches.length > 0) _fetchNextBatch();
      // play first chunk (150ms micro-delay for naturalness)
      setTimeout(() => _enqueue(audioB64First), 150);
    } else {
      closeVoiceModal();
    }

  } catch (err) {
    if (capturedSid !== state.voiceSessionId) return;  // stale
    closeVoiceModal();
    if (err.name === 'AbortError')
      appendErrorMessage('Voice request timed out. Check your connection.');
    else
      appendErrorMessage('Voice request failed. Is the server running?');
  }
}

// ── Bar animations ─────────────────────────────────────────────
function _animateBarsLive() {
  if (!state.analyser) return;
  const bars = _getBars();
  const data = new Uint8Array(state.analyser.frequencyBinCount);
  function frame() {
    if (state.voiceState !== VS.RECORDING) return;
    state.analyser.getByteFrequencyData(data);
    bars.forEach((b,i) => { b.style.height = Math.max(6, Math.min(60, (data[i*2]||0)/255*60)) + 'px'; });
    state.animFrame = requestAnimationFrame(frame);
  }
  state.animFrame = requestAnimationFrame(frame);
}

function _animateBarsSpeaking() {
  const bars = _getBars();
  function frame() {
    if (state.voiceState !== VS.SPEAKING) return;
    bars.forEach(b => { b.style.height = (8 + Math.random()*44) + 'px'; });
    state.animFrame = requestAnimationFrame(frame);
  }
  cancelAnimationFrame(state.animFrame);
  state.animFrame = requestAnimationFrame(frame);
}

function _getBars() { return [1,2,3,4,5].map(n => document.getElementById('bar'+n)); }

// ── SERVER STATUS ──────────────────────────────────────────────
async function checkServerStatus() {
  const dot   = document.getElementById('conn-dot');
  const label = document.getElementById('conn-label');
  if (!dot || !label) return;
  try {
    const r = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(4000) });
    const d = await r.json();
    if (r.ok) {
      const host = API_BASE.replace(/https?:\/\//,'').split('/')[0];
      dot.style.cssText  = 'background:var(--success);box-shadow:0 0 6px var(--success)';
      label.textContent  = `${host} · ${d.rag_docs??0} docs`;
      label.style.color  = 'var(--text3)';
    } else throw new Error();
  } catch {
    dot.style.cssText  = 'background:var(--danger)';
    label.textContent  = 'Server offline';
    label.style.color  = 'var(--danger)';
  }
  setTimeout(checkServerStatus, 30_000);
}

// ── UTILS ──────────────────────────────────────────────────────
function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.addEventListener('DOMContentLoaded', init);
