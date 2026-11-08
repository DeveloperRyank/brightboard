"""LLM-powered tutoring: whiteboard lesson generation, interruptible Q&A, answer diagnosis."""

import concepts
import curriculum
import llm


def _subject_name(subject):
    s = curriculum.get_subject(subject)
    return s["name"] if s else subject.replace("_", " ").title()

CANVAS_RULES = """
The canvas is a 1000 x 620 dark board, CLEARED between steps — each step is a self-contained frame.
Every stroke animates pen-style, like chalk. Keep each step to 1-3 visuals with room to breathe.

PREFERRED — PRE-BUILT TEMPLATES. For any real diagram use a template: its geometry is fixed and
pre-verified, so nothing overlaps or drifts off-board. You only PICK a template and FILL its label
slots with short plain strings. NEVER invent coordinates for apparatus, molecules, graphs or energy
profiles — that is exactly what templates exist to prevent.
  {"type":"template","template":"NAME","labels":{...},"variant":"..."}
Templates (slots you may fill; any slot you omit keeps a sensible default):
  distillation    burner + round flask + thermometer + condenser + beaker
                  slots: mixture, thermometer, condenser, distillate
  titration       burette over a conical flask
                  slots: titrant, analyte, endpoint
  filtration      funnel + filter paper over a conical flask
                  slots: residue, filtrate
  methane         tetrahedral CH4 ball-and-stick
                  slots: name, geometry
  water           bent H2O with lone pairs
                  slots: angle, name, geometry
  energy_profile  reaction-coordinate energy curve — variant REQUIRED: "exo" or "endo"
                  slots: reactants, products, ts, ea, dh
  graph_axes      labelled x/y axes — variant: "line", "curve" or "empty"
                  slots: title, xlabel, ylabel
One template per step is normally plenty; it fills the board. Use the template that matches the concept.

FALLBACK — only for things no template covers (a lone beaker, an ion, a word-equation box, a bare
label). These freeform shapes ARE validated: anything off-board or overlapping another shape is
rejected, so use templates wherever possible and keep freeform shapes few, small and well separated.
  {"type":"draw","glyph":"round_flask|beaker|test_tube|thermometer|condenser|burner|funnel|filter_paper|evaporating_dish|electrode|atom|graph_axes|bulb","x":N,"y":N,"size":N}
  {"type":"draw","shape":"line|arrow","x1":N,"y1":N,"x2":N,"y2":N,"color":"#hex"}
  {"type":"draw","shape":"path","points":[[x,y],...],"color":"#hex"}
  {"type":"draw","shape":"circle","cx":N,"cy":N,"r":N,"color":"#hex"}
  {"type":"label","text":"short text","x":N,"y":N,"color":"#hex","size":14-28}
  {"type":"highlight","shape":"circle","cx":N,"cy":N,"r":N}
Colors — the board is deep navy, so ink is warm. Use ONLY these:
  #F4EBDD chalk (default: any label or stroke that is just the thing itself)
  #FF6B57 coral (the one thing to look at: the answer, the peak, the product)
  #74A98D green (a favourable result: alkali, correct, energy given out)
  #D9A648 ochre (a caution or keyword: acid-side note, activation energy, "KNEC wants")
  #BFB09C dim chalk (captions and secondary notes)
Highlights are coral. Never invent a hex, and never use colour as decoration — it must mean something.
Labels SHORT (a few words), placed next to the part they name, never on top of a drawing.
"""

def _lesson_system(subject_name):
    return f"""You are a KCSE {subject_name} tutor creating a visual whiteboard lesson for a secondary-school student in Kenya.
You output STRICT JSON only — no prose, no code fences, no commentary before or after the JSON.
Your reply must begin with the character {{ and end with the character }}.
{CANVAS_RULES}"""

LESSON_USER_TEMPLATE = """Create a complete whiteboard lesson that teaches the WHOLE topic "{topic}" for a KCSE {subject_name} student — every core concept in the topic, in teaching order, not just one worked example. A student who finishes this lesson must understand the entire topic well enough to answer any KCSE question on it.

The topic's real past-paper questions (use them to know exactly which concepts and question styles the topic covers):
{examples}

{adapt}

Structure the lesson concept by concept:
1. First step orients the student: what the topic is, why KNEC examines it.
2. Then one or more steps per core concept: definition, the mechanism/process/diagram behind it, and a short worked example or exam-style reasoning where relevant.
3. Include the common exam traps and the keywords/units/step order KNEC marking schemes reward.
4. Final step: exam technique summary for the whole topic.

Because the board clears between steps, every step's drawing must stand alone: redraw any apparatus, scheme or graph the step refers to.

Output JSON in exactly this shape (most steps use ONE template action, optionally plus a label):
{{
  "title": "Lesson title",
  "steps": [
    {{"narration": "One or two spoken sentences the student hears and reads for this step (plain, encouraging, exam-focused).",
      "actions": [
        {{"type":"template","template":"distillation","labels":{{"mixture":"Crude oil","distillate":"Fraction collected"}}}}
      ]}}
  ]
}}

Use 8 to 14 steps so the whole topic is genuinely covered. Pick the template that matches each concept; only fall back to freeform shapes when no template fits, and never let them overlap or run off the board."""

CONCEPT_LESSON_TEMPLATE = """Create a complete whiteboard lesson that teaches the WHOLE topic "{topic}" for a KCSE {subject_name} student.

The topic is defined by this concept map. These are the concepts you MUST teach, in this exact teaching order:
{concept_list}

Coverage rules — these are checked, and a lesson that breaks them is thrown away:
- Teach EVERY concept above, in the order given. Do not skip one, do not merge two into a single step unless they are genuinely one idea, and do not invent concepts that are not on the list.
- Tag every step with the concept it teaches: "concept": "<slug>", using a slug copied exactly from the list above. The orientation and exam-technique steps use the slug of the concept they mostly serve.
- Give the bigger concepts more steps; give every concept at least one.

These real KCSE past-paper questions from this topic show the STYLE you are teaching towards — the wording, the command words (state / explain / describe / calculate), the mark allocations and the depth KNEC expects. Use them ONLY as a style and depth reference:
{examples}

Do NOT write exam-style test questions for the student to answer, here or anywhere in the lesson — practice questions come only from the real question bank. You MAY invent short illustrative teaching examples of your own (a molecule to name, a calculation to walk through, an everyday scenario) whenever that makes a concept land.

{adapt}

Structure the lesson:
1. First step orients the student: what the topic is and why KNEC examines it.
2. Then the concepts in map order — for each: the definition, the mechanism/process/diagram behind it, and one short illustrative example or piece of exam-style reasoning.
3. Weave in the common exam traps and the keywords, units and step order KNEC marking schemes reward.
4. Final step: exam technique summary for the whole topic.

Because the board clears between steps, every step's drawing must stand alone: redraw any apparatus, scheme or graph the step refers to.

Output JSON in exactly this shape (most steps use ONE template action, optionally plus a label):
{{
  "title": "Lesson title",
  "steps": [
    {{"concept": "a slug from the list above",
      "narration": "One or two spoken sentences the student hears and reads for this step (plain, encouraging, exam-focused).",
      "actions": [
        {{"type":"template","template":"distillation","labels":{{"mixture":"Crude oil","distillate":"Fraction collected"}}}}
      ]}}
  ]
}}

Use {lo} to {hi} steps so every concept is genuinely covered. Pick the template that matches each concept; only fall back to freeform shapes when no template fits, and never let them overlap or run off the board."""

ASK_SYSTEM = """You are a KCSE {subject_name} tutor. A student paused a visual whiteboard lesson to ask you a question.
You output STRICT JSON only — no prose, no code fences, nothing before or after the JSON:
{{
  "answer": "...",
  "points": ["...", "..."]
}}

"answer" is what you SAY out loud: a clear, COMPLETE, self-contained spoken explanation in plain prose a
Kenyan secondary-school student understands, grounded in the concept the paused step is teaching.
Structure it as: (1) a direct one-sentence answer, (2) the WHY — the mechanism or reasoning, walked through
step by step, (3) the exact keywords, units or step order KNEC marking schemes reward, (4) one line tying it
back to the concept being taught so the student sees where it fits in the topic. 4 to 6 sentences, at most
220 words. Teach the idea properly — never just restate the question or give a bare conclusion.
Write flowing sentences only. NEVER output bare numbered lists, marking-scheme fragments, isolated numbers in
brackets, or disconnected phrases — always full sentences. No markdown headings, no code fences.

"points" is what goes ON THE BOARD while you speak: 3 to 5 short notes of at most 7 words each — the key
terms, the order of steps, or the labels of the idea. No full sentences, no numbering, no markdown."""

ASK_USER_TEMPLATE = """Topic: {topic}
Concept this step is teaching: {concept}
The lesson step the student paused on says: "{narration}"

Student's question: {question}

Answer the student's question directly and completely, in full spoken sentences, and give the board notes."""

GRADE_SYSTEM = """You are a strict but fair KCSE {subject_name} examiner grading one student answer to one past-paper question.
You output STRICT JSON only — no prose, no code fences.
Grade at KCSE level using your knowledge of the syllabus and how KNEC marking schemes award marks (keywords, units, correct reasoning)."""

GRADE_RULES = """Rules for error_type (internal code, null if correct):
- conceptual_gap: wrong idea / wrong science (e.g. wrong process name, wrong ion, wrong reasoning)
- computational: right method, arithmetic/units mistake
- transfer: right science, applied to the wrong context or answered a different question
- time_pressure: incomplete / abandoned / far too short to be an attempt
An empty or nonsense answer is time_pressure. Be honest — do not mark a wrong answer correct for being close.

teacher_note is what you would SAY to the student out loud, in plain spoken language a secondary-school
student understands — never mention internal codes or labels like "conceptual_gap". Say what they got
right (if anything), what they misunderstood or missed, and the one or two things they need to know or
write differently to earn the marks next time. 2-4 sentences, warm but honest, like a good teacher."""

GRADE_USER_TEMPLATE = """Topic: {topic}
Question: {question}
Student's answer: {answer}

Output JSON exactly:
{{
  "correct": true or false,
  "error_type": "conceptual_gap" | "computational" | "transfer" | "time_pressure" | null,
  "teacher_note": "..."
}}

""" + GRADE_RULES

GRADE_BATCH_SYSTEM = """You are a strict but fair KCSE {subject_name} examiner marking a whole set of a student's
answers to past-paper questions in one pass. You output STRICT JSON only — no prose, no code fences.
Grade at KCSE level using your knowledge of the syllabus and how KNEC marking schemes award marks
(keywords, units, correct reasoning). Mark every answer on its own merits; never let a strong answer
drag a weak one up."""

GRADE_BATCH_USER_TEMPLATE = """Topic: {topic}

The student's answers, numbered:

{items}

Output JSON exactly:
{{
  "results": [
    {{"i": 1, "correct": true, "error_type": null, "teacher_note": "..."}},
    {{"i": 2, "correct": false, "error_type": "conceptual_gap", "teacher_note": "..."}}
  ]
}}

Return exactly one entry per numbered answer, in the same order, with "i" equal to the answer's number.
Do not skip any and do not merge them.

""" + GRADE_RULES


BOARD_W, BOARD_H = 1000, 620

# Must mirror TEMPLATES in static/js/whiteboard.js — the renderer owns the geometry,
# this catalog only tells us which names/variants/slots are legitimate.
TEMPLATE_CATALOG = {
    "distillation": {"variants": [], "slots": ["mixture", "thermometer", "condenser", "distillate"]},
    "titration": {"variants": [], "slots": ["titrant", "analyte", "endpoint"]},
    "filtration": {"variants": [], "slots": ["residue", "filtrate"]},
    "methane": {"variants": [], "slots": ["name", "geometry"]},
    "water": {"variants": [], "slots": ["angle", "name", "geometry"]},
    "energy_profile": {"variants": ["exo", "endo"], "slots": ["reactants", "products", "ts", "ea", "dh"]},
    "graph_axes": {"variants": ["line", "curve", "empty"], "slots": ["title", "xlabel", "ylabel"]},
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _action_box(a):
    """Bounding box (x1,y1,x2,y2) for a freeform draw/highlight, or None."""
    t = a.get("type")
    if t == "draw" and a.get("glyph"):
        s = _num(a.get("size")) or 160.0
        x, y = _num(a.get("x")), _num(a.get("y"))
        if x is None or y is None:
            return None
        return (x, y, x + s, y + s)
    if t == "draw" and a.get("shape") == "rect":
        x, y, w, h = _num(a.get("x")), _num(a.get("y")), _num(a.get("w")), _num(a.get("h"))
        if None in (x, y, w, h):
            return None
        return (x, y, x + w, y + h)
    if t == "draw" and a.get("shape") == "circle":
        cx, cy, r = _num(a.get("cx")), _num(a.get("cy")), _num(a.get("r"))
        if None in (cx, cy, r):
            return None
        return (cx - r, cy - r, cx + r, cy + r)
    if t == "draw" and a.get("shape") in ("line", "arrow"):
        x1, y1, x2, y2 = _num(a.get("x1")), _num(a.get("y1")), _num(a.get("x2")), _num(a.get("y2"))
        if None in (x1, y1, x2, y2):
            return None
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if t == "highlight":
        if a.get("shape") == "circle":
            cx, cy, r = _num(a.get("cx")), _num(a.get("cy")), _num(a.get("r"))
            if None in (cx, cy, r):
                return None
            return (cx - r, cy - r, cx + r, cy + r)
        x, y, w, h = _num(a.get("x")), _num(a.get("y")), _num(a.get("w")), _num(a.get("h"))
        if None in (x, y, w, h):
            return None
        return (x, y, x + w, y + h)
    return None


def _in_bounds(b):
    return b[0] >= 0 and b[1] >= 0 and b[2] <= BOARD_W and b[3] <= BOARD_H


def _overlap_frac(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    smallest = max(1.0, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))
    return inter / smallest


def validate_actions(actions, overlap_tolerance=0.35):
    """Problems with a step's freeform geometry (templates are pre-verified, skipped)."""
    problems = []
    placed = []
    for a in actions or []:
        if not isinstance(a, dict) or a.get("type") in ("template", "label"):
            continue
        box = _action_box(a)
        if box is None:
            continue
        if not _in_bounds(box):
            problems.append("shape out of bounds")
            continue
        if any(_overlap_frac(box, p) > overlap_tolerance for p in placed):
            problems.append("shape overlaps a previous shape")
            continue
        placed.append(box)
    return problems


def validate_lesson(lesson, allowed_concepts=None):
    problems = []
    steps = (lesson or {}).get("steps", []) or []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        for p in validate_actions(step.get("actions", [])):
            problems.append(f"step {i + 1}: {p}")
    if allowed_concepts:
        allowed = set(allowed_concepts)
        taught = set()
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            c = step.get("concept")
            if c in allowed:
                taught.add(c)
            elif c:
                problems.append(f"step {i + 1}: unknown concept '{c}'")
        missing = [c for c in allowed_concepts if c not in taught]
        if missing:
            problems.append("concepts never taught: " + ", ".join(missing[:6]))
    return problems


def _sanitize_actions(actions):
    """Keep templates (if known) and freeform shapes that pass bounds+overlap; drop the rest."""
    kept, boxes = [], []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        if t == "template":
            if a.get("template") in TEMPLATE_CATALOG:
                kept.append(a)
            continue
        if t == "label":
            kept.append(a)
            continue
        if t not in ("draw", "highlight"):
            continue
        box = _action_box(a)
        if box is None or not _in_bounds(box):
            continue
        if any(_overlap_frac(box, b) > 0.35 for b in boxes):
            continue
        boxes.append(box)
        kept.append(a)
    return kept


def generate_lesson(subject, topic, example_questions, adapt_context, concept_list=None):
    """Build a lesson. When concept_list is given (the hand-authored map) the lesson
    must cover every concept in order and each step is tagged with its concept slug;
    the real questions then only ground the exam STYLE. Without a map the questions
    define the scope, as before."""
    subject_name = _subject_name(subject)
    topic_name = curriculum.topic_name(subject, topic)
    system = _lesson_system(subject_name)
    slugs = [c["slug"] for c in (concept_list or [])]
    if slugs:
        examples = "\n".join(f"- {q}" for q in example_questions[:12])
        lo = max(8, len(slugs) + 1)
        user = CONCEPT_LESSON_TEMPLATE.format(
            topic=topic_name, subject_name=subject_name,
            concept_list="\n".join(
                f"{i}. {c['slug']} — {c['name']}" for i, c in enumerate(concept_list, 1)),
            examples=examples, adapt=adapt_context, lo=lo, hi=min(20, lo + 6),
        )
    else:
        examples = "\n".join(f"- {q}" for q in example_questions)
        user = LESSON_USER_TEMPLATE.format(
            topic=topic_name, subject_name=subject_name, examples=examples, adapt=adapt_context
        )
    last_raw, last_err = None, None
    for _ in range(2):  # reject-and-retry: burn-up, invalid geometry or a skipped concept
        try:
            raw = llm.chat_json(system, user, temperature=0.5, max_tokens=9000)
        except ValueError as exc:
            last_err = exc
            continue
        last_raw = raw
        problems = validate_lesson(raw, slugs)
        if not problems:
            return normalize_lesson(raw, slugs)
        last_err = ValueError("invalid lesson: " + "; ".join(problems[:5]))
    if last_raw is not None:
        # Never dead-end the student: sanitize the best attempt instead of failing outright.
        return normalize_lesson(last_raw, slugs)
    raise last_err or ValueError("lesson generation failed")


def normalize_lesson(lesson, allowed_concepts=None):
    if not isinstance(lesson, dict) or not isinstance(lesson.get("steps"), list) or not lesson["steps"]:
        raise ValueError("Lesson JSON missing 'steps'")
    allowed = set(allowed_concepts or ())
    steps = []
    for step in lesson["steps"]:
        narration = str(step.get("narration", "")).strip()
        actions = _sanitize_actions(step.get("actions", []))
        concept = step.get("concept") if allowed else None
        if concept not in allowed:
            concept = None
        if narration or actions:
            steps.append({"concept": concept, "narration": narration, "actions": actions})
    if not steps:
        raise ValueError("Lesson contained no usable steps")
    return {"title": str(lesson.get("title", "Lesson")).strip() or "Lesson", "steps": steps}


def _looks_degenerate(text):
    """True when a reply is a fragment / marking-scheme scrap rather than an explanation."""
    t = (text or "").strip()
    if len(t) < 40 or len(t.split()) < 8:
        return True
    if not t.endswith((".", "!", "?")):  # cut off mid-sentence (thinking-budget truncation)
        return True
    alnum = sum(c.isalnum() for c in t)
    return alnum / max(1, len(t)) < 0.5  # mostly digits/punctuation => not prose


def ask_question(subject, topic, step_narration, question, concept=None):
    """Spoken answer plus the short board notes that go with it."""
    system = ASK_SYSTEM.format(subject_name=_subject_name(subject))
    concept_name = concepts.concept_name(subject, topic, concept) if concept else None
    user = ASK_USER_TEMPLATE.format(
        topic=curriculum.topic_name(subject, topic),
        concept=concept_name or "whatever the paused step is teaching",
        narration=step_narration, question=question,
    )
    # Thinking is disabled (GEMINI_THINKING_BUDGET=0), so the budget is pure visible
    # output. The cooler retry is a safety net for the rare degenerate/truncated reply.
    best = {"answer": "", "points": [], "concept": concept_name}
    for temperature in (0.4, 0.2):
        raw = llm.chat_json(system, user, temperature=temperature, max_tokens=1200)
        answer = str(raw.get("answer") or "").strip()
        points = [str(p).strip()[:60] for p in (raw.get("points") or []) if str(p).strip()][:5]
        candidate = {"answer": answer, "points": points, "concept": concept_name}
        if len(answer) > len(best["answer"]):
            best = candidate
        if not _looks_degenerate(answer):
            return candidate
    return best


ERROR_TYPES = ("conceptual_gap", "computational", "transfer", "time_pressure")


def _clean_verdict(result):
    correct = bool(result.get("correct"))
    error_type = result.get("error_type")
    if correct:
        error_type = None
    elif error_type not in ERROR_TYPES:
        error_type = "conceptual_gap"
    note = str(result.get("teacher_note") or result.get("feedback") or "").strip()
    return {"correct": correct, "error_type": error_type, "teacher_note": note}


def grade_answer(subject, topic, question, answer):
    system = GRADE_SYSTEM.format(subject_name=_subject_name(subject))
    user = GRADE_USER_TEMPLATE.format(
        topic=curriculum.topic_name(subject, topic), question=question, answer=answer
    )
    result = llm.chat_json(system, user, temperature=0.1, max_tokens=700)
    return _clean_verdict(result)


def grade_answers(subject, topic, items):
    """Mark a whole exam in ONE call — five answers used to mean five sequential
    round-trips, which is why marking felt endless. Returns a verdict per item, in
    order; any answer the batch reply lost gets marked on its own instead."""
    if not items:
        return []
    if len(items) == 1:
        return [grade_answer(subject, topic, items[0]["question"], items[0]["answer"])]

    numbered = "\n\n".join(
        f"[{i}] Question: {it['question']}\n    Student's answer: {it['answer']}"
        for i, it in enumerate(items, 1)
    )
    system = GRADE_BATCH_SYSTEM.format(subject_name=_subject_name(subject))
    user = GRADE_BATCH_USER_TEMPLATE.format(
        topic=curriculum.topic_name(subject, topic), items=numbered
    )
    slots = [None] * len(items)
    try:
        raw = llm.chat_json(system, user, temperature=0.1,
                            max_tokens=260 * len(items) + 400)
        rows = raw.get("results") if isinstance(raw, dict) else raw
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                i = int(row.get("i"))
            except (TypeError, ValueError):
                continue
            if not 1 <= i <= len(items) or slots[i - 1] is not None:
                continue
            verdict = _clean_verdict(row)
            if verdict["teacher_note"]:  # a noteless row is no use to the student
                slots[i - 1] = verdict
    except (ValueError, RuntimeError):
        pass  # unusable batch reply: fall through and mark them individually

    return [v or grade_answer(subject, topic, it["question"], it["answer"])
            for it, v in zip(items, slots)]


# ---------------- Uploaded document analysis ----------------

ANALYZE_SYSTEM = """You are a KCSE {subject_name} examiner and diagnostician. You are given a student's
uploaded document — a past paper, worksheet, set of notes, or a marked/graded test. Read it carefully;
it may be a scan, so use both the text and the page images. You output STRICT JSON only — no prose, no
code fences, no commentary before or after the JSON."""

ANALYZE_USER_TEMPLATE = """Subject: {subject_name}
Filename: {filename}

The ONLY valid topic slugs for this subject (use these exact slugs when naming topics):
{topic_list}

Analyse the document and return JSON exactly in this shape:
{{
  "document_type": "past_paper" | "worksheet" | "notes" | "marked_test" | "other",
  "title": "a short human title you infer for the document",
  "summary": "2-3 plain sentences: what the document covers and, if it is marked, how the student did",
  "topics": [
    {{"slug": "a valid slug", "covered": true, "questions_found": 0}}
  ],
  "findings": [
    {{"topic": "a valid slug", "question": "short question text", "answer": "the student's written answer if visible, else empty", "correct": true, "error_type": "conceptual_gap" | "computational" | "transfer" | "time_pressure" | null, "note": "one short sentence of feedback"}}
  ],
  "recommended_focus": ["a valid slug", "..."]
}}

Rules:
- Include a finding ONLY where the document actually shows a student answer or a mark for a specific question. For a blank past paper with no student work, leave "findings" empty and just list the topics it covers.
- Map every topic to one of the valid slugs above; omit anything that fits none of them.
- "recommended_focus" lists slugs to study next, weakest first (use the findings if the paper is marked, otherwise the topics the paper emphasises).
- Never invent student answers that are not in the document. Be concise."""


def analyze_paper(subject, file_b64, mime, filename):
    """Send an uploaded PDF/image to Gemini vision and return a structured diagnosis."""
    subject_name = _subject_name(subject)
    topic_list = "\n".join(f"- {t['slug']}: {t['name']}" for t in curriculum.topics(subject))
    system = ANALYZE_SYSTEM.format(subject_name=subject_name)
    user = ANALYZE_USER_TEMPLATE.format(subject_name=subject_name, filename=filename, topic_list=topic_list)
    raw = llm.chat_json_with_file(system, user, {"mime": mime, "data_b64": file_b64},
                                  temperature=0.2, max_tokens=4000)
    return normalize_analysis(subject, raw)


def normalize_analysis(subject, raw):
    valid = curriculum.all_topic_slugs(subject)
    if not isinstance(raw, dict):
        raise ValueError("Analysis returned no usable JSON")
    topics = []
    for t in raw.get("topics", []) or []:
        if not isinstance(t, dict):
            continue
        slug = t.get("slug")
        if slug in valid:
            topics.append({"slug": slug, "name": curriculum.topic_name(subject, slug),
                           "covered": bool(t.get("covered", True)),
                           "questions_found": int(t.get("questions_found") or 0)})
    findings = []
    for f in raw.get("findings", []) or []:
        if not isinstance(f, dict):
            continue
        slug = f.get("topic")
        if slug not in valid:
            continue
        correct = bool(f.get("correct"))
        et = f.get("error_type")
        if correct:
            et = None
        elif et not in ("conceptual_gap", "computational", "transfer", "time_pressure"):
            et = "conceptual_gap"
        findings.append({"topic": slug, "question": str(f.get("question", ""))[:1000],
                         "answer": str(f.get("answer", ""))[:1000], "correct": correct,
                         "error_type": et, "note": str(f.get("note", ""))[:500]})
    focus = [s for s in (raw.get("recommended_focus") or []) if s in valid]
    return {
        "document_type": str(raw.get("document_type") or "other"),
        "title": (str(raw.get("title") or "Uploaded document").strip()[:120] or "Uploaded document"),
        "summary": str(raw.get("summary") or "").strip()[:1200],
        "topics": topics,
        "findings": findings,
        "recommended_focus": focus,
    }


# ---------------- Guided paper walkthrough ----------------

EXTRACT_SYSTEM = """You are a KCSE {subject_name} examiner reading a student's uploaded exam paper.
It may be a scan, so read both the text and the page images. You output STRICT JSON only — no prose,
no code fences, no commentary before or after the JSON."""

EXTRACT_USER_TEMPLATE = """Subject: {subject_name}
Filename: {filename}

The ONLY valid topic slugs for this subject:
{topic_list}
{concept_block}
List EVERY question in the paper, in the order it appears, as JSON exactly in this shape:
{{
  "title": "a short human title for the paper",
  "questions": [
    {{"number": "1(a)", "text": "the question wording", "topic": "a valid slug",
      "concept": "a valid concept slug for that topic, or null", "marks": 3}}
  ]
}}

Rules:
- "text" must be the question AS PRINTED in the paper — copy it faithfully. Never invent, reword or
  extend a question, and never write a model answer.
- Split separately numbered sub-parts (1(a), 1(b), …) into their own entries; keep shared source
  material (a passage, a table, a diagram description) with the first sub-part that needs it.
- Skip rubric, instructions to candidates and cover pages.
- "topic" must be one of the valid slugs above; drop any question that fits none of them.
- "concept" must be one of that topic's concept slugs listed above, or null if the topic has no list
  or the question spans several.
- "marks" is the printed mark allocation, or null.
- Return at most 40 questions, in paper order."""


def _concept_block(subject):
    """The approved concept slugs per topic, so extracted questions can be tagged."""
    lines = []
    for t in curriculum.topics(subject):
        slugs = concepts.concept_slugs(subject, t["slug"])
        if slugs:
            lines.append(f"  {t['slug']}: {', '.join(slugs)}")
    if not lines:
        return "\nNo concept lists are defined for this subject yet — set every \"concept\" to null.\n"
    return "\nConcept slugs available per topic (use null when a topic is not listed):\n" + "\n".join(lines) + "\n"


def extract_questions(subject, file_b64, mime, filename):
    """Read an uploaded paper and return its real questions, in order."""
    subject_name = _subject_name(subject)
    topic_list = "\n".join(f"- {t['slug']}: {t['name']}" for t in curriculum.topics(subject))
    system = EXTRACT_SYSTEM.format(subject_name=subject_name)
    user = EXTRACT_USER_TEMPLATE.format(subject_name=subject_name, filename=filename,
                                        topic_list=topic_list,
                                        concept_block=_concept_block(subject))
    raw = llm.chat_json_with_file(system, user, {"mime": mime, "data_b64": file_b64},
                                  temperature=0.1, max_tokens=8000)
    return normalize_questions(subject, raw)


def normalize_questions(subject, raw):
    if not isinstance(raw, dict):
        raise ValueError("Extraction returned no usable JSON")
    valid = curriculum.all_topic_slugs(subject)
    out = []
    for q in raw.get("questions") or []:
        if not isinstance(q, dict):
            continue
        text = " ".join(str(q.get("text") or "").split())
        topic = q.get("topic")
        if len(text) < 8 or topic not in valid:
            continue
        concept = q.get("concept")
        if concept not in concepts.concept_slugs(subject, topic):
            concept = None
        marks = _num(q.get("marks"))
        out.append({
            "number": str(q.get("number") or len(out) + 1).strip()[:20],
            "text": text[:2000],
            "topic": topic,
            "topic_name": curriculum.topic_name(subject, topic),
            "concept": concept,
            "concept_name": concepts.concept_name(subject, topic, concept) if concept else None,
            "marks": int(marks) if marks else None,
        })
        if len(out) >= 40:
            break
    if not out:
        raise ValueError("No questions could be read from that paper — is it a KCSE "
                         f"{_subject_name(subject)} paper, and is the scan legible?")
    return {"title": (str(raw.get("title") or "Uploaded paper").strip()[:120] or "Uploaded paper"),
            "questions": out}


WALK_USER_TEMPLATE = """A student is being walked through their own uploaded {subject_name} paper, one real
question at a time. Teach the idea behind THIS question on the whiteboard.

The question, exactly as it appears in their paper{number}:
"{question}"

Topic: {topic_name}
Concept to teach: {concept_name}
Marks available: {marks}
{retry_line}
Build a short whiteboard explanation:
1. One step that teaches the idea itself in plain language, with the diagram, scheme or template that
   makes it visible.
2. One or two steps applying that idea to THIS exact question: the method, the reasoning, and the
   keywords, units and step order the KNEC marking scheme rewards for {marks_text}.
3. A final step telling the student what to write and in what order.

Never invent a different question, and never present this as a question for them to answer — you are
teaching, not testing.

Output JSON in exactly this shape:
{{
  "title": "short title for this explanation",
  "steps": [
    {{"concept": {concept_json},
      "narration": "one or two spoken sentences",
      "actions": [{{"type":"template","template":"titration","labels":{{"titrant":"NaOH"}}}}]}}
  ]
}}

Use 3 to 5 steps. Pick the template that matches the idea; only fall back to freeform shapes when no
template fits, and never let them overlap or run off the board."""

RETRY_LINE = ("The student has already seen one explanation of this and asked for a DIFFERENT one. "
              "Do not repeat the same wording, the same analogy or the same diagram: start from a "
              "different angle, build more slowly from first principles, and use a concrete everyday "
              "comparison a Kenyan secondary-school student would recognise.")


def generate_walkthrough_step(subject, question, attempt=1):
    """A short whiteboard explanation of the concept behind one real paper question."""
    topic = question.get("topic")
    concept = question.get("concept")
    allowed = [concept] if concept else []
    marks = question.get("marks")
    system = _lesson_system(_subject_name(subject))
    user = WALK_USER_TEMPLATE.format(
        subject_name=_subject_name(subject),
        number=f" ({question['number']})" if question.get("number") else "",
        question=question.get("text", ""),
        topic_name=curriculum.topic_name(subject, topic),
        concept_name=(concepts.concept_name(subject, topic, concept) if concept
                      else curriculum.topic_name(subject, topic)),
        marks=marks or "not printed",
        marks_text=f"{marks} mark(s)" if marks else "full marks",
        retry_line=RETRY_LINE if attempt > 1 else "",
        concept_json=f'"{concept}"' if concept else "null",
    )
    last_raw, last_err = None, None
    for _ in range(2):
        try:
            raw = llm.chat_json(system, user, temperature=0.7 if attempt > 1 else 0.5,
                                max_tokens=5000)
        except ValueError as exc:
            last_err = exc
            continue
        last_raw = raw
        if not validate_lesson(raw, allowed):
            return normalize_lesson(raw, allowed)
        last_err = ValueError("invalid walkthrough geometry")
    if last_raw is not None:
        return normalize_lesson(last_raw, allowed)
    raise last_err or ValueError("walkthrough generation failed")
