import hashlib

import httpx

ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
MODEL_ID = "eleven_multilingual_v2"
MAX_CHARS = 1500


class TTSUnavailable(Exception):
    pass


def _reason(resp):
    try:
        detail = resp.json().get("detail")
        message = detail.get("message") if isinstance(detail, dict) else detail
        return f": {str(message)[:200]}" if message else ""
    except (ValueError, AttributeError):
        return ""


def synthesize(settings, text, lang="en", client=None):
    """Return MP3 bytes from ElevenLabs, cached on disk by (voice, text)."""
    if not settings.eleven_api_key:
        raise TTSUnavailable("ELEVENLABS_API_KEY is not set")
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        raise TTSUnavailable("empty text")

    cache_dir = settings.data_dir / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{settings.eleven_voice_id}|{MODEL_ID}|{text}".encode()).hexdigest()
    path = cache_dir / f"{key}.mp3"
    if path.exists():
        return path.read_bytes()

    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        resp = client.post(
            ENDPOINT.format(voice=settings.eleven_voice_id),
            headers={"xi-api-key": settings.eleven_api_key, "Accept": "audio/mpeg"},
            json={"text": text, "model_id": MODEL_ID},
        )
    except httpx.HTTPError as exc:
        raise TTSUnavailable(f"network error: {exc}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code != 200:
        raise TTSUnavailable(f"ElevenLabs returned HTTP {resp.status_code}{_reason(resp)}")
    path.write_bytes(resp.content)
    return resp.content
