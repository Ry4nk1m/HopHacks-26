// App entry point: boots the app, builds the HUD, wires up events, and drives the app lifecycle.

import { S, store, visibleFlags } from './state.js';
import { on, emit } from './bus.js';
import { api, token, ApiError } from './api.js';
import { el, icon, toast, closeSheet, fmtDist, cToF, setKids, TYPE_ICON, TYPE_COLOR, TYPE_GLYPH } from './ui.js';
import { t, flagTitle, codeMessage } from './i18n.js';
import { startLocation, getPos, haversine, setFake } from './geo.js';
import { initMap, pushFlags, pushUser, pushMission, setOverlay, flyTo, flyToUser, setSelected } from './mapview.js';
import { openFlagSheet, openMissionSheet, openReportSheet, restoreMission } from './flows.js';
import { openProfile, openAbout } from './profile.js';
import { initDrawer, openDrawer } from './drawer.js';
import { showOnboarding } from './onboarding.js';
import { stopVoice } from './voice.js';

const $ = (id) => document.getElementById(id);
const FILTER_IDS = ['all', 'tree', 'drain', 'cooling', 'reports'];
let hud = null;
let centered = false;
let pollTimer = null;
let overlayMenuOpen = false;
let liveStream = null;

// ------------------------------------------------------------------ HUD
// Build the heads-up display: profile pill, layer toggle, locate button, voice toggle, filter chips, and the report button.
function buildHud() {
  hud = {
    profile: el('button', { class: 'pill', onclick: openDrawer, 'aria-label': t('menu_open') }, icon('user', 18), el('span', { id: 'profileTxt' })),
    layers: el('button', { class: 'tg-btn', onclick: toggleOverlayMenu, 'aria-label': t('ov_title') }, icon('layers')),
    locate: el('button', { class: 'tg-btn', onclick: onLocate, 'aria-label': 'My location' }, icon('locate')),
    voice: el('button', { class: 'tg-btn', onclick: toggleVoice }),
    info: el('button', { class: 'tg-btn muted', onclick: () => openAbout(), 'aria-label': t('about_btn') }, icon('info', 20)),
    chips: el('div', { class: 'chips' }),
    conds: el('div', { class: 'conds' }),
    menu: el('div', { class: 'overlay-menu hidden' }),
    fab: el('button', { class: 'fab', onclick: () => openReportSheet(), 'aria-label': 'Report a problem' }, icon('camera', 26)),
    card: el('button', { class: 'bottom-card hidden' }),
    gps: el('div', { class: 'gps-banner hidden' }),
  };
  $('hud').replaceChildren(
    el('div', { class: 'hud-top' }, hud.profile), hud.chips, hud.conds, hud.menu,
    el('div', { class: 'toolgroup' }, hud.layers, hud.locate, hud.voice, hud.info),
    hud.fab, hud.card, hud.gps);
  renderVoiceButton();
}

const FILTER_TYPE = { tree: 'tree_water', drain: 'drain_clear', cooling: 'cooling_check', reports: 'problem_report' };

// Draw the filter chip row (all / tree / drain / cooling / reports).
function renderChips() {
  const buttons = FILTER_IDS.map((id) => {
    const on = S.filter === id;
    const type = FILTER_TYPE[id];
    const label = t(`f_${id}`);
    return el('button', {
      class: `chip${id === 'all' ? ' all' : on ? '' : ' icon-only'}`, 'aria-pressed': String(on), 'aria-label': label, title: label,
      onclick: () => { S.filter = id; renderChips(); pushFlags(); renderCard(); },
    }, id === 'all' ? label : [
      el('span', { class: 'chip-ic', style: { background: TYPE_COLOR[type], color: TYPE_GLYPH[type] } }, icon(TYPE_ICON[type], 14)),
      on ? label : null]);
  });
  setKids(hud.chips, el('div', { class: 'segbar', role: 'group', 'aria-label': t('f_all') }, buttons));
}

// Update the voice toggle button's icon and label to match the current voice setting.
function renderVoiceButton() {
  setKids(hud.voice, icon(S.voice ? 'volume' : 'volume-x', 21));
  hud.voice.setAttribute('aria-label', S.voice ? t('voice_on') : t('voice_off'));
  hud.voice.setAttribute('aria-pressed', String(S.voice));
  hud.voice.classList.toggle('muted', !S.voice);
}

// Turn voice guidance on or off and save the choice.
function toggleVoice() {
  S.voice = !S.voice;
  store.set('nm_voice', S.voice ? '1' : '0');
  if (!S.voice) stopVoice();
  renderVoiceButton();
  toast(S.voice ? t('voice_on') : t('voice_off'), { ms: 1600 });
}

// Draw the condition pills (heat, rain, dry spell, demo teleport) above the map.
function renderConds() {
  const c = S.conditions;
  const pills = [];
  if (c) {
    const demo = (name) => (c.forced && c.forced[name] === 'on' ? ` (${t('cond_demo')})` : '');
    if (c.heat) {
      // only call it an alert when the National Weather Service actually issued one; otherwise it is our own forecast trigger
      const official = (c.events || []).find((e) => /heat/i.test(e));
      const peak = c.metrics && c.metrics.max_apparent_next3_c;
      const label = official || (peak != null ? t('cond_heat_peak', { f: cToF(peak) }) : t('cond_heat'));
      pills.push(el('span', { class: `cond${official ? ' official' : ''}` }, label + demo('heat')));
    }
    if (c.rain) pills.push(el('span', { class: 'cond rain' }, t('cond_rain') + demo('rain')));
    if (c.dry) pills.push(el('span', { class: 'cond dry' }, t('cond_dry') + demo('dry')));
  }
  if (S.fake) pills.push(el('span', { class: 'cond demo' }, t('teleported')));
  hud.conds.replaceChildren(...pills);
}

// Update the profile button's text with the user's nickname, points, and streak.
function renderProfilePill() {
  const u = S.user;
  setKids($('profileTxt'), el('b', {}, u ? u.nickname : ''), u ? ` · ${t('pts', { n: u.points })}` : '',
    u && u.streak > 1 ? el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '2px', marginLeft: '4px' } }, icon('flame', 14), `${u.streak}`) : null);
}

// Find the closest open, unclaimed flag to the user (or the highest priority one if location is unknown).
function nearestFlag() {
  const p = getPos();
  const open = visibleFlags().filter((f) => f.status === 'open' && !f.claimed && !(f.mine && f.purpose === 'confirm'));
  if (!open.length) return null;
  if (!p) return { flag: [...open].sort((a, b) => b.priority - a.priority)[0], dist: null };
  const best = open.map((f) => ({ flag: f, dist: haversine(p.lat, p.lon, f.lat, f.lon) })).sort((a, b) => a.dist - b.dist)[0];
  return best;
}

// Draw the bottom card: the active mission if there is one, otherwise the nearest flag.
function renderCard() {
  const card = hud.card;
  card.className = 'bottom-card';
  if (S.mission) {
    const f = S.flags.find((x) => x.id === S.mission.flag.id) || S.mission.flag;
    const p = getPos();
    card.classList.add('mission');
    card.onclick = () => openMissionSheet();
    card.replaceChildren(el('div', { class: 'type-dot', style: { background: TYPE_COLOR[f.type], color: TYPE_GLYPH[f.type], width: '38px', height: '38px' } }, icon(TYPE_ICON[f.type], 20)),
      el('div', {}, el('div', { class: 't' }, flagTitle(f)), el('div', { class: 's' }, `${t('mission_active')} · ${p ? fmtDist(haversine(p.lat, p.lon, f.lat, f.lon)) : t('m_gps_waiting')}`)));
    return;
  }
  const n = nearestFlag();
  if (!n) { card.classList.add('hidden'); return; }
  const f = n.flag;
  card.onclick = () => { flyTo(f.lat, f.lon, 18); openFlagSheet(f.id); };
  card.replaceChildren(el('div', { class: 'type-dot', style: { background: TYPE_COLOR[f.type], color: TYPE_GLYPH[f.type], width: '38px', height: '38px' } }, icon(TYPE_ICON[f.type], 20)),
    el('div', {}, el('div', { class: 't' }, flagTitle(f)), el('div', { class: 's' }, `${t('nearest')}${n.dist != null ? ` · ${fmtDist(n.dist)}` : ''} · ${t('pts', { n: f.reward })}`)));
}

// Check whether a position is inside the app's covered area (bounding box).
function inAoi(p) {
  const [w, s, e, n] = S.cfg.aoi;
  return p.lat >= s && p.lat <= n && p.lon >= w && p.lon <= e;
}

// A "use demo location" button, shown only when dev tools are enabled.
function demoButton() {
  if (!S.cfg.dev_tools) return null;
  return el('button', { onclick: () => { const [lat, lon] = S.cfg.center; setFake(lat, lon); flyTo(lat, lon, 16.5); } }, t('use_demo'));
}

// Show a banner explaining GPS state (off, denied, waiting, outside area, weak signal), if any.
function renderGps() {
  const b = hud.gps, p = getPos();
  let msg = null, button = null;
  if (S.fake) msg = null;
  else if (S.locState === 'off') { msg = t('gps_off'); button = el('button', { onclick: enableLocation }, t('ob_loc_enable')); }
  else if (S.locState === 'denied' || S.locState === 'unavailable') { msg = t('gps_denied'); button = demoButton(); }
  else if (!p) { msg = t('gps_waiting'); button = demoButton(); }
  else if (!inAoi(p)) { msg = t('gps_outside'); button = demoButton(); }
  else if ((p.acc || 0) > 80) msg = t('gps_weak', { a: fmtDist(p.acc) });
  if (!msg) { b.classList.add('hidden'); return; }
  b.classList.remove('hidden');
  setKids(b, el('span', {}, msg), button);
}

// Redraw all HUD pieces.
function renderHud() {
  if (!hud) return;
  renderProfilePill(); renderChips(); renderConds(); renderCard(); renderGps(); renderVoiceButton();
  if (overlayMenuOpen) renderOverlayMenu();
}

// ------------------------------------------------------------------ overlay menu
// Caption text for a satellite overlay, describing which dates its imagery is from.
function overlayNote(id) {
  const L = S.cfg.layers;
  if (id === 'ndvi' && L.ndvi) return t('ov_note_ndvi', { dates: L.ndvi.scenes.map((s) => s.date.slice(5)).join(', ') });
  if (id === 'lst' && L.lst) return t('ov_note_lst', { dates: L.lst.scenes.map((s) => s.date.slice(5)).join(', ') });
  return '';
}

// Draw the satellite layer picker menu.
function renderOverlayMenu() {
  const opts = [['off', 'ov_off'], ['ndvi', 'ov_ndvi'], ['lst', 'ov_lst']];
  setKids(hud.menu,
    ...opts.map(([id, key]) => el('button', { 'aria-pressed': String(S.overlay === id), onclick: () => { setOverlay(id); renderOverlayMenu(); } }, t(key))),
    S.overlay === 'ndvi' ? [el('div', { class: 'ramp ndvi' }), el('div', { class: 'ramp-labels' }, el('span', {}, t('ov_low_green')), el('span', {}, t('ov_high_green')))] : null,
    S.overlay === 'lst' ? [el('div', { class: 'ramp lst' }), el('div', { class: 'ramp-labels' }, el('span', {}, t('ov_cool')), el('span', {}, t('ov_hot')))] : null,
    S.overlay !== 'off' ? el('div', { class: 'note' }, overlayNote(S.overlay)) : null);
}

// Show or hide the satellite layer picker menu.
function toggleOverlayMenu() {
  overlayMenuOpen = !overlayMenuOpen;
  hud.menu.classList.toggle('hidden', !overlayMenuOpen);
  hud.layers.classList.toggle('active', overlayMenuOpen);
  if (overlayMenuOpen) renderOverlayMenu();
}

// Remember that location is turned on, and start watching GPS.
function enableLocation() {
  store.set('nm_loc', 'on');
  startLocation();
}

// Handle tapping the locate button: enable location if needed, or fly to the user (or the default center).
function onLocate() {
  if (!S.fake && ['off', 'denied', 'unavailable'].includes(S.locState)) { enableLocation(); return; }
  const p = getPos();
  if (p) flyToUser();
  else { const [lat, lon] = S.cfg.center; flyTo(lat, lon, 16); }
}

// ------------------------------------------------------------------ data
// Fetch the current flags and conditions from the server and refresh the map and HUD.
async function loadFlags() {
  try {
    const r = await api('/api/flags');
    S.flags = r.flags;
    S.conditions = r.conditions;
    if (S.mission) {
      const fresh = S.flags.find((f) => f.id === S.mission.flag.id);
      if (fresh) S.mission.flag = fresh;
    }
    pushFlags();
    renderHud();
  } catch (e) { /* keep the last known flags */ }
}

// Fetch the current user profile and update the HUD.
async function loadUser() {
  try { S.user = await api('/api/me'); renderProfilePill(); } catch (e) { /* handled by auth:lost */ }
}

// If the user has an active mission from before, restore it into the app state.
async function restoreActiveMission() {
  const active = (S.user && S.user.active_missions) || [];
  if (!active.length) return;
  const m = active[0];
  let flag = S.flags.find((f) => f.id === m.flag_id);
  if (!flag) { try { flag = await api(`/api/flags/${m.flag_id}`); } catch (e) { return; } }
  restoreMission(m, flag);
}

// Open a live connection to the server so flag updates from other users (or a mission of our own
// finishing) show up immediately, instead of waiting for the next poll. The browser reconnects
// this on its own if it drops; the 45s poll below stays as a fallback either way.
function startLiveUpdates() {
  if (liveStream || typeof EventSource === 'undefined') return;
  liveStream = new EventSource('/api/stream');
  liveStream.onmessage = () => { if (!document.hidden && S.user) loadFlags(); };
}

// ------------------------------------------------------------------ app lifecycle
// Enter the main app after login/onboarding: start location, load data, and begin polling for updates.
async function enterApp() {
  paintAppChrome();
  renderHud();
  if (store.get('nm_loc') !== 'off') startLocation();
  else { S.locState = 'off'; emit('pos'); }
  await loadFlags();
  await restoreActiveMission();
  renderHud();
  if (S.cfg.validator === 'mock') toast(t('demo_banner'), { ms: 5000 });
  startLiveUpdates();
  if (!pollTimer) pollTimer = setInterval(() => { if (!document.hidden && S.user) loadFlags(); }, 45000);
}

// Wire up all the app-wide event bus listeners.
function wireEvents() {
  on('pos', () => {
    pushUser(); pushMission();
    const p = getPos();
    if (p && !centered && (S.fake || inAoi(p))) { centered = true; flyTo(p.lat, p.lon, 17); }
    if (hud) { renderGps(); renderCard(); }
  });
  on('flag:select', (id) => openFlagSheet(id));
  on('flag:highlight', (id) => setSelected(id));
  on('flag:focus', async (id) => { await loadFlags(); const f = S.flags.find((x) => x.id === id); if (f) { flyTo(f.lat, f.lon, 18); openFlagSheet(id); } });
  on('flags:refresh', () => loadFlags());
  on('user:refresh', () => loadUser());
  on('mission:changed', () => { pushMission(); if (hud) renderCard(); });
  on('map:center', ({ lat, lon }) => flyTo(lat, lon, 18));
  on('map:longpress', ({ lat, lon }) => { setFake(lat, lon); toast(t('teleported')); });
  on('map:fallback', () => toast('Map tiles unavailable, switching to the backup map.', { ms: 5000 }));
  on('auth:lost', () => { S.user = null; S.mission = null; closeSheet(); showOnboarding(enterApp); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S.user) loadFlags(); });
}

// Start the app and always hide the boot splash screen afterward.
async function boot() {
  let ok = false;
  try { ok = await startup(); } finally { if (ok !== false) hideBoot(); }
}

// Load config, set up the HUD/map/drawer, check for a logged-in user, then enter the app or show onboarding.
async function startup() {
  S.voice = store.get('nm_voice') !== '0';
  try {
    S.cfg = await api('/api/config', { auth: false });
  } catch (e) {
    $('bootMsg').textContent = t('err_network');
    return false;
  }
  document.title = S.cfg.app_name;
  buildHud();
  initDrawer($('app'));
  window.addEventListener('nm:voice', renderVoiceButton);
  wireEvents();
  initMap(S.cfg);
  if (token.get()) { try { S.user = await api('/api/me'); } catch (e) { S.user = null; } }
  if (S.user) await enterApp(); else showOnboarding(enterApp);
}

// Set the page background and theme color to match the app (as opposed to the boot splash).
function paintAppChrome() {
  document.documentElement.style.background = '#0f2227';
  document.body.style.background = '#0f2227';
  const theme = document.querySelector('meta[name="theme-color"]');
  if (theme) theme.setAttribute('content', '#152e34');
}

const SPLASH_MS = 2300;

// Hide the boot splash screen, waiting for a minimum display time and a fade-out transition.
function hideBoot() {
  const b = $('boot');
  if (!b) return;
  const wait = SPLASH_MS - performance.now();
  if (wait > 0) { setTimeout(hideBoot, wait); return; }
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    b.remove();
    if (S.user) paintAppChrome();
  };
  b.classList.add('out');
  b.addEventListener('transitionend', finish, { once: true });
  setTimeout(finish, 500);
}

// last resort: the splash must never outlive a slow or failed startup
setTimeout(() => { const b = $('boot'); if (b && $('bootMsg').textContent === 'Loading map…') b.remove(); }, 8000);

boot();
