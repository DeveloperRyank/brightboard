// Lesson player: the board draws itself stroke by stroke while the narration
// types and speaks; steps auto-advance. Questions become a board interlude —
// the board clears to a question frame, the tutor's answer types into the
// narration strip and is spoken, then the lesson redraws and resumes at the
// exact character it paused on.

const SPEEDS = {
  slow: { cps: 26, pause: 1600, rate: 0.9, board: 1.0 },
  normal: { cps: 60, pause: 800, rate: 1.08, board: 1.8 },
  fast: { cps: 110, pause: 400, rate: 1.25, board: 2.6 },
};
const LOAD_MSGS = [
  "Reading the past papers…",
  "Sketching the apparatus…",
  "Sharpening the chalk…",
  "Planning the worked examples…",
  "Rehearsing what to say…",
];

let lesson = null;
let step = 0;
let charPos = 0;
let playing = true;
let asking = false;
let speed = "normal";
let typingDoneAt = null;
let timeline = null;
let boardT = 0;
let mode = "lesson"; // 'lesson' | 'qa'
let qaTimeline = null;
let qaBaseActions = null;
let qaT = 0;
let answerText = null;
let answerPos = 0;
let canvas, narrationEl;
let pollTimer = null;
let msgTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  canvas = document.getElementById("board");
  narrationEl = document.getElementById("narration");
  initBoard(canvas);

  document.getElementById("btn-prev").addEventListener("click", () => goto(step - 1));
  document.getElementById("btn-next").addEventListener("click", onNext);
  document.getElementById("btn-playpause").addEventListener("click", togglePlay);
  document.getElementById("speed").addEventListener("change", (e) => { speed = e.target.value; });
  document.getElementById("btn-ask").addEventListener("click", openAskPanel);
  document.getElementById("btn-cancel-ask").addEventListener("click", resumeLesson);
  document.getElementById("btn-resume-bar").addEventListener("click", resumeLesson);
  document.getElementById("btn-send-ask").addEventListener("click", sendQuestion);
  document.getElementById("btn-regenerate").addEventListener("click", regenerate);
  document.getElementById("btn-retry").addEventListener("click", regenerate);
  document.getElementById("btn-mic").addEventListener("click", toggleMic);
  document.getElementById("ask-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
  });
  let boardW = canvas.clientWidth;
  // The board box is fluid, so re-chalk the current frame whenever its real width
  // changes (rotation, split view, a different monitor's pixel ratio). The width
  // check stops the mobile URL bar sliding in and out from redrawing every frame.
  const onBoardResize = () => {
    if (Math.abs(canvas.clientWidth - boardW) < 2) return;
    boardW = canvas.clientWidth;
    initBoard(canvas);
    redrawCurrent();
  };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(onBoardResize).observe(canvas);
  window.addEventListener("resize", onBoardResize);
  window.addEventListener("orientationchange", () => setTimeout(onBoardResize, 250));
  window.addEventListener("beforeunload", voiceCancel);

  requestAnimationFrame(loop);

  const embedded = JSON.parse(document.getElementById("lesson-data").textContent || "null");
  if (INITIAL_STATUS === "ready" && embedded) {
    initPlayer(embedded);
  } else if (INITIAL_STATUS === "error") {
    showError(INITIAL_ERROR);
  } else {
    showLoading();
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
  let t = 0;
  const paint = () => {
    document.getElementById("load-title").textContent = LOAD_MSGS[t % LOAD_MSGS.length];
    const i = Math.min(t, lis.length - 1);
    for (let k = 0; k < lis.length; k++) {
      lis[k].className = k < i ? "done" : k === i ? "active" : "";
    }
  };
  paint();
  clearInterval(msgTimer);
  msgTimer = setInterval(() => { t++; paint(); }, 3000);
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

function pollStatus() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch("/api/lesson/" + encodeURIComponent(SUBJECT) + "/" + encodeURIComponent(TOPIC) + "/status");
      const data = await readJSON(resp);
      if (data.status === "ready") {
        const d = await fetch("/api/lesson/" + encodeURIComponent(SUBJECT) + "/" + encodeURIComponent(TOPIC) + "/data");
        const lessonData = await readJSON(d);
        // The lesson can vanish between the two answers (a regeneration clears it), and
        // a 404 here would hand initPlayer an undefined lesson and a permanently blank
        // board. Leave the poll running instead.
        if (!lessonData.lesson) return;
        clearInterval(pollTimer);
        hideLoading();
        initPlayer(lessonData.lesson);
      } else if (data.status === "error") {
        clearInterval(pollTimer);
        showError(data.error);
      }
    } catch (e) { /* server restarting; keep polling */ }
  }, 2500);
}

function initPlayer(lessonData) {
  lesson = lessonData;
  step = 0;
  mode = "lesson";
  asking = false;
  playing = true;
  hideLoading(); // reveal the controls whether we arrived via cache or via polling
  paintPlayButton();
  startStep(0);
}

// ---------- lesson playback ----------

function narrationText() {
  return lesson.steps[step].narration || "";
}

function stepConcept() {
  return lesson.steps[step].concept || null;
}

function conceptName(slug) {
  if (!slug) return null;
  return (typeof CONCEPT_NAMES !== "undefined" && CONCEPT_NAMES[slug]) ||
    slug.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function startStep(i) {
  step = Math.max(0, Math.min(lesson.steps.length - 1, i));
  charPos = 0;
  typingDoneAt = null;
  mode = "lesson";
  timeline = buildTimeline(lesson.steps[step]);
  boardT = 0;
  document.getElementById("lesson-done").style.display = "none";
  paintNarration();
  drawFrame(timeline, 0);
  speak(narrationText(), null);
}

function redrawCurrent() {
  if (mode === "qa" && qaTimeline) drawFrame(qaTimeline, qaT);
  else if (timeline) drawFrame(timeline, boardT);
}

// Frames follow the display instead of a 40 ms timer, and move by the time that
// actually passed: a timer that is throttled behind a long paint makes the drawing
// stutter, while real elapsed time keeps it at the same speed with fewer frames.
let lastFrame = null;

function loop(now) {
  const dt = lastFrame === null ? 0 : Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;
  tick(dt);
  requestAnimationFrame(loop);
}

function tick(dt) {
  // Frames start as soon as the page opens, while the lesson is still being written.
  if (!lesson) return;
  if (mode === "qa") {
    qaT += dt * SPEEDS[speed].board;
    drawFrame(qaTimeline, qaT);
    if (answerText !== null) {
      if (answerPos < answerText.length) {
        answerPos = Math.min(answerText.length, answerPos + SPEEDS[speed].cps * dt);
        narrationEl.textContent = answerText.slice(0, Math.floor(answerPos)) + "▌";
      } else {
        narrationEl.textContent = answerText;
      }
    }
    return;
  }
  if (asking || !playing) return;
  boardT += dt * SPEEDS[speed].board;
  drawFrame(timeline, boardT);
  const text = narrationText();
  if (charPos < text.length) {
    charPos = Math.min(text.length, charPos + SPEEDS[speed].cps * dt);
    if (charPos >= text.length) typingDoneAt = performance.now();
    paintNarration();
    return;
  }
  if (typingDoneAt === null) typingDoneAt = performance.now();
  if (!Voice.isDone()) return;
  if (timeline && boardT < timeline.total) return; // never advance mid-drawing
  if (performance.now() - typingDoneAt < SPEEDS[speed].pause) return;
  if (step < lesson.steps.length - 1) {
    startStep(step + 1);
  } else {
    playing = false;
    paintPlayButton();
    document.getElementById("lesson-done").style.display = "";
  }
}

function paintNarration() {
  const text = narrationText();
  const shown = text.slice(0, Math.floor(charPos));
  narrationEl.textContent = charPos < text.length ? shown + "▌" : shown;
  document.getElementById("step-counter").textContent =
    `Step ${step + 1} of ${lesson.steps.length}`;
  const chip = document.getElementById("concept-chip");
  const name = conceptName(stepConcept());
  chip.style.display = name ? "" : "none";
  chip.textContent = name || "";
  document.getElementById("btn-prev").disabled = step === 0;
  const next = document.getElementById("btn-next");
  next.textContent = step === lesson.steps.length - 1 ? "Enter the Exam Center →" : "Next →";
  next.classList.toggle("warn", step === lesson.steps.length - 1);
}

// ---------- voice output ----------
// Narration is delegated to the shared narrator in voice.js so the lesson player
// and the walkthrough player share one TTS stack (rate, cancellation, done).

let soundHintTimer = null;

function speak(text, onEnd) {
  Voice.setRate(SPEEDS[speed].rate);
  Voice.speak(text, onEnd);
  armSoundHint();
}

// Browsers refuse to play audio until the visitor has interacted with the page. The
// lesson must not wait for a button press, so it carries on silently — board drawing
// and typed narration keep going — and offers the voice back on the first tap.
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
    if (mode === "lesson" && !asking && playing) {
      speak(narrationText().slice(Math.floor(charPos)), null);
    }
  };
  document.addEventListener("pointerdown", enable);
  document.addEventListener("keydown", enable);
}

const voiceCancel = () => Voice.cancel();

function togglePlay() {
  playing = !playing;
  if (playing) Voice.resume();
  else Voice.pause();
  paintPlayButton();
}

function paintPlayButton() {
  document.getElementById("btn-playpause").textContent = playing ? "⏸ Pause" : "▶ Play";
}

function onNext() {
  if (step === lesson.steps.length - 1) window.location.href = EXAM_URL;
  else goto(step + 1);
}

function goto(i) {
  startStep(i);
  paintPlayButton();
}

// ---------- question interlude ----------

function wrapText(text, width) {
  const words = String(text).split(/\s+/);
  const lines = [];
  let line = "";
  for (const w of words) {
    const test = line ? line + " " + w : w;
    if (test.length > width && line) { lines.push(line); line = w; }
    else line = test;
  }
  if (line) lines.push(line);
  return lines;
}

// Lay the tutor's explanation on the board: the concept being taught plus the short
// board notes the model returned. The full spoken answer types into the narration
// strip, so the board carries the skeleton a student would copy into their exercise
// book — never a wall of text.
function buildExplanationActions(answer, points, conceptLabel) {
  const notes = (points && points.length)
    ? points.slice(0, 5)
    : wrapText(String(answer).match(/[^.!?]+[.!?]?/g)?.slice(0, 2).join(" ").trim() || "", 46).slice(0, 4);
  const title = conceptLabel ? `Tutor explains · ${conceptLabel}` : "Tutor explains";
  return [
    { type: "draw", glyph: "bulb", x: 830, y: 40, size: 140 },
    { type: "label", text: title.slice(0, 46), x: 70, y: 76, color: "#fde68a", size: 26 },
    { type: "draw", shape: "line", x1: 70, y1: 100, x2: 470, y2: 100, color: "#fde68a" },
    ...notes.slice(0, 5).map((note, i) => ({
      type: "label", text: "• " + String(note).slice(0, 52),
      x: 84, y: 168 + i * 62, color: "#e2e8f0", size: 22,
    })),
  ];
}

function openAskPanel() {
  asking = true;
  playing = false;
  voiceCancel();
  paintPlayButton();
  document.getElementById("ask-bar").classList.remove("open");
  const panel = document.getElementById("ask-panel");
  panel.classList.add("open");
  document.getElementById("paused-step").textContent = narrationText();
  document.getElementById("ask-input").value = "";
  document.getElementById("ask-input").focus();
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function sendQuestion() {
  const input = document.getElementById("ask-input");
  const question = input.value.trim();
  if (!question) return;
  stopMic();
  asking = true;
  mode = "qa";
  const concept = stepConcept();
  const conceptLabel = conceptName(concept);
  document.getElementById("ask-panel").classList.remove("open");
  const bar = document.getElementById("ask-bar");
  bar.classList.add("open");
  document.getElementById("ask-bar-q").textContent = "You asked: " + question;
  document.getElementById("ask-bar-state").textContent = "tutor is thinking…";
  narrationEl.textContent = "Tutor is thinking about your question…";

  qaBaseActions = [
    { type: "draw", glyph: "bulb", x: 430, y: 200, size: 180 },
    { type: "label", text: "Thinking about your question…", x: 300, y: 420, color: "#c4b5fd", size: 22 },
    ...(conceptLabel ? [{ type: "label", text: conceptLabel, x: 300, y: 470, color: "#7dd3fc", size: 20 }] : []),
  ];
  qaTimeline = buildTimeline({ actions: qaBaseActions });
  qaT = 0;
  answerText = null;
  answerPos = 0;

  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        subject: SUBJECT, topic: TOPIC, narration: narrationText(),
        question, concept,
      }),
    });
    const data = await readJSON(resp);
    if (!resp.ok) throw new Error(data.error || "Request failed");
    document.getElementById("ask-bar-state").textContent = "tutor is answering…";
    // the board now teaches the explanation; the narration strip types + speaks it
    qaTimeline = buildTimeline({
      actions: buildExplanationActions(data.answer, data.points, data.concept || conceptLabel),
    });
    qaT = 0;
    answerText = data.answer;
    answerPos = 0;
    speak(data.answer, resumeLesson); // when the tutor finishes saying it, the lesson resumes
  } catch (err) {
    document.getElementById("ask-bar-state").textContent = "";
    narrationEl.textContent = "Couldn't reach the tutor: " + err.message + " — resume when ready.";
    answerText = "";
  }
}

// Resume exactly where we paused: same step, same character position.
function resumeLesson() {
  if (!asking) return;
  asking = false;
  mode = "lesson";
  document.getElementById("ask-panel").classList.remove("open");
  document.getElementById("ask-bar").classList.remove("open");
  playing = true;
  paintPlayButton();
  timeline = buildTimeline(lesson.steps[step]); // board redraws the frame pen-style
  boardT = 0;
  paintNarration();
  const remaining = narrationText().slice(Math.floor(charPos));
  speak(remaining, null);
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
  document.getElementById("mic-state").textContent = "listening… tap 🎤 to stop";
}

function stopMic() {
  if (rec) { try { rec.stop(); } catch (e) {} }
}

// ---------- regenerate ----------

async function regenerate() {
  const btn = document.getElementById("btn-regenerate");
  btn.disabled = true;
  playing = false;
  voiceCancel();
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
