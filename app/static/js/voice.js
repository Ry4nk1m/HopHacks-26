// Reads mission text out loud, using ElevenLabs audio when available or the browser's built-in voice otherwise.

import { S } from './state.js';
import { api } from './api.js';

let audio = null;
let seq = 0; // bumped on every stop/speak so old, in-flight requests know to give up
let inflight = null;
const cache = new Map();

// Stop any voice playback in progress, whether it is ElevenLabs audio or browser speech.
export function stopVoice() {
  seq += 1;
  if (inflight) { inflight.abort(); inflight = null; }
  if (audio) { audio.pause(); audio = null; }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
}

/** Speak with ElevenLabs when configured, else the browser's own voice. */
export async function speak(text, force) {
  if (!text || (!force && !S.voice)) return;
  stopVoice();
  const mine = seq;
  if (S.cfg && S.cfg.tts) {
    const ctl = new AbortController();
    inflight = ctl;
    try {
      let blob = cache.get(text);
      if (!blob) {
        const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }), signal: ctl.signal });
        if (r.ok) {
          blob = await r.blob();
          if (cache.size >= 20) cache.delete(cache.keys().next().value);
          cache.set(text, blob);
        }
      }
      if (blob) {
        if (mine !== seq) return;
        const url = URL.createObjectURL(blob);
        const a = new Audio(url);
        a.onended = () => URL.revokeObjectURL(url);
        audio = a;
        await a.play();
        return;
      }
    } catch (e) {
      if (mine !== seq) return;
    } finally {
      if (inflight === ctl) inflight = null;
    }
  }
  if (mine !== seq) return;
  if ('speechSynthesis' in window) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'en-US';
    window.speechSynthesis.speak(u);
  }
}
