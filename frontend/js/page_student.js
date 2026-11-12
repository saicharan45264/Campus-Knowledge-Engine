/**
 * 🎓 STUDENT DASHBOARD CONTROLLER (RAG INTERFACE)
 * The crown jewel of our FYP frontend. This module orchestrates the chat interface,
 * streaming LLM responses, Markdown parsing, and Voice AI integration.
 * We opted for native Server-Sent Events (SSE) and Streams API instead of bulky libraries like Socket.io.
 */
import { apiFetch } from './api.js';
import { getUsername, requireRole, logout } from './auth.js';
import { timeNow, escapeHtml, simpleMarkdown } from './utils.js';

requireRole('student');

const uname = getUsername() || 'Student';
const initials = uname.slice(0, 2).toUpperCase();

document.getElementById('student-name').textContent = uname.charAt(0).toUpperCase() + uname.slice(1);
document.getElementById('student-avatar').textContent = initials;

const studentProfileReady = apiFetch('/me').then(async response => {
  if (!response.ok) throw new Error('Could not load student profile');
  const profile = await response.json();
  const fullName = profile.full_name || uname;
  document.getElementById('student-name').textContent = fullName;
  document.getElementById('student-avatar').textContent = fullName.slice(0, 2).toUpperCase();
  document.getElementById('student-context').textContent =
    `${profile.roll_number || 'Demo account'} · ${profile.department} · Year ${profile.study_year} · Sem ${profile.semester} · Sec ${profile.section}`;
  return profile;
}).catch(error => {
  console.warn('[Student profile]', error);
  document.getElementById('student-context').textContent = 'Student profile unavailable';
  return null;
});

let isTyping = false;
let currentSessionId = null;
let chatSessions = {};

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
    <div class="es-glyph">M</div>
    <h3>Ready when you are</h3>
    <p>Ask anything about your syllabus, past year questions, class timetable, academic calendar, or regulations. Everything is indexed and authoritative.</p>
    <div class="suggestion-grid">
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What classes do I have on Saturday, June 20, 2026?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg></span>
      </button>
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What is the attendance rule in R.12?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg></span>
      </button>
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">When do end semester exams start for final years?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>
      </button>
      <button class="sug-chip" onclick="window.sendSuggestion(this)">
        <span class="sug-text">What are the prerequisites for Machine Learning?</span>
        <span class="sug-icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="12" r="3"/><path d="m9 6.75 6 5.25-6 5.25"/></svg></span>
      </button>
    </div>
  </div>`;

export function clearChat() {
  if (isTyping) {
    alert("Please wait for the response to finish before starting a new chat.");
    return;
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
    alert("Please wait for the response to finish before switching chats.");
    return;
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
  if (imageB64) {
    imgHtml = `<img src="${imageB64}" class="user-attached-img" alt="Attached Image" />`;
  }
  
  const row = document.createElement('div');
  row.className = 'msg-row user-row';
  row.innerHTML = `
    <div class="msg-av user">${initials}</div>
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
    row.innerHTML = `<div class="msg-av ai vision"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></div><div class="typing-bubble vision-bubble">Analyzing visual context<span class="dot-anim">...</span></div>`;
  } else {
    row.innerHTML = `<div class="msg-av ai">M</div><div class="typing-bubble"><div class="t-dot"></div><div class="t-dot"></div><div class="t-dot"></div></div>`;
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
    <div class="msg-av ai">M</div>
    <div class="msg-content">
      <div class="bubble ai-bubble" id="${id}"></div>
      <div class="bubble-footer">
        <div class="bubble-time">M.A.C.H. · ${timeNow()}</div>
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
  const confirmedProblems = data.confirmed_problems || problems.filter(p =>
    p.mapping_status === 'confirmed' || p.topic_link_type === 'TESTS_TOPIC'
  );
  const suggestedProblems = data.suggested_problems || problems.filter(p =>
    p.mapping_status === 'suggested' || p.topic_link_type === 'SUGGESTED_TOPIC'
  );

  if (!problems.length) {
    if (data.scope_message) {
      return `<div class="problem-list-empty"><p>${escapeHtml(data.scope_message)}</p></div>`;
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
          <p style="font-size:13px;color:#475569;margin:0 0 8px 0;">No past exam papers have been uploaded yet for this course. Below are the key syllabus units and topics from the curriculum:</p>
          ${unitsHtml}
          <div style="margin-top:12px;font-size:12px;color:#64748b;font-style:italic;">Tip: Upload a past question paper for this course in the Upload tab to generate interactive question cards!</div>
        </div>`;
    }
    return `
      <div class="problem-list-empty">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <p>No problems found for <strong>${escapeHtml(topic)}</strong>. Try uploading more PYQ papers for this topic.</p>
      </div>`;
  }

  const btlLabel = (btl) => {
    const map = { 1:'Remember', 2:'Understand', 3:'Apply', 4:'Analyse', 5:'Evaluate', 6:'Create' };
    const n = parseInt(btl);
    return map[n] ? `BTL ${n} · ${map[n]}` : `BTL ${btl}`;
  };

  const renderCards = (items) => items.map((p, idx) => {
    const rawText = p.text || p.question_text || 'Question text unavailable';
    const qText   = escapeHtml(rawText);
    const marks   = p.marks   ? `<span class="pc-mark">${p.marks} marks</span>` : '';
    const btl     = p.btl    ? `<span class="pc-btl">${btlLabel(p.btl)}</span>` : '';
    const course  = p.course_code ? `<span class="pc-course">${escapeHtml(p.course_code)}</span>` : '';
    const topicTag = p.topic_name ? `<span class="pc-topic-badge" style="font-size:11px;padding:2px 7px;background:#ede9fe;color:#6d28d9;border-radius:4px;border:1px solid #ddd6fe;font-weight:500;">📌 ${escapeHtml(p.topic_name)}</span>` : '';
    
    // Zoomable Image
    const imgHtml = p.image_url && p.image_url !== 'None'
      ? `<div class="pc-img-wrap" style="position:relative;margin-top:10px;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0;background:#ffffff;cursor:zoom-in;">
           <img src="${escapeHtml(p.image_url)}" alt="Diagram for Q${idx+1}" class="pc-img" style="width:100%;max-height:260px;object-fit:contain;display:block;">
           <div style="position:absolute;bottom:6px;right:6px;background:rgba(15,23,42,0.75);color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;display:flex;align-items:center;gap:4px;backdrop-filter:blur(4px);pointer-events:none;">
             <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
             Click to Zoom
           </div>
         </div>`
      : '';
      
    // Graph RAG indicators
    let aiBadge = '';
    if (p.mapping_confidence) {
      const conf = Math.round(p.mapping_confidence * 100);
      aiBadge = `<span class="pc-ai-badge" title="AI Confidence: ${conf}% mapped to topic: ${p.topic_name}">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
        AI Mapped
      </span>`;
    }

    return `
      <div class="problem-card">
        <div class="pc-header">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="pc-num">Q${escapeHtml(p.question_number || idx + 1)}</span>
            ${topicTag}
          </div>
          <div class="pc-tags">${aiBadge}${course}${marks}${btl}</div>
        </div>
        <div class="pc-body">${qText}</div>
        ${imgHtml}
        <div class="pc-footer" style="margin-top:10px;padding-top:8px;border-top:1px solid #f1f5f9;display:flex;justify-content:flex-end;">
          <button class="pc-copy-btn" onclick="window.copyProblemText(this, ${JSON.stringify(rawText)})" style="display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:#64748b;background:none;border:1px solid #e2e8f0;padding:3px 9px;border-radius:6px;cursor:pointer;transition:all 0.15s ease;">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            <span>Copy Question</span>
          </button>
        </div>
      </div>`;
  }).join('');

  const confirmedCards = renderCards(confirmedProblems);
  const suggestedCards = renderCards(suggestedProblems);

  return `
    <div class="problem-list-wrap">
      <div class="problem-list-header">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <span>Problems on <strong>${escapeHtml(topic)}</strong></span>
        <span class="pl-count">${confirmedProblems.length + suggestedProblems.length} found</span>
      </div>
      ${confirmedProblems.length ? `
        <section class="problem-group confirmed-problems">
          <h3 style="margin:14px 0 8px;font-size:14px;color:#166534;">Confirmed topic links · TESTS_TOPIC (${confirmedProblems.length})</h3>
          <div class="problem-cards">${confirmedCards}</div>
        </section>` : ''}
      ${suggestedProblems.length ? `
        <section class="problem-group suggested-problems">
          <h3 style="margin:18px 0 8px;font-size:14px;color:#9a6700;">Provisional topic suggestions · SUGGESTED_TOPIC (${suggestedProblems.length})</h3>
          <div class="problem-cards">${suggestedCards}</div>
        </section>` : ''}
    </div>`;
}

window.copyProblemText = function(btn, text) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.innerHTML;
    btn.innerHTML = `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="#16a34a" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span style="color:#16a34a;font-weight:600;">Copied!</span>`;
    setTimeout(() => { btn.innerHTML = orig; }, 1800);
  });
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

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">${cCode}</span>
            <h3 class="curr-name">${cName}</h3>
          </div>
          <span class="graph-fast-badge">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            Graph Fast-Path &le;120ms
          </span>
        </div>
        <div class="curriculum-units-list">${unitsHtml}</div>
      </div>
    `;
  }

  if (matchType === 'course_outcomes') {
    const course = (data.courses || [])[0];
    if (!course) return '<div class="problem-list-empty"><p>No course outcomes were found for this course.</p></div>';
    const outcomes = course.outcomes || [];
    const rows = outcomes.map((outcome) => `
      <li><strong>${escapeHtml(outcome.id || 'Outcome')}</strong> — ${escapeHtml(outcome.description || '')}</li>
    `).join('');
    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">${escapeHtml(course.course_code || '')}</span>
            <h3 class="curr-name">${escapeHtml(course.course_name || 'Course Outcomes')}</h3>
          </div>
          <span class="graph-fast-badge">Indexed Course Outcomes</span>
        </div>
        ${outcomes.length ? `<ol class="course-outcomes-list">${rows}</ol>` : '<p>No course outcomes are indexed for this course.</p>'}
      </div>
    `;
  }

  if (matchType === 'unsupported_aspect') {
    return '<div class="problem-list-empty"><p>' +
      escapeHtml(data.message || 'This curriculum detail is not indexed yet.') +
      '</p></div>';
  }

  if (matchType === 'prerequisites') {
    const course = (data.courses || [])[0];
    if (!course) return '<div class="problem-list-empty"><p>No prerequisite information was found for this course.</p></div>';
    const prerequisites = course.prerequisites || [];
    const rows = prerequisites.map((item) => `
      <li><strong>${escapeHtml(item.course_code || '')}</strong> — ${escapeHtml(item.course_name || '')}</li>
    `).join('');
    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">${escapeHtml(course.course_code || '')}</span>
            <h3 class="curr-name">Prerequisites — ${escapeHtml(course.course_name || '')}</h3>
          </div>
        </div>
        ${prerequisites.length ? `<ul class="course-outcomes-list">${rows}</ul>` : '<p>No prerequisite links are indexed for this course.</p>'}
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

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <div class="curriculum-title-wrap">
            <span class="curr-code-badge">Semester ${sem}</span>
            <h3 class="curr-name">Curriculum & Course Structure</h3>
          </div>
          <span class="pl-count">${courses.length} courses</span>
        </div>
        <div class="sem-course-grid">${courseCards}</div>
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
      const ep = c.evaluation_pattern ? `
        <div class="detail-row">
          <span class="detail-label">Evaluation Pattern:</span>
          <span class="detail-val font-mono">${escapeHtml(c.evaluation_pattern)}</span>
        </div>` : '';
      const cred = c.credits ? `
        <div class="detail-row">
          <span class="detail-label">Credits / LTPC:</span>
          <span class="detail-val">${c.credits} Credits (${c.L || 0}-${c.T || 0}-${c.P || 0})</span>
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
            ${ep}
            ${cred}
            ${tbList}
            ${refList}
          </div>
        </div>
      `;
    }).join('');

    return `
      <div class="curriculum-result-wrap">
        <div class="curriculum-hdr">
          <h3 class="curr-name">Course Specification Details</h3>
          <span class="graph-fast-badge">Zero-LLM Fast-Path</span>
        </div>
        <div class="course-details-list">${details}</div>
      </div>
    `;
  }

  if (matchType === 'similar_list') {
    const courses = data.courses || [];
    const listHtml = courses.map(c => `
      <button class="similar-course-chip" onclick="document.getElementById('chat-input').value='syllabus of ${escapeHtml(c.course_code)}'; sendMessage();">
        <strong>${escapeHtml(c.course_code)}</strong> &mdash; ${escapeHtml(c.course_name)}
      </button>
    `).join('');

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
            <span>Prerequisites (Multi-Hop DAG Traversal):</span>
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
        <span>Curriculum Prerequisite Knowledge Graph</span>
        <span class="graph-fast-badge">Zero-LLM &le;120ms</span>
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
    const imgHtml = (q.image_url && q.image_url !== 'None') ? `<img src="${escapeHtml(q.image_url)}" alt="Diagram" class="pc-img">` : '';

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
 * Renders Academic Calendar lookup results:
 * Displays single-day details (status, working day tag, cohorts, timetable day overrides)
 * and/or matched academic milestones and events.
 */
function renderCalendarResult(data) {
  if (!data) return '<div class="problem-list-empty"><p>No calendar data found.</p></div>';

  const day = data.day_details;
  const events = data.events || [];
  const overrides = data.upcoming_timetable_overrides || [];

  let proseHtml = '';
  // When day card is displayed, cards are good, remove the normal text at the top of the response!
  if (data.markdown_answer && !day) {
    const rawProse = window.marked ? window.marked.parse(data.markdown_answer) : simpleMarkdown(data.markdown_answer);
    proseHtml = `<div class="ai-prose-answer" style="margin-bottom:16px;line-height:1.65;font-size:14px;border-bottom:1px solid var(--border-soft);padding-bottom:12px;">${rawProse}</div>`;
  }

  // These query modes already contain a focused, authoritative answer in
  // markdown. Avoid adding calendar event and override cards underneath it.
  if (['holiday_list', 'next_holiday', 'working_saturdays', 'next_working_saturday', 'festival_holidays', 'next_festival_holiday', 'festival_event', 'milestone_lookup'].includes(data.calendar_mode)) {
    return `<div class="calendar-result-wrap">${proseHtml}</div>`;
  }

  let dayHtml = '';
  if (day) {
    const isHol = day.is_holiday;
    const statusClass = isHol ? 'cal-status-holiday' : 'cal-status-working';
    const statusText = isHol ? (day.particulars || 'Institutional Holiday') : 'Working Day (W)';
    
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
    const eventCards = events.map(e => `
      <div class="cal-event-row">
        <div class="cal-event-date font-mono">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span>${escapeHtml(e.date)}</span>
        </div>
        <div class="cal-event-main">
          <div class="cal-event-name">${escapeHtml(e.name)}</div>
          <div class="cal-event-tags">
            <span class="cal-cat-badge ${e.category || 'milestone'}">${escapeHtml(e.category || 'Milestone')}</span>
            ${e.cohort ? `<span class="cal-cohort-badge">${escapeHtml(e.cohort)}</span>` : ''}
          </div>
        </div>
      </div>
    `).join('');

    eventsHtml = `
      <div class="cal-events-section">
        <div class="cal-sec-title">${data.calendar_mode === 'month_events' ? 'Events' : 'Academic Milestones & Events'}</div>
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

/**
 * Renders weekly timetable schedule sessions for a date/weekday:
 * Displays override banner, holiday notices, and interactive session cards with faculty, venues, and elective bucket options.
 */
function renderTimetableResult(data) {
  if (!data) return '<div class="problem-list-empty"><p>No timetable data found.</p></div>';

  let proseHtml = '';
  if (data.markdown_answer) {
    const rawProse = window.marked ? window.marked.parse(data.markdown_answer) : simpleMarkdown(data.markdown_answer);
    proseHtml = `<div class="ai-prose-answer" style="line-height:1.65;font-size:14px;">${rawProse}</div>`;
    // If markdown_answer is present, it is the authoritative answer — remove any extra session cards below!
    return `<div class="timetable-result-wrap">${proseHtml}</div>`;
  }
  if (!s || (!s.sessions?.length && !s.is_holiday)) {
    return proseHtml ? `<div class="timetable-result-wrap">${proseHtml}</div>` : '<div class="problem-list-empty"><p>No timetable data found.</p></div>';
  }

  if (s.is_holiday) {
    return `
      <div class="timetable-result-wrap">
        ${proseHtml}
        <div class="tt-holiday-card">
          <div class="tt-hol-icon">🎉</div>
          <div class="tt-hol-content">
            <h3 class="tt-hol-title">No Classes — Institutional Holiday</h3>
            <p class="tt-hol-msg">${escapeHtml(s.message || `${s.holiday_name} on ${s.date}`)}</p>
            <div class="tt-meta-row">
              <span class="tt-tag">${escapeHtml(s.department || 'CSE')} · Sem ${escapeHtml(s.semester || 'VII')}-${escapeHtml(s.section || 'F')}</span>
              <span class="tt-tag font-mono">${escapeHtml(s.date || '')}</span>
            </div>
          </div>
        </div>
      </div>`;
  }

  let overrideBanner = '';
  if (s.override_note) {
    overrideBanner = `
      <div class="tt-override-banner">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <div>
          <strong>${escapeHtml(s.override_note)}</strong>
          <span style="display:block;font-size:12px;opacity:0.9;">Regular lectures replaced per official Academic Calendar AY 2026-2027.</span>
        </div>
      </div>`;
  }

  const sessionCards = (s.sessions || []).map((sess, idx) => {
    const isBreak = sess.activity && (sess.activity.toLowerCase().includes('break') || sess.activity.toLowerCase().includes('interval') || sess.activity.toLowerCase().includes('lunch'));
    const isElective = !!sess.elective_bucket;
    const electives = s.available_electives || {};
    const bucketOptions = isElective && electives[sess.elective_bucket] ? electives[sess.elective_bucket] : [];

    let electivesDropdown = '';
    if (bucketOptions.length > 0) {
      const optHtml = bucketOptions.map(opt => `
        <div class="tt-opt-row">
          <div class="tt-opt-code font-mono">${escapeHtml(opt.course_code)}</div>
          <div class="tt-opt-title">${escapeHtml(opt.course_name)}</div>
          <div class="tt-opt-meta">
            ${opt.faculty ? `<span>👤 ${escapeHtml(opt.faculty)}</span>` : ''}
            ${opt.venue ? `<span>📍 ${escapeHtml(opt.venue)}</span>` : ''}
          </div>
        </div>
      `).join('');

      electivesDropdown = `
        <details class="tt-electives-drawer">
          <summary class="tt-elec-summary">
            <span>Elective Offerings (${bucketOptions.length} available)</span>
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </summary>
          <div class="tt-elec-options-list">${optHtml}</div>
        </details>`;
    }

    return `
      <div class="tt-session-card ${isBreak ? 'tt-break-card' : ''}">
        <div class="tt-slot-col">
          <span class="tt-slot-badge">Slot ${sess.slot}</span>
          <span class="tt-time-badge font-mono">${escapeHtml(sess.time)}</span>
        </div>
        <div class="tt-info-col">
          <div class="tt-activity-row">
            <span class="tt-activity-name">${escapeHtml(sess.activity)}</span>
            ${sess.course_code ? `<span class="sem-code font-mono">${escapeHtml(sess.course_code)}</span>` : ''}
            ${isElective ? `<span class="tt-bucket-pill">${escapeHtml(sess.elective_bucket)}</span>` : ''}
          </div>
          ${sess.faculty || sess.venue ? `
            <div class="tt-meta-chips">
              ${sess.faculty ? `<span class="tt-meta-chip">👤 ${escapeHtml(sess.faculty)}</span>` : ''}
              ${sess.venue ? `<span class="tt-meta-chip">📍 ${escapeHtml(sess.venue)}</span>` : ''}
            </div>` : ''}
          ${electivesDropdown}
        </div>
      </div>`;
  }).join('');

  return `
    <div class="timetable-result-wrap">
      ${proseHtml}
      <div class="curriculum-hdr">
        <div class="curriculum-title-wrap">
          <span class="curr-code-badge">${escapeHtml(s.applied_weekday)} Schedule</span>
          <h3 class="curr-name">${escapeHtml(s.department || 'CSE')} · Semester ${escapeHtml(s.semester || 'VII')} (${escapeHtml(s.section || 'F')})</h3>
        </div>
        ${s.date ? `<span class="pl-count">${escapeHtml(s.date)}</span>` : ''}
      </div>
      ${overrideBanner}
      <div class="tt-sessions-list">${sessionCards}</div>
    </div>`;
}

/**
 * Renders Amrita B.Tech Academic Regulations 2023 clauses:
 * Displays clause numbers, parent clause topics, page citations, and formatted ordinance/regulation bodies.
 */
function renderRegulationResult(data) {
  let proseHtml = '';
  if (data.markdown_answer) {
    const rawProse = window.marked ? window.marked.parse(data.markdown_answer) : simpleMarkdown(data.markdown_answer);
    proseHtml = `<div class="ai-prose-answer" style="margin-bottom:16px;line-height:1.65;font-size:14px;border-bottom:1px solid var(--border-soft);padding-bottom:12px;">${rawProse}</div>`;
  }

  if (!data || !data.clauses || !data.clauses.length) {
    return proseHtml ? `<div class="regulation-result-wrap">${proseHtml}</div>` : `
      <div class="problem-list-empty">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <p>No regulations clauses found matching your query.</p>
      </div>`;
  }

  const clauses = data.clauses;
  const cards = clauses.map(c => {
    const rawText = c.clause_text || '';
    const safeText = escapeHtml(rawText);
    const parentTag = c.parent_clause_id && c.parent_clause_id !== c.clause_id
      ? `<span class="reg-parent-tag">Section ${escapeHtml(c.parent_clause_id)}</span>`
      : '';
    const pageBadge = c.page_number
      ? `<span class="reg-page-badge">Page ${c.page_number}</span>`
      : '';

    return `
      <div class="reg-clause-card">
        <div class="reg-clause-hdr">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="reg-id-badge font-mono">${escapeHtml(c.clause_id)}</span>
            <strong class="reg-title">${escapeHtml(c.title || 'Regulation')}</strong>
            ${parentTag}
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            ${pageBadge}
          </div>
        </div>
        <div class="reg-clause-body">${safeText}</div>
        <div class="reg-clause-footer">
          <span class="reg-source-cite">B.Tech Regulations 2023 · Directorate of Academic Regulations</span>
          <button class="pc-copy-btn" onclick="window.copyProblemText(this, ${JSON.stringify(rawText)})">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            <span>Copy Clause</span>
          </button>
        </div>
      </div>`;
  }).join('');

  return `
    <div class="regulation-result-wrap">
      ${proseHtml}
      <div class="curriculum-hdr">
        <div class="curriculum-title-wrap">
          <span class="curr-code-badge">Ordinances & Regulations</span>
          <h3 class="curr-name">Amrita B.Tech Academic Regulations (2023)</h3>
        </div>
        <span class="pl-count">${clauses.length} clause${clauses.length > 1 ? 's' : ''} found</span>
      </div>
      <div class="reg-clauses-list">${cards}</div>
    </div>`;
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
  
  document.getElementById('send-btn').disabled = true;
  showTyping(!!b64);

  try {
    await studentProfileReady;
    const payload = {
      message: text,
      session_id: currentSessionId,
    };
    if (b64) {
      payload.image_b64 = b64;
    }
    
    const res = await apiFetch('/chat', {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    // ── Check if backend returned a structured Fast-Path response (Zero-LLM) or JSON ──
    const contentType = res.headers.get('content-type') || '';
    const responseType = res.headers.get('X-Response-Type');

    if (contentType.includes('application/json') || responseType) {
      const json = await res.json();
      removeTyping();
      const bubble = createAIBubble(null);
      const effectiveType = responseType || json.type;

      if (effectiveType === 'problem_list') {
        bubble.innerHTML = renderProblemCards(json);
      } else if (effectiveType === 'curriculum_result') {
        bubble.innerHTML = renderCurriculumResult(json.data || json);
      } else if (effectiveType === 'prereq_result') {
        bubble.innerHTML = renderPrereqResult(json);
      } else if (effectiveType === 'co_mapping') {
        bubble.innerHTML = renderCoMapping(json);
      } else if (effectiveType === 'calendar_result') {
        bubble.innerHTML = renderCalendarResult(json);
      } else if (effectiveType === 'timetable_result') {
        bubble.innerHTML = renderTimetableResult(json);
      } else if (effectiveType === 'regulation_result') {
        bubble.innerHTML = renderRegulationResult(json);
      } else if (effectiveType === 'academic_document_result') {
        const text = json.markdown_answer || '';
        const rawHtml = window.marked ? window.marked.parse(text) : simpleMarkdown(text);
        bubble.innerHTML = `<div class="timetable-result-wrap">${window.DOMPurify ? window.DOMPurify.sanitize(rawHtml) : rawHtml}</div>`;
      } else if (json.markdown_answer || json.answer || json.response) {
        const text = json.markdown_answer || json.answer || json.response;
        bubble.innerHTML = window.marked ? window.marked.parse(text) : simpleMarkdown(text);
      } else {
        bubble.innerHTML = simpleMarkdown(JSON.stringify(json, null, 2));
      }
      scrollBottom();
      return;
    }

    // ── Normal streaming text response ──
    const msgId = res.headers.get('X-Message-ID') || null;
    removeTyping();
    const bubble = createAIBubble(msgId);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      fullText += decoder.decode(value, { stream: true });
      if (!fullText.trim().startsWith('{')) {
        let rawHtml = window.marked ? window.marked.parse(fullText) : simpleMarkdown(fullText);
        let safeHtml = window.DOMPurify ? window.DOMPurify.sanitize(rawHtml) : rawHtml;
        bubble.innerHTML = safeHtml;
      }
      scrollBottom();
    }

    // Safety fallback: if fullText was a JSON payload, parse and render it as interactive cards!
    const trimmed = fullText.trim();
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const json = JSON.parse(trimmed);
        if (json.type === 'timetable_result') {
          bubble.innerHTML = renderTimetableResult(json);
        } else if (json.type === 'calendar_result') {
          bubble.innerHTML = renderCalendarResult(json);
        } else if (json.type === 'regulation_result') {
          bubble.innerHTML = renderRegulationResult(json);
        } else if (json.markdown_answer) {
          bubble.innerHTML = window.marked ? window.marked.parse(json.markdown_answer) : simpleMarkdown(json.markdown_answer);
        }
      } catch (e) {
        bubble.innerHTML = window.marked ? window.marked.parse(fullText) : simpleMarkdown(fullText);
      }
    }
    
    // Speak the AI's response aloud
    if ('speechSynthesis' in window) {
      // Strip markdown characters so it reads naturally
      const plainText = fullText.replace(/[*#_`]/g, '').replace(/\n/g, '. ');
      const utterance = new SpeechSynthesisUtterance(plainText);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;
      window.speechSynthesis.speak(utterance);
    }

    if (msgId) {
      bubble.parentElement.querySelectorAll('.fb-btn').forEach(btn => {
        const dir = btn.getAttribute('onclick').includes("'up'") ? 'up' : 'down';
        btn.setAttribute('onclick', `vote(this,'${dir}','${msgId}')`);
      });
    }

  } catch (e) {
    removeTyping();
    if (e.message !== "Unauthorized") {
      createAIBubble(null).innerHTML = '<em style="color:var(--brand)">Network error or backend failed.</em>';
    }
  } finally {
    document.getElementById('send-btn').disabled = false;
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
window.logout = logout;

// Voice AI Integration (Speech-to-Text)
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
    // Cancel any ongoing speech synthesis if user interrupts
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
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
  document.getElementById('chat-input').placeholder = "Ask about your syllabus, PYQs, or any topic…";
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

