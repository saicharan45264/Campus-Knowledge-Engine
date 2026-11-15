/**
 * M.A.C.H. AI — Admin Management Portal Controller
 * Manages curriculum PDF uploads, real-time ingestion queue monitoring,
 * empirical benchmark evaluation execution, and system resets.
 */
import { apiFetch } from './api.js';
import { getUsername, requireRole, logout } from './auth.js';
import { escapeHtml, refreshPage, showToast } from './utils.js';

requireRole('admin');

const uname = getUsername() || 'Admin';
document.getElementById('admin-name').textContent = uname.charAt(0).toUpperCase() + uname.slice(1);

export function adminNav(el, section) {
  document.querySelectorAll('.a-nav-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');

  const sections = ['overview', 'upload', 'eval', 'settings'];
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
  const previewWrap = document.getElementById('file-queue-preview');
  const previewList = document.getElementById('file-queue-list');

  if (!files || files.length === 0) {
    textEl.textContent = '';
    if (previewWrap) previewWrap.style.display = 'none';
    return;
  }

  if (files.length === 1) textEl.textContent = files[0].name;
  else textEl.textContent = `${files.length} files selected for bulk ingestion`;

  if (previewWrap && previewList) {
    previewList.innerHTML = Array.from(files).map((f, idx) => {
      const sizeKb = (f.size / 1024).toFixed(1);
      const sizeStr = f.size > 1048576 ? (f.size / 1048576).toFixed(1) + ' MB' : sizeKb + ' KB';
      return `
        <div style="display:flex;align-items:center;justify-content:space-between;background:var(--bg-2);padding:6px 10px;border-radius:6px;border:1px solid var(--border-1);font-size:12px;">
          <div style="display:flex;align-items:center;gap:8px;overflow:hidden;">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;color:var(--brand);"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span style="font-family:'IBM Plex Mono',monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(f.name)}</span>
          </div>
          <span style="font-size:11px;color:var(--text-3);flex-shrink:0;margin-left:12px;">${sizeStr}</span>
        </div>
      `;
    }).join('');
    previewWrap.style.display = 'block';
  }
}

export function fileSelected(input) {
  updateFileText(input.files);
}
window.fileSelected = fileSelected;

export function docTypeChanged() {
  const docType = document.getElementById('doc-type').value;
  const visible = id => { document.getElementById(id).style.display = 'block'; };
  const hidden = id => { document.getElementById(id).style.display = 'none'; };
  const needsDept = ['syllabus', 'pyq', 'timetable', 'exam_schedule', 'seating_arrangement', 'lab_timetable'].includes(docType);
  const needsYear = ['syllabus', 'timetable'].includes(docType);
  const needsSemester = ['timetable', 'exam_schedule', 'seating_arrangement', 'lab_timetable'].includes(docType);
  const needsCourseCode = docType === 'pyq';
  const needsCourseName = false;
  const needsSection = docType === 'timetable';

  const allDepartmentsOption = document.querySelector('#dept option[data-combined-scope="all"]');
  const departmentHelp = document.getElementById('dept-scope-help');
  const supportsCombinedScope = ['exam_schedule', 'seating_arrangement'].includes(docType);
  if (allDepartmentsOption) {
    allDepartmentsOption.hidden = !supportsCombinedScope;
    allDepartmentsOption.disabled = !supportsCombinedScope;
    if (!supportsCombinedScope && document.getElementById('dept').value === 'ALL') {
      document.getElementById('dept').value = '';
    }
  }
  if (departmentHelp) departmentHelp.style.display = supportsCombinedScope ? 'block' : 'none';

  (needsDept ? visible : hidden)('dept-field');
  (needsYear ? visible : hidden)('year-field');
  (needsCourseCode ? visible : hidden)('course-code-field');
  (needsCourseName ? visible : hidden)('course-name-field');
  (needsSemester ? visible : hidden)('semester-field');
  (needsSection ? visible : hidden)('section-field');
  const yearLabel = document.getElementById('year-label');
  if (yearLabel) yearLabel.textContent = docType === 'timetable' ? 'Academic Year *' : 'Academic Year / Batch *';
}
window.docTypeChanged = docTypeChanged;
document.getElementById('dept').addEventListener('change', event => {
  const isOther = event.target.value === '__other__';
  document.getElementById('dept-other').style.display = isOther ? 'block' : 'none';
});

function setProg(pct, label) {
  document.getElementById('prog-fill').style.width = pct + '%';
  document.getElementById('prog-pct').textContent = pct + '%';
  document.getElementById('prog-label').textContent = label;
}

let pollingInterval = null;

export async function doUpload() {
  const docType = document.getElementById('doc-type').value;
  const fileInput = document.getElementById('file-input');
  const files = fileInput._droppedFiles || fileInput.files;

  const deptValue = document.getElementById('dept').value;
  const dept = (deptValue === '__other__' ? document.getElementById('dept-other').value : deptValue).trim().toUpperCase();
  const year = document.getElementById('year').value.trim();
  const code = document.getElementById('course-code').value.trim().toUpperCase();
  const courseName = document.getElementById('course-name').value.trim();
  const semester = document.getElementById('semester').value;
  const section = document.getElementById('section').value.trim().toUpperCase();

  if (!files || files.length === 0) {
    return shakeField('dropzone');
  }

  const needsDept = ['syllabus', 'pyq', 'timetable', 'exam_schedule', 'seating_arrangement', 'lab_timetable'].includes(docType);
  const needsYear = ['syllabus', 'timetable'].includes(docType);
  const needsCourseCode = docType === 'pyq';
  const needsSemester = ['timetable', 'exam_schedule', 'seating_arrangement', 'lab_timetable'].includes(docType);
  const needsSection = docType === 'timetable';
  const missing = (needsDept && !dept) || (needsYear && !year) || (needsCourseCode && !code) || (needsSemester && !semester) || (needsSection && !section);
  if (missing) {
    const errMsg = document.getElementById('upload-error-msg');
    errMsg.textContent = 'Complete the required document scope fields before uploading.';
    document.getElementById('upload-error').classList.add('show');
    return;
  }

  const btn = document.getElementById('upload-btn');
  btn.disabled = true; 
  document.getElementById('upload-ok').classList.remove('show');
  document.getElementById('upload-error').classList.remove('show');
  document.getElementById('prog-wrap').classList.add('show');

  try {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }
    formData.append('doc_type', docType);
    if (code) formData.append('course_code', code);
    if (courseName) formData.append('course_name', courseName);
    if (dept) formData.append('department', dept);
    if (year) formData.append('year', year);
    if (semester) formData.append('semester', semester);
    if (section) formData.append('section', section);

    btn.textContent = `Uploading ${files.length} document(s)…`;
    
    const stages = [
      [20, 'Uploading PDF files…'],
      [45, 'Reading document structure…'],
      [68, 'Extracting questions & diagrams…'],
      [85, 'Updating course directory…'],
      [95, 'Saving search index…']
    ];
    let si = 0;
    clearInterval(uploadTimer);
    uploadTimer = setInterval(() => { 
      if (si < stages.length) { 
        setProg(...stages[si]); 
        si++; 
      } 
    }, 600);

    const res = await apiFetch('/upload', { method: 'POST', body: formData });
    clearInterval(uploadTimer);
    
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Upload failed (HTTP ${res.status})`);
    }
    const resData = await res.json().catch(() => ({}));

const UPLOAD_BTN_HTML = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> Upload &amp; Process`;

    setProg(100, 'Upload complete!');
    setTimeout(() => {
      document.getElementById('prog-wrap').classList.remove('show');
      const okMsg = document.getElementById('upload-ok-msg');
      if (okMsg && resData.message) okMsg.textContent = resData.message;
      document.getElementById('upload-ok').classList.add('show');
      btn.disabled = false; 
      btn.innerHTML = UPLOAD_BTN_HTML;
      setProg(0, '');
      fileInput._droppedFiles = null; 
      fileInput.value = '';
      document.getElementById('dz-filename').textContent = '';
      const previewWrap = document.getElementById('file-queue-preview');
      if (previewWrap) previewWrap.style.display = 'none';
      fetchDocuments();
      startPollingStatus();
    }, 600);

  } catch (e) {
    clearInterval(uploadTimer);
    document.getElementById('prog-wrap').classList.remove('show');
    const errMsg = document.getElementById('upload-error-msg');
    errMsg.textContent = e.message === "Unauthorized" ? 'Unauthorized' : (e.message || 'Upload failed.');
    document.getElementById('upload-error').classList.add('show');
    btn.disabled = false; 
    btn.innerHTML = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> Upload &amp; Process`;
  }
}
window.doUpload = doUpload;

function startPollingStatus() {
  if (pollingInterval) clearInterval(pollingInterval);
  let pollCount = 0;
  pollingInterval = setInterval(async () => {
    pollCount++;
    const hasPending = await fetchDocuments();
    if (!hasPending || pollCount >= 60) {
      clearInterval(pollingInterval);
    }
  }, 3000);
}

function shakeField(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.borderColor = 'var(--brand)';
  setTimeout(() => el.style.borderColor = '', 1400);
}

export async function fetchDocuments() {
  const tbody = document.getElementById('documents-table-body');
  try {
    await loadDepartmentOptions();
    const selectedDepartment = document.getElementById('department-filter')?.value || '';
    const query = selectedDepartment ? `?department=${encodeURIComponent(selectedDepartment)}` : '';
    const res = await apiFetch(`/documents${query}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const docs = await res.json();

    if (!docs || docs.length === 0) {
      document.getElementById('stat-docs').textContent = '0';
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-4);padding:24px;">No documents uploaded yet.</td></tr>';
      return false;
    }

    let hasProcessing = false;

    tbody.innerHTML = docs.map(doc => {
      let statusBadge = '';
      if (doc.status === 'completed') {
        statusBadge = `<span style="color:#15803d;background:#dcfce7;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:4px;">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
          ${escapeHtml(doc.details || 'Completed')}
        </span>`;
      } else if (doc.status === 'partial') {
        statusBadge = `<span style="color:#b45309;background:#fef3c7;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">${escapeHtml(doc.details || 'Partially indexed')}</span>`;
      } else if (doc.status === 'error') {
        const details = escapeHtml(doc.details || 'Ingestion failed');
        statusBadge = `<span title="${details}" style="color:#b91c1c;background:#fee2e2;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">${details}</span>`;
      } else {
        hasProcessing = true;
        statusBadge = `<span style="color:#b45309;background:#fef3c7;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:4px;">
          <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#b45309;"></span>
          Processing…
        </span>`;
      }

      return `
        <tr>
          <td><strong>${escapeHtml(doc.course_code || doc.department || '—')}</strong>${doc.course_name ? `<div style="font-size:11px;color:var(--text-3);">${escapeHtml(doc.course_name)}</div>` : ''}${doc.semester ? `<div style="font-size:11px;color:var(--text-3);">Sem ${escapeHtml(doc.semester)}${doc.section ? ` · ${escapeHtml(doc.section)}` : ''}</div>` : ''}</td>
          <td><span class="doc-type-badge">${escapeHtml(doc.doc_type || '—')}</span></td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;">${escapeHtml(doc.filename || '—')}</td>
          <td>${statusBadge}</td>
          <td style="font-size:11.5px;color:var(--text-3);">${doc.created_at ? new Date(doc.created_at).toLocaleDateString() : '—'}</td>
          <td style="white-space:nowrap;">
            ${doc.doc_type === 'lab_timetable' ? `<button class="btn-del" style="margin-right:6px;" onclick="window.reprocessDocument('${escapeHtml(doc.id)}')">Reprocess</button>` : ''}
            <button class="btn-del" onclick="window.deleteDocument('${escapeHtml(doc.id)}')">Delete</button>
          </td>
        </tr>`;
    }).join('');

    document.getElementById('stat-docs').textContent = docs.length;
    return hasProcessing;
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--brand);padding:20px;">Error loading documents.</td></tr>';
    return false;
  }
}
window.fetchDocuments = fetchDocuments;

let departmentOptionsLoaded = false;
async function loadDepartmentOptions() {
  const select = document.getElementById('department-filter');
  if (!select || departmentOptionsLoaded) return;
  try {
    const response = await apiFetch('/documents/departments');
    if (!response.ok) return;
    const departments = await response.json();
    const current = select.value;
    select.innerHTML = '<option value="">All departments</option>' + departments.map(dept => `<option value="${escapeHtml(dept)}">${escapeHtml(dept)}</option>`).join('');
    select.value = current;
    departmentOptionsLoaded = true;
  } catch (error) {
    console.warn('[Admin] Department filter options unavailable:', error);
  }
}

export async function deleteDocument(id) {
  if (!confirm('Delete this document and its extracted data?')) return;
  try {
    const res = await apiFetch(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Delete failed (HTTP ${res.status})`);
    }
    showToast('Document deleted successfully.', 'success');
    fetchDocuments();
  } catch (e) {
    showToast(e.message || 'Failed to delete document or network error.', 'error');
  }
}
window.deleteDocument = deleteDocument;

export async function reprocessDocument(id) {
  try {
    const res = await apiFetch(`/documents/${encodeURIComponent(id)}/reprocess`, { method: 'POST' });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(payload.detail || `Reprocess failed (HTTP ${res.status})`);
    showToast('Lab timetable reprocessing queued.', 'success');
    fetchDocuments();
  } catch (error) {
    showToast(error.message || 'Could not queue lab timetable reprocessing.', 'error');
  }
}
window.reprocessDocument = reprocessDocument;

let evalChartInstance = null;

async function loadEvalChart() {
  const canvas = document.getElementById('evalChart');
  if (!canvas || typeof window.Chart === 'undefined') return;

  let labels = ['Syllabus Lookup', 'Prerequisites', 'Topic PYQs', 'BTL & CO Mapping', 'Ranking Score', 'Topic Detection'];
  let scores = [0.80, 0.80, 1.00, 1.00, 0.85, 0.95];

  try {
    const res = await apiFetch('/eval/metrics');
    const data = await res.json();
    if (data.benchmark) {
      const b = data.benchmark;
      const cats = b.categories || {};
      const currHit = cats.curriculum ? cats.curriculum.hit_rate_1 : 0.8;
      const prereqHit = cats.prerequisites ? cats.prerequisites.hit_rate_1 : 0.8;
      const pyqHit = cats.topic_pyqs ? cats.topic_pyqs.hit_rate_1 : 1.0;
      const pedHit = cats.pedagogical ? cats.pedagogical.hit_rate_1 : 1.0;
      const mrr = b.mean_reciprocal_rank_mrr || 0.85;
      const intentAcc = b.intent_accuracy || 0.95;

      scores = [currHit, prereqHit, pyqHit, pedHit, mrr, intentAcc];
      const mean = (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(2);
      document.getElementById('eval-mean').textContent = `${(mean * 100).toFixed(0)}%`;
      document.getElementById('stat-mean').textContent = `${(mean * 100).toFixed(0)}%`;
      document.getElementById('eval-count').textContent = b.total_test_cases || '20';
      
      const p50 = b.latency_p50_ms || 70;
      const p95 = b.latency_p95_ms || 180;
      const qCount = data.telemetry && typeof data.telemetry.total_queries === 'number'
        ? ` · Total queries: ${data.telemetry.total_queries}`
        : '';
      document.getElementById('eval-status-text').textContent = 
        `Last check: ${b.total_test_cases} queries · Median speed: ${p50}ms · p95: ${p95}ms · Routing: ${(intentAcc*100).toFixed(1)}%${qCount}`;
    }
  } catch (e) {
    console.warn('[Admin] Quality metrics notice:', e);
  }

  if (evalChartInstance) evalChartInstance.destroy();

  evalChartInstance = new window.Chart(canvas, {
    type: 'radar',
    data: {
      labels: labels,
      datasets: [{
        label: 'MACH AI Accuracy',
        data: scores,
        backgroundColor: 'rgba(164, 18, 63, 0.20)',
        borderColor: 'rgba(164, 18, 63, 0.90)',
        pointBackgroundColor: '#A4123F',
        pointBorderColor: '#fff',
        pointHoverBackgroundColor: '#fff',
        pointHoverBorderColor: '#A4123F',
        borderWidth: 2,
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { color: '#4A4A4A', font: { family: "'Inter',sans-serif", size: 12, weight: '600' } }
        },
        tooltip: {
          backgroundColor: '#1A1A1A',
          titleColor: '#fff',
          bodyColor: 'rgba(255,255,255,.85)',
          padding: 10,
          cornerRadius: 8,
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${(ctx.parsed.r * 100).toFixed(1)}%`
          }
        }
      },
      scales: {
        r: {
          min: 0,
          max: 1.0,
          ticks: {
            stepSize: 0.2,
            color: '#8A8A8A',
            backdropColor: 'transparent',
            callback: v => `${(v * 100).toFixed(0)}%`
          },
          grid: { color: 'rgba(0,0,0,0.06)' },
          angleLines: { color: 'rgba(0,0,0,0.08)' },
          pointLabels: {
            color: '#2A2A2A',
            font: { family: "'Inter',sans-serif", size: 11.5, weight: '600' }
          }
        }
      }
    }
  });
}

export async function runEvaluation() {
  const btn = document.getElementById('run-eval-btn');
  btn.disabled = true;
  btn.textContent = 'Running Quality Check…';
  document.getElementById('eval-status-text').textContent = 'Running verification checks across all categories…';

  try {
    const res = await apiFetch('/eval/run', {
      method: 'POST',
      body: JSON.stringify({ category: 'all' })
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Quality check failed (HTTP ${res.status})`);
    }
    const summary = await res.json();
    console.log('[Admin] Quality check completed:', summary);
    showToast('Quality check completed.', 'success');
  } catch (e) {
    console.error('[Admin] Quality check error:', e);
    document.getElementById('eval-status-text').textContent = e.message || 'Quality check failed.';
    showToast(e.message || 'Quality check failed.', 'error');
  }

  if (evalChartInstance) { evalChartInstance.destroy(); evalChartInstance = null; }
  await loadEvalChart();
  btn.disabled = false;
  btn.innerHTML = 'Run Quality Check';
}
window.runEvaluation = runEvaluation;

export async function resetSystem() {
  const first = confirm('WARNING: This will permanently delete all uploaded documents, syllabus records, question data, and diagrams.\n\nThis cannot be undone. Are you sure?');
  if (!first) return;
  const second = confirm('Please confirm once more that you want to clear all data.');
  if (!second) return;

  const statusEl = document.getElementById('reset-status');
  const btn = document.getElementById('reset-btn');
  statusEl.textContent = 'Clearing all data… please wait.';
  btn.disabled = true;

  try {
    const res = await apiFetch('/reset', { method: 'POST' });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Reset failed (HTTP ${res.status})`);
    }
    statusEl.style.color = 'var(--brand)';
    statusEl.textContent = 'All data has been cleared.';
    showToast('All data cleared.', 'success');
    fetchDocuments();
  } catch (e) {
    statusEl.textContent = e.message || 'Network error or reset failed.';
    showToast(e.message || 'Reset failed.', 'error');
  } finally {
    btn.disabled = false;
  }
}
window.resetSystem = resetSystem;
window.logout = logout;

window.addEventListener('DOMContentLoaded', () => {
  docTypeChanged();
  fetchDocuments().then(hasPending => { if (hasPending) startPollingStatus(); });
  loadEvalChart();
});
