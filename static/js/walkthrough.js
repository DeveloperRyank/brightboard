// Guided paper walkthrough: one real question from the student's own uploaded paper
// at a time. The board teaches the idea behind that question (3-5 steps, drawn
// stroke by stroke and spoken), then the student either moves on or asks for a
// different explanation of the same question.

const SPEEDS = {
  slow: { cps: 26, pause: 1600, rate: 0.9, board: 1.0 },
  normal: { cps: 60, pause: 800, rate: 1.08, board: 1.8 },
  fast: { cps: 110, pause: 400, rate: 1.25, board: 2.6 },
};

let questions = [];
let idx = 0;
let attempt = 1;
let lesson = null;
let step = 0;
let charPos = 0;
let playing = false;
let ready = false;     // an explanation is on screen and awaiting the student's call
let speed = "normal";
let timeline = null;
let boardT = 0;
let typingDoneAt = null;
let canvas, narrationEl;
let loadTimer = null;
let soundHintTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  questions = JSON.parse(document.getElementById("questions-data").textContent || "[]");
  idx = Math.min(START_INDEX, Math.max(0, questions.length - 1));

  canvas = document.getElementById("board");
  narrationEl = document.getElementById("narration");
  initBoard(canvas);

  document.getElementById("btn-playpause").addEventListener("click", togglePlay);
  document.getElementById("btn-prev").addEventListener("click", () => goto(step - 1));
  document.getElementById("btn-next").addEventListener("click", () => goto(step + 1));
  document.getElementById("speed").addEventListener("change", (e) => { speed = e.target.value; });
  document.getElementById("btn-got").addEventListener("click", gotIt);
  document.getElementById("btn-diff").addEventListener("click", explainDifferently);
  document.getElementById("btn-retry").addEventListener("click", loadExplanation);
  let boardW = canvas.clientWidth;
  // The board box is fluid, so re-chalk the current frame whenever its real width
  // changes (rotation, split view, a different monitor's pixel ratio). The width
  // check stops the mobile URL bar sliding in and out from redrawing every frame.
  const onBoardResize = () => {
    if (Math.abs(canvas.clientWidth - boardW) < 2) return;
    boardW = canvas.clientWidth;
    initBoard(canvas);
    if (timeline) drawFrame(timeline, boardT);
  };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(onBoardResize).observe(canvas);
  window.addEventListener("resize", onBoardResize);
  window.addEventListener("orientationchange", () => setTimeout(onBoardResize, 250));
  window.addEventListener("beforeunload", () => Voice.cancel());

  requestAnimationFrame(loop);
  paintQuestion();
  loadExplanation();
});

let lastFrame = null;

function loop(now) {
  const dt = lastFrame === null ? 0 : Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;
  tick(dt);
  requestAnimationFrame(loop);
}

// ---------- getting the next explanation ----------

async function loadExplanation() {
  Voice.cancel();
  ready = false;
  lesson = null;
  setDecideEnabled(false);
  showLoading(attempt > 1
    ? "Rethinking this one — a different angle…"
    : "Reading the question on your paper…");
  document.getElementById("load-sub").textContent = attempt > 1
    ? "The tutor is rebuilding the board from a different starting point."
    : "The tutor is preparing the board for this question — the first draw takes up to a minute.";

  try {
    const resp = await fetch(`/api/walkthrough/${WT_ID}/step`, {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ index: idx, attempt }),
    });
    const data = await readJSON(resp);
    if (!resp.ok) throw new Error(data.error || "The tutor couldn't prepare this question");
    // An answer with no lesson in it would start the player on an empty board.
    if (!data.lesson) throw new Error("the tutor sent back an empty board");
    hideLoading();
    startExplanation(data.lesson);
    prefetchNext();
  } catch (err) {
    showError(err.message);
  }
}

// The student is never waiting on a board they could have had built already: while they
// read this question, the next one is generated in the background and stored, so "got it"
// moves to a drawn board instead of a spinner.
function prefetchNext() {
  const next = idx + 1;
  if (next >= questions.length) return;
  fetch(`/api/walkthrough/${WT_ID}/step`, {
    method: "POST",
    headers: csrfHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ index: next, attempt: 1 }),
  }).catch(() => {});
}

function startExplanation(lessonData) {
  lesson = lessonData;
  playing = true;
  ready = false;
  hideLoading();
  paintPlayButton();
  document.getElementById("wt-controls").style.visibility = "visible";
  startStep(0);
}

// ---------- playback of one explanation ----------

function narrationText() {
  return (lesson && lesson.steps[step] && lesson.steps[step].narration) || "";
}

function startStep(i) {
  step = Math.max(0, Math.min(lesson.steps.length - 1, i));
  charPos = 0;
  typingDoneAt = null;
  timeline = buildTimeline(lesson.steps[step]);
  boardT = 0;
  paintStep();
  drawFrame(timeline, 0);
  speak(narrationText());
}

function goto(i) {
  if (!lesson) return;
  if (i >= lesson.steps.length) { finishExplanation(); return; }
  if (i < 0) return;
  Voice.cancel();
  startStep(i);
}

function tick(dt) {
  if (!lesson || !playing) return;
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
  if (step < lesson.steps.length - 1) startStep(step + 1);
  else finishExplanation();
}

function finishExplanation() {
  playing = false;
  ready = true;
  paintPlayButton();
  setDecideEnabled(true);
  document.getElementById("decide-note").textContent =
    idx === questions.length - 1
      ? "Last question on this paper — got it, and we're done."
      : "Say if it landed, or ask for a different explanation of this same question.";
  document.getElementById("decide").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function paintNarration() {
  const text = narrationText();
  const shown = text.slice(0, Math.floor(charPos));
  narrationEl.textContent = charPos < text.length ? shown + "▌" : shown;
}

function paintStep() {
  document.getElementById("step-counter").textContent =
    `Step ${step + 1} of ${lesson.steps.length}`;
  document.getElementById("btn-prev").disabled = step === 0;
  const next = document.getElementById("btn-next");
  next.textContent = step === lesson.steps.length - 1 ? "Finish this question →" : "Next step →";
}

// ---------- the question on screen ----------

function paintQuestion() {
  const q = questions[idx] || {};
  document.getElementById("wt-now").textContent = idx + 1;
  document.getElementById("wt-fill").style.width = `${(idx / questions.length) * 100}%`;
  document.getElementById("wt-topic").textContent = q.topic_name || "";
  document.getElementById("q-number").textContent = q.number ? `Question ${q.number}` : "Question";
  const marks = document.getElementById("q-marks");
  marks.textContent = q.marks ? `${q.marks} mark${q.marks === 1 ? "" : "s"}` : "";
  marks.style.display = q.marks ? "" : "none";
  const concept = document.getElementById("q-concept");
  concept.textContent = q.concept_name || "";
  concept.style.display = q.concept_name ? "" : "none";
  document.getElementById("q-text").textContent = q.text || "";
  document.getElementById("wt-done").style.display = "none";
  setDecideEnabled(false);
  document.getElementById("decide-note").textContent =
    "The tutor finishes this explanation, then you decide.";
}

// ---------- the student's call ----------

async function gotIt() {
  if (!ready) return;
  setDecideEnabled(false);
  Voice.cancel();
  try {
    const resp = await fetch(`/api/walkthrough/${WT_ID}/advance`, {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ index: idx, understood: true }),
    });
    const data = await readJSON(resp);
    if (!resp.ok) throw new Error(data.error || "Couldn't save your progress");
    if (data.done) {
      document.getElementById("wt-fill").style.width = "100%";
      document.getElementById("decide").style.display = "none";
      document.getElementById("wt-done").style.display = "";
      narrationEl.textContent = "That's the whole paper. Your concept mastery has been updated — check your analytics whenever you like.";
      return;
    }
    idx = data.next_index;
    attempt = 1;
    paintQuestion();
    loadExplanation();
  } catch (err) {
    setDecideEnabled(true);
    document.getElementById("decide-note").textContent = err.message + " — try again.";
  }
}

function explainDifferently() {
  if (!ready) return;
  attempt = Math.min(5, attempt + 1);
  Voice.cancel();
  paintQuestion();
  loadExplanation();
}

function setDecideEnabled(on) {
  document.getElementById("btn-got").disabled = !on;
  document.getElementById("btn-diff").disabled = !on;
}

// ---------- voice output ----------

function speak(text) {
  Voice.setRate(SPEEDS[speed].rate);
  Voice.speak(text);
  armSoundHint();
}

// Audio needs a user gesture, and the walkthrough must not wait for one: playback
// carries on silently and offers the voice back on the first tap.
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
    if (playing && lesson) speak(narrationText().slice(Math.floor(charPos)));
  };
  document.addEventListener("pointerdown", enable);
  document.addEventListener("keydown", enable);
}

function togglePlay() {
  if (!lesson) return;
  playing = !playing;
  if (playing) Voice.resume();
  else Voice.pause();
  paintPlayButton();
}

function paintPlayButton() {
  document.getElementById("btn-playpause").textContent = playing ? "⏸ Pause" : "▶ Play";
}

// ---------- loading / error overlays ----------

function showLoading(title) {
  clearInterval(loadTimer);
  document.getElementById("loading").style.display = "";
  document.getElementById("load-error").style.display = "none";
  document.getElementById("load-title").textContent = title;
  document.getElementById("wt-controls").style.visibility = "hidden";
  narrationEl.textContent = "";
}

function hideLoading() {
  clearInterval(loadTimer);
  document.getElementById("loading").style.display = "none";
  document.getElementById("load-error").style.display = "none";
  document.getElementById("wt-controls").style.visibility = "visible";
}

function showError(msg) {
  hideLoading();
  document.getElementById("load-error").style.display = "";
  document.getElementById("load-error-text").textContent =
    "The tutor couldn't explain this question: " + msg;
}
