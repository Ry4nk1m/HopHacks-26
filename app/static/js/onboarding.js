import { S, store } from './state.js';
import { api, token } from './api.js';
import { el, icon, setKids } from './ui.js';
import { t } from './i18n.js';
import { startLocation } from './geo.js';
import { emit } from './bus.js';
import { speak, stopVoice } from './voice.js';

const TOTAL_STEPS = 4;
const NAME_RE = /^[\p{L}\p{N} ._'-]{2,20}$/u;
const HAS_LETTER = /\p{L}/u;
const ADJ = ['Maple', 'Quiet', 'Bright', 'Brave', 'Sunny', 'Swift', 'Kind', 'Clever', 'Gentle', 'Bold', 'Cedar', 'Amber'];
const NOUN = ['Fox', 'Otter', 'Heron', 'Finch', 'Badger', 'Wren', 'Owl', 'Robin', 'Hare', 'Lynx', 'Sparrow', 'Marten'];
const pick = (a) => a[Math.floor(Math.random() * a.length)];

// static, trusted artwork (an open umbrella with a few flags falling like rain drops of tasks)
const HERO = `<svg viewBox="0 0 240 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">
  <path d="M28 108C28 58 68 22 120 22s92 36 92 86c-14-12-28-12-46 0-16-12-30-12-46 0-16-12-30-12-46 0-18-12-32-12-46 0z" fill="#152e34"/>
  <path d="M120 22v86M120 22C96 44 82 76 74 108M120 22c24 22 38 54 46 86" stroke="#fbefd9" stroke-opacity=".35" stroke-width="3" fill="none"/>
  <path d="M120 108v54a16 16 0 0 1-32 0" stroke="#152e34" stroke-width="7" stroke-linecap="round" fill="none"/>
  <circle cx="40" cy="150" r="7" fill="#e46a46"/><circle cx="196" cy="140" r="7" fill="#234831"/><circle cx="176" cy="176" r="5" fill="#152e34" fill-opacity=".55"/>
</svg>`;

export function showOnboarding(onDone) {
  const old = document.querySelector('.ob');
  if (old) old.remove();
  const ob = { step: 0, name: '', voice: S.voice, agreed: [false, false, false], busy: false, error: '' };
  const root = el('div', { class: 'ob' });
  const top = el('div', { class: 'ob-top' });
  const hero = el('div', { class: 'ob-hero' });
  hero.innerHTML = HERO;
  const card = el('div', { class: 'ob-card' });
  root.append(top, hero, card);
  document.getElementById('app').append(root);

  const go = (n) => { ob.step = n; ob.error = ''; renderCard(); card.scrollTop = 0; };

  function renderTop() {
    setKids(top,
      el('div', { class: 'ob-brand' }, el('div', { class: 'ob-logo' }, icon('pin', 18)), el('span', {}, S.cfg.app_name)));
  }

  function dots() {
    return el('div', { class: 'ob-dots', role: 'img', 'aria-label': t('ob_step', { n: ob.step + 1, t: TOTAL_STEPS }) },
      Array.from({ length: TOTAL_STEPS }, (_, i) => el('span', { class: i === ob.step ? 'on' : i < ob.step ? 'done' : '' })));
  }

  function back() { return ob.step > 0 ? el('button', { class: 'ghost', onclick: () => go(ob.step - 1) }, t('ob_back')) : null; }

  function stepWelcome() {
    const how = [['globe', 'ob_how1_t', 'ob_how1_d'], ['pin', 'ob_how2_t', 'ob_how2_d'], ['camera', 'ob_how3_t', 'ob_how3_d']];
    return [
      el('h1', { class: 'ob-h' }, t('ob_tagline')), el('p', { class: 'ob-sub' }, t('ob_welcome_sub')),
      el('div', { class: 'ob-how' }, how.map(([ic, ti, de]) => el('div', { class: 'ob-row' },
        el('div', { class: 'ob-ic' }, icon(ic, 20)), el('div', {}, el('strong', {}, t(ti)), el('span', {}, t(de)))))),
      el('button', { class: 'cta', onclick: () => go(1) }, t('ob_start'))];
  }

  function validName() {
    const n = ob.name.replace(/\s+/g, ' ').trim();
    return NAME_RE.test(n) && HAS_LETTER.test(n) ? n : null;
  }

  function stepName() {
    const initial = (ob.name.trim()[0] || '?').toUpperCase();
    const avatar = el('div', { class: 'avatar' }, initial);
    const input = el('input', { class: 'field', maxlength: '20', placeholder: t('nickname'), autocomplete: 'off', autocapitalize: 'words', 'aria-label': t('nickname'),
      oninput: (e) => { ob.name = e.target.value; avatar.textContent = (ob.name.trim()[0] || '?').toUpperCase(); err.textContent = ''; } });
    input.value = ob.name;
    const err = el('div', { class: 'err' }, ob.error);
    const sw = el('button', { class: 'switch', role: 'switch', 'aria-checked': String(ob.voice), 'aria-label': t('ob_voice_title'), onclick: () => {
      ob.voice = !ob.voice; S.voice = ob.voice; store.set('nm_voice', ob.voice ? '1' : '0');
      if (ob.voice) speak(t('ob_voice_sample'), true); else stopVoice();
      renderCard();
    } }, el('span'));
    return [
      el('h1', { class: 'ob-h' }, t('ob_name_title')), el('p', { class: 'ob-sub' }, t('ob_name_sub')),
      el('div', { class: 'name-row' }, avatar, input),
      el('div', { class: 'name-tools' }, err,
        el('button', { class: 'ghost sm', onclick: () => { ob.name = `${pick(ADJ)} ${pick(NOUN)}`; ob.error = ''; renderCard(); } }, icon('refresh', 15), t('ob_surprise'))),
      el('div', { class: 'opt' }, el('div', { class: 'ob-ic' }, icon(ob.voice ? 'volume' : 'volume-x', 20)),
        el('div', { class: 'grow' }, el('strong', {}, t('ob_voice_title')), el('span', {}, t('ob_voice_desc')),
          el('button', { class: 'link', onclick: () => speak(t('ob_voice_sample'), true) }, t('ob_voice_sample_btn'))), sw),
      el('button', { class: 'cta', onclick: () => {
        if (!validName()) { ob.error = t('err_bad_nickname'); renderCard(); return; }
        ob.name = validName(); go(2);
      } }, t('ob_continue')), back()];
  }

  function stepSafety() {
    const items = [['shield', 'safe_1'], ['camera', 'safe_2'], ['alert', 'safe_3']];
    const cta = el('button', { class: 'cta', disabled: !ob.agreed.every(Boolean), onclick: () => go(3) }, t('ob_agree'));
    // toggle in place: rebuilding the card here made the whole sheet jump and re-animate
    const rows = items.map(([ic, key], i) => {
      const tick = el('div', { class: 'tick' }, ob.agreed[i] ? icon('check', 16) : null);
      const row = el('button', { class: `pledge${ob.agreed[i] ? ' on' : ''}`, role: 'checkbox', 'aria-checked': String(ob.agreed[i]),
        onclick: () => {
          ob.agreed[i] = !ob.agreed[i];
          row.classList.toggle('on', ob.agreed[i]);
          row.setAttribute('aria-checked', String(ob.agreed[i]));
          setKids(tick, ob.agreed[i] ? icon('check', 16) : null);
          cta.disabled = !ob.agreed.every(Boolean);
        } },
        el('div', { class: 'ob-ic' }, icon(ic, 20)), el('span', {}, t(key)), tick);
      return row;
    });
    return [
      el('h1', { class: 'ob-h' }, t('ob_safe_title')), el('p', { class: 'ob-sub' }, t('ob_safe_sub')),
      el('div', { class: 'ob-how' }, rows), cta, back()];
  }

  async function finish(locChoice) {
    if (ob.busy) return;
    ob.busy = true; renderCard();
    try {
      const u = await api('/api/users', { method: 'POST', json: { nickname: ob.name }, auth: false });
      token.set(u.token);
      S.user = u;
      store.set('nm_voice', S.voice ? '1' : '0');
      store.set('nm_loc', locChoice);
      root.remove();
      await onDone(locChoice);
    } catch (e) {
      ob.busy = false;
      if (e.code === 'nickname_taken' || e.code === 'bad_nickname') { ob.error = e.code === 'nickname_taken' ? t('err_nickname_taken') : t('err_bad_nickname'); ob.step = 1; }
      else ob.error = e.status === 0 ? t('err_network') : e.code === 'rate_limited' ? t('code_rate_limited') : t('err_generic');
      renderCard();
    }
  }

  function stepLocation() {
    return [
      el('div', { class: 'loc-ic' }, el('span', { class: 'ping' }), icon('pin', 34)),
      el('h1', { class: 'ob-h center' }, t('ob_loc_title')), el('p', { class: 'ob-sub center' }, t('ob_loc_desc')),
      el('div', { class: 'note' }, icon('eye', 16), el('span', {}, t('ob_loc_privacy'))),
      ob.error ? el('div', { class: 'err center' }, ob.error) : null,
      el('button', { class: 'cta', disabled: ob.busy, onclick: () => { startLocation(); finish('on'); } }, ob.busy ? '…' : t('ob_loc_enable')),
      el('button', { class: 'ghost', disabled: ob.busy, onclick: () => { S.locState = 'off'; emit('pos'); finish('off'); } }, t('ob_loc_skip'))];
  }

  let lastStep = -1;
  function renderCard() {
    const body = [stepWelcome, stepName, stepSafety, stepLocation][ob.step]();
    const same = lastStep === ob.step;
    const keep = card.scrollTop;
    lastStep = ob.step;
    setKids(card, dots(), el('div', { class: `ob-body${same ? ' still' : ''}` }, body));
    if (same) card.scrollTop = keep;
    const input = card.querySelector('input.field');
    if (input && ob.step === 1 && !ob.name) input.focus({ preventScroll: true });
  }

  renderTop();
  renderCard();
}
