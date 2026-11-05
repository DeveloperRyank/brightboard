"""Concept maps: the authoritative definition of what each topic contains.

Hand-authored per subject at data/<subject>/concept_map.json, shaped:
    { "<topic_slug>": [ {"slug": "...", "name": "..."}, ... ] }   (ordered)

The order of the list is the teaching order. Slugs are snake_case and unique
within a topic; BKT and lesson adaptation key on (topic, concept slug).

AI-drafted proposals live in concept_map.review.json and are NEVER loaded here
— they become authoritative only when the user copies them into
concept_map.json. Until a topic has an approved map, has_map() is False and
callers fall back to the old sampled-question scoping.
"""

import hashlib
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

_maps = {}  # subject -> {topic: [ {slug,name}, ... ]} or None when no file


def _path(subject):
    return DATA_DIR / subject / "concept_map.json"


def concept_map(subject):
    """topic slug -> ordered concept list ({} when the subject has no map)."""
    if subject in _maps:
        return _maps[subject] or {}
    path = _path(subject)
    if not path.exists():
        _maps[subject] = None
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _maps[subject] = None
        return {}
    clean = {}
    for topic, entries in (raw or {}).items():
        concepts = []
        for e in entries or []:
            if isinstance(e, dict) and e.get("slug"):
                concepts.append({"slug": str(e["slug"]), "name": str(e.get("name") or e["slug"])})
        if concepts:
            clean[topic] = concepts
    _maps[subject] = clean
    return clean


def reload(subject=None):
    """Drop the cache (after the user edits a map on disk)."""
    if subject:
        _maps.pop(subject, None)
    else:
        _maps.clear()


def concepts_for(subject, topic):
    return concept_map(subject).get(topic, [])


def has_map(subject, topic):
    return bool(concepts_for(subject, topic))


def concept_slugs(subject, topic):
    return [c["slug"] for c in concepts_for(subject, topic)]


def concept_name(subject, topic, slug):
    for c in concepts_for(subject, topic):
        if c["slug"] == slug:
            return c["name"]
    return (slug or "").replace("_", " ").title()


def concept_entry(subject, topic, slug):
    for c in concepts_for(subject, topic):
        if c["slug"] == slug:
            return c
    return None


def fingerprint(subject, topic):
    """Stable token for the map's contents, so a cached lesson is rebuilt when the
    authoritative concept list changes underneath it."""
    entries = concepts_for(subject, topic)
    if not entries:
        return "nomap"
    return hashlib.sha1(
        json.dumps([[c["slug"], c["name"]] for c in entries]).encode()
    ).hexdigest()[:12]
