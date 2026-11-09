// Exam Center: real past-paper questions, one at a time. A bank row that carries four
// options gets the choice widget and is marked against the answer key the moment the
// paper is handed in; anything else uses the working box for its command word (a
// calculation gets a working area plus a final answer, a "state" question one line).
// Drafts autosave to this device.

const MCQ_TYPE = { id: "mcq", widget: "mcq", label: "Multiple choice",
  hint: "Choose the single best answer, then move on — a stuck question costs more time than a wrong one." };
const TYPES = [
  { id: "calc", widget: "calc", label: "Calculation",
    re: /^(calculate|determine|work\s*out|compute|evaluate|find\s+(the|out))\b/i,
    hint: "Show every step, then give the final answer with its units." },
  { id: "equation", widget: "line", label: "Equation / formula",
    re: /^(write|state|give)\b[^.?]*\b(equation|formula|symbol|notation)\b/i,
    hint: "Balance it, and include state symbols (s), (l), (g), (aq).", mono: true },
  { id: "compare", widget: "long", label: "Comparison",
    re: /^(distinguish|differentiate|compare|contrast)\b/i,
    hint: "Give both sides: “X is …, whereas Y is …”." },
  { id: "explain", widget: "long", label: "Explanation",
    re: /^(explain|describe|discuss|account\s+for|justify|suggest|why|how)\b/i,
    hint: "Reason step by step and finish with the effect." },
  { id: "short", widget: "line", label: "Short answer",
    re: /^(state|name|give|list|identify|define|what|which)\b/i,
    hint: "One precise line — the keyword earns the mark." },
];
const FALLBACK_TYPE = { id: "long", widget: "long", label: "Answer",
  hint: "Write the full answer you would put on the exam paper." };

let questions = [];
let types = [];
let idx = 0;
let draft = { idx: 0, elapsed: 0, answers: {} };
let saveTimer = null;
let clockTimer = null;

const storageKey = () => `bb.exam.${SUBJECT}.${TOPIC}`;

document.addEventListener("DOMContentLoaded", () => {
  questions = JSON.parse(document.getElementById("questions-data").textContent || "[]");
  types = questions.map((q) => (q.options && q.options.length === 4 ? MCQ_TYPE : typeOf(q.question)));
  loadDraft();

  document.getElementById("btn-next").addEventListener("click", () => move(1));
  document.getElementById("btn-prev").addEventListener("click", () => move(-1));
  document.getElementById("btn-clear").addEventListener("click", clearAnswer);
  document.getElementById("btn-submit").addEventListener("click", submitExam);
  document.addEventListener("keydown", (e) => {
    if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); move(1); }
    if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); move(-1); }
    if (types[idx] && types[idx].widget === "mcq" && !e.altKey && !e.ctrlKey && !e.metaKey) {
      const k = e.key.toUpperCase();
      if ("ABCD".includes(k)) { e.preventDefault(); pick(k); }
    }
  });

  renderRail();
  showQuestion();
  startClock();
});

function typeOf(text) {
  const t = String(text || "").trim();
  return TYPES.find((k) => k.re.test(t)) || FALLBACK_TYPE;
}

// ---------- draft persistence ----------

function loadDraft() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(storageKey()) || "null"); } catch (e) { saved = null; }
  if (saved && saved.answers && typeof saved.answers === "object") {
    draft = { idx: Math.min(saved.idx || 0, Math.max(0, questions.length - 1)),
              elapsed: Number(saved.elapsed) || 0, answers: saved.answers };
    note(`Draft restored · ${answeredCount()} of ${questions.length} answered`, true);
  }
}

function saveDraft(quiet) {
  draft.idx = idx;
  try { localStorage.setItem(storageKey(), JSON.stringify(draft)); } catch (e) { /* full or private mode */ }
  if (!quiet) note("Saved on this device");
}

function queueSave() {
  readWidget();
  note("Saving…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveDraft(), 500);
}

function note(text, withReset) {
  const el = document.getElementById("draft-note");
  el.textContent = text || "";
  el.innerHTML = "";
  if (!text) return;
  el.append(document.createTextNode(text));
  if (withReset) {
    const btn = document.createElement("button");
    btn.className = "link-btn";
    btn.textContent = "start fresh";
    btn.addEventListener("click", () => {
      draft = { idx: 0, elapsed: 0, answers: {} };
      idx = 0;
      try { localStorage.removeItem(storageKey()); } catch (e) { /* ignore */ }
      note("");
      renderRail();
      showQuestion();
    });
    el.append(document.createTextNode(" — "), btn);
  }
}

// ---------- answers ----------

function composed(i) {
  const a = draft.answers[i];
  if (!a) return "";
  if (typeof a === "string") return a.trim();
  const working = String(a.working || "").trim();
  const final = String(a.final || "").trim();
  if (working && final) return `${working}\n\nFinal answer: ${final}`;
  return (working || final).trim();
}

function answeredCount() {
  return questions.filter((_, i) => composed(i)).length;
}

function readWidget() {
  const slot = document.getElementById("answer-slot");
  if (!slot.children.length) return;
  const kind = slot.dataset.kind;
  if (kind === "mcq") return;  // a choice is saved the moment it is clicked
  if (kind === "calc") {
    draft.answers[idx] = {
      working: slot.querySelector(".ans-work").value,
      final: slot.querySelector(".ans-final").value,
    };
  } else {
    const field = slot.querySelector("textarea, input");
    draft.answers[idx] = field ? field.value : "";
  }
  if (!composed(idx)) delete draft.answers[idx];
}

function clearAnswer() {
  delete draft.answers[idx];
  showQuestion();
  saveDraft();
}

function pick(letter) {
  draft.answers[idx] = letter;
  paintChoices(letter);
  saveDraft(true);
  paintMeta();
  renderRail();
}

function paintChoices(letter) {
  document.querySelectorAll("#answer-slot .choice").forEach((btn) => {
    const on = btn.dataset.letter === letter;
    btn.classList.toggle("selected", on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
  });
}

// ---------- rendering ----------

function renderRail() {
  const list = document.getElementById("rail-list");
  list.innerHTML = "";
  questions.forEach((q, i) => {
    const li = document.createElement("li");
    const done = !!composed(i);
    li.className = "rail-item" + (i === idx ? " current" : "") + (done ? " done" : "");
    li.innerHTML = `<span class="ri-num mono">${i + 1}</span>
      <span class="ri-label">${types[i].label}</span>
      <span class="ri-mark">${done ? "✓" : ""}</span>`;
    li.title = q.question.slice(0, 120);
    li.addEventListener("click", () => { readWidget(); idx = i; showQuestion(); saveDraft(); });
    list.appendChild(li);
  });

  const done = answeredCount();
  document.getElementById("rail-count").textContent = `${done} / ${questions.length}`;
  document.getElementById("rail-bar").style.width = `${(done / questions.length) * 100}%`;
  const submit = document.getElementById("btn-submit");
  submit.disabled = done === 0;
  submit.textContent = done === questions.length
    ? "Hand in paper"
    : `Hand in paper (${done}/${questions.length})`;
}

function showQuestion() {
  const q = questions[idx];
  const type = types[idx];
  document.getElementById("q-num").textContent = `Question ${idx + 1} of ${questions.length}`;
  document.getElementById("q-type").textContent = type.label;
  const concept = document.getElementById("q-concept");
  concept.textContent = q.concept_name || "";
  concept.style.display = q.concept_name ? "" : "none";
  document.getElementById("q-text").textContent = q.question;
  document.getElementById("q-hint").textContent = type.hint;
  buildWidget(type);

  document.getElementById("btn-prev").disabled = idx === 0;
  const next = document.getElementById("btn-next");
  next.textContent = idx === questions.length - 1 ? "Save answer" : "Save & next →";

  const slot = document.getElementById("answer-slot");
  slot.dataset.kind = type.widget;
  renderRail();
  paintMeta();
  const first = slot.querySelector("textarea, input");
  if (first) first.focus();
}

// The widget is rebuilt on every navigation; readWidget() must therefore run while
// the old question is still on screen (move() and the rail do exactly that).
function buildWidget(type) {
  const slot = document.getElementById("answer-slot");
  const stored = draft.answers[idx] || {};
  slot.innerHTML = "";

  if (type.widget === "mcq") {
    const group = document.createElement("div");
    group.className = "choice-group";
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", "Answer choices");
    questions[idx].options.forEach((text, i) => {
      const letter = "ABCD"[i];
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice";
      btn.dataset.letter = letter;
      const badge = document.createElement("span");
      badge.className = "ch-letter mono";
      badge.textContent = letter;
      const body = document.createElement("span");
      body.className = "ch-text";
      body.textContent = text;
      btn.append(badge, body);
      btn.addEventListener("click", () => pick(letter));
      group.appendChild(btn);
    });
    slot.append(group);
    if (typeof stored === "string") paintChoices(stored.toUpperCase().slice(0, 1));
    return;
  }

  if (type.widget === "calc") {
    const work = document.createElement("textarea");
    work.className = "ans-work mono";
    work.rows = 5;
    work.placeholder = "Your working — formula, substitution, arithmetic…";
    work.value = typeof stored === "string" ? stored : (stored.working || "");
    const label = document.createElement("label");
    label.className = "ans-label mono";
    label.textContent = "Final answer";
    const final = document.createElement("input");
    final.className = "ans-final";
    final.type = "text";
    final.placeholder = "e.g. −92.5 kJ/mol";
    final.value = typeof stored === "string" ? "" : (stored.final || "");
    slot.append(work, label, final);
    [work, final].forEach((el) => el.addEventListener("input", () => { queueSave(); paintMeta(); }));
    work.addEventListener("keydown", ctrlEnter);
    final.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); move(1); } });
    autogrow(work);
  } else {
    const area = document.createElement("textarea");
    area.className = "ans-text" + (type.mono ? " mono" : "");
    area.rows = type.widget === "line" ? 2 : 6;
    area.placeholder = type.widget === "line"
      ? "Type your answer here…"
      : "Type your full answer here, the way you would write it in the exam…";
    area.value = typeof stored === "string" ? stored : (stored.working || "");
    slot.append(area);
    area.addEventListener("input", () => { queueSave(); paintMeta(); });
    area.addEventListener("keydown", ctrlEnter);
    autogrow(area);
  }
}

function ctrlEnter(e) {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); move(1); }
}

function autogrow(el) {
  const fit = () => { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; };
  el.addEventListener("input", fit);
  requestAnimationFrame(fit);
}

function paintMeta() {
  if (types[idx].widget === "mcq") {
    const chosen = String(draft.answers[idx] || "").toUpperCase();
    document.getElementById("q-meta").textContent =
      chosen.length === 1 && "ABCD".includes(chosen) ? `Option ${chosen} chosen` : "not answered yet";
    return;
  }
  const words = composed(idx).split(/\s+/).filter(Boolean).length;
  document.getElementById("q-meta").textContent =
    words ? `${words} word${words === 1 ? "" : "s"} written` : "not answered yet";
}

function move(delta) {
  readWidget();
  const next = idx + delta;
  if (next < 0 || next >= questions.length) {
    saveDraft();
    renderRail();
    // Last question: point at the hand-in button instead of dead-ending.
    const submit = document.getElementById("btn-submit");
    submit.classList.add("attention");
    setTimeout(() => submit.classList.remove("attention"), 1600);
    return;
  }
  idx = next;
  showQuestion();
  saveDraft();
  document.getElementById("q-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- clock ----------

function startClock() {
  const paint = () => {
    const s = Math.floor(draft.elapsed);
    document.getElementById("exam-clock").textContent =
      `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  paint();
  clearInterval(clockTimer);
  clockTimer = setInterval(() => { draft.elapsed += 1; paint(); }, 1000);
}

// ---------- submit ----------

async function submitExam() {
  readWidget();
  const payload = questions
    .map((q, i) => ({ uid: q.uid, question: q.question, concept: q.concept, answer: composed(i) }))
    .filter((a) => a.answer);
  if (!payload.length) return;

  const btn = document.getElementById("btn-submit");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Handing in your paper…';
  try {
    const resp = await fetch("/api/submit_test", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ subject: SUBJECT, topic: TOPIC, answers: payload }),
    });
    const data = await readJSON(resp);
    if (!resp.ok) throw new Error(data.error || "Couldn't hand in your paper");
    try { localStorage.removeItem(storageKey()); } catch (e) { /* ignore */ }
    window.location.href = `/results/${data.job_id}`;
  } catch (err) {
    note(err.message + " — your answers are still here, try again.");
    btn.disabled = false;
    renderRail();
  }
}
