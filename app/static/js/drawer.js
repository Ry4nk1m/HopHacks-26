// The side drawer menu: profile, settings, and demo tools. Can be opened by swiping from the screen edge.

import { S, store } from './state.js';
import { el, icon, closeSheet, toast } from './ui.js';
import { t } from './i18n.js';
import { openProfile, openAbout, demoTools, weatherTools } from './profile.js';
import { api } from './api.js';
import { stopVoice } from './voice.js';

const EDGE_PX = 22;
const OPEN_DIST = 60;
let root, panel, scrim, edge, isOpen = false;

// Move the drawer panel to a given horizontal position, with or without animation.
function setPanelX(px, animate) {
  panel.style.transition = animate ? '' : 'none';
  panel.style.transform = `translateX(${px}px)`;
}

const width = () => panel.getBoundingClientRect().width || 300;

// Follow a pointer drag, moving the panel and scrim along with it, until the drag ends.
function track(startX, from, onEnd) {
  const w = width();
  const move = (e) => { setPanelX(Math.max(-w, Math.min(0, from + (e.clientX - startX))), false); scrim.style.opacity = String(1 + Math.max(-w, Math.min(0, from + (e.clientX - startX))) / w); };
  const up = (e) => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
    onEnd(e.clientX - startX);
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
}

// Open the drawer panel.
export function openDrawer() {
  if (isOpen) return;
  render();
  isOpen = true;
  root.classList.add('open');
  scrim.style.opacity = '';
  setPanelX(0, true);
}

// Close the drawer panel.
export function closeDrawer() {
  if (!isOpen) return;
  isOpen = false;
  root.classList.remove('open');
  scrim.style.opacity = '';
  setPanelX(-width() - 20, true);
}

// Build one clickable row in the drawer, with an icon, title and optional subtitle.
function row(ic, title, sub, onclick) {
  return el('button', { class: 'dr-row', onclick }, el('span', { class: 'dr-ic' }, icon(ic, 19)),
    el('span', { class: 'dr-txt' }, el('b', {}, title), sub ? el('small', {}, sub) : null));
}

// Draw the full contents of the drawer: profile header, settings, mission blurb, and demo tools.
// Five quick taps on the footer ask for the test key, which unlocks the weather test tools on a live site.
let taps = 0, tapTimer = null;
async function footTap() {
  if (S.cfg.dev_tools || !S.cfg.test_unlock || store.get('nm_test')) return;
  taps += 1;
  clearTimeout(tapTimer);
  tapTimer = setTimeout(() => { taps = 0; }, 1500);
  if (taps < 5) return;
  taps = 0;
  const key = (window.prompt(t('test_key_ask')) || '').trim();
  if (!key) return;
  store.set('nm_test', key);
  try {
    await api('/api/dev/state');
    toast(t('test_unlocked'));
  } catch (e) {
    store.del('nm_test');
    toast(t('test_key_bad'));
  }
  render();
}

function render() {
  const u = S.user;
  const voiceSwitch = el('button', { class: 'dr-row', role: 'switch', 'aria-checked': String(S.voice), onclick: () => {
    S.voice = !S.voice;
    store.set('nm_voice', S.voice ? '1' : '0');
    if (!S.voice) stopVoice();
    render();
    window.dispatchEvent(new Event('nm:voice'));
  } }, el('span', { class: 'dr-ic' }, icon(S.voice ? 'volume' : 'volume-x', 19)),
    el('span', { class: 'dr-txt' }, el('b', {}, t('prof_voice'))),
    el('span', { class: 'switch', role: 'presentation', 'aria-checked': String(S.voice) }, el('span')));
  const after = (fn) => () => { closeDrawer(); setTimeout(fn, 180); };

  const kids = [
    el('div', { class: 'dr-head' },
      el('div', { class: 'dr-avatar' }, (u ? u.nickname[0] : '?').toUpperCase()),
      el('div', {}, el('h2', {}, u ? u.nickname : ''), u ? el('div', { class: 'dr-stats' }, `${u.points} ${t('prof_points').toLowerCase()} · ${u.streak} ${t('prof_streak').toLowerCase()}`) : null),
      el('button', { class: 'dr-x', onclick: closeDrawer, 'aria-label': t('menu_close') }, icon('x', 20))),
    row('user', t('menu_profile'), t('menu_profile_sub'), after(openProfile)),
    el('h3', {}, t('prof_settings')),
    voiceSwitch,
    el('p', { class: 'dr-note' }, S.cfg.validator === 'mock' ? t('prof_validator_mock') : t('prof_validator_gemini')),
    row('info', t('menu_about'), null, after(openAbout)),
    el('h3', {}, t('mission_h')),
    el('p', { class: 'dr-mission' }, t('mission_body')),
  ];
  const unlocked = !S.cfg.dev_tools && !!store.get('nm_test');
  if (unlocked) {
    kids.push(el('div', { class: 'dr-demo' }, ...weatherTools(() => { if (isOpen) render(); }),
      el('div', { class: 'actions' }, el('button', { class: 'btn', onclick: () => { store.del('nm_test'); render(); } }, t('test_lock')))));
  }
  kids.push(el('p', { class: 'dr-foot', onclick: footTap }, `${S.cfg.app_name} · ${t('by_club', { site: S.cfg.site_name })}`));
  if (S.cfg.dev_tools) kids.push(el('div', { class: 'dr-demo' }, ...demoTools(() => { if (isOpen) render(); }, () => { closeDrawer(); closeSheet(); })));
  panel.replaceChildren(...kids);
}

// Set up the drawer: create its DOM, and wire up edge-swipe-to-open and drag-to-close gestures.
export function initDrawer(host) {
  scrim = el('div', { class: 'dr-scrim', onclick: closeDrawer });
  panel = el('aside', { class: 'dr-panel', role: 'dialog', 'aria-label': t('menu_open') });
  edge = el('div', { class: 'dr-edge' }, el('i'));
  root = el('div', { id: 'drawer' }, scrim, panel);
  host.append(edge, root);
  setPanelX(-340, false);

  edge.addEventListener('pointerdown', (e) => {
    if (isOpen) return;
    e.preventDefault();
    render();
    root.classList.add('open', 'dragging');
    const w = width();
    const startX = e.clientX;
    setPanelX(-w, false);
    scrim.style.opacity = '0';
    track(startX - EDGE_PX / 2, -w, (dx) => {
      root.classList.remove('dragging');
      if (dx > OPEN_DIST || dx < 4) { isOpen = false; openDrawer(); } else { root.classList.remove('open'); setPanelX(-w - 20, true); scrim.style.opacity = ''; }
    });
  });
  panel.addEventListener('pointerdown', (e) => {
    if (!isOpen || e.target.closest('button, input')) return;
    const w = width();
    track(e.clientX, 0, (dx) => { if (dx < -OPEN_DIST) closeDrawer(); else { setPanelX(0, true); scrim.style.opacity = ''; } });
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
}
