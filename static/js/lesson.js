// The lesson plays as one continuous video.
//
// One clock runs from the first stroke to the last, and the spoken line owns it: while the
// tutor is saying a sentence the clock reads the audio position, so the hand can neither
// race the words nor lag behind them, and whatever time is left in the sentence belongs to
// reading. Nothing animates on its own — the board is a pure function of the clock — which
// is why pause, scrub, replay and an interruption in mid-sentence are all the same three
// moves: stop the clock, move it, say the line from where it stopped.

// One number per setting: how fast the narrator speaks. The board is told in the same
// breath — the clock it runs on is the audio's own position, and the free-running seconds
// between sentences are converted at the same rate — so changing the speed moves the hand
// and the voice together and they cannot come apart. There is deliberately no separate ink
// multiplier: a lesson that would rather be written quickly is a lesson that was planned
// with too little to say.
const SPEEDS = {
  slow: 0.82,
  normal: 1.0,
  fast: 1.25,
};
const LOAD_MSGS = [
  "Reading the notes…",
  "Choosing the worked examples…",
  "Sharpening the pen…",
  "Setting out the board…",
  "Rehearsing what to say…",
];
const SEEK_STEP = 0.1;
// The width below which the board writes itself for the screen it is on rather than
// the wide sheet a desk gets. It is the breakpoint the layout of the page already moves at.
const SMALL_BOARD = window.matchMedia ? window.matchMedia("(max-width: 720px)") : null;

let wb = null;            // the Whiteboard engine
let plan = null;          // wb.planScript(): scenes, beats and one absolute clock
let beats = [];           // wb.schedule(): the lines to be spoken, in order
let lesson = null;        // the script as stored, for questions and for the cache
let loadSeq = 0;

let t = 0;                // the lesson clock, in seconds of board time
let playing = false;
let asking = false;
let scrubbing = false;
let speedKey = "normal";
let spoken = -1;          // index of the last line the voice has started
let voicing = null;       // {i, base, dur, wall} while a line is driving the clock

let pollTimer = null;
let msgTimer = null;
let soundHintTimer = null;
let startedAt = 0;
let doneShown = false;
let lastFrame = null;

let seekEl, nowEl, totalEl, captionEl, sheetEl, noteEl, playBtn;

// The board this tab has already seen for this topic, in this account.
const LESSON_CACHE = "lesson." + CACHE_SCOPE + "." + SUBJECT + "." + TOPIC;
let fromCache = null;      // painted from that cache, waiting for the real answer

document.addEventListener("DOMContentLoaded", () => {
  wb = initBoard(document.getElementById("board"));
  seekEl = document.getElementById("seek");
  nowEl = document.getElementById("t-now");
  totalEl = document.getElementById("t-total");
  captionEl = document.getElementById("caption");
  sheetEl = document.getElementById("sheet-title");
  noteEl = document.getElementById("board-note");
  playBtn = document.getElementById("btn-playpause");
  wireControls();

  requestAnimationFrame(loop);

  const embedded = JSON.parse(document.getElementById("lesson-data").textContent || "null");
  if (INITIAL_STATUS === "ready" && embedded) {
    cacheWrite(LESSON_CACHE, embedded);
    loadScript(embedded);
  } else if (INITIAL_STATUS === "error") {
    // A board from the last visit beats an error box over an empty canvas: the student
    // can still work, and the reason the refresh failed is said out loud. The server
    // sends its stored lesson here precisely because it is the one it could not improve.
    const remembered = embedded || cacheRead(LESSON_CACHE);
    if (remembered) {
      if (embedded) cacheWrite(LESSON_CACHE, embedded);
      loadScript(remembered);
      showNote("A newer version of this lesson could not be drawn — this is the one from " +
        "your last visit.");
    } else {
      showError(INITIAL_ERROR);
    }
  } else {
    // A lesson that is still being written should not leave the student staring at an
    // animation of nothing: the last board for this topic plays at once and the fresh
    // one takes over, but only while they are still on the first line.
    const remembered = cacheRead(LESSON_CACHE);
    if (remembered) {
      fromCache = remembered;
      loadScript(remembered);
    } else {
      showLoading();
    }
    pollStatus();
  }
});

// ---------- loading / generation polling ----------

function showLoading() {
  document.getElementById("loading").style.display = "";
  document.getElementById("load-error").style.display = "none";
  document.getElementById("lesson-controls").style.visibility = "hidden";
  const list = document.getElementById("load-steps");
  list.innerHTML = LOAD_MSGS.map((m) => `<li>${m.replace(/…+$/, "")}</li>`).join("");
  const lis = list.children;
  let n = 0;
  const paint = () => {
    document.getElementById("load-title").textContent = LOAD_MSGS[n % LOAD_MSGS.length];
    const i = Math.min(n, lis.length - 1);
    for (let k = 0; k < lis.length; k++) lis[k].className = k < i ? "done" : k === i ? "active" : "";
  };
  paint();
  clearInterval(msgTimer);
  msgTimer = setInterval(() => { n++; paint(); }, 3000);
}

function hideLoading() {
  clearInterval(msgTimer);
  document.getElementById("loading").style.display = "none";
  document.getElementById("load-error").style.display = "none";
  document.getElementById("lesson-controls").style.visibility = "visible";
}

function showError(msg) {
  clearInterval(msgTimer);
  document.getElementById("loading").style.display = "none";
  document.getElementById("load-error").style.display = "";
  document.getElementById("load-error-text").textContent =
    "The tutor couldn't draw this lesson: " + (msg || "unknown error");
}

function showNote(text) {
  if (!noteEl) return;
  noteEl.textContent = text;
  noteEl.hidden = false;
}

function pollStatus() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch("/api/lesson/" + encodeURIComponent(SUBJECT) + "/" + encodeURIComponent(TOPIC) + "/status");
      const data = await readJSON(resp);
      if (data.status === "ready") {
        const d = await fetch("/api/lesson/" + encodeURIComponent(SUBJECT) + "/" + encodeURIComponent(TOPIC) + "/data");
        const lessonData = await readJSON(d);
        // The lesson can vanish between the two answers (a regeneration clears it), and a
        // 404 here would leave the player with nothing to plan. Leave the poll running.
        if (!lessonData.lesson) return;
        clearInterval(pollTimer);
        cacheWrite(LESSON_CACHE, lessonData.lesson);
        if (fromCache) {
          const untouched = t < 1 && !asking;
          fromCache = null;
          if (untouched && JSON.stringify(lessonData.lesson) !== JSON.stringify(lesson)) {
            loadScript(lessonData.lesson);
          }
          return;
        }
        loadScript(lessonData.lesson);
      } else if (data.status === "error") {
        clearInterval(pollTimer);
        if (fromCache) {
          fromCache = null;
          showNote("The new version of this lesson could not be drawn, so this is the last one.");
        } else {
          showError(data.error);
        }
      }
    } catch (e) { /* server restarting; keep polling */ }
  }, 2500);
}

// ---------- from stored lesson to a playing board ----------

/**
 * Plan the stored script again without losing the student's place. Which rows a line
 * takes up is decided by the size of the hand, so a plan made before the hand arrived —
 * or before the screen was known — is worth making again rather than living with.
 */
function replan() {
  const at = t;
  plan = wb.planScript(wbScriptFrom(lesson));
  beats = wb.schedule();
  seekEl.max = String(plan.total.toFixed(1));
  totalEl.textContent = fmtTime(plan.total);
  seekTo(at, true);
}

async function loadScript(data) {
  const seq = ++loadSeq;
  Voice.cancel();
  voicing = null;
  lesson = data;
  wb.narrow(!!(SMALL_BOARD && SMALL_BOARD.matches));
  // Glyphs are measured, so the hand has to be on the machine before the plan is made.
  // The wait is capped because a font can fail to arrive at all, but a hand that lands
  // after the cap leaves a plan built on the fallback's widths, and lines that should
  // have wrapped are laid out as if they had room they do not have.
  let landed = false;
  const hand = wb.ready().then(() => { landed = true; });
  await Promise.race([hand, new Promise((r) => setTimeout(r, 1500))]);
  if (seq !== loadSeq) return;
  plan = wb.planScript(wbScriptFrom(data));
  beats = wb.schedule();
  t = 0;
  spoken = -1;
  doneShown = false;
  playing = true;
  startedAt = Date.now();
  document.getElementById("lesson-done").style.display = "none";
  seekEl.max = String(plan.total.toFixed(1));
  seekEl.step = String(SEEK_STEP);
  totalEl.textContent = fmtTime(plan.total);
  hideLoading();
  paintPlay();
  wb.render(0);
  beats.slice(0, 3).forEach((b) => Voice.preload(b.text));   // the opening lines, paid for
  startDue();
  if (!landed) hand.then(() => {
    if (seq !== loadSeq) return;
    replan();
  });
}

// ---------- the clock ----------

function loop(now) {
  const dt = lastFrame === null ? 0 : Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;
  tick(dt);
  requestAnimationFrame(loop);
}

function tick(dtWall) {
  if (!plan) return;
  if (playing && !asking && !scrubbing) advance(dtWall);
  wb.render(t);
  paintChrome();
}

function advance(dtWall) {
  if (voicing) {
    voicing.wall += dtWall;
    const patience = (voicing.dur + 10) / SPEEDS[speedKey];
    if (Voice.isDone() || voicing.wall > patience) voicing = null;
    else {
      // The voice is the clock: the board sits at the word being said, no further.
      t = Math.max(t, Math.min(plan.total, voicing.base + Voice.elapsed()));
      startDue();
      return;
    }
  }
  // Between sentences the clock free-runs, and it free-runs in the audio's own seconds:
  // wall time multiplied by the playback rate, so a second of reading at "fast" is the
  // same second of board the plan set aside, and the hand never gains on the voice.
  t = Math.min(plan.total, t + dtWall * SPEEDS[speedKey]);
  startDue();
  if (t >= plan.total - 0.005 && !voicing) finish();
}

/** The last line that had begun by this instant, or -1 before the first. */
function beatAt(time) {
  for (let i = beats.length - 1; i >= 0; i--) if (beats[i].at <= time + 1e-6) return i;
  return -1;
}

function startDue() {
  if (voicing || asking) return;
  const cur = beatAt(t);
  if (cur <= spoken) return;
  spoken = cur;
  speakBeat(cur, Math.max(0, t - beats[cur].at));
}

function speakBeat(i, offset) {
  const b = beats[i];
  const part = offset > 0.05 ? remainder(b.text, offset / (b.speech || b.dur)) : b.text;
  voicing = { i, base: b.at + offset, dur: Math.max(0.3, b.dur - offset), wall: 0 };
  if (!part || !Voice.available()) { voicing = null; return; }
  Voice.setRate(SPEEDS[speedKey]);
  Voice.speak(part);
  // Stay three lines ahead of the pen: a line the server has already voiced plays from
  // cache, and one it hasn't gets begun now while there is still reading time left.
  for (let k = 1; k <= 3; k++) Voice.preload(beats[i + k] ? beats[i + k].text : "");
  armSoundHint();
}

/** The part of a line that has not been said yet, cut at a word. */
function remainder(text, frac) {
  const f = Math.min(0.95, Math.max(0, frac));
  const cut = Math.round(text.length * f);
  if (cut <= 0) return text;
  const space = text.indexOf(" ", cut);
  return text.slice(space > 0 && space < cut + 24 ? space + 1 : cut);
}

function finish() {
  playing = false;
  voicing = null;
  Voice.cancel();
  paintPlay();
  showDone();
}

// ---------- moving the clock ----------

function seekTo(time, speak) {
  t = Math.max(0, Math.min(plan ? plan.total : 0, time));
  voicing = null;
  Voice.cancel();
  spoken = beatAt(t) - 1;
  if (speak !== false && playing && !asking) startDue();
  if (wb.plan) wb.render(t);
  paintChrome();
}

function setPlaying(on) {
  if (!plan || on === playing) return;
  // A press of play during a detour means "carry on with the lesson", not "freeze the
  // board with an answer card open" — the clock cannot advance while asking is set.
  if (on && asking) closeDetour();
  if (on && t >= plan.total - 0.005) seekTo(0, false);
  playing = on;
  if (on) {
    doneShown = false;
    document.getElementById("lesson-done").style.display = "none";
    Voice.resume();
    startDue();
  } else {
    Voice.pause();
  }
  paintPlay();
}

function lineJump(dir) {
  if (!plan) return;
  if (asking) closeDetour();
  const cur = beatAt(t);
  let target = 0;
  if (dir > 0) target = beats[cur + 1] ? beats[cur + 1].at : plan.total;
  else {
    // Twice on the same line means "the start of this one", like any video player.
    const b = beats[cur];
    if (b && t - b.at > 2) target = b.at;
    else target = beats[cur - 1] ? beats[cur - 1].at : 0;
  }
  seekTo(target, true);
}

function fmtTime(s) {
  const secs = Math.max(0, Math.round(s));
  return Math.floor(secs / 60) + ":" + String(secs % 60).padStart(2, "0");
}

// ---------- what the student sees ----------

function paintChrome() {
  if (!plan) return;
  if (!scrubbing) seekEl.value = t.toFixed(1);
  nowEl.textContent = fmtTime(t);
  const cue = wb.cue(t);
  const line = cue && cue.kind === "say" ? cue.text : "";
  if (captionEl.textContent !== line) captionEl.textContent = line;
  const sheet = wb.cue(t, "caption");
  const title = sheet && sheet.kind === "caption" ? sheet.text : "";
  if (sheetEl.textContent !== title) sheetEl.textContent = title;
  if (line && !noteEl.hidden) noteEl.hidden = true;
}

function paintPlay() {
  playBtn.innerHTML = iconSvg(playing ? "pause" : "play") + (playing ? " Pause" : " Play");
  playBtn.setAttribute("aria-pressed", playing ? "true" : "false");
}

// ---------- the reward for reaching the last line ----------

function showDone() {
  const panel = document.getElementById("lesson-done");
  if (!panel || doneShown) return;
  doneShown = true;
  const secs = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
  document.getElementById("done-steps").textContent = String(plan ? plan.scenes.length : 0);
  document.getElementById("done-concepts").textContent = String(beats.length);
  document.getElementById("done-time").textContent =
    secs < 60 ? secs + "s" : Math.round(secs / 60) + " min";
  panel.style.display = "";
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------- controls ----------

function wireControls() {
  playBtn.addEventListener("click", () => setPlaying(!playing));
  document.getElementById("speed").addEventListener("change", (e) => {
    speedKey = e.target.value;
    Voice.setRate(SPEEDS[speedKey]);
  });
  document.getElementById("btn-replay").addEventListener("click", () => {
    closeDetour();
    playing = true;
    paintPlay();
    seekTo(0, true);
  });
  document.getElementById("btn-ask").addEventListener("click", openAskPanel);
  document.getElementById("btn-cancel-ask").addEventListener("click", resumeLesson);
  document.getElementById("btn-resume-bar").addEventListener("click", resumeLesson);
  document.getElementById("btn-send-ask").addEventListener("click", sendQuestion);
  document.getElementById("btn-regenerate").addEventListener("click", regenerate);
  document.getElementById("btn-retry").addEventListener("click", regenerate);
  document.getElementById("done-replay").addEventListener("click", () => {
    playing = true;
    paintPlay();
    seekTo(0, true);
    document.getElementById("board").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  document.getElementById("btn-mic").addEventListener("click", toggleMic);
  document.getElementById("ask-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
  });

  seekEl.addEventListener("pointerdown", () => { scrubbing = true; Voice.cancel(); voicing = null; });
  seekEl.addEventListener("input", () => {
    scrubbing = true;
    t = Number(seekEl.value) || 0;
    if (wb.plan) wb.render(t);
    paintChrome();
  });
  ["change", "pointerup", "pointercancel", "keyup", "blur"].forEach((ev) =>
    seekEl.addEventListener(ev, () => {
      if (!scrubbing) return;
      scrubbing = false;
      // Moving the playhead is leaving the paused instant, so the answer goes with it.
      if (asking) closeDetour();
      seekTo(Number(seekEl.value) || 0, true);
    }));

  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(tag)) return;
    if (e.code === "Space") { e.preventDefault(); setPlaying(!playing); }
    else if (e.key === "ArrowRight") { e.preventDefault(); lineJump(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); lineJump(-1); }
    else if (e.key === "?" || e.key.toLowerCase() === "a") { e.preventDefault(); openAskPanel(); }
  });
  if (SMALL_BOARD) SMALL_BOARD.addEventListener("change", () => {
    // Turning a phone sideways, or dragging a desktop window narrow, moves the board
    // between the two sheets it writes on. The ink is the same; its rows are not.
    if (wb && wb.narrow(SMALL_BOARD.matches) && lesson) replan();
  });
  window.addEventListener("beforeunload", () => Voice.cancel());
}

// ---------- sound ----------

// Browsers refuse to play audio until the visitor has interacted with the page. The
// lesson must not wait for a button press, so the hand keeps moving silently — and the
// sentence the student missed is said again on the first tap.
function armSoundHint() {
  clearTimeout(soundHintTimer);
  soundHintTimer = setTimeout(() => { if (Voice.isBlocked()) showSoundHint(); }, 2200);
}

function showSoundHint() {
  const hint = document.getElementById("sound-hint");
  if (!hint || hint.classList.contains("show")) return;
  hint.classList.add("show");
  const enable = () => {
    hint.classList.remove("show");
    document.removeEventListener("pointerdown", enable);
    document.removeEventListener("keydown", enable);
    if (playing && !asking) seekTo(t, true);
  };
  document.addEventListener("pointerdown", enable);
  document.addEventListener("keydown", enable);
}

// ---------- stopping to ask ----------

function currentLine() {
  const cue = wb.cue(t);
  return cue && cue.kind === "say" ? cue.text : "";
}

function openAskPanel() {
  if (!plan) return;
  asking = true;
  playing = false;
  voicing = null;
  clearTimeout(answerWait);
  Voice.cancel();
  paintPlay();
  closeAsk();
  document.getElementById("tutor-card").style.display = "none";
  document.getElementById("ask-bar").classList.remove("open");
  const panel = document.getElementById("ask-panel");
  panel.classList.add("open");
  document.getElementById("paused-step").textContent = currentLine() || "the board as it stands";
  document.getElementById("ask-input").value = "";
  document.getElementById("ask-input").focus();
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeAsk() {
  document.getElementById("ask-panel").classList.remove("open");
}

async function sendQuestion() {
  const question = document.getElementById("ask-input").value.trim();
  if (!question) return;
  stopMic();
  closeAsk();
  const bar = document.getElementById("ask-bar");
  bar.classList.add("open");
  document.getElementById("ask-bar-q").textContent = "You asked: " + question;
  document.getElementById("ask-bar-state").textContent = "the tutor is answering…";
  const card = document.getElementById("tutor-card");
  card.style.display = "";
  card.className = "tutor-card";
  card.innerHTML = '<div class="tc-eyebrow">Tutor</div><div class="tc-ask">“' +
    escapeHtml(question) + '”</div><div class="tc-wait">Looking at your board…</div>';
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        subject: SUBJECT, topic: TOPIC, narration: currentLine(),
        question, concept: currentConcept(),
      }),
    });
    const data = await readJSON(resp);
    if (!resp.ok) throw new Error(data.error || "Request failed");
    document.getElementById("ask-bar-state").textContent = "";
    renderAnswer(data, question);
  } catch (err) {
    document.getElementById("ask-bar-state").textContent = "";
    card.innerHTML = '<div class="tc-eyebrow">Tutor</div><div class="tc-ask">“' +
      escapeHtml(question) + '”</div><div class="tc-body">Couldn’t reach the tutor: ' +
      escapeHtml(err.message) + "</div>";
  }
}

// The answer is a card, not a drawing: what is on the board stays on the board, and the
// tutor's detour sits under it where it can be read at leisure.
function renderAnswer(data, question) {
  const points = (data.points || []).filter(Boolean);
  const card = document.getElementById("tutor-card");
  card.className = "tutor-card";
  card.innerHTML =
    '<div class="tc-eyebrow">' + (data.concept ? escapeHtml(data.concept) : "Tutor") + "</div>" +
    '<div class="tc-ask">“' + escapeHtml(question) + '”</div>' +
    '<p class="tc-body">' + escapeHtml(data.answer || "") + "</p>" +
    (points.length ? '<ul class="tc-notes">' + points.map((p) =>
      "<li>" + escapeHtml(p) + "</li>").join("") + "</ul>" : "") +
    '<div class="tc-actions"><button class="btn" id="tc-resume">Back to the lesson</button>' +
    '<button class="btn ghost" id="tc-again">Ask something else</button></div>';
  document.getElementById("tc-resume").addEventListener("click", resumeLesson);
  document.getElementById("tc-again").addEventListener("click", () => {
    stopAnswer();
    card.style.display = "none";
    openAskPanel();
  });
  // Say it, then carry on from the exact word the board stopped on.
  voicing = null;
  Voice.setRate(SPEEDS[speedKey]);
  if (data.answer && Voice.available()) Voice.speak(data.answer);
  // A student who has read the answer should not have to wait for the voice, but the
  // lesson has to pick up where it left off, so the wait ends with the sentence.
  waitVoice(() => { if (asking) resumeLesson(); });
}

let answerWait = null;

function stopAnswer() {
  clearTimeout(answerWait);
  answerWait = null;
  Voice.cancel();
}

function waitVoice(then) {
  const deadline = Date.now() + 90000;
  const poll = () => {
    if (Voice.isDone() || Date.now() > deadline) { answerWait = null; then(); return; }
    answerWait = setTimeout(poll, 260);
  };
  poll();
}

function closeDetour() {
  stopAnswer();
  asking = false;
  closeAsk();
  document.getElementById("ask-bar").classList.remove("open");
  document.getElementById("tutor-card").style.display = "none";
}

function resumeLesson() {
  closeDetour();
  playing = true;
  paintPlay();
  seekTo(t, true);   // the same instant, the same half-said sentence
}

function currentConcept() {
  // Legacy lessons tag steps with a concept slug; scripts name their sheets instead.
  const steps = (lesson && lesson.steps) || [];
  for (let i = steps.length - 1; i >= 0; i--) if (steps[i] && steps[i].concept) return steps[i].concept;
  return null;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- voice input ----------

let rec = null;
let micBase = "";

function toggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    document.getElementById("mic-state").textContent = "voice input isn't supported in this browser";
    return;
  }
  if (rec) { rec.stop(); return; }
  rec = new SR();
  rec.lang = "en-GB";
  rec.interimResults = true;
  rec.continuous = false;
  micBase = document.getElementById("ask-input").value;
  if (micBase && !micBase.endsWith(" ")) micBase += " ";
  rec.onresult = (e) => {
    let text = "";
    for (const r of e.results) text += r[0].transcript;
    document.getElementById("ask-input").value = micBase + text;
  };
  rec.onend = () => {
    rec = null;
    document.getElementById("btn-mic").classList.remove("listening");
    document.getElementById("mic-state").textContent = "";
  };
  rec.start();
  document.getElementById("btn-mic").classList.add("listening");
  document.getElementById("mic-state").textContent = "listening… tap the microphone again to stop";
}

function stopMic() {
  if (rec) { try { rec.stop(); } catch (e) {} }
}

// ---------- regenerate ----------

async function regenerate() {
  const btn = document.getElementById("btn-regenerate");
  btn.disabled = true;
  playing = false;
  voicing = null;
  Voice.cancel();
  // The student has just rejected this board: it must not come back as the cached
  // copy on the next visit, and it must not be swapped in underneath the redraw.
  fromCache = null;
  cacheErase(LESSON_CACHE);
  showLoading();
  try {
    const resp = await fetch("/api/lesson/" + encodeURIComponent(SUBJECT) + "/" + encodeURIComponent(TOPIC) + "/regenerate", { method: "POST", headers: csrfHeaders() });
    if (!resp.ok) throw new Error("regeneration failed");
    pollStatus();
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
  }
}
