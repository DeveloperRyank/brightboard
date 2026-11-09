"""Subject notes: the content a lesson is taught from.

A topic used to unlock only when a past-paper question happened to mention it, which
made a subject with no bank in it a wall of locked cards no matter how well the syllabus
was written down. Notes are the other way round: the booklet a teacher would hand out,
transcribed by tools/build_physics_notes.py into one file per topic at

    data/<subject>/notes/<topic_slug>.md

A file there is the topic existing in the student's hands: it unlocks the lesson, and
its text is what the model is told to teach rather than something it recalls. The
question bank still decides the Exam Center — nothing about marking a real paper
changes because notes arrived.
"""

import hashlib
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

_notes = {}  # subject -> {topic slug: text}

# The provenance header the builder writes, kept out of the model's way.
_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)


def _dir(subject):
    return DATA_DIR / subject / "notes"


def load(subject):
    """topic slug -> notes text, {} when the subject has no notes folder."""
    if subject in _notes:
        return _notes[subject]
    found = {}
    path = _dir(subject)
    if path.is_dir():
        for f in sorted(path.glob("*.md")):
            try:
                text = _COMMENT.sub("", f.read_text(encoding="utf-8")).strip()
            except OSError:
                continue
            if len(text) > 200:  # a stub is not a lesson's worth of content
                found[f.stem] = text
    _notes[subject] = found
    return found


def reload(subject=None):
    if subject:
        _notes.pop(subject, None)
    else:
        _notes.clear()


def topic_notes(subject, topic):
    """The notes for one topic, "" when there are none."""
    return load(subject).get(topic, "")


def has_notes(subject, topic):
    return topic in load(subject)


def subject_has_notes(subject):
    return bool(load(subject))


def note_topics(subject):
    return set(load(subject))


def fingerprint(subject, topic):
    """What the notes behind a lesson were built from. A cached lesson is only valid
    while its notes are the ones the student is being taught."""
    text = topic_notes(subject, topic)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else "-"
