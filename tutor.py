"""LLM-powered tutoring: whiteboard lesson generation, interruptible Q&A, answer diagnosis."""

import re

import concepts
import curriculum
import llm


def _subject_name(subject):
    s = curriculum.get_subject(subject)
    return s["name"] if s else subject.replace("_", " ").title()

CANVAS_BASE = """
The canvas is a 1000 x 620 dark board, CLEARED between steps — each step is a self-contained frame.
Every stroke animates pen-style, like chalk. Keep each step to 1-3 visuals with room to breathe.

PREFERRED — PRE-BUILT TEMPLATES. For any real diagram use a template: its geometry is fixed and
pre-verified, so nothing overlaps or drifts off-board. You only PICK a template and FILL its label
slots with short plain strings. NEVER invent coordinates for apparatus, molecules, graphs, rays or
energy profiles — that is exactly what templates exist to prevent.
  {"type":"template","template":"NAME","labels":{...},"variant":"..."}
Templates for THIS subject (slots you may fill; any slot you omit keeps a sensible default):
<<templates>>
One template per step is normally plenty; it fills the board. Teach the great majority of steps
ON THE BOARD: a student should see the thing, the apparatus or the ray the narration names.
Match the template to what the concept IS, not to how a picture of it looks. A spring diagram and a
refraction diagram are both "lines on a board" — only one of them is correct. A wrong template is worse
than no template: it teaches the student something false.
Pick the template by the physics of the step: what the step discusses is what the board draws. A step
about a lens uses the lens diagram even when it is only defining the lens; a step about rays through
a glass block uses the refraction diagram. Only a step that is purely numbers — substituting into a
formula, reducing an expression, listing answers — may go without a diagram, and then write the
working in labels across the board instead of leaving it blank.
ALWAYS give the "variant" when the template lists variants: the variant is the part of the diagram that
carries the meaning, and without it the board draws an empty frame.
If none of these depicts the concept, draw it freeform, or use graph_axes for a relationship between two
quantities — NEVER stretch a template to cover a different subject's apparatus or phenomenon.

FALLBACK — only for things no template covers (a lone beaker, an ion, a word-equation box, a bare
label). These freeform shapes ARE validated: anything off-board or overlapping another shape is
rejected, so use templates wherever possible and keep freeform shapes few, small and well separated.
  {"type":"draw","glyph":"round_flask|beaker|test_tube|thermometer|condenser|burner|funnel|filter_paper|evaporating_dish|electrode|atom|graph_axes|bulb|burette|conical_flask|battery_cell|resistor|lamp|magnet_bar|pivot|speech_bubble|mic","x":N,"y":N,"size":N}
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

# graph_axes carries a relationship for any subject; the rest are locked to the subject
# whose apparatus or phenomenon they actually draw.
_SHARED_TEMPLATES = """\
  graph_axes      labelled x/y axes for any graph of one quantity against another
                  variant REQUIRED: "line", "curve" or "empty"
                  slots: title, xlabel, ylabel"""

_SUBJECT_TEMPLATES = {
    "chemistry": """\
  distillation    burner + round flask + thermometer + condenser + beaker
                  USE FOR: separating liquids by boiling point, fractional distillation
  titration       burette over a conical flask
                  USE FOR: acid-base titration, indicator, endpoint, molar calculations
  filtration      funnel + filter paper over a conical flask
                  USE FOR: separating an insoluble solid from a liquid, residue and filtrate
  methane         tetrahedral CH4 ball-and-stick
                  USE FOR: the shape of a tetrahedral molecule, bond angle 109.5 degrees
  water           bent H2O with lone pairs
                  USE FOR: the shape of a bent molecule, bond angle 104.5, polarity
  energy_profile  reaction-coordinate energy curve — variant REQUIRED: "exo" or "endo"
                  USE FOR: chemical reactions giving out or taking in heat, activation energy
                  A correct pick looks like: {"type":"template","template":"titration",
                  "labels":{"titrant":"Acid in the burette","endpoint":"Pink with phenolphthalein"}}""",
    "physics": """\
  spring          a spring hanging from a fixed support beside a millimetre scale
                  variant REQUIRED: "unstretched", "loaded" (extension measured), "spring_balance"
                  slots: extension, load, reading
  free_body       one body with its force arrows
                  variant REQUIRED: "on_surface", "inclined_plane", "suspended"
                  slots: weight, normal, applied, friction, tension, angle
  lever           a rigid bar on a fulcrum with load, effort and the two arms
                  variant REQUIRED: "first_class", "second_class", "third_class"
                  slots: load, effort, load_arm, effort_arm
  circuit         cell + ammeter + resistor + switch in one loop
                  variant: "switch_closed", "switch_open", "voltmeter_across", "second_lamp",
                  "parallel_lamps"
                  slots: component
  wave            a wave drawn against its rest line
                  variant REQUIRED: "transverse" (crests, wavelength, amplitude), "longitudinal"
                  (particle rows with compressions and rarefactions)
                  slots: wavelength, amplitude, trough_note, crest_note, vibration,
                  compression, rarefaction
  curved_mirror   spherical mirror on the principal axis, object, two rays and the image
                  variant REQUIRED: "concave", "convex"
                  slots: title, object_note, image_note, virtual_note, use
  thin_lens       lens, principal axis, F and 2F marks, two rays and the image
                  variant REQUIRED: "converging", "diverging"
                  slots: title, object_note, image_note, power
  refraction      ray entering a denser medium, with the normal and the angles marked
                  variant REQUIRED: "glass_block", "prism"
                  slots: i, r, r1, r2, e, a, deviation, law, emerge, note
  magnetism       field lines and poles
                  variant REQUIRED: "bar_magnet", "solenoid", "two_wires"
                  slots: field, core
  vector_triangle two forces added as vectors, or several forces closing into equilibrium
                  variant REQUIRED: "parallelogram", "polygon"
                  slots: resultant, angle, rule, equilibrant
  electroscope    gold-leaf electroscope: disc, rod, leaves inside a jar
                  variant: "uncharged", "charged", "positive", "negative", "discharge"
                  slots: observation

Pick by the physics, not the picture:
  Hooke's law, spring balance, elastic limit ......... spring
  forces on one body, friction, inclined plane, tension  free_body
  moments, torque, lever classes, pulley systems ..... lever
  Ohm's law, series and parallel, ammeters, voltmeters, mains wiring ... circuit
  wave terms, ripples, light as a wave ............... wave/transverse
  sound, particles in a medium ....................... wave/longitudinal
  curved mirrors, image formation by reflection ...... curved_mirror
  lenses, image formation by refraction through a lens  thin_lens
  bending at a boundary, glass block, prism, dispersion, total internal reflection, optical fibre,
    real and apparent depth .......................... refraction
  magnets, field lines, electromagnet, solenoid, force between wires ... magnetism
  adding forces, resolving into components, equilibrium of forces ... vector_triangle
  static charges, charging by induction, leaking charge ... electroscope
  any quantity plotted against another (a = Ft, V = IR, T vs L) ... graph_axes
A correct pick looks like: {"type":"template","template":"refraction","variant":"glass_block",
"labels":{"law":"n = sin i / sin r","emerge":"Parallel ray, displaced sideways"}}
A WRONG pick looks like: using spring for a ray diagram, circuit for a prism, or
energy_profile for a Physics energy change — those drawings show something else entirely.""",
}


def _canvas_rules(subject):
    # The rules text is full of literal JSON braces, so the subject block is substituted
    # with a plain replace rather than str.format.
    key = (subject or "").replace("_", " ").strip().lower()
    block = _SUBJECT_TEMPLATES.get(key, "")
    joined = (block + "\n" + _SHARED_TEMPLATES) if block else _SHARED_TEMPLATES
    return CANVAS_BASE.replace("<<templates>>", joined)


def _lesson_system(subject):
    subject_name = _subject_name(subject)
    return f"""You are a KCSE {subject_name} tutor creating a visual whiteboard lesson for a secondary-school student in Kenya.
You output STRICT JSON only — no prose, no code fences, no commentary before or after the JSON.
Your reply must begin with the character {{ and end with the character }}.
{_canvas_rules(subject)}"""


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
        {{"type":"template","template":"graph_axes","variant":"line","labels":{{"title":"Relationship being taught","xlabel":"what is varied","ylabel":"what it changes"}}}}
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
        {{"type":"template","template":"graph_axes","variant":"line","labels":{{"title":"Relationship being taught","xlabel":"what is varied","ylabel":"what it changes"}}}}
      ]}}
  ]
}}

Use {lo} to {hi} steps so every concept is genuinely covered. Pick the template that matches each concept; only fall back to freeform shapes when no template fits, and never let them overlap or run off the board."""

# The notes the student's subject was supplied with, not a question bank, decide what a
# lesson teaches. The booklet is quoted in full and the model is told to stay inside it:
# a lesson that drifts beyond the notes is a lesson the student cannot revise from.
NOTES_LESSON_TEMPLATE = """Create a complete whiteboard lesson that teaches the WHOLE topic "{topic}" for a KCSE {subject_name} student.

What follows is the class notes this topic is actually taught from — a teacher's pages, in the order the student meets them. They are the authority for this lesson: teach what they say, and teach nothing they do not cover.

<notes>
{notes}
</notes>

Coverage rules — these are checked, and a lesson that breaks them is thrown away:
- Work through the notes in their own order. Every heading, every list and every worked example in them is something the student has to be taught; give each one at least one step.
- Keep the notes' definitions, formulae, units and constants exactly as they are written. Where they work an example with real numbers, teach that example with those numbers — do not invent a tidier one.
- The student cannot see the booklet's diagrams. Where the notes say "as shown below" or refer to an arrangement, describe the apparatus in words and draw it on the board yourself.
- Do NOT write exam questions for the student to answer, here or anywhere in the lesson — practice comes only from the real question bank. Use the notes' examples as teaching material, not as tests.
{outline}{style}
{adapt}

Structure the lesson:
1. First step orients the student: what the topic is and why the exam looks for it.
2. Then the notes in order — for each idea: the definition or law, the mechanism or diagram behind it, and one worked or illustrative example from the pages.
3. Weave in the traps, keywords, units and step order the notes themselves insist on.
4. Final step: a summary of the whole topic the student can revise from.

Because the board clears between steps, every step's drawing must stand alone: redraw any apparatus, scheme or graph the step refers to.

Output JSON in exactly this shape (most steps use ONE template action, optionally plus a label):
{{
  "title": "Lesson title",
  "steps": [
    {step_shape}
  ]
}}

Use {lo} to {hi} steps so the whole topic is genuinely covered. Pick the template that matches each idea; only fall back to freeform shapes when no template fits, and never let them overlap or run off the board."""

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
    "spring": {"variants": ["unstretched", "loaded", "spring_balance"],
               "slots": ["extension", "load", "reading"]},
    "free_body": {"variants": ["on_surface", "inclined_plane", "suspended"],
                  "slots": ["weight", "normal", "applied", "friction", "tension", "angle"]},
    "lever": {"variants": ["first_class", "second_class", "third_class"],
              "slots": ["load", "effort", "load_arm", "effort_arm"]},
    "circuit": {"variants": ["switch_closed", "switch_open", "voltmeter_across",
                             "second_lamp", "parallel_lamps"], "slots": ["component"]},
    "wave": {"variants": ["transverse", "longitudinal"],
             "slots": ["wavelength", "amplitude", "trough_note", "crest_note", "vibration",
                       "compression", "rarefaction"]},
    "curved_mirror": {"variants": ["concave", "convex"],
                      "slots": ["title", "object_note", "image_note", "virtual_note", "use"]},
    "thin_lens": {"variants": ["converging", "diverging"],
                  "slots": ["title", "object_note", "image_note", "power"]},
    "refraction": {"variants": ["glass_block", "prism"],
                   "slots": ["i", "r", "r1", "r2", "e", "a", "deviation", "law", "emerge", "note"]},
    "magnetism": {"variants": ["bar_magnet", "solenoid", "two_wires"], "slots": ["field", "core"]},
    "vector_triangle": {"variants": ["parallelogram", "polygon"],
                        "slots": ["resultant", "angle", "rule", "equilibrant"]},
    "electroscope": {"variants": ["uncharged", "charged", "positive", "negative", "discharge"],
                     "slots": ["observation"]},
}

# Most physics variants are the meaningful half of the diagram — a template drawn without
# one renders a bare frame the student learns nothing from. When the model names a
# catalogued template but leaves out (or invents) the variant, supply the common case.
DEFAULT_VARIANT = {
    "spring": "loaded",
    "free_body": "on_surface",
    "wave": "transverse",
    "curved_mirror": "concave",
    "thin_lens": "converging",
    "refraction": "glass_block",
    "magnetism": "bar_magnet",
    "vector_triangle": "parallelogram",
    "lever": "first_class",
    "circuit": "switch_closed",
    "graph_axes": "empty",
    "electroscope": "uncharged",
}

# What the model picks a diagram by is its eye, not the physics: given a refraction lesson it
# happily draws a spring because a spring is "lines on a board". The notes already say which
# phenomena a topic covers, so they decide the allowed diagrams in code instead of hoping the
# prompt is obeyed. A wrong diagram teaches something false; a substituted one does not.
# Phrases must be specific to the phenomenon — bare "cell", "tension" or "magnet" describes
# half the syllabus, and a whole-word match keeps "magnet" out of "electromagnetic".
_TEMPLATE_TRIGGERS = {
    "spring": ("hooke", "spring balance", "spring", "elastic limit", "force-extension",
               "force extension", "unstretched", "loaded spring"),
    "free_body": ("free body", "free-body", "forces acting on", "normal reaction", "upthrust",
                  "inclined plane", "frictional force", "force of friction", "resultant force",
                  "tension in the string", "on a surface", "buoyant", "float", "sink"),
    "lever": ("lever", "fulcrum", "turning effect", "moment of", "moments", "torque", "pulley",
              "wheel and axle", "mechanical advantage", "velocity ratio", "centre of gravity",
              "bottle opener", "wheelbarrow", "crowbar", "tongs", "see-saw", "pair of scissors"),
    "circuit": ("circuit", "ammeter", "voltmeter", "ohm", "resistor", "battery", "emf", "kwh",
                "short circuit", "wiring", "heating effect of", "live wire", "parallel lamp",
                "potential difference", "current through", "electric heater"),
    "wave": ("wave", "wavelength", "crest", "trough", "ripple", "frequency", "amplitude",
             "transverse", "longitudinal", "compression", "rarefaction", "vibration"),
    "curved_mirror": ("concave", "convex", "spherical mirror", "curved mirror", "mirror",
                      "principal focus", "centre of curvature", "radius of curvature"),
    "thin_lens": ("lens", "converging", "diverging", "eyepiece", "objective lens",
                  "magnifying glass", "spectacles", "projector"),
    "refraction": ("refraction", "refractive", "prism", "glass block", "critical angle",
                   "total internal", "optical fibre", "optical fiber", "apparent depth",
                   "dispersion", "snell", "spectrum"),
    "magnetism": ("magnetic field", "bar magnet", "magnet", "electromagnet", "solenoid",
                  "field line", "magnetic pole", "coil of wire", "magnetic flux", "domains"),
    "vector_triangle": ("vector", "resultant", "parallelogram law", "resolution of forces",
                        "triangle of forces", "equilibrant", "vectors in equilibrium",
                        "perpendicular components"),
    "electroscope": ("electroscope", "gold leaf", "leaf diverge", "leaves diverge",
                     "electrostatic", "charging by", "coulomb", "discharge", "capacitor"),
}

_GATE_MATCHES = {
    name: [re.compile(rf"\b{re.escape(p)}(?:es|s)?\b") for p in trigs]
    for name, trigs in _TEMPLATE_TRIGGERS.items()
}

# Which drawing of a template a step wants, when the template has more than one shape. Read off
# the step's own words, because a dispersion step and a glass-block step share one topic.
_VARIANT_HINTS = {
    "wave": (("longitudinal", ("longitudinal", "sound", "compression", "rarefaction")),),
    "thin_lens": (("converging", ("converging", "convex lens", "real image", "magnif")),
                  ("diverging", ("diverging", "concave lens"))),
    "refraction": (("prism", ("prism", "dispersion", "spectrum", "deviation")),
                   ("glass_block", ("glass block", "apparent depth", "critical angle",
                                    "total internal"))),
    "curved_mirror": (("convex", ("convex",)), ("concave", ("concave",))),
    "free_body": (("inclined_plane", ("inclined plane", "slope", "ramp")),
                  ("suspended", ("suspended", "hangs", "ceiling", "string"))),
    "circuit": (("switch_open", ("open switch", "open circuit")),
                ("parallel_lamps", ("in parallel", "parallel lamp")),
                ("voltmeter_across", ("voltmeter",))),
    "magnetism": (("solenoid", ("solenoid", "coil", "electromagnet")),
                  ("two_wires", ("two wires", "between the wires"))),
    "electroscope": (("uncharged", ("uncharged", "no charge")),
                     ("positive", ("positively charged",)),
                     ("negative", ("negatively charged",))),
    "spring": (("unstretched", ("unstretched", "no load", "original length")),
               ("spring_balance", ("spring balance", "newton meter", "force meter"))),
    "lever": (("second_class", ("wheelbarrow", "second class")),
              ("third_class", ("tongs", "fishing rod", "third class"))),
}


# A lens lesson borrows most of a mirror lesson's words — focus, centre of curvature, convex,
# concave — and a ray diagram of the wrong apparatus teaches the wrong thing, so among the
# competing ray diagrams only the topic's own strongest description survives.
_EXCLUSIVE_GROUPS = ({"thin_lens", "curved_mirror", "refraction"},)


def _phrase_hits(regexes, phrases, text):
    """Matched phrases, with a short one swallowed by a longer match counted only once:
    "spring balance" and "spring" are the same piece of evidence, not two."""
    hit = [p for p, rx in zip(phrases, regexes) if rx.search(text)]
    return [p for p in hit if not any(p != q and p in q for q in hit)]


def template_gate(subject, notes_text):
    """Which templates the supplied notes describe, best first; None when there are no notes."""
    key = (subject or "").replace("_", " ").strip().lower()
    text = (notes_text or "").lower()
    if key != "physics" or not text:
        return None
    scored = sorted(((len(_phrase_hits(rxs, _TEMPLATE_TRIGGERS[name], text)), name)
                     for name, rxs in _GATE_MATCHES.items()), key=lambda row: (-row[0], row[1]))
    for group in _EXCLUSIVE_GROUPS:
        winner = next((n for s, n in scored if n in group and s >= 2), None)
        if winner:
            scored = [(s, n) for s, n in scored if n not in group or n == winner]
    # Two separate phrases before a diagram counts, and only the three strongest survive: one
    # stray word in a topic's notes must not lock out the diagram the topic really needs.
    top = [name for n, name in scored if n >= 2][:3]
    return {"allowed": set(top) | {"graph_axes"}, "primary": top[0] if top else None}


# A substituted diagram has nowhere honest to put the teacher's own wording. Nothing in any
# template draws above y=80, so the band at the top of the board is the only free space.
_SPILL_Y = (44, 68)


def _matched_variant(hints, text):
    """The variant this text asks for, or None when it does not clearly ask for one."""
    hit = [name for name, phrases in hints if any(p in text for p in phrases)]
    return hit[0] if len(hit) == 1 else None


def _step_gate(gate, narration):
    """The gate for one step: the same allowed diagrams, but the variant read from that step's
    own words — a dispersion step wants the prism, the step before it wants the glass block."""
    if not gate or not gate.get("primary"):
        return gate
    return {**gate, "hint": _matched_variant(_VARIANT_HINTS.get(gate["primary"], ()),
                                             (narration or "").lower())}


def _spilled_labels(labels, keep):
    out = []
    for key, value in (labels or {}).items():
        if key in keep or not isinstance(value, str) or not value.strip():
            continue
        out.append({"type": "label", "text": value.strip()[:70], "x": 56, "y": _SPILL_Y[len(out)],
                    "size": 15, "color": "#BFB09C"})
        if len(out) == 2:
            break
    return out


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


def _sanitize_actions(actions, gate=None):
    """Keep templates (if known) and freeform shapes that pass bounds+overlap; drop the rest."""
    kept, boxes = [], []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        if t == "template":
            name = a.get("template")
            spec = TEMPLATE_CATALOG.get(name)
            # The notes name the phenomenon, so a diagram of something else is a mistake, not a
            # style choice — and so is a template name the renderer does not own. With nothing in
            # the notes to draw, the model's own pick stands unjudged.
            judged = bool(gate and gate.get("primary"))
            if judged and (spec is None or name not in gate["allowed"]):
                labels = a.get("labels") or {}
                spec = TEMPLATE_CATALOG[gate["primary"]]
                keep = {k: v for k, v in labels.items() if k in spec["slots"]}
                kept.append({"type": "template", "template": gate["primary"],
                             "variant": gate.get("hint") or DEFAULT_VARIANT.get(gate["primary"]),
                             "labels": keep})
                kept.extend(_spilled_labels(labels, keep))
                continue
            if spec:
                # An invented variant would silently fall back to the bare base diagram,
                # so never keep a name we don't have geometry for.
                variant = a.get("variant")
                if variant and variant not in spec["variants"]:
                    variant = None
                if not variant:
                    variant = (gate.get("hint") if judged and gate["primary"] == name else None) \
                        or DEFAULT_VARIANT.get(name)
                kept.append({**a, "variant": variant} if variant else a)
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


_STEP_NARRATION = ('"narration": "One or two spoken sentences the student hears and reads '
                   'for this step (plain, encouraging, exam-focused)."')
_STEP_ACTIONS = ("""      "actions": [
        {"type":"template","template":"graph_axes","labels":{"title":"...","xlabel":"...","ylabel":"..."}}
      ]""")


def _plain_step_shape():
    return "{%s,\n%s}" % (_STEP_NARRATION, _STEP_ACTIONS)


def _annotated_step_shape():
    return ('{"concept": "a slug from the list above",\n      %s,\n%s}'
            % (_STEP_NARRATION, _STEP_ACTIONS))


def _style_block(example_questions):
    """Past papers, used only to set the depth a lesson has to reach. Empty when the
    topic has no bank — the notes alone then decide the scope, which is honest: a
    subject can be taught before it can be examined."""
    if not example_questions:
        return ""
    return ("\nThese real KCSE past-paper questions from this topic show the STYLE the "
            "notes lead to — the wording, the command words and the depth KNEC expects. "
            "Use them for depth only:\n"
            + "\n".join(f"- {q}" for q in example_questions[:8]) + "\n")


def generate_lesson(subject, topic, example_questions, adapt_context, concept_list=None,
                    notes=None):
    """Build a lesson.

    Supplied notes outrank everything else: they are the content, so the lesson walks
    through them in their own order. Next comes the hand-authored concept map, which
    fixes the concepts and their teaching order and tags every step; without either,
    the past-paper questions define the scope, as they did before any of this existed.
    A concept map that arrives alongside notes only adds step tags to the notes."""
    subject_name = _subject_name(subject)
    topic_name = curriculum.topic_name(subject, topic)
    system = _lesson_system(subject)
    slugs = [c["slug"] for c in (concept_list or [])]
    gate = template_gate(subject, notes)
    if notes:
        lo = max(8, min(20, len(notes.split()) // 160))
        user = NOTES_LESSON_TEMPLATE.format(
            topic=topic_name, subject_name=subject_name, notes=notes,
            outline=("\nThis topic also has a concept map. Tag every step with the concept "
                     "it teaches — \"concept\": \"<slug>\", copied exactly from this list — "
                     "and keep this teaching order:\n"
                     + "\n".join(f"{i}. {c['slug']} — {c['name']}"
                                 for i, c in enumerate(concept_list or [], 1)) + "\n"
                     if slugs else ""),
            style=_style_block(example_questions), adapt=adapt_context,
            lo=lo, hi=min(24, lo + 5),
            step_shape=_annotated_step_shape() if slugs else _plain_step_shape(),
        )
    elif slugs:
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
            return normalize_lesson(raw, slugs, gate)
        last_err = ValueError("invalid lesson: " + "; ".join(problems[:5]))
    if last_raw is not None:
        # Never dead-end the student: sanitize the best attempt instead of failing outright.
        return normalize_lesson(last_raw, slugs, gate)
    raise last_err or ValueError("lesson generation failed")


def normalize_lesson(lesson, allowed_concepts=None, gate=None):
    if not isinstance(lesson, dict) or not isinstance(lesson.get("steps"), list) or not lesson["steps"]:
        raise ValueError("Lesson JSON missing 'steps'")
    allowed = set(allowed_concepts or ())
    steps = []
    for step in lesson["steps"]:
        narration = str(step.get("narration", "")).strip()
        actions = _sanitize_actions(step.get("actions", []), _step_gate(gate, narration))
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
    system = _lesson_system(subject)
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
