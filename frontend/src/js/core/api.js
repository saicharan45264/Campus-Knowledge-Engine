/**
 * 🌐 CORE API LAYER
 * Handles all backend communication for CurriculumLens.
 * Designed to act as an interceptor for injecting auth tokens natively without external libraries.
 */

// When the app is served directly from FastAPI (/public/...), we're on the same origin,
// so we use relative paths (API_BASE = ''). This avoids CORS entirely.
// When opened via a local static dev server (e.g. python -m http.server 8080), fall back
// to the explicit backend URL.
export const API_BASE = (
  window.location.protocol !== 'file:' &&
  (window.location.port === '8000' || window.location.hostname !== 'localhost' || window.location.pathname.startsWith('/public'))
) ? '' : 'http://localhost:8000';

import { getToken, logout } from './auth.js';

/**
 * Custom fetch wrapper to automatically handle Authorization headers and 401 unauth states.
 * Keeps our component code clean and DRY.
 * 
 * @param {string} endpoint - The API endpoint relative to API_BASE
 * @param {object} options - Standard fetch options (method, headers, body)
 * @returns {Promise<Response>} 
 */
export async function apiFetch(endpoint, options = {}) {
  const token = getToken();
  const headers = {
    ...options.headers,
  };
  
  // Only add Content-Type if it's not FormData
  if (!(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (res.status === 401) {
    logout();
    throw new Error("Unauthorized");
  }

  return res;
}
