import { emit } from './bus.js';
import { store } from './state.js';

export class ApiError extends Error {
  constructor(code, status, data) {
    super(code);
    this.code = code;
    this.status = status;
    this.data = data || {};
  }
}

export const token = {
  get: () => store.get('nm_token'),
  set: (v) => store.set('nm_token', v),
  clear: () => store.del('nm_token'),
};

export async function api(path, { method = 'GET', json, form, auth = true } = {}) {
  const headers = {};
  const t = token.get();
  if (auth && t) headers.Authorization = `Bearer ${t}`;
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
    if (res.status === 401 && auth && t) { token.clear(); emit('auth:lost'); }
    throw new ApiError(code, res.status, data);
  }
  return data;
}
