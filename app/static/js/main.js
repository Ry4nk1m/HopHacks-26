import { S, store, visibleFlags } from './state.js';
import { on, emit } from './bus.js';
import { api, token, ApiError } from './api.js';
import { el, icon, toast, closeSheet, fmtDist, setKids, TYPE_ICON, TYPE_COLOR, TYPE_GLYPH } from './ui.js';
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

// ------------------------------------------------------------------ HUD
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

function renderVoiceButton() {
  setKids(hud.voice, icon(S.voice ? 'volume' : 'volume-x', 21));
  hud.voice.setAttribute('aria-label', S.voice ? t('voice_on') : t('voice_off'));
  hud.voice.setAttribute('aria-pressed', String(S.voice));
  hud.voice.classList.toggle('muted', !S.voice);
}

function toggleVoice() {
  S.voice = !S.voice;
  store.set('nm_voice', S.voice ? '1' : '0');
  if (!S.voice) stopVoice();
  renderVoiceButton();
  toast(S.voice ? t('voice_on') : t('voice_off'), { ms: 1600 });
}

function renderConds() {
  const c = S.conditions;
  const pills = [];
  if (c) {
    const demo = (name) => (c.forced && c.forced[name] === 'on' ? ` (${t('cond_demo')})` : '');
    if (c.heat) pills.push(el('span', { class: 'cond' }, t('cond_heat') + demo('heat')));
    if (c.rain) pills.push(el('span', { class: 'cond rain' }, t('cond_rain') + demo('rain')));
    if (c.dry) pills.push(el('span', { class: 'cond dry' }, t('cond_dry') + demo('dry')));
  }
  if (S.fake) pills.push(el('span', { class: 'cond demo' }, t('teleported')));
  hud.conds.replaceChildren(...pills);
}

function renderProfilePill() {
  const u = S.user;
  setKids($('profileTxt'), el('b', {}, u ? u.nickname : ''), u ? ` · ${t('pts', { n: u.points })}` : '',
    u && u.streak > 1 ? el('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '2px', marginLeft: '4px' } }, icon('flame', 14), `${u.streak}`) : null);
}

function nearestFlag() {
  const p = getPos();
  const open = visibleFlags().filter((f) => f.status === 'open' && !f.claimed && !(f.mine && f.purpose === 'confirm'));
  if (!open.length) return null;
  if (!p) return { flag: [...open].sort((a, b) => b.priority - a.priority)[0], dist: null };
  const best = open.map((f) => ({ flag: f, dist: haversine(p.lat, p.lon, f.lat, f.lon) })).sort((a, b) => a.dist - b.dist)[0];
  return best;
}

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

function inAoi(p) {
  const [w, s, e, n] = S.cfg.aoi;
  return p.lat >= s && p.lat <= n && p.lon >= w && p.lon <= e;
}

function demoButton() {
  if (!S.cfg.dev_tools) return null;
  return el('button', { onclick: () => { const [lat, lon] = S.cfg.center; setFake(lat, lon); flyTo(lat, lon, 16.5); } }, t('use_demo'));
}

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

function renderHud() {
  if (!hud) return;
  renderProfilePill(); renderChips(); renderConds(); renderCard(); renderGps(); renderVoiceButton();
  if (overlayMenuOpen) renderOverlayMenu();
}

// ------------------------------------------------------------------ overlay menu
function overlayNote(id) {
  const L = S.cfg.layers;
  if (id === 'ndvi' && L.ndvi) return t('ov_note_ndvi', { dates: L.ndvi.scenes.map((s) => s.date.slice(5)).join(', ') });
  if (id === 'lst' && L.lst) return t('ov_note_lst', { dates: L.lst.scenes.map((s) => s.date.slice(5)).join(', ') });
  if (id === 'nasa') return t('ov_note_nasa');
  return '';
}

function renderOverlayMenu() {
  const opts = [['off', 'ov_off'], ['ndvi', 'ov_ndvi'], ['lst', 'ov_lst'], ['nasa', 'ov_nasa']];
  setKids(hud.menu,
    ...opts.map(([id, key]) => el('button', { 'aria-pressed': String(S.overlay === id), onclick: () => { setOverlay(id); renderOverlayMenu(); } }, t(key))),
    S.overlay === 'ndvi' ? [el('div', { class: 'ramp ndvi' }), el('div', { class: 'ramp-labels' }, el('span', {}, t('ov_low_green')), el('span', {}, t('ov_high_green')))] : null,
    S.overlay === 'lst' ? [el('div', { class: 'ramp lst' }), el('div', { class: 'ramp-labels' }, el('span', {}, t('ov_cool')), el('span', {}, t('ov_hot')))] : null,
    S.overlay !== 'off' ? el('div', { class: 'note' }, overlayNote(S.overlay)) : null);
}

function toggleOverlayMenu() {
  overlayMenuOpen = !overlayMenuOpen;
  hud.menu.classList.toggle('hidden', !overlayMenuOpen);
  hud.layers.classList.toggle('active', overlayMenuOpen);
  if (overlayMenuOpen) renderOverlayMenu();
}

function enableLocation() {
  store.set('nm_loc', 'on');
  startLocation();
}

function onLocate() {
  if (!S.fake && ['off', 'denied', 'unavailable'].includes(S.locState)) { enableLocation(); return; }
  const p = getPos();
  if (p) flyToUser();
  else { const [lat, lon] = S.cfg.center; flyTo(lat, lon, 16); }
}

// ------------------------------------------------------------------ data
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

async function loadUser() {
  try { S.user = await api('/api/me'); renderProfilePill(); } catch (e) { /* handled by auth:lost */ }
}

async function restoreActiveMission() {
  const active = (S.user && S.user.active_missions) || [];
  if (!active.length) return;
  const m = active[0];
  let flag = S.flags.find((f) => f.id === m.flag_id);
  if (!flag) { try { flag = await api(`/api/flags/${m.flag_id}`); } catch (e) { return; } }
  restoreMission(m, flag);
}

// ------------------------------------------------------------------ app lifecycle
async function enterApp() {
  paintAppChrome();
  renderHud();
  if (store.get('nm_loc') !== 'off') startLocation();
  else { S.locState = 'off'; emit('pos'); }
  await loadFlags();
  await restoreActiveMission();
  renderHud();
  if (S.cfg.validator === 'mock') toast(t('demo_banner'), { ms: 5000 });
  if (!pollTimer) pollTimer = setInterval(() => { if (!document.hidden && S.user) loadFlags(); }, 45000);
}

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

async function boot() {
  S.voice = store.get('nm_voice') !== '0';
  try {
    S.cfg = await api('/api/config', { auth: false });
  } catch (e) {
    $('bootMsg').textContent = t('err_network');
    return;
  }
  document.title = S.cfg.app_name;
  buildHud();
  initDrawer($('app'));
  window.addEventListener('nm:voice', renderVoiceButton);
  wireEvents();
  initMap(S.cfg);
  if (token.get()) { try { S.user = await api('/api/me'); } catch (e) { S.user = null; } }
  if (S.user) await enterApp(); else showOnboarding(enterApp);
  hideBoot();
}

function paintAppChrome() {
  document.documentElement.style.background = '#0f2227';
  document.body.style.background = '#0f2227';
  const theme = document.querySelector('meta[name="theme-color"]');
  if (theme) theme.setAttribute('content', '#152e34');
}

const SPLASH_MS = 3100;

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
  setTimeout(finish, 700);
}

boot();
