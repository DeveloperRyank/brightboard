// Shared narrator: server-side neural TTS (Edge/Gemini) with a browser
// speechSynthesis fallback. Both paths honour the same done/onEnd contract so
// callers can auto-advance and resume exactly where they paused.
//
// elapsed() is what lets a player treat the voice as a clock rather than as an event:
// it reports how much of the sentence has actually been said, in sentence-seconds, so a
// whiteboard can be positioned against the words instead of against a timer that drifts.

window.Voice = (function () {
  let audioEl = null;
  let token = 0;
  let done = true;
  let rate = 1.0;
  let blocked = false;   // browser refused to play audio (no user gesture yet)
  let serverDownUntil = 0;  // /api/tts failed recently; go straight to the browser voice
  let speechPath = null;    // "clip" | "browser" | null — decides how elapsed() is measured
  let speechStart = 0;      // performance.now() when the current line began
  let pauseAt = 0;          // performance.now() when it was paused, 0 while talking
  let heard = 0;            // sentence-seconds reported so far, kept after the line ends

  const useServer = () => typeof SERVER_TTS !== "undefined" && SERVER_TTS;
  const hasBrowser = () => typeof window !== "undefined" && "speechSynthesis" in window;

  function setRate(r) {
    rate = r;
    if (audioEl) audioEl.playbackRate = r;
  }

  function stopAudio() {
    if (audioEl) {
      try { audioEl.pause(); audioEl.currentTime = 0; } catch (e) { /* already gone */ }
      audioEl.onended = null;
      audioEl.onerror = null;
      audioEl = null;
    }
  }

  function cancel() {
    token++;                       // invalidate any in-flight server fetch
    stopAudio();
    if (hasBrowser()) speechSynthesis.cancel();
    done = true;
  }

  function pause() {
    if (audioEl) { try { audioEl.pause(); } catch (e) { /* ignore */ } return; }
    pauseAt = performance.now();
    if (hasBrowser()) speechSynthesis.pause();
  }

  function resume() {
    if (audioEl) { const p = audioEl.play(); if (p && p.catch) p.catch(() => {}); return; }
    // The browser voice has no position to read, so its clock is wall time: freeze the
    // paused stretch out of it rather than letting the sentence run ahead of the ink.
    if (pauseAt) speechStart += performance.now() - pauseAt;
    pauseAt = 0;
    if (hasBrowser()) speechSynthesis.resume();
  }

  // ---------- how far the sentence has actually got ----------

  /** Sentence-seconds spoken so far — the number a board can be positioned against. */
  function elapsed() {
    if (speechPath === "clip") {
      if (audioEl && isFinite(audioEl.currentTime)) heard = audioEl.currentTime;
    } else if (speechPath === "browser" && !done) {
      heard = Math.max(heard, ((performance.now() - speechStart) / 1000) * rate);
    }
    return heard;
  }

  function beginLine() {
    speechPath = null;
    speechStart = performance.now();
    pauseAt = 0;
    heard = 0;
  }

  function browserSpeak(text, tk, onEnd) {
    if (!hasBrowser() || !text) { done = true; if (onEnd) onEnd(); return; }
    speechPath = "browser";
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = rate;
    const finish = () => { if (tk === token && !done) { done = true; if (onEnd) onEnd(); } };
    u.onend = finish;
    u.onerror = finish;
    speechSynthesis.speak(u);
    // Chrome drops speechSynthesis outright without a user gesture (and a few browsers
    // never fire onend). If nothing is actually talking shortly after, say so —
    // otherwise callers waiting on isDone() would hang forever.
    setTimeout(() => {
      if (tk !== token || done) return;
      if (!speechSynthesis.speaking) { blocked = true; finish(); }
    }, 1500);
  }

  // ---------- 2.5: a line already paid for is never fetched twice ----------
  // Scrubbing back, replaying a step and the auto-advance all ask for the same clips.
  // A dozen covers one lesson; the oldest is dropped when a newer one arrives.
  const CLIPS_KEPT = 12;
  const clips = new Map();          // narration text -> object URL

  function clipFor(text) {
    const url = clips.get(text);
    if (!url) return null;
    clips.delete(text);             // re-insert last so the map stays oldest-first
    clips.set(text, url);
    return url;
  }

  function rememberClip(text, url) {
    clips.set(text, url);
    while (clips.size > CLIPS_KEPT) {
      const oldest = clips.keys().next().value;
      URL.revokeObjectURL(clips.get(oldest));
      clips.delete(oldest);
    }
  }

  function playClip(url, text, tk, onEnd) {
    stopAudio();
    speechPath = "clip";
    audioEl = new Audio(url);
    audioEl.playbackRate = rate;
    const finish = () => { if (tk === token && !done) { done = true; audioEl = null; if (onEnd) onEnd(); } };
    audioEl.onended = finish;
    audioEl.onerror = () => { if (tk === token) { audioEl = null; browserSpeak(text, tk, onEnd); } };
    // A backgrounded or throttled tab can swallow the ended event; guard on the real
    // clip length so narration can never freeze the lesson.
    audioEl.onloadedmetadata = () => {
      const secs = isFinite(audioEl.duration) && audioEl.duration > 0 ? audioEl.duration : 45;
      setTimeout(finish, (secs * 1000) / rate + 5000);
    };
    const p = audioEl.play();
    if (p && p.then) {
      p.then(() => { if (tk === token) blocked = false; })
       .catch((e) => {
          if (tk !== token) return;
          if (e && e.name === "NotAllowedError") blocked = true;
          audioEl = null;
          browserSpeak(text, tk, onEnd);
        });
    }
  }

  async function serverSpeak(text, tk, onEnd) {
    const remembered = clipFor(text);
    if (remembered) { playClip(remembered, text, tk, onEnd); return; }
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ text }),
      });
      if (resp.status === 204) {          // server narrator is still generating this line
        browserSpeak(text, tk, onEnd);    // speak it now; don't blacklist the server
        return;
      }
      if (!resp.ok) throw new Error("tts " + resp.status);
      const blob = await resp.blob();
      if (tk !== token) return;                       // superseded while fetching
      const url = URL.createObjectURL(blob);
      rememberClip(text, url);
      playClip(url, text, tk, onEnd);
    } catch (e) {
      if (tk !== token) return;
      serverDownUntil = Date.now() + 60000;            // don't pay the round-trip every step
      browserSpeak(text, tk, onEnd);                   // network / provider failure -> browser voice
    }
  }

  function speak(text, onEnd) {
    if (!text) { done = true; speechPath = null; if (onEnd) onEnd(); return; }
    const tk = ++token;
    stopAudio();
    if (hasBrowser()) speechSynthesis.cancel();
    done = false;
    beginLine();
    if (useServer() && Date.now() >= serverDownUntil) serverSpeak(text, tk, onEnd);
    else browserSpeak(text, tk, onEnd);
  }

  // The line a scrub or a resume is about to need should already be paid for: a lesson
  // whose clock waits on a round trip is a lesson whose hand stops moving.
  const preloading = new Set();

  function preload(text) {
    const line = String(text || "").trim();
    if (!line || !useServer() || clips.has(line) || preloading.has(line)) return;
    if (Date.now() < serverDownUntil) return;
    preloading.add(line);
    fetch("/api/tts", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text: line }),
    }).then((resp) => {
      if (resp.status === 204) throw new Error("still warming");  // not worth a URL yet
      if (!resp.ok) throw new Error("tts " + resp.status);
      return resp.blob();
    }).then((blob) => {
      if (!clips.has(line)) rememberClip(line, URL.createObjectURL(blob));
    }).catch(() => { /* spoken later, fetched then */ })
      .then(() => { preloading.delete(line); });
  }

  return {
    speak,
    cancel,
    pause,
    resume,
    setRate,
    elapsed,
    preload,
    isDone: () => done,
    isBlocked: () => blocked,
    available: () => useServer() || hasBrowser(),
  };
})();
