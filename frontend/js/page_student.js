/**
 * M.A.C.H. AI — Student Conversational Tutoring Controller
 * Handles chat sessions, real-time token streaming with Gemma reasoning accordions,
 * past year question card rendering, zoomable diagrams, and Web Speech dictation.
 */
import { API_BASE, apiFetch } from './api.js';
import { getUsername, requireRole, logout } from './auth.js';
import { timeNow, escapeHtml, simpleMarkdown, refreshPage } from './utils.js';

requireRole('student');

const uname = getUsername() || 'Student';
document.getElementById('student-name').textContent = uname.charAt(0).toUpperCase() + uname.slice(1);

let isTyping = false;
let activeAbortController = null;
let activeStreamReader = null;
let userStopped = false;
let currentSessionId = null;
let chatSessions = {};

function setGeneratingUI(active) {
  const sendBtn = document.getElementById('send-btn');
  const stopBtn = document.getElementById('stop-btn');
  if (sendBtn) {
    sendBtn.disabled = active;
    sendBtn.style.display = active ? 'none' : 'flex';
  }
  if (stopBtn) {
    stopBtn.style.display = active ? 'flex' : 'none';
  }
}

export function stopGeneration() {
  if (!isTyping && !activeAbortController) return;
  userStopped = true;
  if (activeAbortController) {
    activeAbortController.abort();
  }
  if (activeStreamReader) {
    activeStreamReader.cancel().catch(() => {});
  }
}
window.stopGeneration = stopGeneration;

function scrollBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

function saveCurrentSession() {
  if (!currentSessionId) return;
  const msgs = document.getElementById('messages');
  if (chatSessions[currentSessionId]) {
    chatSessions[currentSessionId].html = msgs.innerHTML;
  }
}

const EMPTY_STATE_HTML = `
  <div class="empty-state" id="empty-state">
    <img src="icons/icon-192.png" alt="MACH AI" class="es-logo-img no-lightbox" />
    <h3>Ready when you are</h3>
    <div class="suggestion-grid">
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What is the syllabus of 23CSE303?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg></span>
      </button>
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What are the courses in Semester 5?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg></span>
      </button>
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What are the prerequisites for 23CSE211?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 17 22 12"/></svg></span>
      </button>
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What are the electives in Artificial Intelligence?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
      </button>
    </div>
  </div>`;

export function clearChat() {
  if (isTyping) {
    stopGeneration();
  }
  saveCurrentSession();
  currentSessionId = crypto.randomUUID();
  chatSessions[currentSessionId] = { id: currentSessionId, title: null, html: EMPTY_STATE_HTML };

  document.getElementById('messages').innerHTML = EMPTY_STATE_HTML;
  removeTyping();

  document.querySelectorAll('.hist-item').forEach(h => h.classList.remove('active'));
}
window.clearChat = clearChat;

export function loadChat(id) {
  if (isTyping) {
    stopGeneration();
  }
  if (currentSessionId === id) return;

  saveCurrentSession();
  currentSessionId = id;
  const session = chatSessions[id];
  document.getElementById('messages').innerHTML = session.html;

  document.querySelectorAll('.hist-item').forEach(h => h.classList.remove('active'));
  const activeItem = document.getElementById('hist-' + id);
  if (activeItem) activeItem.classList.add('active');

  scrollBottom();
}
window.loadChat = loadChat;

clearChat();

function addUserMessage(text, imageB64 = null) {
  const es = document.getElementById('empty-state');
  if (es) es.remove();

  let imgHtml = '';
  if (imageB64 && (imageB64.startsWith('data:image/') || imageB64.startsWith('http://') || imageB64.startsWith('https://') || imageB64.startsWith('/static/'))) {
    const resolvedSrc = imageB64.startsWith('/') ? `${API_BASE}${imageB64}` : imageB64;
    imgHtml = `<img src="${escapeHtml(resolvedSrc)}" class="user-attached-img" alt="Attached Image" />`;
  }

  const row = document.createElement('div');
  row.className = 'msg-row user-row';
  row.innerHTML = `
    <div class="msg-av user"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg></div>
    <div class="msg-content">
      <div class="bubble">
        ${imgHtml}
        <div>${escapeHtml(text)}</div>
      </div>
      <div class="bubble-footer">
        <div></div>
        <div class="bubble-time">${timeNow()}</div>
      </div>
    </div>`;
  document.getElementById('messages').appendChild(row);
  scrollBottom();
}

function showTyping(isVision = false) {
  if (isTyping) return;
  isTyping = true;
  const row = document.createElement('div');
  row.className = 'typing-row'; row.id = 'typing-row';

  if (isVision) {
    row.innerHTML = `<div class="msg-av ai vision"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></div><div class="typing-bubble vision-bubble">Reading uploaded image<span class="dot-anim">...</span></div>`;
  } else {
    row.innerHTML = `<div class="msg-av ai"><img src="icons/icon-192.png" alt="M" class="no-lightbox" /></div><div class="typing-bubble"><div class="t-dot"></div><div class="t-dot"></div><div class="t-dot"></div></div>`;
  }

  document.getElementById('messages').appendChild(row);
  scrollBottom();
}

function removeTyping() {
  const tr = document.getElementById('typing-row');
  if (tr) tr.remove();
  isTyping = false;
}

function createAIBubble(msgId) {
  const id = 'msg-' + Date.now();
  const safeId = msgId || '';
  const row = document.createElement('div');
  row.className = 'msg-row';
  row.innerHTML = `
    <div class="msg-av ai"><img src="icons/icon-192.png" alt="M" class="no-lightbox" /></div>
    <div class="msg-content">
      <div class="bubble ai-bubble" id="${id}"></div>
      <div class="bubble-footer">
        <div class="bubble-time">MACH AI · ${timeNow()}</div>
        <div class="feedback-btns">
          <button class="fb-btn" onclick="window.vote(this,'up','${safeId}')" title="Helpful">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
          </button>
          <button class="fb-btn" onclick="window.vote(this,'down','${safeId}')" title="Not helpful">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
        </div>
      </div>
    </div>`;
  document.getElementById('messages').appendChild(row);
  scrollBottom();
  return document.getElementById(id);
}

/**
 * Renders a structured "Problem List" response as visual cards.
 * Called when the backend returns X-Response-Type: problem_list (PROBLEM_LIST intent).
 * Each card shows: question number, marks, BTL level, and the question text.
 *
 * @param {object} data - The parsed JSON from the /chat endpoint
 * @param {string} data.topic - The extracted topic name
 * @param {Array}  data.problems - Array of problem objects from Neo4j
 * @returns {string} Safe HTML string
 */
function renderProblemCards(data) {
  const { topic = 'Topic', problems = [] } = data;

  if (!problems.length) {
    if (data.subgraph_expansion && data.subgraph_expansion.course_code) {
      const sub = data.subgraph_expansion;
      const topicsHtml = (sub.topics || []).map(t => `<span style="display:inline-block;padding:2px 7px;background:#f1f5f9;color:#334155;border-radius:4px;font-size:11.5px;border:1px solid #cbd5e1;">${escapeHtml(t)}</span>`).join('');
      const prereqsHtml = (sub.prerequisites || []).map(p => `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 7px;background:#e0f2fe;color:#0369a1;border-radius:4px;font-size:11.5px;border:1px solid #bae6fd;font-weight:600;"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>${escapeHtml(p)}</span>`).join('');
      const coHtml = (sub.course_outcomes || []).map(co => `<div style="display:flex;align-items:flex-start;gap:6px;font-size:12px;color:#475569;margin-top:4px;"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#059669" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-top:2px;"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg><span><strong>${escapeHtml(co)}</strong></span></div>`).join('');
      return `
        <div class="problem-list-empty" style="text-align:left;max-width:680px;margin:0 auto;padding:16px 20px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#c92a42" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            <strong style="font-size:14.5px;color:#0f172a;">${escapeHtml(sub.course_name)} (${escapeHtml(sub.course_code)})</strong>
          </div>
          <p style="font-size:13px;color:#475569;margin:0 0 10px 0;">No direct past exam questions found for <strong>${escapeHtml(topic)}</strong> yet. Showing curriculum details for this course:</p>
          ${prereqsHtml ? `<div style="margin-bottom:10px;"><div style="font-size:12px;font-weight:600;color:#64748b;margin-bottom:4px;">Prerequisites:</div><div style="display:flex;flex-wrap:wrap;gap:5px;">${prereqsHtml}</div></div>` : ''}
          ${topicsHtml ? `<div style="margin-bottom:10px;"><div style="font-size:12px;font-weight:600;color:#64748b;margin-bottom:4px;">Key Syllabus Topics:</div><div style="display:flex;flex-wrap:wrap;gap:5px;">${topicsHtml}</div></div>` : ''}
          ${coHtml ? `<div style="margin-bottom:10px;"><div style="font-size:12px;font-weight:600;color:#64748b;margin-bottom:4px;">Course Outcomes:</div>${coHtml}</div>` : ''}
          <div style="margin-top:12px;font-size:12px;color:#64748b;font-style:italic;">Past question papers for this course will appear here once uploaded.</div>
        </div>`;
    }
    if (data.syllabus_guidance) {
      const sg = data.syllabus_guidance;
      const unitsHtml = (sg.units || []).map(u => `
        <div style="margin-top:10px;padding:8px 12px;background:rgba(255,255,255,0.7);border-radius:8px;border:1px solid #e2e8f0;">
          <div style="font-weight:600;font-size:12.5px;color:#c92a42;">${escapeHtml(u.unit)}</div>
          <div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:5px;">
            ${(u.topics || []).map(t => `<span style="display:inline-block;padding:2px 7px;background:#f1f5f9;color:#334155;border-radius:4px;font-size:11.5px;border:1px solid #cbd5e1;">${escapeHtml(t)}</span>`).join('')}
          </div>
        </div>
      `).join('');
      return `
        <div class="problem-list-empty" style="text-align:left;max-width:680px;margin:0 auto;padding:16px 20px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#c92a42" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            <strong style="font-size:14.5px;color:#0f172a;">${escapeHtml(sg.course_name)} (${escapeHtml(sg.course_code)})</strong>
          </div>
          <p style="font-size:13px;color:#475569;margin:0 0 8px 0;">No past exam papers are uploaded for this course yet. Here are the syllabus units and topics from the curriculum:</p>
          ${unitsHtml}
          <div style="margin-top:12px;font-size:12px;color:#64748b;font-style:italic;">Past exam cards will show up here once question papers are uploaded.</div>
        </div>`;
    }
    return `
      <div class="problem-list-empty">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <p>No past exam questions found for <strong>${escapeHtml(topic)}</strong> yet.</p>
      </div>`;
  }

  const btlLabel = (btl) => {
    const map = { 1:'Remember', 2:'Understand', 3:'Apply', 4:'Analyse', 5:'Evaluate', 6:'Create' };
    const n = parseInt(btl);
    return map[n] ? `BTL ${n} · ${map[n]}` : `BTL ${btl}`;
  };

  const cards = problems.map((p, idx) => {
    const rawText = p.text || p.question_text || 'Question text unavailable';
    const qText   = escapeHtml(rawText);
    const marks   = p.marks   ? `<span class="pc-mark">${p.marks} marks</span>` : '';
    const btl     = p.btl    ? `<span class="pc-btl">${btlLabel(p.btl)}</span>` : '';
    const course  = p.course_code ? `<span class="pc-course">${escapeHtml(p.course_code)}</span>` : '';
    const topicTag = p.topic_name ? `<span class="pc-topic-badge" style="font-size:11px;padding:2px 7px;background:#ede9fe;color:#6d28d9;border-radius:4px;border:1px solid #ddd6fe;font-weight:500;display:inline-flex;align-items:center;gap:4px;"><svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>${escapeHtml(p.topic_name)}</span>` : '';

    // Zoomable Image
    const resolvedImgUrl = p.image_url && p.image_url !== 'None'
      ? (p.image_url.startsWith('/') ? `${API_BASE}${p.image_url}` : p.image_url)
      : '';
    const imgHtml = resolvedImgUrl
      ? `<div class="pc-img-wrap" style="position:relative;margin-top:10px;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0;background:#ffffff;cursor:zoom-in;">
           <img src="${escapeHtml(resolvedImgUrl)}" alt="Diagram for Q${idx+1}" class="pc-img" style="width:100%;max-height:260px;object-fit:contain;display:block;">
           <div style="position:absolute;bottom:6px;right:6px;background:rgba(15,23,42,0.75);color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;display:flex;align-items:center;gap:4px;backdrop-filter:blur(4px);pointer-events:none;">
             <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
             Click to Zoom
           </div>
         </div>`
      : '';

    // Topic match badge
    let aiBadge = '';
    if (p.mapping_confidence) {
      const conf = Math.round(p.mapping_confidence * 100);
      aiBadge = `<span class="pc-ai-badge" title="Match Confidence: ${conf}% (${escapeHtml(p.topic_name || '')})">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
        Topic Match
      </span>`;
    }

    return `
      <div class="problem-card">
        <div class="pc-header">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="pc-num">Q${idx + 1}</span>
            ${topicTag}
          </div>
          <div class="pc-tags">${aiBadge}${course}${marks}${btl}</div>
        </div>
        <div class="pc-body">${qText}</div>
        ${imgHtml}
        <div class="pc-footer" style="margin-top:10px;padding-top:8px;border-top:1px solid #f1f5f9;display:flex;justify-content:flex-end;">
          <button class="pc-copy-btn" onclick="window.copyProblemCard(this)" style="display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:#64748b;background:none;border:1px solid #e2e8f0;padding:3px 9px;border-radius:6px;cursor:pointer;transition:all 0.15s ease;">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            <span>Copy Question</span>
          </button>
        </div>
      </div>`;
  }).join('');

  return `
    <div class="problem-list-wrap">
      <div class="problem-list-header">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <span>Problems on <strong>${escapeHtml(topic)}</strong></span>
        <span class="pl-count">${problems.length} found</span>
      </div>
      <div class="problem-cards">${cards}</div>
    </div>`;
}

function showCopiedFeedback(btn) {
  const orig = btn.innerHTML;
  btn.innerHTML = `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="#16a34a" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span style="color:#16a34a;font-weight:600;">Copied!</span>`;
  setTimeout(() => { btn.innerHTML = orig; }, 1800);
}

window.copyProblemText = function(btn, text) {
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => showCopiedFeedback(btn));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    showCopiedFeedback(btn);
  }
};

window.copyProblemCard = function(btn) {
  const card = btn.closest('.problem-card');
  const body = card ? card.querySelector('.pc-body') : null;
  if (body) window.copyProblemText(btn, body.textContent || '');
};

/**
 * Renders a structured curriculum result (course units, semester table, elective stream, or details).
 */
function renderCurriculumResult(data) {
  if (!data) return '<div class="problem-list-empty"><p>No curriculum data found.</p></div>';
  const matchType = data.match_type;

  if (matchType === 'exact') {
    const records = data.records || [];
    if (!records.length) return '<div class="problem-list-empty"><p>No syllabus content found for this course.</p></div>';
    const cCode = escapeHtml(records[0].course_code || '');
    const cName = escapeHtml(records[0].course_name || '');

    const unitsMap = {};
    for (const r of records) {
      const uNum = r.unit_number || 1;
      const uTitle = r.unit_title || `Unit ${uNum}`;
      if (!unitsMap[uNum]) {
        unitsMap[uNum] = { title: uTitle, topics: new Set() };
      }
      (r.topics || []).forEach(t => unitsMap[uNum].topics.add(t));
    }

    const unitsHtml = Object.keys(unitsMap).sort((a,b) => parseInt(a)-parseInt(b)).map(uNum => {
      const u = unitsMap[uNum];
      const topics = Array.from(u.topics).map(t => `<span class="topic-pill">${escapeHtml(t)}</span>`).join('');
      return `
        <div class="curriculum-unit-card">
          <div class="unit-hdr">
            <span class="unit-badge">Unit ${uNum}</span>
            <span class="unit-title">${escapeHtml(u.title)}</span>
          </div>
          <div class="unit-topics">${topics}</div>
        </div>
      `;
    }).join('');

    let extraHtml = '';
    if (data.extra_details) {
      const c = data.extra_details;
      const sem = (c.semester !== null && c.semester !== undefined) ? `
        <div class="detail-row">
          <span class="detail-label">Semester:</span>
          <span class="detail-val">Semester ${escapeHtml(String(c.semester))}</span>
        </div>` : '';
      const ep = c.evaluation_pattern ? `
        <div class="detail-row">
          <span class="detail-label">Evaluation Pattern:</span>
          <span class="detail-val font-mono">${escapeHtml(c.evaluation_pattern)}</span>
        </div>` : '';
      const cred = c.credits ? `
        <div class="detail-row">
          <span class="detail-label">Credits / LTPC:</span>
          <span class="detail-val">${escapeHtml(String(c.credits))} Credits (${c.L ?? 0}-${c.T ?? 0}-${c.P ?? 0})</span>
        </div>` : '';
      const labList = (c.lab_experiments && c.lab_experiments.length) ? `
        <div class="detail-block">
          <span class="detail-label">Lab Experiments:</span>
          <ul class="detail-list">${c.lab_experiments.map(l => `<li>${escapeHtml(l)}</li>`).join('')}</ul>
        </div>` : (c.has_no_lab_experiments ? `
        <div class="detail-row">
          <span class="detail-label">Lab Experiments:</span>
          <span class="detail-val" style="color:var(--text-3);font-style:italic;">No prescribed lab experiments for this course.</span>
        </div>` : '');
      const prereqList = (c.prerequisites && c.prerequisites.length) ? `
        <div class="detail-row">
          <span class="detail-label">Pre-Requisites:</span>
          <span class="detail-val">${c.prerequisites.map(p => escapeHtml(p)).join(', ')}</span>
        </div>` : '';
      const objList = (c.objectives && c.objectives.length) ? `
        <div class="detail-block">
          <span class="detail-label">Course Objectives:</span>
          <ul class="detail-list">${c.objectives.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul>
        </div>` : '';
      const coList = (c.outcomes && c.outcomes.length) ? `
        <div class="detail-block">
          <span class="detail-label">Course Outcomes:</span>
          <ul class="detail-list">${c.outcomes.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul>
        </div>` : '';
      const tbList = (c.textbooks && c.textbooks.length) ? `
        <div class="detail-block">
          <span class="detail-label">Textbooks:</span>
          <ul class="detail-list">${c.textbooks.map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul>
        </div>` : '';
      const refList = (c.references && c.references.length) ? `
        <div class="detail-block">
          <span class="detail-label">References:</span>
          <ul class="detail-list">${c.references.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
        </div>` : '';

      const detailBody = `${sem}${ep}${cred}${labList}${prereqList}${objList}${coList}${tbList}${refList}`;
      if (detailBody.trim()) {
        extraHtml = `
          <div class="course-detail-card" style="margin-top:12px;">
            <div class="detail-body">
              ${detailBody}
            </div>
          </div>
        `;
      }
    }

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">${cCode}</span>
            <h3 class="curr-name">${cName}</h3>
          </div>
          <span class="graph-fast-badge">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            Verified Syllabus
          </span>
        </div>
        <div class="curriculum-units-list">${unitsHtml}</div>
        ${extraHtml}
      </div>
    `;
  }

  if (matchType === 'semester_courses') {
    const sem = data.semester;
    const courses = data.courses || [];
    const courseCards = courses.map(c => {
      const cred = c.credits ? `<span class="sem-cred">${c.credits} Credits</span>` : '';
      const cat = c.category ? `<span class="sem-cat">${escapeHtml(c.category)}</span>` : '';
      const ltpc = (c.L !== undefined && c.T !== undefined && c.P !== undefined) ? `<span class="sem-ltpc">${c.L}-${c.T}-${c.P}</span>` : '';
      return `
        <div class="sem-course-card">
          <div class="sem-card-top">
            <span class="sem-code">${escapeHtml(c.course_code)}</span>
            <div class="sem-tags">${cat}${cred}${ltpc}</div>
          </div>
          <div class="sem-name">${escapeHtml(c.course_name)}</div>
        </div>
      `;
    }).join('');

    const totalCredBadge = data.total_credits ? ` &middot; ${escapeHtml(String(data.total_credits))} Total Credits` : '';

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">Semester ${sem}</span>
            <h3 class="curr-name">Curriculum & Course Structure</h3>
          </div>
          <span class="pl-count">${courses.length} courses${totalCredBadge}</span>
        </div>
        <div class="sem-course-grid">${courseCards}</div>
      </div>
    `;
  }

  if (matchType === 'curriculum_info') {
    const items = data.items || [];
    const listHtml = items.map(it => `
      <div class="curriculum-unit-card" style="padding:10px 14px;">
        <span class="curr-code-badge" style="margin-right:8px;">${escapeHtml(it.id || 'Info')}</span>
        <span style="font-size:13.5px;color:var(--text-1);line-height:1.5;">${escapeHtml(it.description || '')}</span>
      </div>
    `).join('');
    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <h3 class="curr-name">${escapeHtml(data.title || 'Curriculum Information')}</h3>
          <span class="graph-fast-badge">Verified Curriculum</span>
        </div>
        <div class="curriculum-units-list">${listHtml}</div>
      </div>
    `;
  }

  if (matchType === 'elective_baskets') {
    const baskets = data.baskets || [];
    const titleText = data.title || 'Available Elective Streams';
    const basketCards = baskets.map(b => `
      <div class="sem-course-card">
        <div class="sem-card-top">
          <span class="sem-code">${escapeHtml(String(b.course_count))} courses</span>
        </div>
        <div class="sem-name">${escapeHtml(b.basket_name)}</div>
      </div>
    `).join('');

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">Elective Baskets</span>
            <h3 class="curr-name">${escapeHtml(titleText)}</h3>
          </div>
          <span class="pl-count">${baskets.length} streams</span>
        </div>
        <div class="sem-course-grid">${basketCards}</div>
      </div>
    `;
  }

  if (matchType === 'elective_courses') {
    const stream = escapeHtml(data.stream || 'Electives');
    const courses = data.courses || [];
    const courseCards = courses.map(c => `
      <div class="sem-course-card">
        <div class="sem-card-top">
          <span class="sem-code">${escapeHtml(c.course_code)}</span>
          ${c.credits ? `<span class="sem-cred">${c.credits} Credits</span>` : ''}
          ${c.basket_name ? `<span class="sem-cat">${escapeHtml(c.basket_name)}</span>` : ''}
        </div>
        <div class="sem-name">${escapeHtml(c.course_name)}</div>
      </div>
    `).join('');

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">Elective Basket</span>
            <h3 class="curr-name">${stream}</h3>
          </div>
          <span class="pl-count">${courses.length} electives</span>
        </div>
        <div class="sem-course-grid">${courseCards}</div>
      </div>
    `;
  }

  if (matchType === 'course_detail') {
    const courses = data.courses || [];
    const details = courses.map(c => {
      const sem = (c.semester !== null && c.semester !== undefined) ? `
        <div class="detail-row">
          <span class="detail-label">Semester:</span>
          <span class="detail-val">Semester ${escapeHtml(String(c.semester))}</span>
        </div>` : '';
      const ep = c.evaluation_pattern ? `
        <div class="detail-row">
          <span class="detail-label">Evaluation Pattern:</span>
          <span class="detail-val font-mono">${escapeHtml(c.evaluation_pattern)}</span>
        </div>` : '';
      const cred = c.credits ? `
        <div class="detail-row">
          <span class="detail-label">Credits / LTPC:</span>
          <span class="detail-val">${escapeHtml(String(c.credits))} Credits (${c.L ?? 0}-${c.T ?? 0}-${c.P ?? 0})</span>
        </div>` : '';
      const labList = (c.lab_experiments && c.lab_experiments.length) ? `
        <div class="detail-block">
          <span class="detail-label">Lab Experiments:</span>
          <ul class="detail-list">${c.lab_experiments.map(l => `<li>${escapeHtml(l)}</li>`).join('')}</ul>
        </div>` : (c.has_no_lab_experiments ? `
        <div class="detail-row">
          <span class="detail-label">Lab Experiments:</span>
          <span class="detail-val" style="color:var(--text-3);font-style:italic;">No prescribed lab experiments for this course.</span>
        </div>` : '');
      const prereqList = (c.prerequisites && c.prerequisites.length) ? `
        <div class="detail-row">
          <span class="detail-label">Pre-Requisites:</span>
          <span class="detail-val">${c.prerequisites.map(p => escapeHtml(p)).join(', ')}</span>
        </div>` : '';
      const objList = (c.objectives && c.objectives.length) ? `
        <div class="detail-block">
          <span class="detail-label">Course Objectives:</span>
          <ul class="detail-list">${c.objectives.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul>
        </div>` : '';
      const coList = (c.outcomes && c.outcomes.length) ? `
        <div class="detail-block">
          <span class="detail-label">Course Outcomes:</span>
          <ul class="detail-list">${c.outcomes.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul>
        </div>` : '';
      const tbList = (c.textbooks && c.textbooks.length) ? `
        <div class="detail-block">
          <span class="detail-label">Textbooks:</span>
          <ul class="detail-list">${c.textbooks.map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul>
        </div>` : '';
      const refList = (c.references && c.references.length) ? `
        <div class="detail-block">
          <span class="detail-label">References:</span>
          <ul class="detail-list">${c.references.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
        </div>` : '';

      return `
        <div class="course-detail-card">
          <div class="detail-hdr">
            <span class="curr-code-badge">${escapeHtml(c.course_code)}</span>
            <span class="curr-name">${escapeHtml(c.course_name)}</span>
          </div>
          <div class="detail-body">
            ${sem}
            ${ep}
            ${cred}
            ${labList}
            ${prereqList}
            ${objList}
            ${coList}
            ${tbList}
            ${refList}
          </div>
        </div>
      `;
    }).join('');

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <h3 class="curr-name">Course Details</h3>
          <span class="graph-fast-badge">Verified Curriculum</span>
        </div>
        <div class="course-details-list">${details}</div>
      </div>
    `;
  }

  if (matchType === 'similar_list') {
    const courses = data.courses || [];
    const origQ = data.original_question || '';
    const listHtml = courses.map(c => {
      let clickAction = '';
      if (origQ) {
        // Strip out any previous ambiguous course name phrasing and append the selected code
        const safeQ = origQ.replace(/'/g, "\\'");
        clickAction = `document.getElementById('chat-input').value='${safeQ} (${escapeHtml(c.course_code)})'; window.sendMessage();`;
      } else {
        clickAction = `document.getElementById('chat-input').value='course details of ${escapeHtml(c.course_code)}'; window.sendMessage();`;
      }
      return `
        <button class="similar-course-chip" onclick="${clickAction}">
          <strong>${escapeHtml(c.course_code)}</strong> &mdash; ${escapeHtml(c.course_name)}
        </button>
      `;
    }).join('');

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <h3 class="curr-name">Multiple Courses Found</h3>
        </div>
        <p style="font-size:13px; color:var(--text-2); margin-bottom:10px;">Select a course to view details:</p>
        <div class="similar-courses-list">${listHtml}</div>
      </div>
    `;
  }

  return '<div class="problem-list-empty"><p>No matching curriculum found.</p></div>';
}

/**
 * Renders a visual Prerequisite DAG chain for MULTI_HOP_PREREQ.
 */
function renderPrereqResult(data) {
  const results = data.results || [];
  if (!results.length) {
    return `
      <div class="prereq-result-wrap">
        <div class="prereq-empty">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>
          <div>
            <strong>No Prerequisites Required</strong>
            <p style="font-size:12.5px; margin:3px 0 0; opacity:0.8;">You can directly enroll in this course without prior prerequisite dependencies in the academic graph.</p>
          </div>
        </div>
      </div>
    `;
  }

  const cards = results.map(r => {
    const target = escapeHtml(r.course || 'Course');
    const targetCode = r.course_code ? `<span class="prereq-target-code">${escapeHtml(r.course_code)}</span>` : '';
    const prereqs = r.all_prerequisites || [];
    const prereqPills = prereqs.map(p => `
      <div class="prereq-pill">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
        <span>${escapeHtml(p)}</span>
      </div>
    `).join('');

    return `
      <div class="prereq-dag-card">
        <div class="prereq-card-top">
          <div class="prereq-target-box">
            <span class="prereq-label">Target Course</span>
            <div class="prereq-target-title">${targetCode}<strong>${target}</strong></div>
          </div>
        </div>
        <div class="prereq-deps-wrap">
          <div class="prereq-deps-title">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="m5 16 4-6M19 16l-4-6"/></svg>
            <span>Required Prior Courses:</span>
          </div>
          <div class="prereq-pills-list">${prereqPills}</div>
        </div>
      </div>
    `;
  }).join('');

  return `
    <div class="prereq-result-wrap">
      <div class="prereq-hdr">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="6" y1="9" x2="6" y2="15"/><circle cx="18" cy="12" r="3"/><path d="M9 6.75 15 12l-6 5.25"/></svg>
        <span>Course Prerequisite Map</span>
        <span class="graph-fast-badge">Verified Curriculum</span>
      </div>
      <div class="prereq-cards-list">${cards}</div>
    </div>
  `;
}

/**
 * Renders Course Outcome / Bloom's Taxonomy Level mapped PYQs for GRAPH_PYQ_MAPPING.
 */
function renderCoMapping(data) {
  const results = data.results || [];
  if (!results.length) {
    return `
      <div class="problem-list-empty">
        <p>No questions mapped to this Course Outcome or Bloom's Taxonomy Level yet.</p>
      </div>
    `;
  }

  const btlLabel = (btl) => {
    const map = { 1:'Remember', 2:'Understand', 3:'Apply', 4:'Analyse', 5:'Evaluate', 6:'Create' };
    const n = parseInt(btl);
    return map[n] ? `BTL ${n} · ${map[n]}` : (btl ? `BTL ${btl}` : '');
  };

  const rows = results.map((q, idx) => {
    const co = q.co_id ? `<span class="pc-course">${escapeHtml(q.co_id)}</span>` : '';
    const course = q.course_code ? `<span class="pc-course">${escapeHtml(q.course_code)}</span>` : '';
    const btl = q.btl ? `<span class="pc-btl">${btlLabel(q.btl)}</span>` : '';
    const marks = q.marks ? `<span class="pc-mark">${q.marks}M</span>` : '';
    const coImgUrl = (q.image_url && q.image_url !== 'None')
      ? (q.image_url.startsWith('/') ? `${API_BASE}${q.image_url}` : q.image_url)
      : '';
    const imgHtml = coImgUrl ? `<img src="${escapeHtml(coImgUrl)}" alt="Diagram" class="pc-img">` : '';

    return `
      <div class="problem-card">
        <div class="pc-header">
          <span class="pc-num">Q${idx + 1}</span>
          <div class="pc-tags">${course}${co}${btl}${marks}</div>
        </div>
        <div class="pc-body">${escapeHtml(q.question || '')}</div>
        ${imgHtml}
      </div>
    `;
  }).join('');

  return `
    <div class="problem-list-wrap">
      <div class="problem-list-header">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        <span>Course Outcome & Bloom's Taxonomy Mapped Questions</span>
        <span class="pl-count">${results.length} questions</span>
      </div>
      <div class="problem-cards">${rows}</div>
    </div>
  `;
}

/**
 * Renders streaming AI response, separating explicit reasoning traces (<think> blocks)
 * from user-facing answers into an interactive collapsible accordion.
 */
function renderStreamWithThinking(fullText, isDone = false) {
  if (!fullText.includes('<think>')) {
    let rawHtml = window.marked ? window.marked.parse(fullText) : simpleMarkdown(fullText);
    return window.DOMPurify ? window.DOMPurify.sanitize(rawHtml) : rawHtml;
  }

  const parts = fullText.split('<think>');
  const preThink = parts[0] || '';
  const postThinkSplit = parts[1] ? parts[1].split('</think>') : ['', ''];
  const thinkingText = postThinkSplit[0] || '';
  const responseText = postThinkSplit.length > 1 ? postThinkSplit.slice(1).join('</think>') : '';
  const isStillThinking = !fullText.includes('</think>');

  let preHtml = preThink.trim() ? (window.marked ? window.marked.parse(preThink) : simpleMarkdown(preThink)) : '';
  let thinkHtml = thinkingText.trim() ? (window.marked ? window.marked.parse(thinkingText) : simpleMarkdown(thinkingText)) : '';
  let respHtml = responseText.trim() ? (window.marked ? window.marked.parse(responseText) : simpleMarkdown(responseText)) : '';

  const accordionOpen = isStillThinking ? 'open' : '';
  const statusLabel = isStillThinking ? 'Working step by step...' : 'Working Completed';
  const iconHtml = isStillThinking
    ? '<span class="thinking-spinner"></span>'
    : '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>';

  let outputHtml = '';
  if (preHtml) {
    outputHtml += `<div class="pre-think-content">${preHtml}</div>`;
  }

  outputHtml += `
    <details class="thinking-accordion ${isStillThinking ? 'thinking-active' : 'thinking-done'}" ${accordionOpen}>
      <summary class="thinking-summary">
        <div class="thinking-summary-left">
          ${iconHtml}
          <span>Step-by-Step Working</span>
          <span class="thinking-status-badge">${statusLabel}</span>
        </div>
        <span class="thinking-toggle-hint">${isStillThinking ? 'Live' : 'Show / Hide'}</span>
      </summary>
      <div class="thinking-body">
        ${thinkHtml || '<em>Checking question details and formulas...</em>'}
      </div>
    </details>
  `;

  if (respHtml) {
    outputHtml += `<div class="final-response-content">${respHtml}</div>`;
  }

  return window.DOMPurify
    ? window.DOMPurify.sanitize(outputHtml, { ADD_TAGS: ['details', 'summary'], ADD_ATTR: ['open'] })
    : outputHtml;
}

function renderAcademicMarkdown(markdown = '') {
  const inline = value => escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>');
  const lines = String(markdown).split(/\r?\n/);
  const blocks = [];
  let list = [];
  let table = [];
  const flushList = () => {
    if (list.length) blocks.push(`<ul style="margin:6px 0 10px;padding-left:22px;">${list.map(item => `<li>${inline(item)}</li>`).join('')}</ul>`);
    list = [];
  };
  const flushTable = () => {
    if (table.length) {
      const rows = table.filter(line => !/^\|?\s*:?-{2,}/.test(line));
      const [head, ...body] = rows;
      const cells = line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
      blocks.push(`<div style="overflow-x:auto;margin:8px 0;"><table style="border-collapse:collapse;width:100%;font-size:13px;"><thead><tr>${cells(head || '').map(cell => `<th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0;">${inline(cell)}</th>`).join('')}</tr></thead><tbody>${body.map(row => `<tr>${cells(row).map(cell => `<td style="padding:8px;border-bottom:1px solid #eef2f7;">${inline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    }
    table = [];
  };
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { flushList(); flushTable(); continue; }
    if (/^\|.*\|$/.test(trimmed)) { flushList(); table.push(trimmed); continue; }
    flushTable();
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushList();
      const level = Math.min(heading[1].length + 1, 5);
      blocks.push(`<h${level} style="margin:12px 0 6px;">${inline(heading[2])}</h${level}>`);
    } else if (/^[-*]\s+/.test(trimmed)) {
      list.push(trimmed.replace(/^[-*]\s+/, ''));
    } else {
      flushList();
      blocks.push(`<p style="margin:5px 0;">${inline(trimmed)}</p>`);
    }
  }
  flushList(); flushTable();
  return blocks.join('');
}

function renderCalendarResult(data) {
  if (!data) return '<div class="problem-list-empty"><p>No calendar data found.</p></div>';

  const day = data.day_details;
  const events = data.events || [];
  const overrides = data.upcoming_timetable_overrides || [];

  let proseHtml = '';
  // When day card is displayed, cards are good, remove the normal text at the top of the response!
  if (data.markdown_answer && !day) {
    const rawProse = renderAcademicMarkdown(data.markdown_answer);
    proseHtml = `<div class="ai-prose-answer" style="margin-bottom:16px;line-height:1.65;font-size:14px;border-bottom:1px solid var(--border-soft);padding-bottom:12px;">${rawProse}</div>`;
  }

  // These query modes already contain a focused, authoritative answer in
  // markdown. Avoid adding calendar event and override cards underneath it.
  if (['holiday_list', 'next_holiday', 'working_saturdays', 'next_working_saturday', 'festival_holidays', 'next_festival_holiday', 'festival_event', 'milestone_lookup', 'timetable_substitutions'].includes(data.calendar_mode)) {
    return `<div class="calendar-result-wrap">${proseHtml}</div>`;
  }

  let dayHtml = '';
  if (day) {
    const isHol = day.is_holiday;
    const statusClass = isHol ? 'cal-status-holiday' : 'cal-status-working';
    let statusText = 'Working Day (W)';
    if (isHol) {
      let holName = day.particulars || day.status || 'Holiday';
      if (holName.toLowerCase() === 'institutional holiday' || holName.toLowerCase() === 'institutional') {
        holName = 'Holiday';
      }
      statusText = holName;
    }

    let overrideBanner = '';
    if (day.timetable_override) {
      overrideBanner = `
        <div class="cal-override-alert">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          <div>
            <strong>Timetable Day Substitution:</strong>
            <span>${escapeHtml(day.day_of_week)} operates on <strong>${escapeHtml(day.timetable_override)}'s Timetable</strong></span>
          </div>
        </div>`;
    }

    const cohortsHtml = (day.cohorts && day.cohorts.length)
      ? `<div class="cal-cohorts-grid">
           ${day.cohorts.map(c => `
             <div class="cal-cohort-chip">
               <span class="coh-name">${escapeHtml(c.cohort)}</span>
               <span class="coh-cnt">${c.class_day_count !== null ? `Class Day ${c.class_day_count}` : 'No class count'}</span>
             </div>`).join('')}
         </div>`
      : '';

    dayHtml = `
      <div class="cal-day-card">
        <div class="cal-day-top">
          <div>
            <div class="cal-day-name">${escapeHtml(day.day_of_week)}</div>
            <div class="cal-date-title font-mono">${escapeHtml(day.date)}</div>
          </div>
          <span class="cal-status-pill ${statusClass}">${statusText}</span>
        </div>
        ${overrideBanner}
        ${day.particulars && !day.timetable_override ? `<div class="cal-notes">${escapeHtml(day.particulars)}</div>` : ''}
        ${cohortsHtml}
      </div>`;
  }

  let eventsHtml = '';
  if (events.length) {
    const eventCards = events.map(e => {
      let cat = (e.category || '').trim();
      if (cat.toLowerCase() === 'institutional holiday' || cat.toLowerCase() === 'institutional') {
        cat = 'Holiday';
      } else if (cat.toLowerCase() === 'milestone') {
        cat = '';
      }
      const catBadge = cat ? `<span class="cal-cat-badge ${escapeHtml(cat.toLowerCase())}">${escapeHtml(cat)}</span>` : '';
      const cohortBadge = e.cohort ? `<span class="cal-cohort-badge">${escapeHtml(e.cohort)}</span>` : '';
      const tagsHtml = (catBadge || cohortBadge) ? `<div class="cal-event-tags">${catBadge}${cohortBadge}</div>` : '';
      return `
      <div class="cal-event-row">
        <div class="cal-event-date font-mono">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span>${escapeHtml(e.date)}</span>
        </div>
        <div class="cal-event-main">
          <div class="cal-event-name">${escapeHtml(e.name)}</div>
          ${tagsHtml}
        </div>
      </div>
    `;
    }).join('');

    eventsHtml = `
      <div class="cal-events-section">
        <div class="cal-sec-title">${data.calendar_mode === 'month_events' ? 'Events' : 'Academic Events'}</div>
        <div class="cal-events-list">${eventCards}</div>
      </div>`;
  }

  let overridesHtml = '';
  if (!day && overrides.length) {
    const rows = overrides.map(o => `
      <div class="cal-override-item">
        <div class="co-date"><strong>${escapeHtml(o.date)}</strong> (${escapeHtml(o.natural_weekday)})</div>
        <div class="co-arr">&rarr;</div>
        <div class="co-applied">Operates on <strong>${escapeHtml(o.applied_timetable)}'s Timetable</strong></div>
      </div>
    `).join('');

    overridesHtml = `
      <div class="cal-overrides-section">
        <div class="cal-sec-title">Calendar Timetable Substitutions</div>
        <div class="cal-overrides-list">${rows}</div>
      </div>`;
  }

  return `
    <div class="calendar-result-wrap">
      ${proseHtml}
      ${dayHtml || eventsHtml || overridesHtml ? `
      <div class="curriculum-hdr">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        <h3 class="curr-name">${data.calendar_mode === 'month_events' ? `Academic Events — ${escapeHtml(data.calendar_period || '')}` : 'Academic Calendar (AY 2026-2027)'}</h3>
        <span class="graph-fast-badge">Authoritative Calendar</span>
      </div>
      ${dayHtml}
      ${eventsHtml}
      ${overridesHtml}` : ''}
    </div>`;
}


function renderAcademicResult(data) {
  if (data.intent === "CALENDAR_LOOKUP") {
    const calendar = { ...data, events: data.events || data.holidays || [], upcoming_timetable_overrides: data.overrides || [] };
    return renderCalendarResult(calendar);
  }
  if (data.intent === 'TIMETABLE_LOOKUP' && Array.isArray(data.sessions) && data.sessions.length) {
    const heading = String(data.markdown_answer || '').match(/^###\s+([^\n]+)/m)?.[1] || 'Class timetable';
    const detail = data.date_details || {};
    const override = detail.timetable_override ? `<div class="schedule-override">${escapeHtml(detail.date || '')} follows ${escapeHtml(detail.timetable_override)}'s timetable.</div>` : '';
    const cards = data.sessions.map(session => {
      const time = [session.start_time, session.end_time].filter(Boolean).join(' – ');
      const meta = [session.faculty ? `Faculty: ${session.faculty}` : '', session.venue ? `Venue: ${session.venue}` : ''].filter(Boolean);
      return `<section class="schedule-card">
        <div class="schedule-slot"><span>Slot ${escapeHtml(String(session.slot || '—'))}</span>${time ? `<small>${escapeHtml(time)}</small>` : ''}</div>
        <div class="schedule-content"><strong>${escapeHtml(session.activity || session.course_code || 'Class')}</strong>
          ${session.course_code ? `<span class="schedule-code">${escapeHtml(session.course_code)}</span>` : ''}
          ${session.weekday && data.timetable_mode !== 'day_schedule' ? `<div class="schedule-meta">${escapeHtml(session.weekday)}</div>` : ''}
          ${meta.length ? `<div class="schedule-meta">${meta.map(escapeHtml).join(' · ')}</div>` : ''}
        </div>
      </section>`;
    }).join('');
    const note = data.timetable_mode === 'free_periods' ? '<p class="schedule-meta">The source timetable leaves these cells blank.</p>' : '';
    return `<article class="schedule-result"><h3>${escapeHtml(heading)}</h3>${note}${override}<div class="schedule-cards">${cards}</div></article>`;
  }
  const title = data.title || ({
    CALENDAR_LOOKUP: 'Academic Calendar',
    TIMETABLE_LOOKUP: 'Class Timetable',
    ACADEMIC_DOCUMENT_LOOKUP: 'Academic Schedule',
    REGULATIONS_QUERY: 'Academic Regulations',
  }[data.intent] || 'Academic Document Result');
  const recordLimit = data.document_mode === 'exam_schedule' ? 60 : 12;
  const records = Array.isArray(data.records) ? data.records.slice(0, recordLimit) : [];
  if (data.document_mode === 'exam_schedule' && records.length) {
    const formatDate = (value) => {
      const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (!match) return escapeHtml(value || 'Date not extracted');
      return escapeHtml(new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
        .toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }));
    };
    const readableCourseName = (value) => String(value || '')
      .replace(/([A-Za-z])(and|of|for|in|to)(?=[A-Z])/gi, '$1 $2 ')
      .replace(/(?<=[a-z])(?=[A-Z])/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const cards = records.map((row) => {
      const courses = String(row.text || '').split(/\s*\/\s*/).filter(Boolean).map((course) => {
        const match = course.match(/^(\d{2}[A-Z]{3,4}\d{3,4}[A-Z]?)\s*[-–:]?\s*(.*)$/i);
        const code = match ? `<span style="font:600 12px ui-monospace,SFMono-Regular,Menlo,monospace;color:#b20f48;background:#fff1f5;border:1px solid #f4cad8;border-radius:6px;padding:4px 7px;white-space:nowrap;">${escapeHtml(match[1].toUpperCase())}</span>` : '';
        const name = readableCourseName(match ? match[2] : course);
        return `<div style="display:flex;align-items:flex-start;gap:9px;padding:10px 12px;border:1px solid #eceef2;border-radius:9px;background:#fff;min-width:0;">
          ${code}<span style="font-size:13px;line-height:1.5;color:#303640;overflow-wrap:anywhere;">${escapeHtml(name || course)}</span>
        </div>`;
      }).join('');
      const session = String(row.session || '').split('/').map((value) => value.toUpperCase());
      const sessionText = session.map((value) => value === 'FN' ? 'FN · Forenoon · 9:30 AM–12:30 PM' : value === 'AN' ? 'AN · Afternoon · 1:30 PM–4:30 PM' : value).join(' / ') || 'Session not extracted';
      return `<section style="border:1px solid #e6e8ed;border-radius:12px;padding:14px;margin:10px 0;background:#fafbfc;">
        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:11px;">
          <strong style="font-size:15px;color:#202630;">${formatDate(row.date)}</strong>
          <span style="font-size:11px;font-weight:650;color:#6941c6;background:#f3efff;border:1px solid #ddd2ff;border-radius:999px;padding:4px 9px;">${escapeHtml(sessionText)}</span>
          ${row.department ? `<span style="font-size:11px;font-weight:650;color:#475467;background:#f2f4f7;border:1px solid #e4e7ec;border-radius:999px;padding:4px 9px;">${escapeHtml(row.department)}</span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:7px;">${courses || '<span>No courses were extracted for this row.</span>'}</div>
        <div style="margin-top:10px;color:#8a8f98;font-size:11px;">Source: ${escapeHtml(row.filename || 'Uploaded exam schedule')}${row.page ? ` · page ${escapeHtml(String(row.page))}` : ''}</div>
      </section>`;
    }).join('');
    return `<article style="min-width:min(100%,680px);max-width:900px;">
      <header style="font-size:17px;font-weight:700;margin-bottom:10px;">${escapeHtml(title)}</header>
      ${cards}
    </article>`;
  }
  const answer = renderAcademicMarkdown(data.markdown_answer || 'No matching academic records were found.');
  const sourceRecords = records.length ? `
    <details style="margin-top:12px;border-top:1px solid #eef0f3;padding-top:10px;">
      <summary style="cursor:pointer;font-size:12px;color:#667085;">Source rows (${records.length}${records.length === 12 ? '+' : ''})</summary>
      <div style="display:grid;gap:7px;margin-top:8px;">${records.map(row => `<div style="padding:8px 10px;border:1px solid #eceef2;border-radius:8px;background:#fafafa;font-size:12px;line-height:1.5;">${escapeHtml(row.text || row.record_text || '')}${row.page ? `<div style="margin-top:4px;color:#8a8f98;">Page ${escapeHtml(row.page)}</div>` : ''}</div>`).join('')}</div>
    </details>` : '';
  return `<article style="min-width:min(100%,680px);max-width:900px;">
    <header style="font-size:17px;font-weight:700;margin-bottom:10px;">${escapeHtml(title)}</header>
    <div style="font-size:14px;line-height:1.65;color:#252a31;">${answer}</div>${sourceRecords}
  </article>`;
}

export async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  const b64 = attachedImageB64;

  if (!text && !b64 || isTyping) return;

  input.value = ''; input.style.height = '';

  addUserMessage(text, b64);
  addToHistory(text || 'Image Attachment');
  clearAttachment();

  activeAbortController = new AbortController();
  activeStreamReader = null;
  userStopped = false;
  setGeneratingUI(true);
  showTyping(!!b64);

  let streamBubble = null;
  let fullText = '';

  try {
    const payload = { message: text, session_id: currentSessionId };
    if (b64) {
      payload.image_b64 = b64;
    }

    const res = await apiFetch('/chat', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: activeAbortController.signal,
    });

    if (!res.ok) {
      const errJson = await res.json().catch(() => ({}));
      removeTyping();
      const errBubble = createAIBubble(null);
      errBubble.innerHTML = `<em style="color:var(--brand)">${escapeHtml(errJson.detail || `Request failed (HTTP ${res.status})`)}</em>`;
      scrollBottom();
      return;
    }

    // ── Check if backend returned a structured Fast-Path response (Zero-LLM) ──
    const msgId = res.headers.get('X-Message-ID') || null;
    const responseType = res.headers.get('X-Response-Type');
    if (responseType === 'academic_result') {
      const data = await res.json();
      removeTyping();
      const bubble = createAIBubble(msgId);
      bubble.innerHTML = renderAcademicResult(data);
      scrollBottom();
      return;
    }
    if (responseType === 'guardrail_refusal') {
      const data = await res.json();
      removeTyping();
      const bubble = createAIBubble(msgId);
      bubble.innerHTML = `
        <div style="background:#FEF2F2;border:1px solid #FCA5A5;border-radius:10px;padding:14px 18px;color:#991B1B;font-size:13.5px;line-height:1.5;">
          <div style="display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:6px;">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            Academic Guardrail Advisory
          </div>
          ${escapeHtml(data.message || 'Non-academic or unauthorized command sequence.')}
        </div>`;
      scrollBottom();
      return;
    }
    if (responseType === 'problem_list') {
      const data = await res.json();
      removeTyping();
      const bubble = createAIBubble(msgId);
      bubble.innerHTML = renderProblemCards(data);
      scrollBottom();
      return;  // skip streaming logic
    }
    if (responseType === 'curriculum_result') {
      const json = await res.json();
      removeTyping();
      const bubble = createAIBubble(msgId);
      bubble.innerHTML = renderCurriculumResult(json.data || json);
      scrollBottom();
      return;
    }
    if (responseType === 'prereq_result') {
      const json = await res.json();
      removeTyping();
      const bubble = createAIBubble(msgId);
      bubble.innerHTML = renderPrereqResult(json);
      scrollBottom();
      return;
    }
    if (responseType === 'co_mapping') {
      const json = await res.json();
      removeTyping();
      const bubble = createAIBubble(msgId);
      bubble.innerHTML = renderCoMapping(json);
      scrollBottom();
      return;
    }

    // ── Normal streaming text response ──
    removeTyping();
    streamBubble = createAIBubble(msgId);

    const reader = res.body.getReader();
    activeStreamReader = reader;
    const decoder = new TextDecoder();

    while (true) {
      const { value, done } = await reader.read();
      if (done || userStopped) break;
      fullText += decoder.decode(value, { stream: true });
      streamBubble.innerHTML = renderStreamWithThinking(fullText, false);
      scrollBottom();
    }
    streamBubble.innerHTML = renderStreamWithThinking(fullText, true);

    if (userStopped) {
      streamBubble.insertAdjacentHTML('beforeend', '<div class="stopped-note">Generation paused.</div>');
    }

    if (msgId) {
      streamBubble.parentElement.querySelectorAll('.fb-btn').forEach(btn => {
        const dir = btn.getAttribute('onclick').includes("'up'") ? 'up' : 'down';
        btn.setAttribute('onclick', `window.vote(this,'${dir}','${msgId}')`);
      });
    }

  } catch (e) {
    removeTyping();
    if (e.name === 'AbortError' || userStopped) {
      if (streamBubble) {
        streamBubble.innerHTML = renderStreamWithThinking(fullText, true);
        streamBubble.insertAdjacentHTML('beforeend', '<div class="stopped-note">Generation paused.</div>');
      } else {
        createAIBubble(null).innerHTML = '<div class="stopped-note" style="margin-top:0;padding-top:0;border-top:none;">Query paused before response completed.</div>';
      }
      scrollBottom();
    } else if (e.message !== "Unauthorized") {
      createAIBubble(null).innerHTML = '<em style="color:var(--brand)">Network error or backend failed.</em>';
    }
  } finally {
    activeAbortController = null;
    activeStreamReader = null;
    userStopped = false;
    setGeneratingUI(false);
    isTyping = false;
  }
}
window.sendMessage = sendMessage;

export function sendSuggestion(btn) {
  document.getElementById('chat-input').value = btn.querySelector('.sug-text').textContent;
  sendMessage();
}
window.sendSuggestion = sendSuggestion;

export async function vote(btn, dir, msgId) {
  btn.parentElement.querySelectorAll('.fb-btn').forEach(b => b.classList.remove('voted-up', 'voted-down'));
  btn.classList.add(dir === 'up' ? 'voted-up' : 'voted-down');

  if (!msgId) return;
  try {
    await apiFetch(`/feedback/${msgId}`, {
      method: 'POST',
      body: JSON.stringify({ score: dir === 'up' ? 1 : -1 }),
    });
  } catch (e) { /* silent */ }
}
window.vote = vote;

function addToHistory(text) {
  if (!chatSessions[currentSessionId].title) {
    chatSessions[currentSessionId].title = text;
    updateHistoryUI(currentSessionId, text);
  }
}

function updateHistoryUI(id, text) {
  const hist = document.getElementById('sidebar-history');
  let item = document.getElementById('hist-' + id);
  if (!item) {
    item = document.createElement('div');
    item.id = 'hist-' + id;
    item.className = 'hist-item active';
    item.setAttribute('onclick', `window.loadChat('${id}')`);
    hist.prepend(item);
  }

  hist.querySelectorAll('.hist-item').forEach(h => h.classList.remove('active'));
  item.classList.add('active');

  item.innerHTML = `<span class="hist-icon"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></span><span class="hist-text">${escapeHtml(text.slice(0, 48))}${text.length > 48 ? '…' : ''}</span>`;
}

document.getElementById('chat-input').addEventListener('input', function () {
  this.style.height = '';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

document.getElementById('chat-input').addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

let attachedImageB64 = null;

export function handleAttach(input) {
  if (input.files && input.files[0]) {
    const file = input.files[0];
    if (!file.type.startsWith('image/')) {
      alert('Only image files (PNG, JPG, WEBP, etc.) can be attached.');
      input.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = function(e) {
      attachedImageB64 = e.target.result;
      document.getElementById('image-preview-img').src = attachedImageB64;
      document.getElementById('image-preview-wrapper').style.display = 'inline-block';
      document.getElementById('chat-input').focus();
    };
    reader.readAsDataURL(file);
  }
}

export function clearAttachment() {
  attachedImageB64 = null;
  document.getElementById('image-preview-img').src = '';
  document.getElementById('image-preview-wrapper').style.display = 'none';
  const fileInput = document.querySelector('.btn-attach input[type="file"]');
  if (fileInput) fileInput.value = '';
}

window.handleAttach = handleAttach;
window.clearAttachment = clearAttachment;
window.logout = function() {
  stopGeneration();
  logout();
};

// Voice input (Speech-to-Text)
let recognition = null;
let isListening = false;

if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-US';

  recognition.onstart = () => {
    isListening = true;
    document.getElementById('mic-btn').classList.add('listening');
    document.getElementById('chat-input').placeholder = "Listening...";
  };

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    document.getElementById('chat-input').value = transcript;
    window.sendMessage();
  };

  recognition.onerror = (e) => {
    console.error("Speech recognition error:", e);
    stopListening();
  };

  recognition.onend = () => {
    stopListening();
  };
}

function stopListening() {
  isListening = false;
  document.getElementById('mic-btn').classList.remove('listening');
  document.getElementById('chat-input').placeholder = "Ask about your syllabus, curriculum, or past exam questions…";
}

export function toggleSpeech() {
  if (!recognition) {
    alert("Speech recognition is not supported in this browser. Try Google Chrome.");
    return;
  }
  if (isListening) {
    recognition.stop();
  } else {
    recognition.start();
  }
}
window.toggleSpeech = toggleSpeech;

// ── Image Paste Support (Cmd+V) ──
document.addEventListener('paste', function(e) {
  const items = (e.clipboardData || e.originalEvent.clipboardData).items;
  for (let index in items) {
    const item = items[index];
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const blob = item.getAsFile();
      const reader = new FileReader();
      reader.onload = function(event) {
        attachedImageB64 = event.target.result;
        document.getElementById('image-preview-img').src = attachedImageB64;
        document.getElementById('image-preview-wrapper').style.display = 'inline-block';
        document.getElementById('chat-input').focus();
      };
      reader.readAsDataURL(blob);
    }
  }
});

// ── Drag and Drop Support ──
const dropZone = document.body;
dropZone.addEventListener('dragover', function(e) {
  e.preventDefault();
});
dropZone.addEventListener('drop', function(e) {
  e.preventDefault();
  if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    const file = e.dataTransfer.files[0];
    if (file.type.startsWith('image/')) {
      const reader = new FileReader();
      reader.onload = function(event) {
        attachedImageB64 = event.target.result;
        document.getElementById('image-preview-img').src = attachedImageB64;
        document.getElementById('image-preview-wrapper').style.display = 'inline-block';
        document.getElementById('chat-input').focus();
      };
      reader.readAsDataURL(file);
    } else {
      alert('Only image files (PNG, JPG, WEBP, etc.) can be attached.');
    }
  }
});

// ── Click-to-Zoom (Lightbox) ──
document.getElementById('messages').addEventListener('click', function(e) {
  if (e.target.tagName === 'IMG' && !e.target.classList.contains('no-lightbox')) {
    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    lightboxImg.src = e.target.src;
    lightbox.style.display = 'flex';
  }
});
