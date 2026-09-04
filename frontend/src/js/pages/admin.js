import { apiFetch } from '../core/api.js';
import { getUsername, requireRole, logout } from '../core/auth.js';

requireRole('admin');

const uname = getUsername() || 'Admin';
document.getElementById('admin-name').textContent = uname.charAt(0).toUpperCase() + uname.slice(1);

export function adminNav(el, section) {
  document.querySelectorAll('.a-nav-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');

  const sections = ['overview', 'upload', 'eval', 'curriculum-review', 'pyq-diagnostics', 'topic-mappings', 'settings'];
  sections.forEach(s => {
    const sectionEl = document.getElementById(`section-${s}`);
    if (sectionEl) sectionEl.style.display = 'none';
  });

  const activeSection = document.getElementById(`section-${section}`);
  if (activeSection) activeSection.style.display = 'block';
}
window.adminNav = adminNav;

let uploadTimer = null;

export function dzOver(e) { e.preventDefault(); document.getElementById('dropzone').classList.add('over'); }
export function dzLeave() { document.getElementById('dropzone').classList.remove('over'); }
export function dzDrop(e) {
  e.preventDefault(); dzLeave();
  const files = e.dataTransfer.files;
  if (files.length > 0) {
    document.getElementById('file-input').files = files; // Works in modern browsers or we just keep reference
    document.getElementById('file-input')._droppedFiles = files;
    updateFileText(files);
  }
}
window.dzOver = dzOver;
window.dzLeave = dzLeave;
window.dzDrop = dzDrop;

function updateFileText(files) {
  const textEl = document.getElementById('dz-filename');
  if (files.length === 1) textEl.textContent = files[0].name;
  else if (files.length > 1) textEl.textContent = `${files.length} files selected`;
  else textEl.textContent = '';
}

export function fileSelected(input) {
  updateFileText(input.files);
}
window.fileSelected = fileSelected;

export function docTypeChanged() {
  const docType = document.getElementById('doc-type').value;
  document.getElementById('course-code-field').style.display = docType === 'pyq' ? 'block' : 'none';
  document.getElementById('dept-field').style.display = docType === 'syllabus' ? 'block' : 'none';
  document.getElementById('year-field').style.display = docType === 'syllabus' ? 'block' : 'none';
}
window.docTypeChanged = docTypeChanged;

function setProg(pct, label) {
  document.getElementById('prog-fill').style.width = pct + '%';
  document.getElementById('prog-pct').textContent = pct + '%';
  document.getElementById('prog-label').textContent = label;
}

export async function doUpload() {
  const docType = document.getElementById('doc-type').value;
  const fileInput = document.getElementById('file-input');
  const files = fileInput._droppedFiles || fileInput.files;

  let code = '';
  let dept = '';
  let year = '';

  if (docType === 'pyq') {
    code = document.getElementById('course-code').value.trim();
    if (!code) return shakeField('course-code');
  } else if (docType === 'syllabus') {
    dept = document.getElementById('dept').value.trim();
    year = document.getElementById('year').value.trim();
    if (!dept) return shakeField('dept');
    if (!year) return shakeField('year');
  } else {
    return shakeField('doc-type');
  }

  if (!files || files.length === 0) {
    return shakeField('dropzone');
  }

  const btn = document.getElementById('upload-btn');
  btn.disabled = true; 
  document.getElementById('upload-ok').classList.remove('show');
  document.getElementById('upload-error').classList.remove('show');
  document.getElementById('prog-wrap').classList.add('show');

  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      btn.textContent = `Uploading ${i+1}/${files.length}…`;
      
      const stages = [[20,'Uploading PDF…'],[45,'Parsing document…'],[68,'Chunking content…'],[85,'Embedding chunks…'],[95,'Indexing into VectorDB…']];
      let si = 0;
      clearInterval(uploadTimer);
      uploadTimer = setInterval(() => { if (si < stages.length) { setProg(...stages[si]); si++; } }, 700);

      const formData = new FormData();
      formData.append('files', file); // changed 'file' to 'files' to match backend parameter
      formData.append('doc_type', docType);
      if (docType === 'pyq') formData.append('course_code', code);
      if (docType === 'syllabus') {
        // Use 'department' to match the FastAPI param name (backend also accepts 'dept' as alias)
        formData.append('department', dept);
        formData.append('year', year);
      }

      const res = await apiFetch('/upload', { method: 'POST', body: formData });
      clearInterval(uploadTimer);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Upload failed (HTTP ${res.status})`);
      }
      await res.json().catch(() => ({}));
    }

    setProg(100, 'Complete!');
    setTimeout(() => {
      document.getElementById('prog-wrap').classList.remove('show');
      document.getElementById('upload-ok').classList.add('show');
      btn.disabled = false; btn.innerHTML = 'Upload &amp; Index';
      setProg(0, '');
      fileInput._droppedFiles = null; fileInput.value = '';
      document.getElementById('dz-filename').textContent = '';
      fetchDocuments();
    }, 600);

  } catch (e) {
    clearInterval(uploadTimer);
    console.error("Upload error:", e);
    document.getElementById('prog-wrap').classList.remove('show');
    const errMsg = document.getElementById('upload-error-msg');
    errMsg.textContent = e.message === "Unauthorized" ? 'Unauthorized' : `Upload failed: ${e.message}`;
    document.getElementById('upload-error').classList.add('show');
    btn.disabled = false; btn.innerHTML = 'Upload &amp; Index';
  }
}
window.doUpload = doUpload;

function shakeField(id) {
  const el = document.getElementById(id);
  el.style.borderColor = 'var(--brand)';
  setTimeout(() => el.style.borderColor = '', 1400);
}

export async function fetchDocuments() {
  const tbody = document.getElementById('documents-table-body');
  try {
    const res = await apiFetch('/documents');
    const docs = await res.json();

    if (!docs || docs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-4);padding:24px;">No documents indexed yet.</td></tr>';
      return;
    }

    tbody.innerHTML = docs.map(doc => {
      let statusHtml = doc.processing_status || '—';
      if (statusHtml === 'completed') statusHtml = '<span style="color:green;">Completed</span>';
      else if (statusHtml === 'failed') statusHtml = '<span style="color:var(--brand);">Failed</span>';
      else if (statusHtml === 'partially_completed') statusHtml = '<span style="color:#d97706;">Partial</span>';
      else if (statusHtml === 'processing') statusHtml = '<span style="color:#2563eb;">Processing</span>';

      return `
      <tr>
        <td><strong>${doc.course_code || '—'}</strong></td>
        <td><span class="doc-type-badge">${doc.doc_type || '—'}</span></td>
        <td style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;">${doc.filename || '—'}</td>
        <td style="font-size:11.5px;color:var(--text-3);">${doc.created_at ? new Date(doc.created_at).toLocaleDateString() : '—'}</td>
        <td>${statusHtml}</td>
        <td><button class="btn-del" onclick="window.deleteDocument('${doc.id}')">Delete</button></td>
      </tr>`;
    }).join('');

    document.getElementById('stat-docs').textContent = docs.length;
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--brand);padding:20px;">Error loading documents.</td></tr>';
  }
}
window.fetchDocuments = fetchDocuments;

export async function deleteDocument(id) {
  if (!confirm('Delete this document and its indexed data?')) return;
  try {
    await apiFetch(`/documents/${id}`, { method: 'DELETE' });
    fetchDocuments();
  } catch (e) {
    alert('Failed to delete document or network error.');
  }
}
window.deleteDocument = deleteDocument;

let evalChartInstance = null;

async function loadEvalChart() {
  const canvas = document.getElementById('evalChart');
  if (!canvas || typeof window.Chart === 'undefined') return;

  let scores = [0.82, 0.91, 0.78, 0.88, 0.85];
  try {
    const res = await apiFetch('/evaluate');
    const data = await res.json();
    if (data.metrics) {
      scores = Object.values(data.metrics);
      const mean = (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(2);
      document.getElementById('eval-mean').textContent = mean;
      document.getElementById('stat-mean').textContent = mean;
      document.getElementById('eval-count').textContent = data.num_samples || '5';
      document.getElementById('eval-status-text').textContent = `Last run: ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})} · Model: ${data.model || 'local'}`;
    }
  } catch (e) { /* use placeholder */ }

  if (evalChartInstance) evalChartInstance.destroy();

  evalChartInstance = new window.Chart(canvas, {
    type: 'bar',
    data: {
      labels: ['Context\nPrecision', 'Faithfulness', 'Answer\nRelevance', 'Context\nRecall', 'Answer\nCorrectness'],
      datasets: [{
        label: 'Score (0–1)',
        data: scores,
        backgroundColor: ['rgba(164,18,63,0.80)','rgba(164,18,63,0.70)','rgba(164,18,63,0.55)','rgba(164,18,63,0.70)','rgba(164,18,63,0.65)'],
        borderColor: 'rgba(164,18,63,0.90)',
        borderWidth: 1.5,
        borderRadius: 5,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1A1A1A', titleColor: '#fff',
          bodyColor: 'rgba(255,255,255,.7)', padding: 10, cornerRadius: 8,
          callbacks: { label: ctx => ` Score: ${ctx.parsed.x.toFixed(2)}` }
        }
      },
      scales: {
        x: {
          min: 0, max: 1,
          grid: { color: 'rgba(0,0,0,.05)', drawTicks: false },
          border: { display: false },
          ticks: { color: '#8A8A8A', font: { family: "'Inter',sans-serif", size: 11 }, callback: v => v.toFixed(1) }
        },
        y: {
          grid: { display: false }, border: { display: false },
          ticks: { color: '#4A4A4A', font: { family: "'Inter',sans-serif", size: 11.5, weight: '500' } }
        }
      }
    }
  });
}

export async function runEvaluation() {
  const btn = document.getElementById('run-eval-btn');
  btn.disabled = true; btn.textContent = 'Running…';
  document.getElementById('eval-status-text').textContent = 'Running evaluation pipeline…';

  if (evalChartInstance) { evalChartInstance.destroy(); evalChartInstance = null; }
  await loadEvalChart();
  btn.disabled = false; btn.innerHTML = 'Run Evaluation Pipeline';
}
window.runEvaluation = runEvaluation;

export async function resetSystem() {
  const first = confirm('WARNING: This will permanently delete ALL documents, ALL knowledge graph data, and ALL uploaded files.\n\nThis cannot be undone. Are you absolutely sure?');
  if (!first) return;
  const second = confirm('Last chance — confirm you want to wipe the entire system.');
  if (!second) return;

  const statusEl = document.getElementById('reset-status');
  const btn = document.getElementById('reset-btn');
  statusEl.textContent = 'Resetting system… please wait.';
  btn.disabled = true;

  try {
    await apiFetch('/reset', { method: 'POST' });
    statusEl.style.color = 'var(--brand)';
    statusEl.textContent = 'System reset complete. All data has been wiped.';
    fetchDocuments();
  } catch (e) {
    statusEl.textContent = 'Network error or reset failed.';
  } finally {
    btn.disabled = false;
  }
}
window.resetSystem = resetSystem;
window.logout = logout;

window.addEventListener('DOMContentLoaded', () => {
  fetchDocuments();
  loadEvalChart();
  fetchAdminCourses();
});

// ============================================================================
// New Admin Graph RAG Functions
// ============================================================================

export async function fetchAdminCourses() {
  try {
    const res = await apiFetch('/admin/extraction-review/courses');
    const data = await res.json();
    const courses = data.courses || [];

    const populate = (selectId) => {
      const select = document.getElementById(selectId);
      if (!select) return;
      const currentVal = select.value;
      select.innerHTML = '<option value="">Select course…</option>' + 
        courses.map(c => `<option value="${c.code}">${c.code} - ${c.name}</option>`).join('');
      if (currentVal && courses.find(c => c.code === currentVal)) {
        select.value = currentVal;
      }
    };

    populate('cr-course-select');
    populate('pyq-course-select');
    populate('tm-course-select');
  } catch(e) {
    console.error('Failed to fetch courses:', e);
  }
}

// -- Curriculum Review --
export async function loadCurriculumReview() {
  const code = document.getElementById('cr-course-select').value;
  const content = document.getElementById('cr-content');
  const approveAllBtn = document.getElementById('cr-approve-all-btn');
  
  if (!code) {
    content.innerHTML = '<p style="color:var(--text-4);font-size:13px;">Select a course to begin review.</p>';
    approveAllBtn.style.display = 'none';
    return;
  }

  content.innerHTML = '<p>Loading...</p>';
  try {
    const res = await apiFetch(`/admin/extraction-review/courses/${code}`);
    const data = await res.json();
    const units = data.units || [];
    
    if (units.length === 0) {
      content.innerHTML = '<p>No curriculum data found for this course.</p>';
      approveAllBtn.style.display = 'none';
      return;
    }

    approveAllBtn.style.display = 'block';

    let html = '';
    units.forEach(u => {
      html += `
        <div style="border:1px solid var(--border); border-radius:6px; margin-bottom:16px; padding:12px;">
          <h4 style="margin:0 0 8px 0;">Unit ${u.unit_number}: ${u.unit_title}</h4>
          <table class="docs-table" style="margin-top:8px;">
            <thead>
              <tr><th>Topic Name</th><th>Confidence</th><th>Status</th><th>Actions</th></tr>
            </thead>
            <tbody>
              ${(u.topics || []).map(t => `
                <tr>
                  <td>${t.name}</td>
                  <td>${t.extraction_confidence != null ? t.extraction_confidence.toFixed(2) : '-'}</td>
                  <td>${t.approved ? '<span style="color:green;">Approved</span>' : '<span style="color:#d97706;">Pending</span>'}</td>
                  <td>
                    <button class="btn-upload" style="padding:4px 8px; font-size:11px;" onclick="window.updateTopic('${t.id}', true)">Approve</button>
                    <button class="btn-del" style="padding:4px 8px; font-size:11px;" onclick="window.updateTopic('${t.id}', false)">Reject</button>
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;
    });
    content.innerHTML = html;

  } catch(e) {
    content.innerHTML = `<p style="color:red;">Error loading curriculum: ${e.message}</p>`;
  }
}
window.loadCurriculumReview = loadCurriculumReview;

export async function updateTopic(topicId, approved) {
  try {
    await apiFetch(`/admin/extraction-review/topics/${topicId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved })
    });
    loadCurriculumReview();
  } catch(e) {
    alert('Failed to update topic: ' + e.message);
  }
}
window.updateTopic = updateTopic;

export async function approveAllTopics() {
  const code = document.getElementById('cr-course-select').value;
  if (!code) return;
  if (!confirm(`Approve all topics for ${code}?`)) return;
  
  try {
    await apiFetch(`/admin/extraction-review/courses/${code}/approve`, { method: 'POST' });
    loadCurriculumReview();
  } catch(e) {
    alert('Failed to approve all: ' + e.message);
  }
}
window.approveAllTopics = approveAllTopics;

// -- PYQ Diagnostics --
let pyqChartInstance = null;

export async function loadPYQDiagnostics() {
  const code = document.getElementById('pyq-course-select').value;
  const tbody = document.getElementById('pyq-questions-body');
  const summary = document.getElementById('pyq-diag-summary');
  const histWrap = document.getElementById('pyq-histogram-wrap');
  
  if (!code) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-4);padding:20px;">Select a course above.</td></tr>';
    summary.style.display = 'none';
    histWrap.style.display = 'none';
    return;
  }

  tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;">Loading...</td></tr>';
  summary.style.display = 'none';
  histWrap.style.display = 'none';

  try {
    const summaryRes = await apiFetch(`/admin/diagnostics/pyq/${code}`);
    const summaryData = await summaryRes.json();
    
    if (summaryData.summary) {
      document.getElementById('pyq-stat-total').textContent = summaryData.summary.total_questions || 0;
      document.getElementById('pyq-stat-co').textContent = summaryData.summary.questions_with_co || 0;
      document.getElementById('pyq-stat-topic').textContent = summaryData.summary.questions_mapped_to_topic || 0;
      document.getElementById('pyq-stat-docs').textContent = summaryData.summary.source_documents || 0;
      summary.style.display = 'flex';
    }

    const qRes = await apiFetch(`/admin/diagnostics/pyq/${code}/questions`);
    const qData = await qRes.json();
    const questions = qData.questions || [];

    if (questions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;">No questions found.</td></tr>';
      return;
    }

    tbody.innerHTML = questions.map(q => `
      <tr>
        <td>${q.question_number || '-'}</td>
        <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(q.question||'').replace(/"/g, '&quot;')}">${q.question || '-'}</td>
        <td>${q.marks || '-'}</td>
        <td>${q.btl || '-'}</td>
        <td>${q.course_outcome || '-'}</td>
        <td>${q.mapped_syllabus_topic || '-'}</td>
        <td>${q.mapping_confidence != null ? q.mapping_confidence.toFixed(2) : '-'}</td>
        <td>${q.image_url ? '<a href="'+q.image_url+'" target="_blank">Image</a>' : '-'}</td>
      </tr>
    `).join('');

    // Draw histogram
    const confidences = questions.map(q => q.mapping_confidence).filter(c => c != null);
    if (confidences.length > 0 && typeof window.Chart !== 'undefined') {
      const bins = [0,0,0,0,0]; // <0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0
      confidences.forEach(c => {
        if (c < 0.2) bins[0]++;
        else if (c < 0.4) bins[1]++;
        else if (c < 0.6) bins[2]++;
        else if (c < 0.8) bins[3]++;
        else bins[4]++;
      });

      histWrap.style.display = 'block';
      const canvas = document.getElementById('pyq-confidence-chart');
      if (pyqChartInstance) pyqChartInstance.destroy();
      pyqChartInstance = new window.Chart(canvas, {
        type: 'bar',
        data: {
          labels: ['<0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0'],
          datasets: [{
            label: 'Questions',
            data: bins,
            backgroundColor: 'rgba(164,18,63,0.7)',
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false }, title: {display: true, text: 'Mapping Confidence Distribution'} },
          scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
        }
      });
    }

  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:red;padding:20px;">Error: ${e.message}</td></tr>`;
  }
}
window.loadPYQDiagnostics = loadPYQDiagnostics;

// -- Topic Mappings --
export async function loadTopicMappings() {
  const code = document.getElementById('tm-course-select').value;
  const tbody = document.getElementById('tm-mappings-body');
  
  if (!code) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-4);padding:20px;">Select a course above.</td></tr>';
    return;
  }

  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;">Loading...</td></tr>';
  try {
    const res = await apiFetch(`/admin/topic-mapping-review/${code}`);
    const data = await res.json();
    const mappings = data.mappings || [];
    
    if (mappings.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;">No mappings found.</td></tr>';
      return;
    }

    tbody.innerHTML = mappings.map(m => `
      <tr>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(m.question_text||'').replace(/"/g, '&quot;')}">${m.question_text || '-'}</td>
        <td>${m.topic_name || '-'}</td>
        <td>${m.semantic_score != null ? m.semantic_score.toFixed(2) : '-'}</td>
        <td>${m.keyword_score != null ? m.keyword_score.toFixed(2) : '-'}</td>
        <td><strong>${m.confidence != null ? m.confidence.toFixed(2) : '-'}</strong></td>
        <td>${m.review_status === 'approved' ? '<span style="color:green;">Approved</span>' : (m.review_status === 'rejected' ? '<span style="color:var(--brand);">Rejected</span>' : '<span style="color:#d97706;">Pending</span>')}</td>
        <td>
          <button class="btn-upload" style="padding:4px 8px; font-size:11px;" onclick="window.updateMapping(${m.relationship_id}, 'approved')">Approve</button>
          <button class="btn-del" style="padding:4px 8px; font-size:11px;" onclick="window.updateMapping(${m.relationship_id}, 'rejected')">Reject</button>
        </td>
      </tr>
    `).join('');

  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:red;padding:20px;">Error: ${e.message}</td></tr>`;
  }
}
window.loadTopicMappings = loadTopicMappings;

export async function updateMapping(relId, status) {
  try {
    await apiFetch(`/admin/topic-mapping-review/${relId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ review_status: status })
    });
    loadTopicMappings();
  } catch(e) {
    alert('Failed to update mapping: ' + e.message);
  }
}
window.updateMapping = updateMapping;

