/**
 * M.A.C.H. AI — Core API Transport Layer (Multimodal Academic Cognitive Hub)
 * Injects cryptographic Bearer tokens and handles unauthorized redirection.
 */

// Determine API base URL dynamically based on environment.
export const API_BASE = (
  window.location.protocol !== 'file:' &&
  (window.location.port === '8000' || window.location.hostname !== 'localhost' || window.location.pathname.startsWith('/public'))
) ? '' : 'http://localhost:8000';

import { getToken, logout } from './auth.js';

/**
 * Custom fetch wrapper to automatically handle Authorization headers and 401 unauth states.
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
