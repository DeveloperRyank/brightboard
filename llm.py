"""LLM + TTS client.

Provider is chosen by LLM_PROVIDER in .env:
  - "gemini"     -> Google Gemini (GEMINI_API_KEY). Chat via generateContent with
                    JSON mode, plus neural text-to-speech. Free tier, deploys cleanly.
  - "openrouter" -> OpenRouter (OPENROUTER_API_KEY). Text chat only; no TTS.

Keys/models come from .env. Nothing is hardcoded.
"""

import base64
import hashlib
import io
import itertools
import json
import os
import queue
import re
import threading
import time
import wave

import requests
from dotenv import load_dotenv

load_dotenv()

PROVIDER = (os.environ.get("LLM_PROVIDER", "openrouter") or "openrouter").strip().lower()

# OpenRouter
OR_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODEL = os.environ.get("LLM_MODEL", "").strip()
OR_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()

# Google Gemini
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL", "") or "gemini-3.5-flash").strip()
GEMINI_TTS_MODEL = (os.environ.get("GEMINI_TTS_MODEL", "") or "gemini-3.8-flash-tts").strip()
GEMINI_VOICE = (os.environ.get("GEMINI_VOICE", "") or "Kore").strip()

# Gemini flash is a *thinking* model: it spends hidden reasoning tokens before it
# writes any visible output, which is the single biggest source of latency here.
# thinkingBudget=0 turns that off (fastest). Set GEMINI_THINKING_BUDGET=-1 in .env
# to omit the parameter entirely if a future model rejects it.
_tb = (os.environ.get("GEMINI_THINKING_BUDGET", "0") or "0").strip()
try:
    GEMINI_THINKING_BUDGET = int(_tb)
except ValueError:
    GEMINI_THINKING_BUDGET = 0

# Narration order: Kokoro (local ONNX, no key, no quota, works offline) first whenever
# models/ holds the v1.0 files, then Edge TTS (Microsoft neural, free) and Gemini TTS as
# hosted fallbacks. TTS_PROVIDER="edge" skips straight to the hosted pair, and
# KOKORO_VOICE picks one of its 54 voices (e.g. af_heart, af_bella, bf_emma, am_michael).
# Ava is one of Microsoft's conversational-class neural voices — noticeably more
# natural prosody than the older "General" voices (Sonia et al.), still free.
TTS_PROVIDER = (os.environ.get("TTS_PROVIDER", "edge") or "edge").strip().lower()
EDGE_VOICE = (os.environ.get("EDGE_TTS_VOICE", "") or "en-US-AvaNeural").strip()

# Backwards-compatible name used elsewhere in the app for error messages.
MODEL = GEMINI_MODEL if PROVIDER == "gemini" else OR_MODEL
API_KEY = GEMINI_API_KEY if PROVIDER == "gemini" else OR_API_KEY

# (voice, text) -> finished WAV, so repeats don't re-burn the TTS quota.
_TTS_CACHE = {}
# Circuit breaker: when the free-tier TTS quota is spent, every step would otherwise
# sit silent through a long retry before falling back to the browser voice. Trip the
# breaker on quota-429 so the lesson degrades to browser speech instantly, then probe
# again after an escalating cooldown in case the quota window resets.
_TTS_BREAKER_UNTIL = 0.0
_TTS_QUOTA_FAILS = 0

# Text-chat breaker: Gemini's free tier is small (20 requests/min, and a daily cap
# that can lock out for hours), and it also goes 5xx when Google is at capacity. When
# either happens we switch text calls to OpenRouter for a while, so the tutor degrades
# instead of dead-ending the student. Vision stays on Gemini.
_CHAT_SKIP_GEMINI_UNTIL = 0.0
_CHAT_FALLBACK_ANNOUNCED = False


class QuotaExhausted(RuntimeError):
    """The provider refused the call because a free-tier quota is spent."""

    def __init__(self, message, retry_in=None):
        super().__init__(message)
        self.retry_in = retry_in


class ProviderUnavailable(RuntimeError):
    """The provider is at capacity (5xx) — transient, and worth degrading around."""


def check_config():
    """Return a list of config problems, empty if ready to call the LLM."""
    problems = []
    if PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            problems.append(
                "GEMINI_API_KEY is missing. Get a free key at aistudio.google.com/apikey "
                "and add it to .env (LLM_PROVIDER=gemini)."
            )
    else:
        if not OR_API_KEY:
            problems.append(
                "OPENROUTER_API_KEY is missing. Copy .env.example to .env and paste your key from openrouter.ai/keys."
            )
        if not OR_MODEL:
            problems.append("LLM_MODEL is missing from .env (e.g. nvidia/nemotron-3-super-120b-a12b:free).")
    return problems


# ---------------- OpenRouter ----------------

def _or_headers():
    return {"Authorization": f"Bearer {OR_API_KEY}", "Content-Type": "application/json"}


def _openrouter_chat(system, user, temperature, max_tokens):
    payload = {
        "model": OR_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    last_error = None
    for attempt, wait in enumerate((0, 10, 30)):
        if wait:
            time.sleep(wait)
        resp = requests.post(OR_API_URL, headers=_or_headers(), json=payload, timeout=180)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices")
            if choices:
                return choices[0]["message"]["content"]
            last_error = f"LLM returned no usable completion: {str(data)[:300]}"
        else:
            last_error = f"LLM API error {resp.status_code}: {resp.text[:500]}"
            if resp.status_code not in (429, 500, 502, 503):
                break
            if resp.status_code == 429 and (
                "free-models-per-day" in resp.text or "openrouter_free_tier_daily" in resp.text
            ):
                raise RuntimeError(
                    "OpenRouter's free daily limit is used up (50 requests/day). "
                    "It resets at midnight UTC, or add $10 of credits at "
                    "openrouter.ai/settings/credits for 1,000 requests/day."
                )
    raise RuntimeError(last_error)


# ---------------- Google Gemini ----------------

def _gemini_headers():
    return {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}


def _retry_hint(resp):
    """Seconds the server asks us to wait: Retry-After header, or the
    'Please retry in ...' text Gemini puts in a free-tier quota 429 body
    (which mixes units, e.g. '8h18m11.574810442s')."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    m = re.search(r"retry in ([\dhms.]+)", resp.text or "", re.IGNORECASE)
    if not m:
        return None
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)\s*([hms])", m.group(1).lower()):
        total += float(value) * {"h": 3600.0, "m": 60.0, "s": 1.0}[unit]
    return total or None


def _gemini_chat(system, user, temperature, max_tokens, json_mode, file=None):
    url = f"{GEMINI_BASE}/{GEMINI_MODEL}:generateContent"
    gen_config = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if GEMINI_THINKING_BUDGET >= 0:
        gen_config["thinkingConfig"] = {"thinkingBudget": GEMINI_THINKING_BUDGET}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    user_parts = [{"text": user}]
    if file:  # inline PDF/image — Gemini reads text layers AND scans (vision/OCR)
        user_parts.append({"inlineData": {"mimeType": file["mime"], "data": file["data_b64"]}})
    body = {
        "contents": [{"role": "user", "parts": user_parts}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": gen_config,
    }
    last_error = None
    overloaded = False
    # With OpenRouter configured we can degrade instead of waiting a quota window out,
    # so one quick retry is plenty; without it, sit out the per-minute cap properly.
    waits = [0.0, 5.0] if _or_ready() else [0.0, 5.0, 15.0, 30.0]
    quota_hint = None
    for attempt in range(len(waits)):
        if waits[attempt]:
            time.sleep(waits[attempt])
        resp = requests.post(url, headers=_gemini_headers(), json=body, timeout=180)
        if resp.status_code == 200:
            data = resp.json()
            cands = data.get("candidates") or []
            if cands:
                parts = cands[0].get("content", {}).get("parts", []) or []
                text = "".join(p.get("text", "") for p in parts)
                if text.strip():
                    return text
            last_error = f"Gemini returned no usable completion: {str(data)[:300]}"
            continue
        last_error = f"Gemini API error {resp.status_code}: {resp.text[:500]}"
        if resp.status_code == 429:
            # Free tier is ~20 requests/minute. Honor the server's retry hint so the
            # next attempt lands after the quota window clears instead of failing early.
            hint = _retry_hint(resp)
            quota_hint = hint if hint is not None else 60.0
            if hint and hint > 90.0:
                # A daily/hourly cap, not the per-minute one: retrying here just burns
                # a minute of the student's time. Bail out so the caller can degrade.
                raise QuotaExhausted(last_error, retry_in=hint)
            if hint and attempt + 1 < len(waits):
                waits[attempt + 1] = min(60.0, max(waits[attempt + 1], hint + 1.0))
            continue
        if resp.status_code not in (500, 502, 503):
            break
        overloaded = True
    if quota_hint is not None:
        raise QuotaExhausted(last_error, retry_in=quota_hint)
    if overloaded:
        raise ProviderUnavailable(last_error)
    raise RuntimeError(last_error)


def _or_ready():
    return bool(OR_API_KEY and OR_MODEL)


def _announce_fallback(reason):
    global _CHAT_FALLBACK_ANNOUNCED
    if not _CHAT_FALLBACK_ANNOUNCED:
        _CHAT_FALLBACK_ANNOUNCED = True
        print(f"[llm] {reason} -> text calls fall back to OpenRouter ({OR_MODEL}); vision stays on Gemini")


def chat(system, user, temperature=0.4, max_tokens=3000, json_mode=False, file=None):
    """One chat completion. Returns the assistant text.

    `file` (Gemini only) is {"mime": str, "data_b64": str} for an inline PDF/image,
    letting the model read uploaded papers — text layers and scans alike.

    When Gemini's free-tier quota is spent, text calls quietly continue on OpenRouter
    (if configured) rather than dead-ending the student; vision calls cannot.
    """
    problems = check_config()
    if problems:
        raise RuntimeError(" ".join(problems))
    if PROVIDER != "gemini":
        if file:
            raise RuntimeError("Document analysis needs LLM_PROVIDER=gemini (OpenRouter is text-only).")
        return _openrouter_chat(system, user, temperature, max_tokens)

    if file:
        try:
            return _gemini_chat(system, user, temperature, max_tokens, json_mode, file=file)
        except QuotaExhausted as exc:
            wait = f" (resets in about {int(exc.retry_in // 60)} min)" if exc.retry_in else ""
            raise RuntimeError(
                "Gemini's free quota is spent" + wait + ". Reading uploaded papers needs Gemini's "
                "vision — OpenRouter is text-only. Try again once the quota resets."
            ) from exc
        except ProviderUnavailable as exc:
            raise RuntimeError(
                "Gemini is at capacity right now (its API returned 503). Reading uploaded papers "
                "needs Gemini's vision, so there is no fallback for this one — try again in a minute."
            ) from exc

    global _CHAT_SKIP_GEMINI_UNTIL
    if _or_ready() and time.time() < _CHAT_SKIP_GEMINI_UNTIL:
        return _openrouter_chat(system, user, temperature, max_tokens)
    try:
        return _gemini_chat(system, user, temperature, max_tokens, json_mode)
    except QuotaExhausted as exc:
        if not _or_ready():
            raise
        # Skip straight to OpenRouter until the Gemini window should be open again.
        cooldown = min(max(exc.retry_in or 300.0, 60.0), 900.0)
        _CHAT_SKIP_GEMINI_UNTIL = time.time() + cooldown
        _announce_fallback("Gemini quota spent")
        return _openrouter_chat(system, user, temperature, max_tokens)
    except ProviderUnavailable as exc:
        if not _or_ready():
            raise
        _CHAT_SKIP_GEMINI_UNTIL = time.time() + 120.0
        _announce_fallback("Gemini is at capacity (503)")
        return _openrouter_chat(system, user, temperature, max_tokens)


# ---------------- Text-to-speech (Kokoro local + Edge + Gemini) ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KOKORO_MODEL = os.environ.get("KOKORO_MODEL_PATH", "").strip() or os.path.join(BASE_DIR, "models", "kokoro-v1.0.onnx")
KOKORO_VOICES = os.environ.get("KOKORO_VOICES_PATH", "").strip() or os.path.join(BASE_DIR, "models", "voices-v1.0.bin")
KOKORO_WAV_DIR = os.environ.get("KOKORO_WAV_DIR", "").strip() or os.path.join(BASE_DIR, "data", "tts_cache")
KOKORO_VOICE = (os.environ.get("KOKORO_VOICE", "") or "af_heart").strip()
# Kokoro-82M takes 1.6-2.4x as long to generate a line of narration as it does to hear
# it on this CPU, so a spoken step can't be synthesised on demand without dead air. A
# request therefore waits only this long for a line that is still being generated; the
# lesson prefetches the rest ahead of the playhead, and anything not ready in time is
# covered by the browser's own voice (see TtsWarming).
KOKORO_WAIT = float(os.environ.get("KOKORO_WAIT_SECONDS", "") or 3)
KOKORO_WORKERS = max(1, int(os.environ.get("KOKORO_WORKERS", "") or 4))


class TtsWarming(RuntimeError):
    """No cached narration for this line yet — speak it with the browser voice now and
    let the same line come back in the local narrator's voice once it's generated."""


_kokoro_engine = None
_kokoro_jobs = None      # PriorityQueue of (priority, seq, wav path), with worker threads
_KOKORO_LOCK = threading.Lock()
_KOKORO_SEQ = itertools.count()
_KOKORO_INFLIGHT = {}    # wav path -> {done, prio, text, voice, started}
_KOKORO_BROKEN = False   # model missing or the runtime can't load it: stop trying
_KOKORO_BREAKER_UNTIL = 0.0
_KOKORO_FAILS = 0
# A line the student is waiting on now outranks the ones prefetch queued ahead of it.
PLAYBACK_PRIORITY = 0
PREFETCH_PRIORITY = 5


def _kokoro_installed():
    return os.path.isfile(KOKORO_MODEL) and os.path.isfile(KOKORO_VOICES)


def _kokoro_ok():
    return (_kokoro_installed() and not _KOKORO_BROKEN
            and time.monotonic() >= _KOKORO_BREAKER_UNTIL)


def _kokoro():
    """The loaded ONNX pipeline, built once. ~4 s to load, so start_narrator() warms it
    at boot instead of charging it to the student's first spoken step."""
    global _kokoro_engine, _KOKORO_BROKEN
    if _kokoro_engine is not None:
        return _kokoro_engine
    with _KOKORO_LOCK:
        if _kokoro_engine is None:
            try:
                from kokoro_onnx import Kokoro
                _kokoro_engine = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
                print(f"[tts] Kokoro loaded (voice {KOKORO_VOICE})")
            except Exception as exc:
                _KOKORO_BROKEN = True
                print(f"[tts] Kokoro unavailable, falling back to hosted voices: {exc}")
                raise
    return _kokoro_engine


def _kokoro_failed():
    global _KOKORO_BREAKER_UNTIL, _KOKORO_FAILS
    _KOKORO_FAILS += 1
    _KOKORO_BREAKER_UNTIL = time.monotonic() + min(1800, 60 * (2 ** (_KOKORO_FAILS - 1)))


def _kokoro_wav_path(voice, text):
    digest = hashlib.sha1(f"{voice}|{text}".encode("utf-8", "replace")).hexdigest()
    return os.path.join(KOKORO_WAV_DIR, digest + ".wav")


def _kokoro_read(path):
    with open(path, "rb") as f:
        return f.read(), "audio/wav"


def _kokoro_queue():
    """The narrator's job queue and its worker threads, created on first use.

    A priority queue rather than a thread pool because the line a student is waiting on
    must jump in front of the ten the prefetch put there first.
    """
    global _kokoro_jobs, _kokoro_seq
    if _kokoro_jobs is None:
        _kokoro_jobs = queue.PriorityQueue()
        for _ in range(KOKORO_WORKERS):
            threading.Thread(target=_kokoro_worker, daemon=True, name="narrator").start()
    return _kokoro_jobs


def _kokoro_write_wav(path, text, voice):
    import numpy as np
    import soundfile as sf
    audio, sr = _kokoro().create(text, voice, speed=1.0)
    buf = io.BytesIO()
    sf.write(buf, np.asarray(audio, dtype="float32"), sr, format="WAV", subtype="PCM_16")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(buf.getvalue())


def _kokoro_worker():
    while True:
        _, _, path = _kokoro_queue().get()
        with _KOKORO_LOCK:
            job = _KOKORO_INFLIGHT.get(path)
            if job is None or job["started"] or os.path.isfile(path):
                continue  # another ticket for this line already claimed it
            job["started"] = True
            text, voice, done = job["text"], job["voice"], job["done"]
        try:
            _kokoro_write_wav(path, text, voice)
            global _KOKORO_FAILS
            _KOKORO_FAILS = 0
        except Exception as exc:
            print(f"[tts] Kokoro synthesis failed: {exc}")
            _kokoro_failed()
        finally:
            with _KOKORO_LOCK:
                _KOKORO_INFLIGHT.pop(path, None)
            done.set()


def _kokoro_schedule(text, voice, priority=PREFETCH_PRIORITY):
    """Queue a line unless it's already on disk, and hand back the Event that fires when
    it is. Re-requesting a queued line at a higher priority re-queues it at the front."""
    path = _kokoro_wav_path(voice, text)
    if os.path.isfile(path):
        return None
    with _KOKORO_LOCK:
        jobs = _kokoro_queue()
        seq = next(_KOKORO_SEQ)
        job = _KOKORO_INFLIGHT.get(path)
        if job is None:
            _KOKORO_INFLIGHT[path] = job = {
                "done": threading.Event(), "prio": priority,
                "text": text, "voice": voice, "started": False,
            }
            jobs.put((priority, seq, path))
        elif priority < job["prio"] and not job["started"]:
            job["prio"] = priority
            jobs.put((priority, seq, path))
        return job["done"]


def _kokoro_synth(text, voice):
    """Cached WAV if we have one; otherwise generate and wait up to KOKORO_WAIT for it."""
    if not _kokoro_ok():
        raise TtsWarming("local narrator unavailable")
    path = _kokoro_wav_path(voice, text)
    if not os.path.isfile(path):
        done = _kokoro_schedule(text, voice, PLAYBACK_PRIORITY)
        if done is None or not done.wait(KOKORO_WAIT) or not os.path.isfile(path):
            raise TtsWarming("narration still generating")
    return _kokoro_read(path)


def prefetch_narration(texts):
    """Speak-ahead: generate the coming steps' audio while the student watches step 1.

    Returns immediately. Narration that isn't cached by the time the playhead reaches it
    falls back to the browser voice (see TtsWarming), so this is pure upside.
    """
    if not _kokoro_ok():
        return
    for raw in texts or []:
        line = str(raw or "").strip()[:1000]  # same clamp as /api/tts, so keys match
        if line:
            _kokoro_schedule(line, KOKORO_VOICE)


def start_narrator():
    """Boot hook: pay the model load before any request needs it."""
    if _kokoro_ok() and _kokoro_engine is None:
        threading.Thread(target=_kokoro, daemon=True).start()


_EDGE_BREAKER_UNTIL = 0.0
_EDGE_FAILS = 0


def _edge_ok():
    """True when the edge-tts package is importable (no API key needed) and Microsoft's
    read-aloud endpoint isn't currently refusing us."""
    if time.monotonic() < _EDGE_BREAKER_UNTIL:
        return False
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


def _edge_failed():
    """Trip the Edge breaker. Microsoft 403s the endpoint whenever it rejects the
    client build, and retrying on every lesson step just adds seconds of dead air."""
    global _EDGE_BREAKER_UNTIL, _EDGE_FAILS
    _EDGE_FAILS += 1
    _EDGE_BREAKER_UNTIL = time.monotonic() + min(3600, 120 * (2 ** (_EDGE_FAILS - 1)))


def _edge_recovered():
    global _EDGE_BREAKER_UNTIL, _EDGE_FAILS
    _EDGE_BREAKER_UNTIL = 0.0
    _EDGE_FAILS = 0


def _gemini_tts_ok():
    return (PROVIDER == "gemini" and bool(GEMINI_API_KEY)
            and time.monotonic() >= _TTS_BREAKER_UNTIL)


def tts_available():
    """True when a server-side narrator can actually answer right now — the page uses
    this to decide whether the browser should even try /api/tts."""
    return _kokoro_ok() or _edge_ok() or _gemini_tts_ok()


def _pcm_to_wav(pcm, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _edge_synthesize(text, voice):
    """Return (mp3_bytes, "audio/mpeg") using Microsoft Edge neural voices.

    No API key and effectively unlimited requests. edge-tts is async, so we run a
    tiny collector loop synchronously (Flask handler threads are plain sync).
    """
    import asyncio
    import edge_tts

    async def _collect():
        chunks = []
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    mp3 = asyncio.run(_collect())
    if not mp3:
        raise RuntimeError("Edge TTS returned empty audio")
    return mp3, "audio/mpeg"


def _gemini_synthesize(text, voice):
    """Return (wav_bytes, "audio/wav") using Gemini TTS. Raises RuntimeError on failure.

    The free tier's TTS quota is tight and a lesson speaks one call per step, so it
    429s easily. Finished audio is cached so repeats don't re-burn quota, and a
    circuit breaker trips on quota-429 so the caller falls back to the next voice
    instantly instead of sitting silent through retries.
    """
    url = f"{GEMINI_BASE}/{GEMINI_TTS_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    global _TTS_BREAKER_UNTIL, _TTS_QUOTA_FAILS
    if time.monotonic() < _TTS_BREAKER_UNTIL:
        raise RuntimeError("Gemini TTS quota cooling down")
    last_error = None
    for attempt, wait in enumerate((0, 20)):  # one retry for transient 5xx only
        if wait:
            time.sleep(wait)
        resp = requests.post(url, headers=_gemini_headers(), json=body, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            try:
                part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            except (KeyError, IndexError, TypeError):
                raise RuntimeError(f"Gemini TTS returned no audio: {str(data)[:300]}")
            mime = part.get("mimeType", "") or ""
            pcm = base64.b64decode(part.get("data", "") or "")
            if not pcm:
                raise RuntimeError("Gemini TTS returned empty audio")
            m = re.search(r"rate=(\d+)", mime)
            rate = int(m.group(1)) if m else 24000
            _TTS_QUOTA_FAILS = 0
            return _pcm_to_wav(pcm, rate), "audio/wav"
        last_error = f"Gemini TTS error {resp.status_code}: {resp.text[:300]}"
        if resp.status_code == 429:
            # Quota won't clear mid-retry. Trip the breaker so the caller drops to the
            # next voice immediately instead of sitting silent, and probe later.
            _TTS_QUOTA_FAILS += 1
            _TTS_BREAKER_UNTIL = time.monotonic() + min(3600, 120 * (2 ** (_TTS_QUOTA_FAILS - 1)))
            break
        if resp.status_code not in (500, 502, 503):
            break
    raise RuntimeError(last_error or "Gemini TTS failed")


def synthesize(text, voice=None):
    """Return (audio_bytes, mimetype) for `text`, trying narrators in preference order.

    The local Kokoro narrator comes first whenever its models are present: cached lines
    answer instantly and nothing here costs a request. TtsWarming means "still
    generating" — other narrators get the chance, and if none can answer the caller
    should let the browser voice cover this step rather than stall the lesson.
    Each provider's output is cached so repeats don't re-fetch.
    """
    hosted = ("edge", "gemini") if TTS_PROVIDER == "edge" else ("gemini", "edge")
    order = (("kokoro",) if _kokoro_ok() else ()) + hosted
    warming = False
    errors = []
    for prov in order:
        if prov == "kokoro":
            # Its cache is the WAV file on disk, so there's nothing to hold in memory.
            try:
                return _kokoro_synth(text, KOKORO_VOICE)
            except TtsWarming:
                warming = True
                continue
            except Exception as exc:  # fall through to the next provider
                errors.append(f"kokoro: {exc}")
                continue
        if prov == "edge":
            if not _edge_ok():
                continue
            v = voice if (voice and not voice[0].isupper()) else EDGE_VOICE
            key = ("edge", v, text)
            if key in _TTS_CACHE:
                return _TTS_CACHE[key]
            try:
                audio = _edge_synthesize(text, v)
            except Exception as exc:  # fall through to the next provider
                _edge_failed()
                errors.append(f"edge: {exc}")
                continue
            _edge_recovered()
        else:
            if not _gemini_tts_ok():
                continue
            v = voice or GEMINI_VOICE
            key = ("gemini", v, text)
            if key in _TTS_CACHE:
                return _TTS_CACHE[key]
            try:
                audio = _gemini_synthesize(text, v)
            except Exception as exc:
                errors.append(f"gemini: {exc}")
                continue
        if len(_TTS_CACHE) < 64:  # bound memory; lessons are short-lived
            _TTS_CACHE[key] = audio
        return audio
    if warming:
        raise TtsWarming("local narrator is still generating this line")
    raise RuntimeError("TTS failed: " + (" | ".join(errors) if errors else "no provider available"))


# ---------------- JSON helpers ----------------

def extract_json(text):
    """Pull the first JSON object/array out of an LLM reply, tolerating code fences
    and the reasoning preamble some models emit."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No JSON found in LLM reply: {text[:300]}")


def chat_json(system, user, temperature=0.4, max_tokens=3000):
    return extract_json(chat(system, user, temperature, max_tokens, json_mode=True))


def chat_json_with_file(system, user, file, temperature=0.2, max_tokens=4000):
    """JSON-mode completion with an inline PDF/image (Gemini vision). `file` is
    {"mime": str, "data_b64": str}."""
    return extract_json(
        chat(system, user, temperature, max_tokens, json_mode=True, file=file)
    )
