/**
 * M.A.C.H. AI — Shared Frontend Utilities
 * Formatting, HTML escaping, safe Markdown rendering, and notification toasts.
 */

export function timeNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function escapeHtml(s) {
  if (!s) return '';
  return String(s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m]);
}

export function decodeJwtPayload(token) {
  if (!token || !token.includes('.')) return {};
  const b64Url = token.split('.')[1];
  const b64 = b64Url.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(b64Url.length / 4) * 4, '=');
  return JSON.parse(atob(b64));
}

export function simpleMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Parse markdown images safely (only allow http, https, or relative path /)
    .replace(/!\[([^\]]*)\]\(((?:https?:\/\/|\/)[^\s\)\"]+)\)/g, '<img src="$2" alt="$1" style="max-width: 100%; border-radius: 8px; margin-top: 8px;">')
    // Parse markdown links safely (only allow http, https, or relative path /)
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\s\)\"]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/^[-•]\s+(.+)$/gm, '<li>$1</li>')
    .replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>')
    .replace(/\n/g, '<br>');
}

export function refreshPage(event) {
  if (event) event.preventDefault();
  document.body.style.transition = 'opacity 140ms ease';
  document.body.style.opacity = '0.35';
  setTimeout(() => window.location.reload(), 130);
}
window.refreshPage = refreshPage;

export function showToast(message, type = 'success') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.style.borderLeftColor = type === 'error' ? 'red' : 'var(--brand)';
  toast.textContent = message;
  
  container.appendChild(toast);
  
  // Remove toast after 3 seconds
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}
