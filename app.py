"""Brightboard — AI diagnostic tutor.

Flow: onboarding -> pick curriculum/subject -> pick topic -> Lesson | Test | Upload.
Chemistry is live on its real past-paper bank; other KCSE subjects are content-gated
until a question CSV is added at data/<subject>/questions.csv.

Run: python app.py   (needs .env with GEMINI_API_KEY; see .env.example)
"""

import base64
import os
import secrets

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

import classifier
import concepts
import curriculum
import llm
import profile
import tutor

app = Flask(__name__)
# Uploads travel inline to Gemini, and a deployed function only accepts a 4.5 MB request
# body, so MAX_UPLOAD_MB caps the form below whichever limit applies.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB") or 15)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.errorhandler(413)
def too_large(_):
    """Vercel answers an oversized body with plain text, which the uploader tried to read
    as JSON. The page needs the same shape as every other failure here."""
    return jsonify({"error": f"That file is over the {MAX_UPLOAD_MB} MB limit for this "
                             "server. Re-export the scan at a lower resolution, or upload "
                             "one page at a time."}), 413


def _load_secret_key():
    """Persist a random secret across restarts so signed session cookies (the logged-in
    account) survive a server reload. Deployments set SESSION_SECRET; a local run keeps
    it in .secret_key, which is git-ignored."""
    key = (os.environ.get("SESSION_SECRET") or "").strip()
    if key:
        return key
    path = os.path.join(os.path.dirname(__file__), ".secret_key")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


app.secret_key = _load_secret_key()


@app.before_request
def _bind_current_student():
    """Every profile read/write defaults to the logged-in account for this request.
    Jobs that outlive the request capture the sid explicitly instead."""
    profile.set_current(session.get("sid"))

ALLOWED_UPLOAD_MIME = {
    "application/pdf": "application/pdf",
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
}

# Lesson generation is the slow part of the app: one long LLM call per topic. The work
# runs inside the request that asks for it (see lesson_state), and gen_jobs in the
# database is what stops the next poll from doing the same lesson twice.


def _gkey(subject, topic, sid=None):
    sid = sid if sid is not None else profile.current_sid()
    return f"{sid}|{subject}:{topic}"


def topic_ready(subject, topic):
    """A topic can run a lesson/test only when it has real questions behind it."""
    return bool(classifier.questions_for_topic(subject, topic))


def current_curriculum():
    """The curriculum the logged-in account is working in (default KCSE)."""
    cur = (profile.get_student() or {}).get("curriculum") or "kcse"
    return cur if curriculum.get_curriculum(cur) else "kcse"


def _lesson_sig(subject, topic, sid=None):
    """What a cached lesson was built from: the student's concept state AND the
    concept map, so editing the map on disk rebuilds every lesson for that topic."""
    return profile.topic_signature(subject, topic, sid) + "|" + concepts.fingerprint(subject, topic)


def _prefetch_narration(lesson):
    """Warm the local narrator's audio for every step while the student is still on the
    first one, so playback never waits on synthesis."""
    if lesson:
        llm.prefetch_narration([s.get("narration") for s in lesson.get("steps", [])])


def generate_lesson(subject, topic, sid=None):
    """Build one lesson and cache it. Raises if the model can't deliver."""
    sid = sid if sid is not None else profile.current_sid()
    questions = classifier.questions_for_topic(subject, topic)
    lesson = tutor.generate_lesson(
        subject, topic, [q["question"] for q in questions],
        adapt_context(subject, topic, sid),
        concept_list=concepts.concepts_for(subject, topic) or None,
    )
    profile.save_lesson(subject, topic, lesson,
                        _lesson_sig(subject, topic, sid), sid=sid)
    _prefetch_narration(lesson)
    return lesson


def lesson_state(subject, topic, work=False):
    """(status, error, cached_lesson) for the loading screen.

    work=False (the page render) only queues the job, so the screen appears at once.
    work=True (the status poll) may spend its request generating, and claiming the job
    in gen_jobs is what keeps the overlapping polls from each generating the same
    lesson."""
    cached, cached_sig = profile.load_lesson(subject, topic)
    if cached is not None and cached_sig == _lesson_sig(subject, topic):
        return "ready", None, cached
    key = _gkey(subject, topic)
    status, error = profile.generation_state(key)
    if status == "error":
        return "error", error, cached
    profile.queue_generation(key)
    if not work or not profile.claim_generation(key):
        return "generating", None, cached
    try:
        lesson = generate_lesson(subject, topic)
    except Exception as exc:
        message = str(exc).strip()
        profile.finish_generation(key, error=message)
        return "error", message, cached
    profile.finish_generation(key)
    return "ready", None, lesson


def adapt_context(subject, topic, sid=None):
    """Profile-aware instructions for the lesson generator, aimed at specific concepts."""
    concepts = profile.get_concepts(subject, topic, sid)
    if not concepts:
        confidence = (profile.get_student(sid) or {}).get("confidence")
        if confidence == "pretty_strong":
            return (
                "STUDENT CONTEXT: first lesson on this topic, but the student rates themselves "
                "confident overall. Skip the basics: teach at exam pace, keep definitions to one "
                "line, and spend the steps on exam traps, harder applications and marking-scheme "
                "precision."
            )
        if confidence == "getting_there":
            return (
                "STUDENT CONTEXT: first lesson on this topic and the student has some grounding. "
                "Open each concept with a one-line recap, then move quickly into application and "
                "worked exam-style examples."
            )
        return (
            "STUDENT CONTEXT: first lesson on this topic — assume no prior mastery. "
            "Start from plain definitions and build up slowly with a labelled diagram or "
            "worked example for every concept."
        )

    tally = profile.get_error_tally(subject, topic, sid=sid)
    weak, strong = [], []
    for c in concepts:  # already sorted weakest first
        errs = tally.get((c["key"], c["concept"]), {})
        label = max(errs.items(), key=lambda x: x[1])[0] if errs else None
        name = concepts.concept_name(subject, topic, c["concept"])
        note = f"{name} ({round(c['p'] * 100)}%{', ' + profile.ERROR_LABELS.get(label, label) if label else ''})"
        (weak if c["p"] < 0.6 else strong).append(note)
    weakest = concepts[0]
    mean = round(sum(c["p"] for c in concepts) / len(concepts), 3)

    if mean < 0.4:
        mode = (
            "the student is still shaky across this topic. Simplify: short sentences, everyday "
            "language, build every concept from basics, and add extra worked examples and labelled "
            "diagrams. Repeat the exam keywords they keep missing."
        )
    elif mean < 0.7:
        mode = (
            "the student is partway there. Teach the whole topic, but give the weak concepts below "
            "more steps, a labelled diagram and a worked example each, while the strong concepts get "
            "a one-line recap and a harder application instead of re-teaching."
        )
    else:
        mode = (
            "the student is strong here overall. Move briskly: recap the strong concepts in one line "
            "each and spend the steps on the weak ones below, on exam traps and on marking-scheme "
            "precision."
        )
    return (
        f"STUDENT CONTEXT: this topic is tracked concept by concept. Average mastery {round(mean * 100)}% "
        f"over {sum(c['answers'] for c in concepts)} graded answer(s). "
        f"Weakest concepts, most urgent first: {', '.join(weak) or 'none below 60%'}. "
        f"Already strong: {', '.join(strong) or 'nothing yet'}. "
        f"Spend the most board time on '{concepts.concept_name(subject, topic, weakest['concept'])}'. "
        f"Adapt the lesson because {mode}"
    )


def _topic_rows(subject=None, sid=None):
    """Topic-level rollup of per-concept BKT: mean P(L), answers, weakest concept."""
    by_topic = {}
    for c in profile.get_concepts(subject, sid=sid):
        by_topic.setdefault(c["key"], []).append(c)
    tally = profile.get_error_tally(subject, sid=sid)
    rows = []
    for key, cs in by_topic.items():
        subj, _, topic = key.partition(":")
        errors = {}
        for c in cs:
            for err, n in tally.get((key, c["concept"]), {}).items():
                errors[err] = errors.get(err, 0) + n
        weakest = cs[0]  # get_concepts sorts weakest first
        rows.append({
            "key": key, "subject": subj, "topic": topic,
            "name": curriculum.topic_name(subj, topic),
            "subject_name": (curriculum.get_subject(subj) or {}).get("name", subj),
            "score": round(sum(c["p"] for c in cs) / len(cs), 3),
            "answers": sum(c["answers"] for c in cs),
            "concepts": len(cs),
            "weakest": weakest["concept"],
            "weakest_name": concepts.concept_name(subj, topic, weakest["concept"]),
            "weakest_p": weakest["p"],
            "errors": errors,
        })
    rows.sort(key=lambda r: r["score"])
    return rows


def _concept_rows(subject=None, sid=None):
    """Every tracked (topic, concept) with its BKT state, weakest first."""
    tally = profile.get_error_tally(subject, sid=sid)
    rows = []
    for c in profile.get_concepts(subject, sid=sid):
        errs = tally.get((c["key"], c["concept"]), {})
        top = max(errs.items(), key=lambda x: x[1])[0] if errs else None
        rows.append({
            **c,
            "topic_name": curriculum.topic_name(c["subject"], c["topic"]),
            "subject_name": (curriculum.get_subject(c["subject"]) or {}).get("name", c["subject"]),
            "name": concepts.concept_name(c["subject"], c["topic"], c["concept"]),
            "error_type": top,
            "error_type_label": profile.ERROR_LABELS.get(top, top),
        })
    return rows


def _named_rec(rec):
    """A recommendation with the display names attached (slugs alone read badly)."""
    if not rec:
        return None
    rec = dict(rec)
    rec["topic_name"] = curriculum.topic_name(rec["subject"], rec["topic"])
    rec["concept_name"] = (concepts.concept_name(rec["subject"], rec["topic"], rec["concept"])
                           if rec.get("concept") else None)
    rec["error_label"] = profile.ERROR_LABELS.get(rec.get("error_type"))
    return rec


def _subject_stats(subjects, sid=None):
    """Per-subject 'studied' + average BKT for the subject cards / analytics chart."""
    rows = _topic_rows(sid=sid)
    for s in subjects:
        mine = [r for r in rows if r["subject"] == s["slug"]]
        s["studied"] = len(mine)
        s["avg_score"] = round(sum(r["score"] for r in mine) / len(mine), 2) if mine else None
        s["has_data"] = classifier.subject_has_data(s["slug"])
    return subjects


@app.context_processor
def inject_globals():
    cur = current_curriculum()
    cur_obj = curriculum.get_curriculum(cur) or {}
    return {
        "student": profile.get_student(),
        "config_problems": llm.check_config(),
        "max_upload_mb": MAX_UPLOAD_MB,
        "error_labels": profile.ERROR_LABELS,
        "curricula": curriculum.curricula(),
        "cur": cur,
        "cur_name": cur_obj.get("name", cur.upper()),
        "cur_country": cur_obj.get("country", ""),
        "topic_name": curriculum.topic_name,
        "subject_name": lambda s: (curriculum.get_subject(s) or {}).get("name", s.title()),
        "concept_label": lambda h: (concepts.concept_name(h.get("subject"), h.get("topic"), h["concept"])
                                    if h.get("concept") else None),
    }


# ---------------- Pages ----------------

@app.route("/")
def index():
    if profile.get_student() is None:
        return redirect(url_for("onboarding"))
    cur = current_curriculum()
    subs = _subject_stats(curriculum.subjects(cur))
    recommendation = _named_rec(profile.recommend_next(valid_keys=_all_valid_keys(cur)))
    return render_template("index.html", subjects=subs, recommendation=recommendation)


@app.route("/curriculum", methods=["POST"])
def set_curriculum():
    sid = session.get("sid")
    if sid is None:
        return redirect(url_for("onboarding"))
    slug = request.form.get("curriculum", "").strip()
    if curriculum.get_curriculum(slug):
        profile.set_curriculum(sid, slug)
    return redirect(request.referrer or url_for("index"))


def _all_valid_keys(cur=None):
    keys = set()
    for s in curriculum.subjects(cur or current_curriculum()):
        for t in curriculum.topics(s["slug"]):
            if topic_ready(s["slug"], t["slug"]):
                keys.add(_gkey(s["slug"], t["slug"]))
    return keys


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    """Account manager: sign in as an existing student or create a new one."""
    students = profile.list_students()
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:60]
        grade = request.form.get("grade", "").strip()[:20]
        if not name or not grade:
            return render_template("onboarding.html", students=students, new=True,
                                   error="Please tell us your name and your form/grade.",
                                   name=name, grade=grade), 400
        sid = profile.create_student(name, grade)
        session["sid"] = sid
        return redirect(url_for("quiz"))
    return render_template("onboarding.html", students=students,
                           new=bool(request.args.get("new")) or not students,
                           error=None, name="", grade="")


@app.route("/login/<int:sid>", methods=["POST"])
def login(sid):
    if profile.get_student(sid) is None:
        return redirect(url_for("onboarding"))
    session["sid"] = sid
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("sid", None)
    return redirect(url_for("onboarding"))


def _quiz_topic_map(cur):
    """subject slug -> its syllabus topics, for the starter quiz's topic picker."""
    return {
        s["slug"]: [{"slug": t["slug"], "name": t["name"]} for t in curriculum.topics(s["slug"])]
        for s in curriculum.subjects(cur)
    }


@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    """Beginner starter quiz — a few general questions that seed the profile."""
    sid = session.get("sid")
    student = profile.get_student(sid)
    if student is None:
        return redirect(url_for("onboarding"))
    cur = student.get("curriculum") or "kcse"
    if not curriculum.get_curriculum(cur):
        cur = "kcse"

    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        confidence = request.form.get("confidence", "").strip()
        style = request.form.get("style", "").strip()
        raw_topics = request.form.getlist("topics")

        error = None
        if subject not in curriculum.valid_subjects(cur):
            error = "Please pick the subject you want to start with."
        elif confidence not in profile.CONFIDENCE_LEVELS:
            error = "Please tell us how confident you feel."
        elif style not in profile.STUDY_STYLES:
            error = "Please pick how you like to study."

        if error:
            return render_template("quiz.html", student=student,
                                   subjects=curriculum.subjects(cur),
                                   topic_map=_quiz_topic_map(cur),
                                   confidence_levels=profile.CONFIDENCE_LEVELS,
                                   study_styles=profile.STUDY_STYLES,
                                   error=error, form=request.form), 400

        valid = {t["slug"] for t in curriculum.topics(subject)}
        picked = [t for t in raw_topics if t in valid][:8]
        struggles = [f"{subject}:{t}" for t in picked]
        profile.update_student(sid, struggles=struggles, confidence=confidence, style=style)
        return redirect(url_for("index"))

    return render_template("quiz.html", student=student,
                           subjects=curriculum.subjects(cur),
                           topic_map=_quiz_topic_map(cur),
                           confidence_levels=profile.CONFIDENCE_LEVELS,
                           study_styles=profile.STUDY_STYLES,
                           error=None, form={})


@app.route("/subject/<subject>")
def subject(subject):
    s = curriculum.get_subject(subject)
    if not s:
        return redirect(url_for("index"))
    has_data = classifier.subject_has_data(subject)
    counts = classifier.topic_counts(subject)
    rollup = {r["topic"]: r for r in _topic_rows(subject)}
    topics = []
    for t in s["topics"]:
        r = rollup.get(t["slug"])
        topics.append({
            "slug": t["slug"],
            "name": t["name"],
            "form": t.get("form"),
            "count": counts.get(t["slug"], 0),
            "ready": counts.get(t["slug"], 0) > 0,
            "score": r["score"] if r else None,
            "answers": r["answers"] if r else 0,
            "concepts": r["concepts"] if r else 0,
            "weakest": r["weakest_name"] if r else None,
            "weakest_p": r["weakest_p"] if r else None,
        })
    recommendation = _named_rec(profile.recommend_next(subject, valid_keys=_all_valid_keys()))
    return render_template("subject.html", subject=s, topics=topics, has_data=has_data,
                           recommendation=recommendation)


@app.route("/lesson/<subject>/<topic>")
def lesson(subject, topic):
    if not curriculum.get_subject(subject) or not topic_ready(subject, topic):
        return redirect(url_for("subject", subject=subject))
    status, error, cached = lesson_state(subject, topic)
    if status == "ready":
        _prefetch_narration(cached)
    return render_template(
        "lesson.html",
        subject=subject,
        topic=topic,
        name=curriculum.topic_name(subject, topic),
        lesson=cached if status == "ready" else None,
        status=status,
        error=error,
        server_tts=llm.tts_available(),
        concept_names={c["slug"]: c["name"] for c in concepts.concepts_for(subject, topic)},
    )


@app.route("/api/lesson/<subject>/<topic>/status")
def lesson_status(subject, topic):
    status, error, _ = lesson_state(subject, topic, work=True)
    return jsonify({"status": status, "error": error})


@app.route("/api/lesson/<subject>/<topic>/data")
def lesson_data(subject, topic):
    cached, _ = profile.load_lesson(subject, topic)
    if cached is None:
        return jsonify({"error": "No lesson yet"}), 404
    return jsonify({"lesson": cached})


@app.route("/api/lesson/<subject>/<topic>/regenerate", methods=["POST"])
def regenerate_lesson(subject, topic):
    if not topic_ready(subject, topic):
        return jsonify({"error": "Unknown topic"}), 404
    profile.clear_lesson(subject, topic)
    profile.queue_generation(_gkey(subject, topic))
    return jsonify({"started": True})


@app.route("/exam/<subject>/<topic>")
def exam(subject, topic):
    if not curriculum.get_subject(subject) or not topic_ready(subject, topic):
        return redirect(url_for("subject", subject=subject))
    available = classifier.questions_for_topic(subject, topic)
    n = min(5, max(3, len(available)))
    picked = classifier.questions_for_topic(subject, topic, limit=n)
    return render_template(
        "exam.html",
        subject=subject,
        topic=topic,
        name=curriculum.topic_name(subject, topic),
        questions=[{
            "question": q["question"],
            "concept": q.get("concept"),
            "concept_name": (concepts.concept_name(subject, topic, q["concept"])
                             if q.get("concept") else None),
        } for q in picked],
    )


@app.route("/results/<int:job_id>")
def results(job_id):
    job = profile.get_grading_job(job_id, session.get("sid"))
    if job is None:
        return redirect(url_for("analytics"))
    return render_template("results.html", job_id=job_id, subject=job["subject"],
                           topic=job["topic"],
                           name=curriculum.topic_name(job["subject"], job["topic"]),
                           status=job["status"])


@app.route("/upload", methods=["GET"])
def upload():
    subs = curriculum.subjects(current_curriculum())
    docs = profile.get_documents(limit=20)
    return render_template("upload.html", subjects=subs, documents=docs,
                           walkthroughs=profile.list_walkthroughs(session.get("sid"), limit=8))


@app.route("/api/upload", methods=["POST"])
def api_upload():
    subject = request.form.get("subject", "chemistry")
    if not curriculum.get_subject(subject):
        return jsonify({"error": "Unknown subject"}), 400
    if llm.PROVIDER != "gemini":
        return jsonify({"error": "Document analysis needs LLM_PROVIDER=gemini."}), 501
    if session.get("sid") is None:
        return jsonify({"error": "Not signed in"}), 401
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file uploaded"}), 400
    mime = ALLOWED_UPLOAD_MIME.get((f.mimetype or "").lower())
    if not mime:
        return jsonify({"error": "Upload a PDF or an image (PNG/JPEG/WebP)."}), 415
    blob = f.read()
    if not blob:
        return jsonify({"error": "Empty file"}), 400
    filename = secure_filename(f.filename)[:120] or "document"
    doc_id = profile.save_document(subject, filename, status="analyzing")
    try:
        analysis = tutor.analyze_paper(subject, base64.b64encode(blob).decode(), mime, filename)
    except Exception as exc:
        profile.update_document(doc_id, status=f"error: {exc}"[:300])
        return jsonify({"error": str(exc)}), 502
    profile.update_document(doc_id, analysis=analysis, status="done")
    if analysis.get("findings"):
        profile.apply_document_findings(subject, analysis["findings"])
    return jsonify({"id": doc_id, "analysis": analysis})


# ---------------- Guided paper walkthrough ----------------
# One real question at a time: teach the concept behind it on the board, then let the
# student say "got it" (a positive BKT observation) or ask for a different explanation.
# Built steps live in the database, so a step the browser prefetched while the student
# was still reading the previous one is served without a second model call.


@app.route("/api/walkthrough", methods=["POST"])
def create_walkthrough():
    subject = request.form.get("subject", "chemistry")
    if not curriculum.get_subject(subject):
        return jsonify({"error": "Unknown subject"}), 400
    if llm.PROVIDER != "gemini":
        return jsonify({"error": "Reading a paper needs LLM_PROVIDER=gemini."}), 501
    sid = session.get("sid")
    if sid is None:
        return jsonify({"error": "Not signed in"}), 401
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file uploaded"}), 400
    mime = ALLOWED_UPLOAD_MIME.get((f.mimetype or "").lower())
    if not mime:
        return jsonify({"error": "Upload a PDF or an image (PNG/JPEG/WebP)."}), 415
    blob = f.read()
    if not blob:
        return jsonify({"error": "Empty file"}), 400
    filename = secure_filename(f.filename)[:120] or "paper"
    try:
        paper = tutor.extract_questions(subject, base64.b64encode(blob).decode(), mime, filename)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    wt_id = profile.create_walkthrough(sid, subject, filename, paper["questions"])
    return jsonify({"id": wt_id, "title": paper["title"], "total": len(paper["questions"])})


@app.route("/walkthrough/<int:wt_id>")
def walkthrough(wt_id):
    wt = profile.get_walkthrough(wt_id, session.get("sid"))
    if wt is None:
        return redirect(url_for("upload"))
    return render_template("walkthrough.html", wt_id=wt_id, subject=wt["subject"],
                           filename=wt["filename"], questions=wt["questions"],
                           start_index=min(wt["idx"], max(0, len(wt["questions"]) - 1)),
                           server_tts=llm.tts_available())


@app.route("/api/walkthrough/<int:wt_id>/step", methods=["POST"])
def walkthrough_step(wt_id):
    wt = profile.get_walkthrough(wt_id, session.get("sid"))
    if wt is None:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    index = int(data.get("index") or 0)
    attempt = max(1, min(5, int(data.get("attempt") or 1)))
    if not 0 <= index < len(wt["questions"]):
        return jsonify({"error": "No such question"}), 404
    cached = profile.load_walkthrough_step(wt_id, index, attempt)
    if cached:
        _prefetch_narration(cached)
        return jsonify({"lesson": cached})
    try:
        lesson = tutor.generate_walkthrough_step(wt["subject"], wt["questions"][index], attempt)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    profile.save_walkthrough_step(wt_id, index, attempt, lesson)
    _prefetch_narration(lesson)
    return jsonify({"lesson": lesson})


@app.route("/api/walkthrough/<int:wt_id>/advance", methods=["POST"])
def walkthrough_advance(wt_id):
    sid = session.get("sid")
    wt = profile.get_walkthrough(wt_id, sid)
    if wt is None:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    index = int(data.get("index") or 0)
    understood = bool(data.get("understood"))
    if not 0 <= index < len(wt["questions"]):
        return jsonify({"error": "No such question"}), 404
    q = wt["questions"][index]

    p = None
    if understood and q.get("concept"):
        # "Got it" is the student's own read on the concept: one positive BKT
        # observation, exactly like a correct practice answer on that concept.
        p = profile.record_answer(
            wt["subject"], q["topic"], True, concept=q["concept"],
            question=q["text"], answer="walkthrough: student confirmed they follow",
            sid=sid,
        )
    nxt = index + 1
    done = nxt >= len(wt["questions"])
    profile.update_walkthrough(wt_id, idx=nxt, status="done" if done else "active")
    return jsonify({
        "done": done,
        "next_index": None if done else nxt,
        "total": len(wt["questions"]),
        "mastery": p,
        "recommendation": _named_rec(profile.recommend_next(
            wt["subject"], valid_keys=_all_valid_keys(), sid=sid)),
    })


@app.route("/analytics")
def analytics():
    mastery = _topic_rows()
    concept_rows = _concept_rows()
    subs = _subject_stats(curriculum.subjects(current_curriculum()))
    recommendation = _named_rec(profile.recommend_next(valid_keys=_all_valid_keys()))
    return render_template(
        "analytics.html",
        mastery=mastery,
        concepts=concept_rows,
        subjects=subs,
        history=profile.get_history(limit=60),
        documents=profile.get_documents(limit=20),
        recommendation=recommendation,
        error_types=profile.ERROR_TYPES,
        jobs=profile.list_grading_jobs(session.get("sid") or 0),
    )


# ---------------- APIs used by the lesson/test players ----------------

@app.route("/api/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    subject = data.get("subject", "chemistry")
    topic = data.get("topic", "")
    narration = str(data.get("narration", ""))[:2000]
    question = str(data.get("question", "")).strip()
    concept = str(data.get("concept") or "").strip().lower() or None
    if concept and concept not in classifier.tagged_concepts(subject, topic):
        concept = None
    if not question:
        return jsonify({"error": "Empty question"}), 400
    try:
        return jsonify(tutor.ask_question(subject, topic, narration, question[:1000], concept))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/api/submit_test", methods=["POST"])
def submit_test():
    """Record the answered test as a grading job and return its id.

    The student navigates to /results/<job_id> and polls; the poll whose request claims
    the job does the marking, and the whole set is marked in a single LLM call rather
    than one per answer.
    """
    data = request.get_json(force=True)
    subject = data.get("subject", "")
    topic = data.get("topic", "")
    if not curriculum.get_subject(subject) or not topic_ready(subject, topic):
        return jsonify({"error": "Unknown subject or topic"}), 404
    sid = session.get("sid")
    if sid is None:
        return redirect(url_for("onboarding"))

    valid_concepts = classifier.tagged_concepts(subject, topic)
    answers = []
    for item in (data.get("answers") or [])[:12]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", ""))[:4000].strip()
        answer = str(item.get("answer", ""))[:4000].strip()
        concept = str(item.get("concept") or "").strip().lower() or None
        if not question or not answer:
            continue
        answers.append({"question": question, "answer": answer,
                        "concept": concept if concept in valid_concepts else None})
    if not answers:
        return jsonify({"error": "No answers to mark"}), 400

    job_id = profile.create_grading_job(sid, subject, topic, answers)
    return jsonify({"job_id": job_id})


def run_grading(job_id, sid, subject, topic, answers, valid_keys=None):
    """Mark the whole set in one LLM call and store the verdicts on the job."""
    try:
        verdicts = tutor.grade_answers(
            subject, topic,
            [{"question": a["question"], "answer": a["answer"]} for a in answers],
        )
        results = []
        for a, verdict in zip(answers, verdicts):
            p = profile.record_answer(
                subject, topic, verdict["correct"], verdict["error_type"],
                concept=a["concept"], question=a["question"], answer=a["answer"],
                feedback=verdict["teacher_note"], sid=sid,
            )
            results.append({
                **a, **verdict, "p": p,
                "concept_name": (concepts.concept_name(subject, topic, a["concept"])
                                 if a["concept"] else None),
            })
        profile.finish_grading_job(job_id, {
            "subject": subject,
            "topic": topic,
            "topic_name": curriculum.topic_name(subject, topic),
            "mastery": profile.topic_mean_p(subject, topic, sid),
            "results": results,
            "concepts": [c for c in _concept_rows(subject, sid) if c["topic"] == topic],
            "recommendation": _named_rec(
                profile.recommend_next(subject, valid_keys=valid_keys, sid=sid)),
        })
    except Exception as exc:
        profile.finish_grading_job(job_id, {"error": str(exc)}, status="error")


@app.route("/api/results/<int:job_id>")
def results_data(job_id):
    job = profile.get_grading_job(job_id, session.get("sid"))
    if job is None:
        return jsonify({"error": "Not found"}), 404
    # Nothing marks the paper on a background thread here, so whichever poll claims the
    # job first does the marking inside its own request (see profile.claim_grading_job).
    if job["status"] in ("grading", "running") and profile.claim_grading_job(job_id):
        run_grading(job_id, job["student_id"], job["subject"], job["topic"], job["payload"],
                    valid_keys=_all_valid_keys(current_curriculum()))
        job = profile.get_grading_job(job_id, session.get("sid"))
    return jsonify({"status": job["status"], "result": job["result"],
                    "done": job["status"] in ("done", "error")})


@app.route("/api/tts", methods=["POST"])
def tts():
    """Neural narration audio (local Kokoro, else Edge/Gemini). Returns the provider's
    mimetype; 501 when no server TTS is configured and 204 when the local narrator is
    still generating that line, so the browser speaks it instead of stalling."""
    if not llm.tts_available():
        return jsonify({"error": "server TTS unavailable"}), 501
    data = request.get_json(force=True)
    text = str(data.get("text", "")).strip()[:1000]
    if not text:
        return jsonify({"error": "empty"}), 400
    try:
        audio, mime = llm.synthesize(text)
        return app.response_class(audio, mimetype=mime)
    except llm.TtsWarming:
        return "", 204
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    classifier.load_all()
    # In debug mode the reloader runs this file twice (monitor + server); only the
    # serving process should hold a 330 MB narrator model in memory.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        llm.start_narrator()
    # threaded: lesson generation and document analysis are slow LLM calls; without
    # this every other page and API request queues behind them.
    app.run(debug=True, port=5000, threaded=True)
