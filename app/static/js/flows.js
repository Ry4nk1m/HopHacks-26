// Main mission flows: viewing a flag, accepting it, arriving, submitting a photo for verification, and reporting a problem.

import { S } from './state.js';
import { emit, on } from './bus.js';
import { api } from './api.js';
import { el, icon, TYPE_ICON, TYPE_COLOR, TYPE_GLYPH, toast, openSheet, closeSheet, sheetOpts, fmtDist, timeAgo, dateShort, cToF } from './ui.js';
import { t, flagTitle, codeMessage } from './i18n.js';
import { getPos, freshPos, haversine } from './geo.js';
import { speak, stopVoice } from './voice.js';

// ------------------------------------------------------------------ small helpers
// The flag tied to the current mission, refreshed from the latest flag list if possible.
const missionFlag = () => (S.mission ? S.flags.find((f) => f.id === S.mission.flag.id) || S.mission.flag : null);
// Distance from the user to a flag, or null if either position is unknown.
const distTo = (flag) => { const p = getPos(); return p && flag ? haversine(p.lat, p.lon, flag.lat, flag.lon) : null; };
// Which task instructions to show for a flag.
const taskKey = (flag) => (flag.purpose === 'confirm' ? 'task_confirm' : `task_${flag.type}`);
// Build a Google Maps walking directions link to a flag.
const mapsUrl = (f) => `https://www.google.com/maps/dir/?api=1&destination=${f.lat},${f.lon}&travelmode=walking`;

// Format a list of dates as a single date or a range, for satellite scene captions.
function dateRange(dates) {
  if (!dates || !dates.length) return '';
  const s = [...dates].sort();
  return s.length === 1 || s[0] === s[s.length - 1] ? dateShort(s[0]) : `${dateShort(s[0])} – ${dateShort(s[s.length - 1])}`;
}

// Turn a flag's "reason" code and data into a readable sentence.
function reasonText(r) {
  switch (r.code) {
    case 'dry_spell': return t('r_dry_spell', { past: r.past7_mm != null ? Math.round(r.past7_mm) : '?', next: r.next3_mm != null ? Math.round(r.next3_mm) : '?' });
    case 'heat_forecast': return r.max_c != null ? t('r_heat_forecast', { f: cToF(r.max_c) }) : t('cond_heat');
    case 'rain_forecast': return t('r_rain_forecast', { mm: Math.round(r.next2_mm || 0) });
    case 'hours_unverified': return t('r_hours_unverified');
    case 'city_request': return t('r_city_request', { when: timeAgo(r.created), status: r.status });
    case 'user_report': return t('r_user_report');
    default: return '';
  }
}

// Build the satellite info box (vegetation/heat readings) for a flag, or null if there is no data.
function satBlock(sat) {
  if (!sat || sat.ndvi == null) return null;
  const veg = sat.veg_low_pct >= 0.66 ? t('sat_veg_low') : sat.veg_low_pct <= 0.33 ? t('sat_veg_high') : t('sat_veg_mid');
  const heat = sat.heat_pct >= 0.66 ? t('sat_heat_high') : sat.heat_pct <= 0.33 ? t('sat_heat_low') : t('sat_heat_mid');
  return el('div', { class: 'box' },
    el('strong', {}, t('sat_title')),
    el('div', {}, t('sat_line', { ndvi: sat.ndvi.toFixed(2), veg, f: sat.lst_c != null ? cToF(sat.lst_c) : '?', heat })),
    el('small', {}, t('sat_src', { ndvi: dateRange(sat.ndvi_dates), lst: dateRange(sat.lst_dates) })));
}

// Build the info box for a cooling center's address, hours, phone, and verification status.
function featureBlock(flag) {
  const f = (flag.context || {}).feature;
  if (!f || flag.type !== 'cooling_check') return null;
  const rows = [];
  if (f.address) rows.push(el('div', {}, el('strong', {}, `${t('info_address')}: `), f.address));
  if (f.hours) rows.push(el('div', {}, el('strong', {}, `${t('info_hours')}: `), f.hours));
  if (f.phone) rows.push(el('div', {}, el('strong', {}, `${t('info_phone')}: `), el('a', { href: `tel:${String(f.phone).replace(/[^\d+]/g, '')}` }, f.phone)));
  if (f.verified) {
    const v = f.verified;
    rows.push(el('div', {}, v.hours_text ? t('info_verified', { when: timeAgo(f.last_verified_at), hours: v.hours_text }) : t('info_verified_nohours', { when: timeAgo(f.last_verified_at) }),
      v.accessible === true ? ` · ${t('info_accessible')}` : v.accessible === false ? ` · ${t('info_steps')}` : ''));
  }
  return rows.length ? el('div', { class: 'box' }, rows) : null;
}

// Draw the colored round icon used to represent a flag type.
function typeDot(type, size) {
  return el('div', { class: 'type-dot', style: { background: TYPE_COLOR[type], color: TYPE_GLYPH[type], width: `${size || 42}px`, height: `${size || 42}px` } }, icon(TYPE_ICON[type], 22));
}

// ------------------------------------------------------------------ flag detail
// Show the sheet with a flag's full details: tags, reasons, task instructions, and the accept/confirm button.
export function openFlagSheet(flagId) {
  const flag = S.flags.find((f) => f.id === flagId);
  if (!flag) return;
  emit('flag:highlight', flag.id);
  const ctx = flag.context || {};
  const d = distTo(flag);
  const mine = S.mission && S.mission.flag.id === flag.id;
  const tags = [
    el('span', { class: `tag${flag.urgency === 3 ? ' hot' : ''}` }, t(`urg_${flag.urgency}`)),
    d != null ? el('span', { class: 'tag' }, fmtDist(d)) : null,
    el('span', { class: 'tag reward' }, t('pts', { n: flag.reward })),
    flag.source === '311' ? el('span', { class: 'tag' }, t('tag_city')) : null,
    ctx.feature && ctx.feature.official ? el('span', { class: 'tag' }, t('tag_official')) : null,
    flag.mine ? el('span', { class: 'tag' }, t('tag_mine')) : null,
    flag.unconfirmed ? el('span', { class: 'tag hot' }, t('tag_unconfirmed')) : flag.source === 'user' ? el('span', { class: 'tag' }, t('tag_confirmed')) : null,
    flag.claimed ? el('span', { class: 'tag' }, t('tag_claimed')) : null,
    flag.status === 'pending' ? el('span', { class: 'tag' }, t('tag_pending')) : null,
  ];
  const reasons = (ctx.reasons || []).map(reasonText).filter(Boolean);
  const ownReport = flag.mine && flag.purpose === 'confirm';
  const primary = mine ? el('button', { class: 'btn primary', onclick: () => openMissionSheet() }, t('continue_m'))
    : ownReport ? null
    : flag.status === 'pending' ? el('button', { class: 'btn', disabled: true }, t('pending_btn'))
    : flag.claimed ? el('button', { class: 'btn', disabled: true }, t('claimed_btn'))
    : el('button', { class: 'btn primary', onclick: () => acceptFlag(flag) }, t('accept'));
  openSheet(
    el('div', { class: 'head' }, typeDot(flag.type), el('div', {}, el('h2', {}, flagTitle(flag)))),
    el('div', { class: 'tags' }, tags),
    reasons.length ? el('div', {}, el('h3', {}, t('why_flagged')), el('ul', { class: 'list' }, reasons.map((r) => el('li', {}, r)))) : null,
    featureBlock(flag),
    (ctx.city && ctx.city.address) ? el('div', { class: 'box' }, el('strong', {}, ctx.city.address), ctx.city.neighborhood ? el('small', {}, ctx.city.neighborhood) : null) : null,
    (ctx.report && ctx.report.note) ? el('div', { class: 'box' }, el('small', {}, t('note_label')), ctx.report.note) : null,
    satBlock(ctx.satellite),
    el('h3', {}, t('what_to_do')),
    el('p', {}, t(taskKey(flag))),
    el('p', { class: 'muted small' }, t('safety')),
    el('div', { class: 'actions' },
      primary,
      flag.unconfirmed && !flag.mine ? el('button', { class: 'btn', onclick: () => quickConfirm(flag) }, t('confirm_btn')) : null,
      el('a', { class: 'btn', href: mapsUrl(flag), target: '_blank', rel: 'noopener' }, icon('navigation', 18), t('directions'))),
    sheetOpts({ onClose: () => emit('flag:highlight', null) }));
}

// Confirm an unconfirmed flag is still there, using the user's current location.
async function quickConfirm(flag) {
  try {
    const p = await freshPos();
    const r = await api(`/api/flags/${flag.id}/confirm`, { method: 'POST', json: { lat: p.lat, lon: p.lon, accuracy: Math.round(p.acc || 0) } });
    toast(t('confirm_done', { n: r.points }));
    closeSheet();
    emit('user:refresh');
    emit('flags:refresh');
  } catch (e) {
    toast(e.code === 'too_far' && e.data.distance_m != null ? t('m_far', { d: fmtDist(e.data.distance_m), r: fmtDist(e.data.radius_m) }) : codeMessage(e.code));
  }
}

// ------------------------------------------------------------------ mission
// Local state for the in-progress mission UI: photos taken, submission status, and the last result.
const MS = { id: null, photo: null, before: null, wasProblem: null, busy: false, result: null, resultFlag: null, arriving: false, lastAuto: 0 };

// Reset the mission UI state for a new (or restored) mission.
function resetMissionState(id) {
  Object.assign(MS, { id, photo: null, before: null, wasProblem: null, busy: false, result: null, resultFlag: null, arriving: false, lastAuto: 0 });
}

// Accept a flag as a new mission and open the mission sheet.
async function acceptFlag(flag) {
  try {
    const r = await api(`/api/flags/${flag.id}/accept`, { method: 'POST' });
    resetMissionState(r.mission.id);
    S.mission = { mission: r.mission, flag };
    emit('mission:changed');
    emit('flags:refresh');
    speak(t('brief', { title: flagTitle(flag), task: t(taskKey(flag)) }));
    openMissionSheet();
  } catch (e) {
    toast(codeMessage(e.code));
    emit('flags:refresh');
  }
}

// Restore a mission that was already active (e.g. after a page reload).
export function restoreMission(mission, flag) {
  resetMissionState(mission.id);
  S.mission = { mission, flag };
  emit('mission:changed');
}

// Draw the 3-dot progress bar showing accepted / arrived / verified state.
function stepsBar(state) {
  const done = [state !== 'accepted', !!MS.photo && state !== 'accepted', !!(MS.result && MS.result.outcome === 'verified')];
  return el('div', { class: 'steps' }, done.map((d) => el('div', { class: `step${d ? ' done' : ''}` })));
}

// A tappable box that shows a taken photo, or a camera icon and label if none yet.
function photoSlot(label, photo, onPick) {
  return el('button', { class: `photo-slot${photo ? ' filled' : ''}`, onclick: onPick, 'aria-label': label },
    photo ? el('img', { src: photo.url, alt: '' }) : [icon('camera', 26), el('span', {}, label)]);
}

// Shrink and compress a photo before upload; falls back to the original file if that fails.
async function prepPhoto(file) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
    c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.85));
    if (!blob) throw new Error('encode');
    return { blob, url: URL.createObjectURL(blob) };
  } catch (e) {
    return { blob: file, url: URL.createObjectURL(file) }; // let the server try the original
  }
}

/** Opens the phone's own camera app (full-quality photo, autofocus, HDR) via a file input. */
function pickPhoto() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = 'image/*'; input.setAttribute('capture', 'environment'); input.style.display = 'none';
    document.body.append(input);
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      input.remove();
      resolve(file ? await prepPhoto(file) : null);
    });
    input.addEventListener('cancel', () => { input.remove(); resolve(null); });
    input.click();
  });
}

// Show the mission sheet: directions while heading there, or the photo/checklist form once arrived.
export function openMissionSheet() {
  if (!S.mission && !MS.result) return;
  if (MS.result) { renderResult(); return; }
  const { mission } = S.mission;
  const flag = missionFlag();
  const arrived = mission.status === 'arrived';
  const state = MS.busy ? 'busy' : arrived ? 'arrived' : 'accepted';
  const head = el('div', { class: 'head' }, typeDot(flag.type, 38), el('div', {}, el('h2', {}, flagTitle(flag)),
    el('div', { class: 'muted small' }, t('m_tries', { n: mission.remaining_attempts }))));
  const body = [head, stepsBar(state === 'busy' ? 'arrived' : state)];

  if (state === 'busy') {
    body.push(el('div', { class: 'verdict' }, el('div', { class: 'spinner', style: { borderColor: 'var(--line)', borderTopColor: 'var(--accent)' } }), el('p', {}, t('m_checking'))));
  } else if (state === 'accepted') {
    body.push(
      el('div', { class: 'box' }, el('strong', { id: 'mDist' }, t('m_away', { d: fmtDist(distTo(flag)) })), el('small', { id: 'mHint' }, hintText(flag))),
      el('h3', {}, t('what_to_do')), el('p', {}, t(taskKey(flag))), el('p', { class: 'muted small' }, t('safety')),
      el('div', { class: 'actions' },
        el('button', { class: 'btn primary', onclick: () => arrive(true) }, icon('pin', 18), t('m_imhere')),
        el('a', { class: 'btn', href: mapsUrl(flag), target: '_blank', rel: 'noopener' }, icon('navigation', 18), t('directions'))),
      el('div', { class: 'actions' }, el('button', { class: 'btn danger', onclick: cancelMission }, t('cancel_mission')), el('button', { class: 'btn', onclick: closeSheet }, t('close'))));
  } else {
    const confirm = flag.purpose === 'confirm';
    body.push(
      el('p', {}, el('strong', {}, t('m_arrived')), ' · ', t(taskKey(flag))),
      el('div', { class: 'photos' },
        confirm ? null : photoSlot(t('m_before'), MS.before, async () => { const p = await pickPhoto(); if (p) { MS.before = p; openMissionSheet(); } }),
        photoSlot(confirm ? t('m_photo_hint_confirm') : t('m_after'), MS.photo, async () => { const p = await pickPhoto(); if (p) { MS.photo = p; openMissionSheet(); } })),
      el('label', { class: 'lbl' }, t('m_real')),
      el('div', { class: 'seg' }, [['yes', 'm_yes'], ['no', 'm_no'], ['unsure', 'm_unsure']].map(([v, k]) =>
        el('button', { 'aria-pressed': String(MS.wasProblem === v), onclick: () => { MS.wasProblem = MS.wasProblem === v ? null : v; openMissionSheet(); } }, t(k)))),
      S.cfg.validator === 'mock' ? el('p', { class: 'muted small' }, t('m_sim')) : null,
      el('div', { class: 'actions' },
        el('button', { class: 'btn primary', onclick: submitMission, disabled: !MS.photo }, t('m_submit')),
        el('button', { class: 'btn danger', onclick: cancelMission }, t('cancel_mission'))));
  }
  openSheet(...body, sheetOpts({ dismissible: !MS.busy }));
}

// Text telling the user how far they still need to walk to reach the flag.
function hintText(flag) {
  const p = getPos();
  if (!p) return t('m_gps_waiting');
  const need = flag.radius_m + Math.min(p.acc || 0, S.cfg.rules.accuracy_cap_m);
  return t('m_far', { d: fmtDist(distTo(flag)), r: fmtDist(need) });
}

// Refresh the live distance readout in the mission sheet as the GPS position updates.
export function updateMissionLive() {
  const flag = missionFlag();
  if (!S.mission || !flag) return;
  const dist = document.getElementById('mDist'), hint = document.getElementById('mHint');
  if (dist) dist.textContent = t('m_away', { d: fmtDist(distTo(flag)) });
  if (hint) hint.textContent = hintText(flag);
  maybeArrive();
}

// Automatically mark the mission as arrived once the user is close enough, without needing a tap.
function maybeArrive() {
  if (!S.mission || S.mission.mission.status !== 'accepted' || MS.arriving || Date.now() - MS.lastAuto < 8000) return;
  const flag = missionFlag(), p = getPos(), d = distTo(flag);
  if (!flag || !p || d == null) return;
  if (d <= flag.radius_m + Math.min(p.acc || 0, S.cfg.rules.accuracy_cap_m) + 5) arrive(false);
}

// Tell the server the user has arrived at the mission site (either tapped manually or detected automatically).
async function arrive(manual) {
  if (!S.mission || MS.arriving) return;
  MS.arriving = true; MS.lastAuto = Date.now();
  try {
    const p = await freshPos();
    const r = await api(`/api/missions/${S.mission.mission.id}/arrive`, { method: 'POST', json: { lat: p.lat, lon: p.lon, accuracy: Math.round(p.acc || 0), manual } });
    if (r.arrived) {
      S.mission.mission.status = 'arrived';
      S.mission.mission.manual_arrival = !!r.manual;
      emit('mission:changed');
      if (document.getElementById('mDist') || MS.id) openMissionSheet();
      toast(t('m_arrived'));
    } else if (manual) {
      toast(t('m_far', { d: fmtDist(r.distance_m), r: fmtDist(r.radius_m) }));
    }
  } catch (e) {
    if (manual || e.code === 'not_active') toast(codeMessage(e.code));
    if (e.code === 'not_active' || e.code === 'mission_not_found') { S.mission = null; emit('mission:changed'); }
  } finally { MS.arriving = false; }
}

// Cancel the active mission.
async function cancelMission() {
  if (!S.mission) return;
  try { await api(`/api/missions/${S.mission.mission.id}/cancel`, { method: 'POST' }); } catch (e) { /* already gone */ }
  S.mission = null;
  stopVoice();
  closeSheet();
  emit('mission:changed');
  emit('flags:refresh');
}

// Submit the mission's photo(s) and answers for photo verification.
async function submitMission() {
  if (!S.mission) return;
  if (!MS.photo) { toast(t('m_need_photo')); return; }
  const { mission } = S.mission, flag = missionFlag();
  MS.busy = true; openMissionSheet();
  let pos;
  try { pos = await freshPos(); } catch (e) { MS.busy = false; openMissionSheet(); toast(codeMessage(e && e.code ? e.code : 'bad_location')); return; }
  const form = new FormData();
  form.append('photo', MS.photo.blob, 'photo.jpg');
  if (MS.before) form.append('before', MS.before.blob, 'before.jpg');
  form.append('lat', pos.lat); form.append('lon', pos.lon); form.append('accuracy', Math.round(pos.acc || 0));
  if (MS.wasProblem) form.append('was_problem', MS.wasProblem);
  try {
    const r = await api(`/api/missions/${mission.id}/submit`, { method: 'POST', form });
    MS.busy = false;
    if (r.outcome === 'error') { openMissionSheet(); toast(codeMessage(r.code), { ms: 5000 }); return; }
    if (r.mission) Object.assign(S.mission.mission, r.mission);
    MS.result = r; MS.resultFlag = flag;
    const over = r.outcome === 'verified' || r.outcome === 'pending' || !r.retry;
    if (over) { S.mission = null; emit('mission:changed'); }
    emit('user:refresh'); emit('flags:refresh');
    renderResult();
  } catch (e) {
    MS.busy = false;
    if (e.code === 'no_arrival' || e.code === 'arrival_expired') S.mission.mission.status = 'accepted';
    if (e.code === 'not_active' || e.code === 'attempts_exhausted' || e.code === 'mission_not_found') { S.mission = null; closeSheet(); emit('mission:changed'); emit('flags:refresh'); }
    else openMissionSheet();
    toast(e.code === 'too_far' && e.data.distance_m != null ? t('m_far', { d: fmtDist(e.data.distance_m), r: fmtDist(e.data.radius_m) }) : codeMessage(e.code), { ms: 5000 });
  }
}

const OUTCOME_ICON = { verified: 'check', pending: 'refresh', rejected: 'x', error: 'alert' };

// Build the verdict view shown after a mission or report is checked (verified, pending, rejected, or error).
function resultView(r, flag, onRetry, onDone) {
  const title = { verified: t('v_verified'), pending: t('v_pending'), rejected: t('v_rejected'), error: t('v_error') }[r.outcome] || t('v_error');
  const cls = OUTCOME_ICON[r.outcome] ? r.outcome : 'error';
  return [
    el('div', { class: `verdict ${cls}` },
      el('div', { class: 'badge' }, icon(OUTCOME_ICON[cls], 34)),
      el('h2', {}, title),
      el('p', {}, codeMessage(r.code)),
      r.points ? el('div', { class: 'bigpts' }, t('earned', { n: r.points })) : null,
      r.streak ? el('div', { class: 'muted' }, el('span', { style: { display: 'inline-flex', gap: '4px', alignItems: 'center' } }, icon('flame', 16), t('streak_days', { n: r.streak }))) : null,
      (r.new_badges || []).map((b) => el('p', {}, el('strong', {}, t('new_badge', { name: t(`b_${b}`) })))),
      r.reasoning ? el('div', { class: 'quote' }, el('small', {}, `${t('v_says')}: `), r.reasoning) : null,
      S.cfg.validator === 'mock' ? el('p', { class: 'muted small' }, t('m_sim')) : null,
      r.mission && r.outcome === 'rejected' && r.retry ? el('p', { class: 'muted small' }, t('m_tries', { n: r.mission.remaining_attempts })) : null),
    el('div', { class: 'actions' },
      r.outcome === 'rejected' && r.retry ? el('button', { class: 'btn primary', onclick: onRetry }, t('v_retry')) : null,
      el('button', { class: `btn${r.outcome === 'rejected' && r.retry ? '' : ' primary'}`, onclick: onDone }, t('done'))),
  ];
}

// Show the mission result sheet.
function renderResult() {
  const r = MS.result;
  openSheet(...resultView(r, MS.resultFlag,
    () => { MS.result = null; MS.photo = null; MS.before = null; openMissionSheet(); },
    () => { MS.result = null; closeSheet(); }), sheetOpts({ onClose: () => { if (!S.mission) MS.result = null; } }));
}

// ------------------------------------------------------------------ report a problem
// Local state for the "report a problem" form.
// the server works out flood vs other from the photo, so the reporter doesn't choose
const RS = { type: 'problem_report', photo: null, note: '', busy: false, result: null };

// Open the "report a problem" sheet, starting fresh.
export function openReportSheet() {
  RS.result = null;
  renderReport();
}

// Draw the report form, or the result screen if a report was just submitted.
function renderReport() {
  const p = getPos();
  if (RS.result) {
    const r = RS.result;
    if (r.outcome === 'created') {
      openSheet(el('div', { class: 'verdict verified' }, el('div', { class: 'badge' }, icon('check', 34)), el('h2', {}, t('rep_created')),
        el('p', {}, r.unconfirmed ? t('rep_unconfirmed') : t('rep_points', { n: r.points })),
        (r.new_badges || []).map((b) => el('p', {}, el('strong', {}, t('new_badge', { name: t(`b_${b}`) })))),
        r.reasoning ? el('div', { class: 'quote' }, el('small', {}, `${t('v_says')}: `), r.reasoning) : null,
        S.cfg.validator === 'mock' ? el('p', { class: 'muted small' }, t('m_sim')) : null),
        el('div', { class: 'actions' }, el('button', { class: 'btn primary', onclick: () => { closeSheet(); emit('flag:focus', r.flag_id); } }, t('done'))));
    } else if (r.outcome === 'duplicate') {
      openSheet(el('div', { class: 'verdict pending' }, el('div', { class: 'badge' }, icon('info', 34)), el('h2', {}, t('code_duplicate_report'))),
        el('div', { class: 'actions' }, el('button', { class: 'btn primary', onclick: () => { closeSheet(); emit('flag:focus', r.flag_id); } }, t('rep_view_existing')),
          el('button', { class: 'btn', onclick: closeSheet }, t('close'))));
    } else {
      openSheet(...resultView(r, null, () => { RS.result = null; RS.photo = null; renderReport(); }, closeSheet));
    }
    return;
  }
  const note = el('textarea', { class: 'field', rows: '2', maxlength: '200', placeholder: t('rep_note'), oninput: (e) => { RS.note = e.target.value; } });
  note.value = RS.note;
  openSheet(
    el('h2', {}, t('rep_title')),
    el('p', { class: 'muted' }, t('rep_help')),
    el('label', { class: 'lbl' }, t('rep_photo')),
    el('div', { class: 'photos' }, photoSlot(t('m_take'), RS.photo, async () => { const ph = await pickPhoto(); if (ph) { RS.photo = ph; renderReport(); } })),
    note,
    el('p', { class: 'muted small' }, p ? t('rep_loc', { a: `±${fmtDist(p.acc || 0)}` }) : t('rep_locate_first')),
    S.cfg.validator === 'mock' ? el('p', { class: 'muted small' }, t('m_sim')) : null,
    el('p', { class: 'muted small' }, t('safety')),
    el('div', { class: 'actions' },
      el('button', { class: 'btn primary', disabled: !RS.photo || RS.busy, onclick: submitReport }, RS.busy ? [el('span', { class: 'spin-inline' }), t('m_checking')] : t('rep_submit')),
      el('button', { class: 'btn', onclick: closeSheet }, t('close'))),
    sheetOpts({ dismissible: !RS.busy }));
}

// Submit the problem report with its photo and location.
async function submitReport() {
  if (!RS.photo || RS.busy) return;
  RS.busy = true; renderReport();
  try {
    const pos = await freshPos();
    const form = new FormData();
    form.append('photo', RS.photo.blob, 'photo.jpg'); form.append('type', RS.type); form.append('note', RS.note || '');
    form.append('lat', pos.lat); form.append('lon', pos.lon); form.append('accuracy', Math.round(pos.acc || 0));
    const r = await api('/api/reports', { method: 'POST', form });
    RS.busy = false;
    if (r.outcome === 'error') { renderReport(); toast(codeMessage(r.code), { ms: 5000 }); return; }
    RS.result = r;
    if (r.outcome === 'created') { RS.photo = null; RS.note = ''; }
    emit('user:refresh'); emit('flags:refresh');
    renderReport();
  } catch (e) {
    RS.busy = false; renderReport();
    toast(e && e.code ? codeMessage(e.code) : codeMessage('bad_location'), { ms: 5000 });
  }
}

// keep the open mission panel's distance readout fresh as the GPS moves
on('pos', () => { updateMissionLive(); });
