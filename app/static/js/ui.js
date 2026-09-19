import { S } from './state.js';
import { t } from './i18n.js';

export function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v);
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, '');
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

export function setKids(node, ...kids) {
  node.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (k.nodeType ? k : document.createTextNode(String(k)))));
}

const ICONS = {
  x: ['M18 6L6 18M6 6l12 12'], check: ['M20 6L9 17l-5-5'], plus: ['M12 5v14M5 12h14'],
  'chevron-down': ['M6 9l6 6 6-6'], 'chevron-right': ['M9 18l6-6-6-6'],
  layers: ['M12 2L2 7l10 5 10-5-10-5z', 'M2 17l10 5 10-5', 'M2 12l10 5 10-5'],
  locate: ['M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14z', 'M12 1v4M12 19v4M1 12h4M19 12h4'],
  camera: ['M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z', 'M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'],
  user: ['M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2', 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'],
  navigation: ['M3 11l19-9-9 19-2-8-8-2z'],
  alert: ['M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z', 'M12 9v4', 'M12 17h.01'],
  volume: ['M11 5L6 9H2v6h4l5 4V5z', 'M15.54 8.46a5 5 0 0 1 0 7.07', 'M19.07 4.93a10 10 0 0 1 0 14.14'],
  'volume-x': ['M11 5L6 9H2v6h4l5 4V5z', 'M23 9l-6 6M17 9l6 6'],
  award: ['M12 15a7 7 0 1 0 0-14 7 7 0 0 0 0 14z', 'M8.21 13.89L7 23l5-3 5 3-1.21-9.12'],
  flame: ['M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z'],
  refresh: ['M23 4v6h-6', 'M1 20v-6h6', 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'],
  info: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z', 'M12 16v-4M12 8h.01'],
  lock: ['M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z', 'M7 11V7a5 5 0 0 1 10 0v4'],
  tree: ['M12 3a6 6 0 1 0 0 12A6 6 0 0 0 12 3z', 'M12 15v7'],
  drain: ['M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z'],
  snow: ['M12 2v20', 'M3.3 7l17.4 10', 'M3.3 17L20.7 7'],
  waves: ['M2 8c2-2 4-2 6 0s4 2 6 0 4-2 6 0', 'M2 14c2-2 4-2 6 0s4 2 6 0 4-2 6 0'],
  bang: ['M12 4v10', 'M12 19h.01'],
  pin: ['M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z', 'M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
  share: ['M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8', 'M16 6l-4-4-4 4', 'M12 2v13'],
  shield: ['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'],
  eye: ['M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
  globe: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z', 'M2 12h20', 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'],
};
export const TYPE_ICON = { tree_water: 'tree', drain_clear: 'drain', cooling_check: 'snow', flood_report: 'waves', problem_report: 'bang' };
export const TYPE_COLOR = { tree_water: '#234831', drain_clear: '#2a7b8a', cooling_check: '#eba745', flood_report: '#152e34', problem_report: '#e46a46' };
// amber needs a dark glyph to stay readable; the others use white
export const TYPE_GLYPH = { tree_water: '#ffffff', drain_clear: '#ffffff', cooling_check: '#152e34', flood_report: '#ffffff', problem_report: '#ffffff' };

export function icon(name, size) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size || 22); svg.setAttribute('height', size || 22);
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2'); svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  (ICONS[name] || ICONS.pin).forEach((d) => { const p = document.createElementNS(ns, 'path'); p.setAttribute('d', d); svg.append(p); });
  return svg;
}

export function toast(message, opts = {}) {
  const node = el('div', { class: 'toast', role: 'status' },
    el('span', {}, opts.spinner ? el('span', { class: 'spin-inline' }) : null, message),
    opts.action ? el('button', { onclick: () => { node.remove(); opts.action.fn(); } }, opts.action.label) : null);
  document.getElementById('toasts').append(node);
  if (opts.ms !== 0) setTimeout(() => node.remove(), opts.ms || 3500);
  return node;
}

// ---------- bottom sheet (one at a time)
let onSheetClose = null;

export function openSheet(...content) {
  const opts = content[content.length - 1] && content[content.length - 1].__opts ? content.pop() : {};
  const wrap = document.getElementById('sheetWrap');
  const sheet = el('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' }, el('div', { class: 'grab' }), content);
  wrap.replaceChildren(sheet);
  wrap.classList.add('open');
  onSheetClose = opts.onClose || null;
  wrap.onclick = (e) => { if (e.target === wrap && opts.dismissible !== false) closeSheet(); };
  return sheet;
}

export const sheetOpts = (o) => Object.assign(Object.create(null), { __opts: true }, o);

export function closeSheet() {
  const wrap = document.getElementById('sheetWrap');
  wrap.classList.remove('open');
  const cb = onSheetClose;
  onSheetClose = null;
  if (cb) cb();
}

export function sheetIsOpen() { return document.getElementById('sheetWrap').classList.contains('open'); }

// ---------- formatting
export function fmtDist(m) {
  if (m == null || Number.isNaN(m)) return '—';
  const ft = m * 3.28084;
  if (ft < 1000) return `${Math.max(Math.round(ft / 10) * 10, 10)} ft`;
  return `${(ft / 5280).toFixed(1)} mi`;
}

export const cToF = (c) => Math.round(c * 9 / 5 + 32);

export function timeAgo(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 90) return t('just_now');
  if (s < 3600) return t('min_ago', { n: Math.round(s / 60) });
  if (s < 86400) return t('hours_ago', { n: Math.round(s / 3600) });
  return t('days_ago', { n: Math.round(s / 86400) });
}

export function dateShort(iso) {
  if (!iso) return '';
  return new Date(iso.length === 10 ? iso + 'T12:00:00' : iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
