// Handles talking to the backend API from the browser.
// Adds the auth token to requests and turns failed responses into ApiError objects.

import { emit } from './bus.js';
import { store } from './state.js';

// Error thrown when an API call fails. Carries the error code, HTTP status and any extra data.
export class ApiError extends Error {
  constructor(code, status, data) {
    super(code);
    this.code = code;
    this.status = status;
    this.data = data || {};
  }
}

// Reads and writes the saved auth token in local storage.
export const token = {
  get: () => store.get('nm_token'),
  set: (v) => store.set('nm_token', v),
  clear: () => store.del('nm_token'),
};

// Make an API request. Sends JSON or form data, attaches the auth token, and returns parsed JSON.
// Throws an ApiError on network failure or a non-ok response.
export async function api(path, { method = 'GET', json, form, auth = true } = {}) {
  const headers = {};
  const t = token.get();
  if (auth && t) headers.Authorization = `Bearer ${t}`;
  if (path.startsWith('/api/dev/')) { const k = store.get('nm_test'); if (k) headers['X-Admin-Key'] = k; }
  let body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  else if (form) body = form;
  let res;
  try {
    res = await fetch(path, { method, headers, body });
  } catch (e) {
    throw new ApiError('network', 0);
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty or non-JSON body */ }
  if (!res.ok) {
    const code = (data && (data.code || (data.detail && data.detail.code))) || (res.status === 413 ? 'too_large' : 'generic');
    // if the token was rejected, clear it and tell the rest of the app the user is logged out
    if (res.status === 401 && auth && t) { token.clear(); emit('auth:lost'); }
    throw new ApiError(code, res.status, data);
  }
  return data;
}
