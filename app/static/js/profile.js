// Profile, about, and demo tools sheets: shows points/badges/leaderboard and dev-only test controls.

import { S } from './state.js';
import { emit } from './bus.js';
import { api } from './api.js';
import { el, icon, toast, openSheet, closeSheet, sheetOpts, fmtDist } from './ui.js';
import { t, flagTitle, codeMessage } from './i18n.js';
import { getPos, setFake, clearFake, haversine, DEMO_SPOT } from './geo.js';
import { stopVoice } from './voice.js';

let boardTab = 'weekly';
const short = (s) => (s.length > 28 ? `${s.slice(0, 27)}…` : s);

// Fetch the leaderboard and neighborhood impact stats, ignoring either if it fails.
async function loadExtras() {
  const [board, impact] = await Promise.all([api('/api/leaderboard', { auth: false }).catch(() => null), api('/api/impact', { auth: false }).catch(() => null)]);
  return { board, impact };
}

// Show the "About" sheet with app info, privacy notes, and data credits.
export function openAbout() {
  const days = (S.cfg && S.cfg.photo_retention_days) || 14;
  openSheet(
    el('h2', {}, t('about_title')),
    el('p', {}, t('about_intro')),
    el('h3', {}, t('about_privacy_h')),
    el('p', { class: 'small' }, t('about_privacy', { days })),
    el('p', { class: 'small' }, t('about_limits')),
    el('p', { class: 'small' }, t('safety')),
    el('p', { class: 'small muted' }, t('by_club', { site: S.cfg.site_name })),
    el('h3', {}, t('about_data')),
    ['credit_map', 'credit_sat', 'credit_weather', 'credit_city', 'credit_ai'].map((k) => el('p', { class: 'small muted' }, t(k))),
    el('div', { class: 'actions' }, el('button', { class: 'btn', onclick: closeSheet }, t('close'))));
}

// Show the profile sheet: stats, badges, and the leaderboard.
export async function openProfile() {
  const { board, impact } = await loadExtras();
  const u = S.user;
  const earned = new Set(u.badges || []);
  const rows = (board && board[boardTab === 'weekly' ? 'weekly' : 'all_time']) || [];

  const stat = (label, value) => el('div', { class: 'box', style: { textAlign: 'center', margin: 0 } }, el('b', { style: { fontSize: '1.3rem' } }, value), el('small', {}, label));
  const seg = (options, current, onPick) => el('div', { class: 'seg' }, options.map(([v, label]) => el('button', { 'aria-pressed': String(current === v), onclick: () => onPick(v) }, label)));

  const parts = [
    el('div', { class: 'head' }, el('div', { class: 'type-dot', style: { background: 'var(--accent)', color: 'var(--accent-fg)', fontWeight: 700 } }, (u.nickname[0] || '?').toUpperCase()),
      el('div', {}, el('h2', {}, u.nickname), el('div', { class: 'muted small' }, t('prof_title')))),
    el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px', margin: '12px 0' } },
      stat(t('prof_points'), u.points), stat(t('prof_streak'), u.streak), stat(t('prof_week'), u.weekly_points || 0)),
    el('h3', {}, t('prof_badges')),
    el('div', { class: 'badge-grid' }, S.cfg.rules.badges.map((code) =>
      el('div', { class: `bdg${earned.has(code) ? ' on' : ''}` }, el('div', { class: 'ic' }, icon(code.startsWith('streak') ? 'flame' : 'award', 22)), t(`b_${code}`)))),
    el('h3', {}, t('prof_board')),
    seg([['weekly', t('prof_weekly')], ['all', t('prof_all')]], boardTab, (v) => { boardTab = v; openProfile(); }),
    rows.length
      ? el('table', { class: 'lb' }, rows.map((r, i) => el('tr', {}, el('td', {}, `${i + 1}`), el('td', {}, r.nickname, r.id === u.id ? ` (${t('prof_you')})` : ''), el('td', {}, r.points))))
      : el('p', { class: 'muted' }, t('prof_none_yet')),
    impact ? el('p', { class: 'muted small' }, `${t('prof_impact')}: ${t('prof_impact_line', { n: impact.verified_total, v: impact.volunteers, o: impact.open_flags })}`) : null,
  ];
  parts.push(el('div', { class: 'actions' }, el('button', { class: 'btn', onclick: closeSheet }, t('close'))));
  openSheet(...parts);
}

// Weather test controls: one-tap presets plus a per-condition override. Forced conditions apply to everyone using the app.
export function weatherTools(reopen) {
  const forced = (S.conditions && S.conditions.forced) || {};
  const send = async (name, mode) => api('/api/dev/force', { method: 'POST', json: { name, mode } });
  const preset = async (modes) => {
    try { for (const [name, mode] of Object.entries(modes)) await send(name, mode); toast(t('force_applied')); }
    catch (e) { toast(codeMessage(e.code)); }
    emit('flags:refresh');
    setTimeout(reopen, 700);
  };
  const force = (name, label) => el('div', {}, el('label', { class: 'lbl' }, label),
    el('div', { class: 'seg' }, [['auto', t('force_auto')], ['on', t('force_on')], ['off', t('force_off')]].map(([mode, text]) =>
      el('button', { 'aria-pressed': String((forced[name] || 'auto') === mode), onclick: async () => {
        try { await send(name, mode); } catch (e) { toast(codeMessage(e.code)); }
        emit('flags:refresh');
        setTimeout(reopen, 700);
      } }, text))));
  const anyForced = ['heat', 'rain', 'dry'].some((n) => (forced[n] || 'auto') !== 'auto');
  const PRESETS = [
    ['preset_heat', { heat: 'on', dry: 'on', rain: 'off' }],
    ['preset_storm', { rain: 'on', heat: 'off', dry: 'off' }],
    ['preset_all', { heat: 'on', rain: 'on', dry: 'on' }],
    ['preset_real', { heat: 'auto', rain: 'auto', dry: 'auto' }],
  ];
  // a preset shows as selected while the current overrides match it exactly
  const isActive = (modes) => Object.entries(modes).every(([n, m]) => (forced[n] || 'auto') === m);
  const presetButtons = PRESETS.map(([key, modes]) => {
    const btn = el('button', { class: 'btn', 'aria-pressed': String(isActive(modes)), onclick: () => {
      grid.querySelectorAll('.btn').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
      preset(modes);
    } }, t(key));
    return btn;
  });
  const grid = el('div', { class: 'preset-grid' }, ...presetButtons);
  return [
    el('h3', {}, t('prof_weather_test')),
    el('p', { class: 'muted small' }, anyForced ? t('force_active') : t('force_help')),
    grid,
    el('h3', {}, t('prof_force')), force('heat', t('force_heat')), force('rain', t('force_rain')), force('dry', t('force_dry')),
    el('div', { class: 'actions' },
      el('button', { class: 'btn', onclick: async () => { try { await api('/api/dev/refresh', { method: 'POST' }); } catch (e) { toast(codeMessage(e.code)); } emit('flags:refresh'); toast(t('prof_refresh')); } }, icon('refresh', 18), t('prof_refresh'))),
  ];
}

// Build the dev-only demo controls: teleport near a flag, weather tests, reset data.
export function demoTools(reopen, done) {
  const p = getPos();
  const near = [...S.flags].filter((f) => f.status === 'open').map((f) => ({ f, d: p ? haversine(p.lat, p.lon, f.lat, f.lon) : 0 }))
    .sort((a, b) => (p ? a.d - b.d : b.f.priority - a.f.priority)).slice(0, 6);
  return [
    el('h3', {}, t('prof_demo')),
    el('p', { class: 'muted small' }, t('prof_fake_help')),
    el('div', { class: 'tags' }, el('button', { class: 'tag', onclick: () => { setFake(DEMO_SPOT.lat, DEMO_SPOT.lon); emit('map:center', { lat: DEMO_SPOT.lat, lon: DEMO_SPOT.lon }); toast(t('teleported')); done(); } }, DEMO_SPOT.name), near.map(({ f }) => el('button', { class: 'tag', onclick: () => { setFake(f.lat + 0.00006, f.lon); emit('map:center', { lat: f.lat, lon: f.lon }); toast(t('teleported')); done(); } }, short(flagTitle(f))))),
    S.fake ? el('div', { class: 'actions' }, el('button', { class: 'btn', onclick: () => { clearFake(); done(); } }, t('prof_real'))) : null,
    ...weatherTools(reopen),
    el('div', { class: 'actions' },
      el('button', { class: 'btn danger', onclick: async () => {
        if (!confirm(t('prof_reset_confirm'))) return;
        try { await api('/api/dev/reset', { method: 'POST' }); } catch (e) { toast(codeMessage(e.code)); }
        emit('user:refresh'); emit('flags:refresh'); done();
      } }, t('prof_reset'))),
  ];
}
