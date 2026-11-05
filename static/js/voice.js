// Shared narrator: server-side neural TTS (Edge/Gemini) with a browser
// speechSynthesis fallback. Both paths honour the same done/onEnd contract so
// callers can auto-advance and resume exactly where they paused.

window.Voice = (function () {
  let audioEl = null;
  let token = 0;
  let done = true;
  let rate = 1.0;
  let blocked = false;   // browser refused to play audio (no user gesture yet)
  let serverDownUntil = 0;  // /api/tts failed recently; go straight to the browser voice

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
    if (hasBrowser()) speechSynthesis.pause();
  }

  function resume() {
    if (audioEl) { const p = audioEl.play(); if (p && p.catch) p.catch(() => {}); return; }
    if (hasBrowser()) speechSynthesis.resume();
  }

  function browserSpeak(text, tk, onEnd) {
    if (!hasBrowser() || !text) { done = true; if (onEnd) onEnd(); return; }
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

  async function serverSpeak(text, tk, onEnd) {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (resp.status === 204) {          // server narrator is still generating this line
        browserSpeak(text, tk, onEnd);    // speak it now; don't blacklist the server
        return;
      }
      if (!resp.ok) throw new Error("tts " + resp.status);
      const blob = await resp.blob();
      if (tk !== token) return;                       // superseded while fetching
      stopAudio();
      audioEl = new Audio(URL.createObjectURL(blob));
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
    } catch (e) {
      if (tk !== token) return;
      serverDownUntil = Date.now() + 60000;            // don't pay the round-trip every step
      browserSpeak(text, tk, onEnd);                   // network / provider failure -> browser voice
    }
  }

  function speak(text, onEnd) {
    if (!text) { done = true; if (onEnd) onEnd(); return; }
    const tk = ++token;
    stopAudio();
    if (hasBrowser()) speechSynthesis.cancel();
    done = false;
    if (useServer() && Date.now() >= serverDownUntil) serverSpeak(text, tk, onEnd);
    else browserSpeak(text, tk, onEnd);
  }

  return {
    speak,
    cancel,
    pause,
    resume,
    setRate,
    isDone: () => done,
    isBlocked: () => blocked,
    available: () => useServer() || hasBrowser(),
  };
})();
