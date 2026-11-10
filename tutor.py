"""LLM-powered tutoring: whiteboard lesson generation, interruptible Q&A, answer diagnosis."""

import json
import re
import time

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
  distillation    one connected train: burner under gauze, flask clamped at the neck, thermometer
                  bulb level with the side arm, tilted condenser with water in low and out high,
                  delivery tube hanging above the receiver
                  USE FOR: separating liquids by boiling point, fractional distillation
  titration       burette held by a boss on a retort stand over a conical flask on a white tile
                  USE FOR: acid-base titration, indicator, endpoint, molar calculations
  filtration      funnel seated on an iron ring, paper folded inside, stem tip against the beaker
                  wall, pouring down a glass rod
                  USE FOR: separating an insoluble solid from a liquid, residue and filtrate
                  slots for filtration: residue, filtrate, funnel, paper
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
  capacitor       two facing plates wired to a cell, or several capacitors wired together
                  variant REQUIRED: "air_gap", "dielectric", "series", "parallel", "variable"
                  slots: title, gap, note, effect, rule, pds

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
  capacitors, capacitance, dielectric in a gap, charging and discharging, capacitors in series or
    parallel, variable capacitor ........................ capacitor
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


# ---------- the lesson SCRIPT: what the generator produces ----------
#
# The model writes a lesson the way a teacher plans one — say this, write this, ring the
# answer — and never touches a coordinate. The whiteboard engine owns the sheet: it
# measures the real glyphs of every line, lays rows down in order and places every mark on
# the words it names. A script that tries to do any of that itself is rejected.

# What a good lesson is asked to look like, as a version. It rides in the cached lesson's
# signature, so changing the shape below — not only the notes or the concept map behind a
# topic — retires the lessons written under the old one instead of serving them forever.
SCRIPT_VERSION = "question-before-answer-1"

SCRIPT_BASE = """THE BOARD IS PLAYED, NOT DRAWN BY YOU. You write a lesson script: operations the
whiteboard engine performs in order, while the same words are spoken out loud. One operation per
line, and nothing else on the line.
You never choose a position, a size, a colour code or a canvas — the engine measures the real
handwriting and places every line itself. Any coordinate in your script is a bug and that
operation is thrown away.

Operations (every one of these keys is exactly as written):
  {"op":"caption","text":"Title of this sheet of board — at most 6 words"}
  {"op":"say","text":"One or two spoken sentences. This is also what appears as the caption line."}
  {"op":"write","text":"What the hand writes on the board","id":"short_snake_id","row":0-6,"color":"accent"}
  {"op":"ring","target":"id","find":"exact words inside that write"}
  {"op":"underline","target":"id","find":"exact words inside that write"}
  {"op":"arrow","target":"id","find":"exact words","from":"another id on the same sheet"}
  {"op":"tick","target":"id"}
  {"op":"figure","name":"template","variant":"...","labels":{"slot":"short text"},"row":0-6}
  {"op":"pause","ms":400}

Hard rules, all of them checked:
- A caption starts a sheet. Seven rows fit on a sheet, so use a new caption before row 0 again.
- "row" is which line of the sheet, counting from 0 at the top — never a pixel.
- "write" is what a student would copy into an exercise book. A step of the working is ONE short
  line of at most 48 characters: a formula, a substitution, the answer, a phrase. A question is
  allowed 96, and the engine bends it over two rows on its own. Nothing runs past 96, and no
  working line repeats a sentence you have also said.
- EVERY WORKED EXAMPLE BEGINS BY WRITING ITS QUESTION. The first write on the sheet is the whole
  question, in the words the notes use, with an "id" of its own so the answer can be rung under it
  and an arrow drawn back to it. Reading a question out and then writing only the answer leaves
  the student with a board that only makes sense to someone who happened to remember being told.
  The question is the one thing on the board you also say aloud: say it while the hand writes it.
  Name that write's id "question" and the answer's id "answer" — a sheet that writes an answer it
  never asked is thrown away, and the engine is the one that checks.
- "say" is heard once, by someone meeting the idea for the first time. Short sentences, plain
  words, one idea in each — and talk about the line the hand is writing at that moment, not the
  one it finished two sheets ago.
- "id" is required on any line a later ring / underline / arrow / tick points at, and must be
  unique within the sheet. "find" must appear character-for-character inside that write's text,
  so quote the words instead of paraphrasing them.
- "figure" draws real apparatus, a ray diagram, a molecule or an energy curve. Use it on most
  sheets that have a physical thing to show. Only names listed below exist, and only the slots
  listed for that name may be filled.
- Colour is a word, not a hex: leave "color" out for the working, or "accent" for the one thing
  on the sheet the student must look at — the answer, the product, the peak. One accent per sheet.
- Worked examples are written line by line the way a teacher does it: the question, then the given
  data, then the formula, then the substitution, then the answer. Ring or tick the answer after
  writing it.
- A sheet holds a thought, so it holds two to five writes — and the question it starts with is one
  of them. One write per sheet is a slideshow, and the hand would spend half the lesson standing
  still."""

FIGURE_LETTERS = """FIGURES AVAILABLE TO YOU (name — variants — slots):
<<figures>>
A figure you have not been given here does not exist; if none of them fits the idea, teach the
idea in written lines instead."""


def _figure_docs(subject):
    key = (subject or "").replace("_", " ").strip().lower()
    block = _SUBJECT_TEMPLATES.get(key, "")
    return (block + "\n" + _SHARED_TEMPLATES) if block else _SHARED_TEMPLATES


def _script_system(subject):
    return f"""You are a KCSE {_subject_name(subject)} tutor planning one continuous whiteboard
lesson for a secondary-school student in Kenya, to be played back with your words spoken aloud.
Your whole reply is the lesson script and nothing else: ONE operation per line, each line a
complete JSON object on its own. No array brackets, no commas between the lines, no indentation,
no code fences, no commentary before or after.
{SCRIPT_BASE}
{FIGURE_LETTERS.replace("<<figures>>", _figure_docs(subject))}"""


def _script_head():
    """The shape, in the same one-per-line form the model is asked to write in."""
    return ('{"op":"caption","text":"What this sheet is about"}\n'
            '{"op":"say","text":"The sentence the tutor says out loud."}\n'
            '{"op":"write","text":"20 g of salt is dissolved in 100 cm3 of water. How many '
            'moles of salt are in the solution?","id":"question","row":0}\n'
            '{"op":"write","text":"n = m/M = 20/100","id":"working","row":2}\n'
            '{"op":"write","text":"n = 0.2 mol","id":"answer","row":3,"color":"accent"}\n'
            '{"op":"ring","target":"answer","find":"0.2 mol"}')


SCRIPT_SHAPE_NOTES = """Teach the topic as a teacher would at the board, in this order:
1. HOOK — one sheet that shows why the topic matters, and the single idea the whole lesson rests on.
2. RULE — at most ONE short written line for the rule or formula. Everything after that is worked.
3. FOUR TO SIX WORKED EXAMPLES, easiest first, each on its own sheet. At least 70% of the whole
   lesson must be examples being worked, not explanations being read out. One idea per example.
   For each, in this order: write the WHOLE QUESTION as the first line of the sheet and read it
   out as the hand writes it, then write the data, then the substitution, then the answer, then
   ring or tick the answer and say one sentence about why it came out that way. A sheet whose
   first mark is a number is a sheet the student cannot follow after the lesson is over.
4. TRAPS — the mistakes students actually make, shown by working the wrong way and crossing it out.
5. YOU TRY — one example where the saying guides but the board shows the question in full and
   nothing of the working under it.
6. MINI-CHECK then RECAP — two spoken questions and then the three lines worth copying down.

The board keeps what a student copies out of the exercise book: the question, the working, the
answer. The teaching that connects them is spoken, so write a line and then say the sentence
about it — never a paragraph of explanation under the chalk."""

NOTES_SCRIPT_TEMPLATE = """Plan a complete lesson that teaches the WHOLE topic "{topic}" to a
KCSE {subject_name} student, as one continuous board rather than a set of slides.

What follows is the class notes this topic is actually taught from — a teacher's pages, in the
order the student meets them. They are the authority for this lesson: teach what they say, and
teach nothing they do not cover.

<notes>
{notes}
</notes>

Coverage rules — these are checked, and a lesson that breaks them is thrown away:
- Work through the notes in their own order. Every heading, list and worked example in them has
  to be taught; give each one at least one sheet.
- Keep the notes' definitions, formulae, units and constants exactly as written. Where the notes
  work an example with real numbers, teach that example with those numbers — do not invent a
  tidier one, and write its question on the board in the notes' own words before you work it.
- The student cannot see the booklet's diagrams. Where the notes say "as shown below" or refer to
  an arrangement, describe it in "say" and draw it with "figure" if a figure exists for it.
- Do NOT set the student a question to answer. Writing a question on the board and working it
  through is teaching; leaving it on the board for them to try is not, and practice comes only
  from the question bank. Use the notes' examples as teaching material.
{outline}{style}
{adapt}

{shape}

Write the script itself, nothing else: ONE operation per line, exactly in this shape,
{shape_example}

Use {lo} to {hi} sheets. Every sheet needs a caption, at least one say, and at least one write
or figure."""

CONCEPT_SCRIPT_TEMPLATE = """Plan a complete lesson that teaches the WHOLE topic "{topic}" to a
KCSE {subject_name} student, as one continuous board rather than a set of slides.

The topic is defined by this concept map. These are the concepts you MUST teach, in this order:
{concept_list}

Coverage rules — these are checked, and a lesson that breaks them is thrown away:
- Teach EVERY concept listed, in the order given. Do not skip one, do not merge two unless they
  are genuinely one idea, and do not invent concepts that are not on the list.
- Put "concept": "<slug>" on the first op of every sheet, copied exactly from the list.
- Give the bigger concepts more sheets; every concept gets at least one.
- Every worked example you take from the questions below starts by writing that question on the
  board, in its own words, and then works it. A number on the board with no question over it is
  the thing students cannot revise from.

These real past-paper questions show the STYLE to teach towards — the command words, the mark
allocations and the depth KNEC expects. Use them for depth only, and never as a question you
leave the student to answer on their own:
{examples}

{adapt}

{shape}

Write the script itself, nothing else: ONE operation per line, exactly in this shape,
{shape_example}

Use {lo} to {hi} sheets. Every sheet needs a caption, at least one say, and at least one write
or figure."""

PLAIN_SCRIPT_TEMPLATE = """Plan a complete lesson that teaches the WHOLE topic "{topic}" to a
KCSE {subject_name} student, as one continuous board rather than a set of slides. Every concept
in the topic, in teaching order — a student who finishes it must handle any exam question on it.

The past-paper questions the topic is examined by. Take each worked example's question from
them and write it on the board in full before working it — never set one for the student to
answer and move on:
{examples}

{adapt}

{shape}

Write the script itself, nothing else: ONE operation per line, exactly in this shape,
{shape_example}

Use {lo} to {hi} sheets. Every sheet needs a caption, at least one say, and at least one write
or figure."""



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

"answer" is what you SAY out loud while the lesson is paused, so it has to finish before
the student loses the thread: a clear, self-contained spoken explanation in plain prose a
Kenyan secondary-school student understands, grounded in the concept the paused step is
teaching. Structure it as: (1) a direct one-sentence answer, (2) the WHY in one or two
sentences — the mechanism, not a list of facts, (3) the exact keywords, units or step
order the marking scheme rewards, tied back to where this sits in the topic.
3 to 4 sentences, at most 90 words. Teach the idea properly — never just restate the
question or give a bare conclusion.
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
    "filtration": {"variants": [], "slots": ["residue", "filtrate", "funnel", "paper"]},
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
    "capacitor": {"variants": ["air_gap", "dielectric", "series", "parallel", "variable"],
                  "slots": ["title", "gap", "note", "effect", "rule", "pds"]},
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
    "capacitor": "air_gap",
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
                     "electrostatic", "charging by", "coulomb", "discharge"),
    "capacitor": ("capacitor", "capacitance", "farad", "dielectric", "parallel plate",
                  "mica", "ceramic", "electrolytic", "variable capacitor"),
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
    "capacitor": (("dielectric", ("dielectric", "mica", "ceramic", "electrolytic", "oil")),
                  ("series", ("in series", "series combination", "series arrangement")),
                  ("parallel", ("in parallel", "parallel combination", "parallel arrangement")),
                  ("variable", ("variable capacitor", "tuning", "trimmer", "mesh"))),
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
                # Which drawing of the apparatus a step wants is better read from that step's
                # own words than from the guess the model made for the lesson as a whole.
                hint = gate.get("hint") if judged and gate["primary"] == name else None
                variant = hint or variant or DEFAULT_VARIANT.get(name)
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


# ---------- the script's own validator ----------
#
# Everything checkable about a script is checked here, before a student ever sees it: that
# the ops are real, that a mark names a line that exists on the same sheet, that "find" is
# quoted rather than paraphrased, that no line is a paragraph, that the board is not asked
# for a row past the bench, and above all that the model has kept its hands off the geometry.

SCRIPT_OPS = {"caption", "say", "write", "ring", "underline", "arrow", "tick", "figure", "pause"}
MARK_OPS = {"ring", "underline", "arrow", "tick"}
ROW_LIMIT = 7
LINE_LIMIT = 96
# How many characters the engine's own hand fits across one row of the sheet, at the size
# it writes at. Measured off Caveat 600 in the browser against the 880 units of board
# between the margins: 15.7 units a character in prose, which is fifty-six a row, and 17.6
# in the fractions and superscripts a step of working is made of, which is forty-nine. The
# tighter figure is the one to book rows with, because this number decides how much room a
# line is given: guessing too few leaves air on the sheet, guessing too many writes the next
# line straight through it.
CHARS_PER_ROW = 49
_SHEET_LIMIT = 26
# What one lesson is asked for when nothing fixes its size. Every sheet is a caption, a
# spoken line and two to five writes, so the count is what the student waits through.
LO_PLAIN, HI_PLAIN = 8, 12

# Characters a second the narrator gets through. Measured at 14.5 on the local voice at its
# own pace, then scaled by the pace the lesson is actually spoken at, so a line the audio
# has not been heard yet is timed the way the audio would time it. The board's own fallback
# uses the same figure (whiteboard.js, _estimate).
SPEECH_CHARS_PER_SEC = 14.5 * llm.NARRATION_SPEED
# The longest breath the tutor is allowed to take before the board moves on. Anything past
# twenty seconds of one sentence is a lecture, and the tail of it would be cut off anyway:
# two short sentences are an explanation, and the student gets a gap between them to look at
# what the hand just wrote.
SAY_LIMIT = 260

# A lesson is written while the student watches a loading board, so the whole job — both
# attempts, and every retry the provider makes inside them — has one wall-clock ceiling.
# Measured on the free tiers this runs on, a good script arrives in about 50 seconds; past
# two and a half minutes the queue is the problem, not the lesson, and the caller still has
# the last lesson cached to open instead.
LESSON_BUDGET = 150.0
# Anything that looks like this in a reply is the model reaching for the canvas, which
# it does not own. The engine measures every one of these off real glyphs.
_GEO_KEYS = {"x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "size", "width",
             "points", "w", "h"}

_FIGURE_NAMES = {
    "chemistry": {"distillation", "titration", "filtration", "methane", "water", "energy_profile"},
    "physics": {"spring", "free_body", "lever", "circuit", "wave", "curved_mirror", "thin_lens",
                "refraction", "magnetism", "vector_triangle", "electroscope", "capacitor"},
}


def _figure_names(subject):
    key = (subject or "").replace("_", " ").strip().lower()
    return {"graph_axes"} | _FIGURE_NAMES.get(key, set())


def _as_text(v):
    return str(v if v is not None else "").strip()


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _escape_inner_quotes(line):
    """Put the missing backslash in front of a quote the model left open inside a value.

    {"op":"say","text":"the acid "eats" the metal"} is one character away from valid JSON,
    and it is the mistake these replies really make. A quote only closes a string when what
    follows it could follow a closing quote."""
    out, in_str, esc = [], False, False
    for i, ch in enumerate(line):
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = in_str
            continue
        if ch == '"':
            if in_str:
                if line[i + 1:].lstrip()[:1] in (",", ":", "}", "]"):
                    in_str = False
                    out.append(ch)
                else:
                    out.append('\\"')
                continue
            in_str = True
        out.append(ch)
    return "".join(out)


def _iter_objects(text):
    """Every top-level {...} in the reply, in order, ignoring braces inside strings."""
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield text[start:i + 1]


def _read_object(chunk):
    """One JSON object, repaired once if a quote was left open inside a value."""
    text = chunk.strip().rstrip(",").strip()
    for candidate in (text, _escape_inner_quotes(text)):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def parse_script(text, title=None, repairs=None):
    """Read a lesson reply operation by operation, wherever the model put the line breaks.

    A lesson used to be one nested JSON blob, so a single unescaped quote — or a reply cut
    off by the output budget — threw thirty seconds of writing away and left the student
    with an error. Read object by object instead: one broken operation costs that operation,
    a reply that ran out of room keeps everything it finished, and a model that ignores the
    one-per-line instruction and returns a wrapped {"steps": [...]} still works."""
    body = _as_text(text)
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1)
    steps, broken = [], 0
    for chunk in _iter_objects(body):
        obj = _read_object(chunk)
        if obj is None:
            if '"op"' in chunk:
                broken += 1
            continue
        if isinstance(obj.get("steps"), list):
            steps.extend(o for o in obj["steps"] if isinstance(o, dict) and o.get("op"))
            title = _as_text(obj.get("title")) or title
        elif obj.get("op"):
            steps.append(obj)
    if repairs is not None and broken:
        repairs.append(f"{broken} operation(s) in the reply could not be read, so they "
                       "were left out")
    return {"title": _as_text(title) or "Lesson", "steps": steps}


_QUESTION_LEADS = ("determine", "find", "calculate", "compute", "state", "explain", "define",
                   "name", "what", "how", "why", "which", "work out", "give", "show", "write")
# These ask wherever they sit in a sentence, because a line of working does not use them
# any other way.
_QUESTION_MIDS = ("what ", "how ", "why ", "which ")
_ANSWER_LEADS = ("answer", "ans", "solution", "solve")


def _lead_of(text):
    """A line's own words with the labels a teacher puts in front of them taken off:
    "a) ", "1. ", "Example:", "Q3." — the question is the sentence underneath."""
    return re.sub(r"^\s*(?:example|question|q\s*\d+|[a-h]\s*[\.\)]|\d+\s*[\.\)])\s*[:\-]?\s*",
                  "", text, flags=re.I).lower()


def _writes_a_question(wid, text):
    """A line that puts an example's question on the board: it asks in its own words, or the
    model titled the write as the question."""
    lead = _lead_of(text)
    return ("?" in text or (wid or "").lower().startswith("question")
            or any(lead.startswith(w) for w in _QUESTION_LEADS)
            or any(w in lead for w in _QUESTION_MIDS))


def _is_answer_line(wid, text):
    """A line that finishes an example with its answer — named as one, or written as one.
    Only these two shapes are counted: an accented number can be a formula a rule sheet is
    standing out, and calling that an answer would fault a sheet that never claimed to be
    worked example."""
    lead = _lead_of(text)
    return ((wid or "").lower().startswith(("answer", "result", "final"))
            or any(lead.startswith(w) for w in _ANSWER_LEADS))


def validate_script(script, allowed_concepts=None, gate=None, subject=None):
    """A list of human-readable problems; empty means the engine can play this script."""
    problems = []
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list) \
            or not script["steps"]:
        return ["the reply must be an object with a non-empty 'steps' list of operations"]
    # A runaway reply, not a full lesson. A sheet of worked example is a caption, a spoken
    # line, the question and the working under it and a mark on the answer — so eight
    # operations is the honest ceiling per sheet, and anything past that is the model
    # writing past its own lesson.
    if len(script["steps"]) > _SHEET_LIMIT * 8:
        return ["the script is far too long — keep it to {} to {} sheets".format(3, _SHEET_LIMIT)]

    allowed = set(allowed_concepts or ())
    figures = _figure_names(subject)
    sheet = None            # caption text of the sheet being read
    rows = 0                # rows used on the current sheet
    ids = {}                # id -> full text, for the current sheet only
    accents = 0
    shown = False           # has the current sheet anything on it yet
    asked = False           # has this sheet's question been written
    asked_faulted = False   # ... and has the missing one been said once already
    sheets = 0
    writes = 0
    say_lines = 0

    def _new_sheet():
        """The board wipes when a line will not fit, and the work carries on underneath.
        The engine does that on its own, so counting it as a sheet here — rather than
        calling it a fault and making the model write the whole lesson again — is the
        honest reading of the same script."""
        nonlocal sheet, rows, ids, accents, shown, sheets
        sheet = f"{sheet} (continued)" if sheet else "Untitled sheet"
        # The question the hand put up belongs to the work, not to the piece of board it
        # was written on, so a continuation sheet starts out having asked it.
        rows, ids, accents, shown = 0, {}, 0, False
        sheets += 1

    for i, op in enumerate(script["steps"]):
        if not isinstance(op, dict):
            problems.append(f"step {i}: an operation must be an object")
            continue
        kind = op.get("op")
        if kind not in SCRIPT_OPS:
            problems.append(f"step {i}: '{kind}' is not an operation the board understands")
            continue
        stray = _GEO_KEYS & set(op)
        if stray:
            problems.append(f"step {i}: the board owns the geometry — remove {', '.join(sorted(stray))}")
        if kind == "caption":
            if not _as_text(op.get("text")):
                problems.append(f"step {i}: a caption needs the sheet's title")
            if sheets and not shown:
                problems.append(f"'{sheet}': a sheet the hand never writes on is not a sheet")
            sheet = _as_text(op.get("text"))
            rows, ids, accents, shown, asked, asked_faulted = 0, {}, 0, False, False, False
            sheets += 1
            continue
        if kind == "say":
            text = _as_text(op.get("text"))
            if not text:
                problems.append(f"step {i}: a spoken line with nothing in it")
            if len(text) > SAY_LIMIT:
                problems.append(f"step {i}: say at most two sentences before the board moves on")
            if not text.endswith((".", "!", "?")):
                problems.append(f"step {i}: 'say' must end in a full stop so the narrator stops cleanly")
            say_lines += 1
            continue
        if kind == "pause":
            if not (100 <= (op.get("ms") or 0) <= 2000):
                problems.append(f"step {i}: a pause is between 100 and 2000 milliseconds")
            continue
        if kind == "write":
            text = _as_text(op.get("text"))
            if not text:
                problems.append(f"step {i}: a write with nothing in it")
                continue
            if len(text) > LINE_LIMIT:
                problems.append(f"step {i}: '{text[:24]}…' is {len(text)} characters — the board holds "
                                f"{LINE_LIMIT} on a line, however wide the question is; say the rest")
            # The engine wipes the board when the rows run out, whatever row number the
            # next line asks for, so an eighth line of working is a new sheet rather than a
            # fault. Counting it on the requested row instead would call it a second
            # "line1" on the old sheet and reject a lesson the board plays happily.
            if rows >= ROW_LIMIT:
                _new_sheet()
            row = op.get("row")
            if row is not None and not (isinstance(row, (int, float)) and 0 <= int(row) < ROW_LIMIT):
                problems.append(f"step {i}: row must be 0 to {ROW_LIMIT - 1}")
                row = None
            # A wide line is wrapped onto the rows under it by the engine, so it is counted
            # as standing on all of them here — or the next write would be put on a row the
            # end of this one is already occupying.
            taken = _rows_taken(text)
            needed = (int(row) + taken) if isinstance(row, (int, float)) else rows + taken
            rows = max(rows, needed)
            if op.get("color") not in (None, "", "accent"):
                problems.append(f"step {i}: colour is a word, 'accent' or nothing at all")
            if op.get("color") == "accent":
                accents += 1
                if accents > 1:
                    problems.append(f"step {i}: sheet '{sheet}' has two things in accent — only one may stand out")
            wid = _as_text(op.get("id"))
            if wid:
                if wid in ids:
                    problems.append(f"step {i}: id '{wid}' is used twice on sheet '{sheet}'")
                ids[wid] = text
            # A board that ends an example with nothing but its answer is a board that only
            # makes sense to someone who happened to remember being told the question.
            if _writes_a_question(wid, text):
                asked = True
            elif _is_answer_line(wid, text) and not asked and not asked_faulted:
                asked_faulted = True
                problems.append(f"sheet '{sheet}' writes its answer without its "
                                "question — the WHOLE question goes on the board first, the way "
                                "a teacher writes it up before working underneath it")
            shown = True
            writes += 1
            continue
        if kind == "figure":
            name = _as_text(op.get("name"))
            if name not in figures:
                problems.append(f"step {i}: there is no figure called '{name}' for {subject or 'this subject'} "
                                f"(available: {', '.join(sorted(figures))})")
            if gate and gate.get("allowed") and name in _TEMPLATE_TRIGGERS and name not in gate["allowed"]:
                problems.append(f"step {i}: '{name}' is not the apparatus these notes describe — "
                                f"use one of {', '.join(sorted(gate['allowed']))}")
            if not isinstance(op.get("labels"), dict) and op.get("labels") is not None:
                problems.append(f"step {i}: a figure's labels must be an object of slot to text")
            for slot, value in (op.get("labels") or {}).items():
                if len(_as_text(value)) > 48:
                    problems.append(f"step {i}: label '{slot}' is a sentence — the notes beside a diagram "
                                    "are a few words")
            if rows >= ROW_LIMIT:
                _new_sheet()
            # A drawing fills the sheet from its row to the bench, so the engine sends the
            # next line to a fresh board; counting it that way here keeps the two in step.
            rows = ROW_LIMIT
            shown = True
            continue
        # marks
        target = _as_text(op.get("target"))
        if not target or target not in ids:
            problems.append(f"step {i}: '{kind}' points at '{target}', which no write on "
                            "this sheet has an id of")
            continue
        find = op.get("find")
        if find is not None and _as_text(find) not in ids[target]:
            problems.append(f"step {i}: '{_as_text(find)}' is not written inside '{ids[target]}' — "
                            "quote the board's own words in 'find'")
        if kind == "tick" and find is not None:
            problems.append(f"step {i}: a tick marks a whole line, so it takes no 'find'")

    if sheets and not shown:
        problems.append(f"'{sheet}': a sheet the hand never writes on is not a sheet")
    if not say_lines:
        problems.append("nothing is spoken: a lesson is the tutor talking while the hand moves")
    if not writes:
        problems.append("no sheet has anything written on it")
    if len(script["steps"]) < 6:
        problems.append("six operations at the very least — this is a whole topic, not one example")
    # A reply that ran out of output tokens leaves exactly this shape behind: one sheet and
    # a couple of lines. Shipping it as the cached lesson would quietly replace a real one.
    if sheets < 4:
        problems.append(f"only {sheets} sheet(s): a lesson runs hook, rule, worked examples, "
                        "a trap, you-try and recap — at least four sheets")
    if say_lines < 6:
        problems.append(f"only {say_lines} spoken line(s): the tutor talks through every sheet")
    if allowed:
        used = {_as_text(o.get("concept")) for o in script["steps"] if isinstance(o, dict)}
        missing = sorted(allowed - used)
        if missing:
            problems.append("the concept map asks for concepts this script never teaches: "
                            + ", ".join(missing))
    return problems


def _thin_sheets(steps, max_sheets):
    """Fit an over-written lesson to the board instead of throwing it away.

    A model asked for ten sheets often writes twenty, and the old answer to that was to
    reject the lesson and spend another minute of the student's time asking for it again.
    The hook, the last worked example and the recap are worth more than the sixth example,
    so the middle is cut in teaching order and the ends survive."""
    if not max_sheets or max_sheets < 5 or len(steps) <= max_sheets:
        return steps
    bounds = [i for i, op in enumerate(steps)
              if isinstance(op, dict) and op.get("op") == "caption"]
    if not bounds:
        return steps
    sheets = []
    for j, start in enumerate(bounds):
        stop = bounds[j + 1] if j + 1 < len(bounds) else len(steps)
        sheets.append(steps[start:stop])
    if len(sheets) <= max_sheets:
        return steps
    head, middle, tail = sheets[:1], sheets[1:-3], sheets[-3:]
    keep = head + middle[:max(0, max_sheets - len(head) - len(tail))] + tail
    out = list(steps[:bounds[0]])
    for sheet in keep:
        out.extend(sheet)
    return out


def normalize_script(script, allowed_concepts=None, durations=None, subject=None,
                     gate=None, repairs=None, max_sheets=None):
    """Repair the script down to what the engine can actually play, and stamp spoken time.

    Most of what a model gets wrong about this contract is mechanical — a coordinate it
    has no business choosing, a ring aimed at a line that does not exist, a row past the
    bottom of the sheet. Those are cut out here rather than thrown the lesson away for.
    What is NOT repairable is a script with nothing to say or nothing to show, and that
    is what validate_script() reports back after this has run.

    A mark whose line is missing is dropped instead of re-pointed: a ring around the
    wrong number teaches the wrong number. A mark whose "find" is paraphrased keeps its
    target and loses the phrase, which rings the whole line — honest, if less tidy."""
    note = repairs if repairs is not None else []
    figures = _figure_names(subject)
    allowed = set(allowed_concepts or ())
    given = (script or {}).get("steps") or []
    written = _thin_sheets(given, max_sheets)
    if len(written) < len(given):
        note.append(f"the reply held {len(given)} operations, more than the board can "
                    f"teach in {max_sheets} sheets, so {len(given) - len(written)} were "
                    "left out and the lesson still reaches its recap")
    steps = []
    sheets = 0
    used = set()          # rows taken on the current sheet
    ids = {}
    accents = 0
    shown = False
    last_id = None        # the line the hand wrote most recently on this sheet

    def flush_sheet():
        """An empty sheet is a board wipe for no reason: drop its caption."""
        if not shown and steps and steps[-1]["op"] == "caption":
            note.append(f"dropped the empty sheet '{steps[-1]['text']}'")
            steps.pop()

    def place(wanted, need=1):
        """A run of rows on this sheet that nothing else is standing on. Two items given the
        same row write one over the other, which is the one overlap the engine cannot fix —
        and a line wide enough to be a whole question stands on two rows, so it has to be
        given both before the hand reaches for them."""
        want = _num(wanted)
        if want is None:
            order = range(ROW_LIMIT)                      # no row asked for: go in order
        else:
            start = max(0, min(int(want), ROW_LIMIT - 1))
            order = list(range(start, ROW_LIMIT)) + list(range(0, start))
        for row in order:
            if all(r not in used for r in range(row, min(ROW_LIMIT, row + need))):
                return row
        return None

    def wipe_sheet():
        """A full sheet is not the end of the lesson — the board wipes and the work goes
        on, exactly as it does in class. The engine already does this on its own, so the
        generator has to as well, or it drops content the board has room to show."""
        nonlocal sheets, shown, accents, last_id
        if sheets >= _SHEET_LIMIT:
            return False
        sheets += 1
        used.clear()
        ids.clear()
        last_id = None      # the lines it could point at are off the board now
        shown, accents = False, 0
        return True

    for i, op in enumerate(written):
        if not isinstance(op, dict) or op.get("op") not in SCRIPT_OPS:
            note.append(f"step {i}: not an operation the board understands")
            continue
        kind = op["op"]
        geo = _GEO_KEYS & set(op)
        if geo:
            note.append(f"step {i}: the board chose its own {', '.join(sorted(geo))}")
            op = {k: v for k, v in op.items() if k not in _GEO_KEYS}
        concept = op.get("concept") if op.get("concept") in allowed else None

        if kind == "caption":
            text = _as_text(op.get("text"))[:48]
            if not text:
                note.append(f"step {i}: a caption with no title")
                continue
            flush_sheet()
            if sheets >= _SHEET_LIMIT:
                note.append(f"step {i} and after: the board only holds {_SHEET_LIMIT} "
                            "sheets, so the rest of the lesson was left out")
                break
            sheets += 1
            used, ids, accents, shown, last_id = set(), {}, 0, False, None
            steps.append({"op": "caption", "text": text})
            continue

        if kind == "say":
            line = _say(op.get("text"), concept, durations, _as_text(op.get("text")))
            if line:
                steps.append(line)
            continue

        if kind == "pause":
            # A beat's gap is reading time, not dead air: whatever the model asked for,
            # the lesson waits between a quarter of a second and seven tenths.
            steps.append({"op": "pause", "ms": int(min(700, max(250, _num(op.get("ms")) or 400)))})
            continue

        if kind == "write":
            text, rest = _split_line(_as_text(op.get("text")))
            if not text:
                note.append(f"step {i}: a write with nothing in it")
                continue
            need = _rows_taken(text)
            row = place(op.get("row"), need)
            if row is None:
                if not wipe_sheet():
                    note.append(f"step {i}: the board is out of sheets, so '{text[:24]}' "
                                "was left out")
                    continue
                row = place(op.get("row"), need)
            used.update(range(row, min(ROW_LIMIT, row + need)))
            line = {"op": "write", "text": text, "row": row}
            # A line has to be nameable or nothing can be marked on it later, so a write
            # the model never gave an id still gets one. Models very often skip the id and
            # then point a ring at the word "write" — that mark is kept below, on the line
            # the hand actually wrote last, which is the line a teacher would be ringing.
            wid = _as_text(op.get("id"))[:28].replace(" ", "_")
            if wid and wid in ids:
                note.append(f"step {i}: id '{wid}' was already used on this sheet")
                wid = None
            if not wid:
                wid = f"line{len(ids) + 1}"
            line["id"] = wid
            ids[wid] = text
            last_id = wid
            if op.get("color") == "accent" and accents < 1:
                line["color"] = "accent"
                accents += 1
            elif op.get("color") == "accent":
                note.append(f"step {i}: a second thing in accent — only one may stand out")
            if concept:
                line["concept"] = concept
            shown = True
            steps.append(line)
            # The part that would not fit still has to reach the student: it goes on the
            # next free rows, and whatever is left after the rows run out is spoken.
            while rest:
                more, rest = _split_line(rest)
                if not more:
                    break
                spot = place(None, _rows_taken(more))
                if spot is None:
                    rest = more
                    break
                used.update(range(spot, min(ROW_LIMIT, spot + _rows_taken(more))))
                more_id = f"line{len(ids) + 1}"
                ids[more_id] = more
                steps.append({"op": "write", "text": more, "row": spot, "id": more_id,
                              **({"concept": concept} if concept else {})})
                last_id = more_id
                shown = True
            if rest:
                note.append(f"step {i}: '{text[:18]}…' went over the row, so the rest is said")
                steps.append(_say(rest, concept, durations))
            continue

        if kind == "figure":
            name = _as_text(op.get("name"))
            if name not in figures:
                note.append(f"step {i}: there is no figure called '{name}'")
                continue
            if gate and gate.get("allowed") and name in _TEMPLATE_TRIGGERS \
                    and name not in gate["allowed"]:
                note.append(f"step {i}: '{name}' is not the apparatus these notes describe")
                continue
            row = place(op.get("row"))
            if row is None:
                if not wipe_sheet():
                    note.append(f"step {i}: the board is out of sheets, so the {name} "
                                "diagram was left out")
                    continue
                row = place(op.get("row"))
            # A drawing fills the sheet from its row to the bench, so it claims every row
            # below it; the next line of working belongs on a fresh sheet.
            used.update(range(row, ROW_LIMIT))
            line = {"op": "figure", "name": name, "row": row}
            variant = _as_text(op.get("variant"))
            if variant:
                line["variant"] = variant[:28]
            labels = {}
            for slot, value in (op.get("labels") or {}).items():
                text = _short_line(_as_text(value), 48)
                if text:
                    labels[str(slot)[:24]] = text
            if labels:
                line["labels"] = labels
            if concept:
                line["concept"] = concept
            shown = True
            steps.append(line)
            continue

        # marks
        target = _as_text(op.get("target"))
        if target not in ids:
            if last_id is None:
                note.append(f"step {i}: '{kind}' pointed at '{target}', and nothing has been "
                            "written on this sheet yet")
                continue
            note.append(f"step {i}: '{kind}' pointed at '{target}', which is not on this "
                        "sheet, so it marks the line written just before it")
            target = last_id
        line = {"op": kind, "target": target}
        find = _as_text(op.get("find"))
        if kind != "tick" and find:
            if find in ids[target]:
                line["find"] = find
            else:
                note.append(f"step {i}: '{find}' is not written inside that line, so "
                            "the mark rings the whole of it")
        origin = _as_text(op.get("from"))
        if kind == "arrow" and origin in ids:
            line["from"] = origin
        steps.append(line)
    flush_sheet()
    if steps and steps[0]["op"] != "caption":
        title = _as_text((script or {}).get("title")) or "This lesson"
        steps.insert(0, {"op": "caption", "text": title[:48]})
    return {"title": _as_text((script or {}).get("title")) or "Lesson", "steps": steps}


def _rows_taken(text):
    """How many rows of the sheet one write stands on.

    The engine wraps a line at the real width of its glyphs, so a whole question is two rows
    of board and a step of working is one. Nothing here measures the font — that is the
    browser's job — but the rows have to be booked out in advance, on both sides of the same
    lesson, or the next line of working lands on top of the question's second one."""
    return max(1, -(-len(str(text or "").strip()) // CHARS_PER_ROW))


def _short_line(text, limit=LINE_LIMIT):
    """A board line, cut at a word — never in the middle of a formula."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit - 1]
    return cut.rstrip(" ,;:+-=")


def _split_line(text, limit=LINE_LIMIT):
    """Cut an over-long board line where a reader would breathe, and hand back the rest.

    A model that wants to write a paragraph on one row is really writing two rows. The
    break looks for punctuation or the start of a numbered item — the places a hand
    would lift — rather than stopping at whatever character lands on column 96."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text, ""
    window = text[:limit]
    # (?<![\d.]) keeps a list marker ("4. ") and a decimal ("24.0 cm3") from looking like
    # the end of a sentence — the break must fall after words, not after a number.
    breaths = [m.end() for m in re.finditer(r"(?<![\d.])[.;:!,]\s|\s[-–—]{1,2}\s"
                                            r"|\s(?=\d+[.)]\s)|\s(?=[a-z][.)]\s)"
                                            r"|\s(?=[A-Z][a-z])", window)]
    cut = max([b for b in breaths if b >= 24] or [window.rfind(" ") + 1])
    # A break taken just AFTER a list marker strands the number at the end of the row
    # ("…whenever working 4"). Move it in front of the marker, where the item starts.
    marker = re.search(r"(\d{1,3}[.)]|[a-z][.)])\s*$", text[:cut])
    if marker and marker.start() >= 12 and text[marker.start() - 1] in " -:—–":
        cut = marker.start()
    head, tail = text[:cut].strip(" ,;:.-="), text[cut:].strip(" ,;:.-=")
    if len(head) < 12:
        trimmed = _short_line(text)
        return trimmed, text[len(trimmed):].strip(" ,;:.-=")
    return head, tail


def _say(text, concept=None, durations=None, raw=None):
    """A spoken line, stamped with the seconds the narrator measured for it."""
    spoken = _spoken_line(text)
    if not spoken:
        return None
    line = {"op": "say", "text": spoken}
    secs = (durations or {}).get(spoken) or (durations or {}).get(raw or "")
    line["dur"] = round(secs, 2) if secs else max(1.0, round(len(spoken) / SPEECH_CHARS_PER_SEC, 2))
    if concept:
        line["concept"] = concept
    return line


def _spoken_line(text):
    """A sentence the narrator can finish: whole sentences only, and a stop at the end."""
    text = " ".join(str(text or "").split())
    if len(text) <= SAY_LIMIT:
        return text if text.endswith((".", "!", "?")) else (text + "." if text else "")
    head = text[:SAY_LIMIT]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind(". "))
    if cut < 60:
        cut = head.rfind(" ")
    trimmed = text[:cut + 1].strip() if cut > 0 else head
    return _spoken_line(trimmed)


def narration_lines(lesson):
    """Every spoken line of a stored lesson, in order, in either schema.

    Old lessons put one narration string on each step; a script says things with "say"
    operations. The narrator and the player both need the same list, so only this
    function knows about both shapes."""
    out = []
    for step in (lesson or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        text = _as_text(step.get("text")) if step.get("op") == "say" \
            else _as_text(step.get("narration"))
        if text:
            out.append(text)
    return out


def speech_durations(lines):
    """Seconds each line will take to say, measured off the narrator's own audio when that
    line has already been generated.

    A line that has not been heard yet is estimated at SPEECH_CHARS_PER_SEC characters a
    second — the rate the narrator runs at, measured off real recordings of it — so the
    server and the board never disagree about how long a sentence lasts. The player reads
    the clock from the voice every frame regardless, so an estimate buys a slightly wrong
    reading pause; it cannot desynchronise anything."""
    out = {}
    for text in lines:
        secs = llm.narration_seconds(text)
        if not secs:
            secs = max(1.0, len(str(text)) / SPEECH_CHARS_PER_SEC)
        out[text] = round(secs, 2)
    return out


def generate_lesson(subject, topic, example_questions, adapt_context, concept_list=None,
                    notes=None):
    """Build a lesson SCRIPT, and only a script.

    Supplied notes outrank everything else: they are the content, so the lesson walks
    through them in their own order. Next comes the hand-authored concept map, which
    fixes the concepts and their teaching order and tags every sheet; without either,
    the past-paper questions define the scope. A concept map that arrives alongside
    notes only adds sheet tags to the notes.

    The model is given the operation vocabulary, never the canvas. Its reply is first
    repaired — a stray coordinate, a full sheet or a ring aimed at nothing is cut, not
    fatal — and only what is left standing is validated. Problems that survive are the
    ones only the model can fix, so they go back into the prompt once. A second failure
    raises and the caller keeps the lesson it already had: the board never renders a
    script it cannot play."""
    subject_name = _subject_name(subject)
    topic_name = curriculum.topic_name(subject, topic)
    system = _script_system(subject)
    slugs = {c["slug"] for c in (concept_list or [])}
    gate = template_gate(subject, notes)
    shape = _script_head()
    if notes:
        lo = max(8, min(14, len(notes.split()) // 170))
        hi = min(16, lo + 4)
        user = NOTES_SCRIPT_TEMPLATE.format(
            topic=topic_name, subject_name=subject_name, notes=notes,
            outline=("\nThis topic also has a concept map. Tag the first operation of every "
                     "sheet with \"concept\": \"<slug>\", copied exactly from this list, and "
                     "keep this teaching order:\n"
                     + "\n".join(f"{i}. {c['slug']} — {c['name']}"
                                 for i, c in enumerate(concept_list or [], 1)) + "\n"
                     if concept_list else ""),
            style=_style_block(example_questions), adapt=adapt_context,
            lo=lo, hi=hi, shape=SCRIPT_SHAPE_NOTES, shape_example=shape,
        )
    elif slugs:
        examples = "\n".join(f"- {q}" for q in example_questions[:12])
        lo = max(8, min(14, len(slugs) + 2))
        hi = min(16, lo + 4)
        user = CONCEPT_SCRIPT_TEMPLATE.format(
            topic=topic_name, subject_name=subject_name,
            concept_list="\n".join(
                f"{i}. {c['slug']} — {c['name']}" for i, c in enumerate(concept_list, 1)),
            examples=examples, adapt=adapt_context, lo=lo, hi=hi,
            shape=SCRIPT_SHAPE_NOTES, shape_example=shape,
        )
    else:
        examples = "\n".join(f"- {q}" for q in example_questions[:14])
        lo, hi = LO_PLAIN, HI_PLAIN
        user = PLAIN_SCRIPT_TEMPLATE.format(
            topic=topic_name, subject_name=subject_name, examples=examples,
            adapt=adapt_context, lo=lo, hi=hi,
            shape=SCRIPT_SHAPE_NOTES, shape_example=shape,
        )

    last_err = None
    deadline = time.time() + LESSON_BUDGET
    for _ in range(2):
        raw = llm.chat(system, user, temperature=0.5, max_tokens=9000, json_mode=True,
                       deadline=deadline)
        reply = parse_script(raw, topic_name)
        script = normalize_script(reply, slugs, speech_durations(narration_lines(reply)),
                                  subject, gate, max_sheets=hi)
        problems = validate_script(script, slugs, gate, subject)
        if not problems:
            return script
        # Say exactly what is still wrong after the repairs, to the same prompt that
        # produced it. Anything normalize_script could fix silently is not worth a round trip.
        last_err = ValueError("unplayable lesson script: " + "; ".join(problems[:6]))
        thin = any("sheet(s)" in p or "spoken line" in p for p in problems)
        if not script["steps"]:
            user += ("\n\nThat reply had no lesson operations in it. Write the script again as "
                     "ONE operation per line — a complete JSON object on each line, no array "
                     "brackets, no commas at the ends of lines, no prose.")
            continue
        user += ("\n\nThe board engine cannot play your script because:\n- "
                 + "\n- ".join(problems[:8])
                 + ("\n\nYour last reply ran out of room before it finished. Write a tighter "
                    "script: 8 to 10 sheets, one spoken line each, and reach the recap."
                    if thin else "")
                 + "\nWrite the whole script again, one operation per line.")
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


def _trim_spoken(text, limit=560):
    """An interruption must end so the lesson can carry on.

    The model is asked for 90 words and usually obliges; when it does not, the surplus is
    cut at a sentence boundary rather than left to run for another forty seconds of
    speech. Half a sentence is never spoken aloud."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    keep, rest = "", text
    while rest:
        m = re.search(r"[.!?](?=\s|$)", rest)
        if not m:
            break
        sentence = rest[:m.end()]
        if len(keep) + len(sentence) > limit:
            break
        keep = (keep + " " + sentence).strip()
        rest = rest[m.end():].strip()
    return keep or text[:limit].rsplit(" ", 1)[0] + "…"


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
        answer = _trim_spoken(str(raw.get("answer") or ""))
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
