"""Subject-aware question data layer.

Each subject's past-paper bank lives at data/<subject_slug>/questions.csv with
columns: question,topic[,concept]  (topic = a canonical syllabus slug for that
subject; concept = an optional slug from that topic's concept map, which is what
mastery is tracked against).
Chemistry also falls back to the legacy data/questions.csv, whose older topic
slugs are remapped to canonical ones via curriculum.LEGACY_TOPIC_MAP.

A subject with no CSV is "content-gated": it appears in the taxonomy but its
lessons/tests are disabled until a question bank is added. The TF-IDF classifier
is kept per-subject for labelling ad-hoc text (e.g. uploaded papers), but the
main flow picks topics straight from the CSV.
"""

import os
from pathlib import Path

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

import concepts
import curriculum

DATA_DIR = Path(__file__).parent / "data"

_frames = {}       # subject -> DataFrame (question, topic[canonical])
_classifiers = {}  # subject -> (vectorizer, clf)


def _csv_path(subject):
    """Preferred per-subject CSV, with a legacy fallback for Chemistry."""
    primary = DATA_DIR / subject / "questions.csv"
    if primary.exists():
        return primary
    if subject == "chemistry":
        legacy = DATA_DIR / "questions.csv"
        if legacy.exists():
            return legacy
    return None


def load_subject(subject):
    """Load (and cache) a subject's questions with canonicalised topic slugs."""
    if subject in _frames:
        return _frames[subject]
    path = _csv_path(subject)
    if path is None:
        _frames[subject] = None
        return None
    df = pd.read_csv(path)
    if "question" not in df.columns or "topic" not in df.columns:
        raise ValueError(f"{path} must have 'question' and 'topic' columns")
    df = df.copy()
    df["topic"] = df["topic"].astype(str).map(curriculum.canonical_topic)
    # Concept tags are optional and blank until the bank is tagged; keep them as
    # None (not "" or "nan") so BKT skips untagged questions.
    if "concept" in df.columns:
        tags = df["concept"].fillna("").astype(str).str.strip().str.lower()
        df["concept"] = tags.where(tags != "", None)
    else:
        df["concept"] = None
    _frames[subject] = df
    return df


def subject_has_data(subject):
    df = load_subject(subject)
    return df is not None and len(df) > 0


def questions_df(subject):
    return load_subject(subject)


def topic_counts(subject):
    """canonical topic slug -> number of real questions (only topics with data)."""
    df = load_subject(subject)
    if df is None or df.empty:
        return {}
    return df["topic"].value_counts().to_dict()


def topics_with_data(subject):
    return set(topic_counts(subject).keys())


def questions_for_topic(subject, topic, limit=None):
    df = load_subject(subject)
    if df is None:
        return []
    rows = df[df["topic"] == topic]
    if limit:
        rows = rows.sample(n=min(limit, len(rows)), random_state=None)
    return rows.to_dict("records")


def concept_counts(subject, topic):
    """concept slug -> number of tagged real questions in this topic."""
    df = load_subject(subject)
    if df is None:
        return {}
    rows = df[(df["topic"] == topic) & df["concept"].notna()]
    return rows["concept"].value_counts().to_dict()


def tagged_concepts(subject, topic):
    """Concept slugs that appear in the question bank OR the hand-authored map."""
    return set(concept_counts(subject, topic)) | {
        c["slug"] for c in concepts.concepts_for(subject, topic)
    }


def questions_for_concept(subject, topic, concept, limit=None):
    df = load_subject(subject)
    if df is None:
        return []
    rows = df[(df["topic"] == topic) & (df["concept"] == concept)]
    if limit:
        rows = rows.sample(n=min(limit, len(rows)), random_state=None)
    return rows.to_dict("records")


def classify(subject, text):
    """Label ad-hoc text with the most likely topic for this subject (TF-IDF)."""
    df = load_subject(subject)
    if df is None or df.empty:
        return None
    if subject not in _classifiers:
        vec = TfidfVectorizer(stop_words="english", max_features=2000)
        X = vec.fit_transform(df["question"])
        clf = LogisticRegression(max_iter=1000)
        clf.fit(X, df["topic"])
        _classifiers[subject] = (vec, clf)
    vec, clf = _classifiers[subject]
    return clf.predict(vec.transform([text]))[0]


def load_all():
    """Warm the cache for every subject that has data (call at startup)."""
    for s in curriculum.valid_subjects():
        load_subject(s)
