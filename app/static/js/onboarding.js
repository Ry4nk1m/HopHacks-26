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

const HERO_SRC = '/static/umbrella.gif';

export function showOnboarding(onDone) {
  const old = document.querySelector('.ob');
  if (old) old.remove();
  const ob = { step: 0, name: '', voice: S.voice, agreed: [false, false, false], busy: false, error: '' };
  const root = el('div', { class: 'ob' });
  const top = el('div', { class: 'ob-top' });
  const hero = el('div', { class: 'ob-hero' });
  hero.append(el('img', { class: 'ob-hero-gif', src: HERO_SRC, alt: '', decoding: 'async' }));
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
    const all = ob.agreed.every(Boolean);
    return [
      el('h1', { class: 'ob-h' }, t('ob_safe_title')), el('p', { class: 'ob-sub' }, t('ob_safe_sub')),
      el('div', { class: 'ob-how' }, items.map(([ic, key], i) => el('button', { class: `pledge${ob.agreed[i] ? ' on' : ''}`, role: 'checkbox', 'aria-checked': String(ob.agreed[i]),
        onclick: () => { ob.agreed[i] = !ob.agreed[i]; renderCard(); } },
        el('div', { class: 'ob-ic' }, icon(ic, 20)), el('span', {}, t(key)), el('div', { class: 'tick' }, ob.agreed[i] ? icon('check', 16) : null)))),
      el('button', { class: 'cta', disabled: !all, onclick: () => go(3) }, t('ob_agree')), back()];
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

  function renderCard() {
    const body = [stepWelcome, stepName, stepSafety, stepLocation][ob.step]();
    setKids(card, dots(), el('div', { class: 'ob-body' }, body));
    const input = card.querySelector('input.field');
    if (input && ob.step === 1 && !ob.name) input.focus({ preventScroll: true });
  }

  renderTop();
  renderCard();
}
