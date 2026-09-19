import { S } from './state.js';
import { api } from './api.js';

let audio = null;

export function stopVoice() {
  if (audio) { audio.pause(); audio = null; }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
}

/** Speak with ElevenLabs when configured, else the browser's own voice. */
export async function speak(text, force) {
  if (!text || (!force && !S.voice)) return;
  stopVoice();
  if (S.cfg && S.cfg.tts) {
    try {
      const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
      if (r.ok) {
        const url = URL.createObjectURL(await r.blob());
        const a = new Audio(url);
        a.onended = () => URL.revokeObjectURL(url);
        audio = a;
        await a.play();
        return;
      }
    } catch (e) { /* fall through to the browser voice */ }
  }
  if ('speechSynthesis' in window) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'en-US';
    window.speechSynthesis.speak(u);
  }
}
