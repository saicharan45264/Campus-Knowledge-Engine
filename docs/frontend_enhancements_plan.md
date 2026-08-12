# Frontend UI/UX Enhancements Execution Plan

**Purpose:** This document is the technical blueprint for upgrading the Vanilla JS frontend of CurriculumLens. These features do not require any backend modifications, but they elevate the user experience from a "student project" to a production-grade, enterprise-ready application.

---

## Phase 1: Progressive Web App (PWA) Integration

A PWA allows users to install the web app natively on their mobile devices (iOS/Android) and caches core files for fast loading.

**1. Create `frontend/public/manifest.json`**
```json
{
  "name": "CurriculumLens AI",
  "short_name": "M.A.C.H.",
  "start_url": "/public/login.html",
  "display": "standalone",
  "background_color": "#0E0E0E",
  "theme_color": "#A4123F",
  "icons": [
    {
      "src": "/static/icon-192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "/static/icon-512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
```

**2. Create `frontend/public/service-worker.js`**
```javascript
const CACHE_NAME = 'mach-cache-v1';
const urlsToCache = [
  '/public/login.html',
  '/src/css/main.css',
  '/src/css/variables.css',
  '/src/css/base.css'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
});

self.addEventListener('fetch', event => {
  event.respondWith(caches.match(event.request).then(response => response || fetch(event.request)));
});
```

**3. Link them in the `<head>` of HTML files**
```html
<link rel="manifest" href="/public/manifest.json">
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/public/service-worker.js');
  }
</script>
```

---

## Phase 2: Advanced Markdown & Math Rendering

Academic syllabi often contain complex tables and mathematical formulas. We will replace our custom `simpleMarkdown` function with industry-standard renderers.

**1. Add CDN links to `<head>` of `student.html`**
```html
<!-- DOMPurify to prevent XSS attacks -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.6/purify.min.js"></script>
<!-- Marked.js for robust markdown parsing -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/11.1.0/marked.min.js"></script>
<!-- KaTeX for math rendering -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>
```

**2. Update `frontend/src/js/pages/student.js` Streaming Loop**
```javascript
// Inside the streaming loop
fullText += decoder.decode(value, { stream: true });

// 1. Convert markdown to HTML
let rawHtml = marked.parse(fullText);

// 2. Sanitize HTML to prevent XSS
let safeHtml = DOMPurify.sanitize(rawHtml);

// 3. Render any Math equations (assuming LaTeX delimiters like $$ math $$)
// Note: You would write a regex to replace $$ math $$ with katex.renderToString(math)
bubble.innerHTML = safeHtml;
```

---

## Phase 3: Custom Toast Notification System

Replacing standard browser `alert()` popups with smooth, animated toasts improves the SaaS feel of the app.

**1. Add CSS to `frontend/src/css/components.css`**
```css
.toast-container {
  position: fixed; bottom: 20px; right: 20px;
  display: flex; flex-direction: column; gap: 10px; z-index: 9999;
}
.toast {
  background: var(--bg-1); color: var(--text-1);
  padding: 12px 20px; border-radius: 8px; font-size: 13px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  border-left: 4px solid var(--brand);
  animation: slide-in 0.3s ease forwards;
}
@keyframes slide-in {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}
```

**2. Add JavaScript to `frontend/src/js/core/utils.js`**
```javascript
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
```

---

## Phase 4: Accessibility (a11y) Standards

Ensuring the platform is usable by all students, including those using screen readers or relying purely on keyboard navigation.

**1. Keyboard Navigation**
Ensure all interactive elements (buttons, inputs) in `admin.html` and `student.html` have `tabindex="0"` (most buttons do implicitly, but custom `<div>` buttons need it).

**2. ARIA Labels**
Update icon-only buttons so screen readers know what they do.
```html
<!-- Before -->
<button class="btn-send" onclick="window.sendMessage()">
  <svg>...</svg>
</button>

<!-- After -->
<button class="btn-send" onclick="window.sendMessage()" aria-label="Send Message" title="Send">
  <svg aria-hidden="true">...</svg>
</button>
```

**3. Focus States**
Update `base.css` so users can see exactly where they are tabbing on the screen.
```css
*:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
```
