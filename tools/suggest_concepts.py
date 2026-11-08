"""Draft a concept map + per-question concept tags for review.

Nothing here is authoritative. It writes two review artefacts:

    data/<subject>/concept_map.review.json      topic -> ordered [{slug, name}]
    data/<subject>/questions.concept_review.csv  question, topic, suggested concept, confidence, approved

`concepts.py` only ever loads concept_map.json, so drafts cannot change app
behaviour. Read the drafts, edit or delete what you disagree with, then run
tools/apply_concepts.py to promote them.

Usage:  python tools/suggest_concepts.py [subject]      (default: chemistry)
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import classifier  # noqa: E402
import curriculum  # noqa: E402
import llm  # noqa: E402

REVIEW_DIR = ROOT / "data"

SYSTEM = """You are a KCSE {subject} syllabus expert building a concept map for one topic.
You output STRICT JSON only — no prose, no code fences.

{{
  "concepts": [{{"slug": "snake_case_slug", "name": "Short Human Name"}}],
  "tags": [{{"i": 1, "concept": "snake_case_slug", "confidence": "high" | "medium" | "low"}}]
}}

Rules for "concepts":
- 4 to 9 concepts, in the order they should be TAUGHT (foundations first).
- Each concept is a distinct examinable idea, not a heading for the whole topic.
- snake_case slugs, unique within the topic, 1-4 words; "name" is what a student would
  recognise (title case, no jargon abbreviations).
- Together they must cover the whole topic as KNEC examines it.

Rules for "tags":
- One entry per numbered question, using "i" = the question's number.
- "concept" MUST be one of the slugs you just defined — pick the single concept the
  question mainly tests.
- "confidence": high = unambiguous, medium = defensible but arguable, low = the question
  spans several concepts or fits the map badly."""

USER = """Topic: {topic_name}  (slug: {topic})

These are the real KCSE past-paper questions banked for this topic:
{questions}

Return the concept map for this topic and tag every question with one concept slug."""

TAGS_USER = """Topic: {topic_name}  (slug: {topic})

The concept map for this topic is already agreed — use these slugs and only these:
{concepts}

These are the real KCSE past-paper questions banked for this topic:
{questions}

Return only the "tags" array, one entry per numbered question, each "concept" set to the
single slug the question mainly tests."""


def _tags_from(raw, concepts, questions):
    """Validate a tags reply against the topic's slugs, reusing clean()'s rules."""
    _, tags = clean({"concepts": concepts, "tags": raw.get("tags") or []}, questions)
    return tags


def draft_tags(subject, topic, concepts, questions):
    """Tag questions against a map the user already approved — the map is not touched."""
    listed = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    slug_list = "\n".join(f"- {c['slug']}: {c['name']}" for c in concepts)
    system = SYSTEM.format(subject=curriculum.get_subject(subject)["name"])
    user = TAGS_USER.format(topic_name=curriculum.topic_name(subject, topic), topic=topic,
                            concepts=slug_list, questions=listed)
    last = None
    for temperature in (0.2, 0.5):
        try:
            raw = llm.chat_json(system, user, temperature=temperature, max_tokens=6000)
        except (ValueError, RuntimeError) as exc:
            last = exc
            continue
        tags = _tags_from(raw, concepts, questions)
        if tags:
            return tags, None
        last = ValueError("no usable tags returned")
    return None, last


def draft_topic(subject, topic, questions):
    listed = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    system = SYSTEM.format(subject=curriculum.get_subject(subject)["name"])
    user = USER.format(topic_name=curriculum.topic_name(subject, topic), topic=topic,
                       questions=listed)
    last = None
    for temperature in (0.3, 0.6):
        try:
            raw = llm.chat_json(system, user, temperature=temperature, max_tokens=6000)
        except (ValueError, RuntimeError) as exc:  # bad JSON, or a spent provider quota
            last = exc
            continue
        concepts = [c for c in (raw.get("concepts") or [])
                    if isinstance(c, dict) and c.get("slug")]
        if concepts:
            return raw, None
        last = ValueError("no concepts returned")
    return None, last


def clean(raw, questions):
    """Keep only well-formed concepts, and tags that point at one of them."""
    seen, concepts = set(), []
    for c in raw.get("concepts") or []:
        slug = str(c.get("slug", "")).strip().lower().replace(" ", "_")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        concepts.append({"slug": slug, "name": str(c.get("name") or slug.replace("_", " ")).strip()})
    valid = {c["slug"] for c in concepts}
    tags = {}
    for t in raw.get("tags") or []:
        if not isinstance(t, dict):
            continue
        try:
            i = int(t.get("i"))
        except (TypeError, ValueError):
            continue
        slug = str(t.get("concept") or "").strip().lower()
        conf = str(t.get("confidence") or "medium").strip().lower()
        if 1 <= i <= len(questions) and slug in valid:
            tags[i] = {"concept": slug, "confidence": conf if conf in ("high", "medium", "low") else "medium"}
    return concepts, tags


def _prior_rows(csv_path):
    """(topic, question) -> previously written row, so a re-run never wipes your edits."""
    prior = {}
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                prior[(r.get("topic"), r.get("question"))] = r
    return prior


def main():
    subject = (sys.argv[1] if len(sys.argv) > 1 else "chemistry").strip().lower()
    if not curriculum.get_subject(subject):
        sys.exit(f"unknown subject: {subject}")
    if llm.check_config():
        sys.exit("LLM not configured: " + "; ".join(llm.check_config()))

    out_dir = REVIEW_DIR / subject
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "concept_map.review.json"
    csv_path = out_dir / "questions.concept_review.csv"

    drafts = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}
    prior = _prior_rows(csv_path)

    counts = classifier.topic_counts(subject)
    rows, drafted = [], 0
    for topic in sorted(counts, key=lambda t: -counts[t]):
        questions = [q["question"] for q in classifier.questions_for_topic(subject, topic)]
        if topic in drafts:
            print(f"= {topic}: already drafted ({len(drafts[topic])} concepts), keeping your edits")
            topic_rows = []
            for q in questions:
                old = prior.get((topic, q), {})
                topic_rows.append({"topic": topic, "question": q,
                                   "suggested_concept": old.get("suggested_concept", ""),
                                   "confidence": old.get("confidence", ""),
                                   "approved": old.get("approved", "")})
            # A topic drafted before it had tags would otherwise stay invisible to the
            # concept layer forever, so fill only the blanks and keep every edit.
            blanks = [r for r in topic_rows if not r["suggested_concept"]]
            if blanks:
                print(f"  -> {len(blanks)} question(s) have no tag yet")
                questions_to_tag = [r["question"] for r in blanks]
                tags, err = draft_tags(subject, topic, drafts[topic], questions_to_tag)
                if tags is None:
                    print(f"  ! failed: {err}")
                else:
                    for i, row in enumerate(blanks, 1):
                        t = tags.get(i)
                        if not t:
                            continue
                        row["suggested_concept"] = t["concept"]
                        row["confidence"] = t["confidence"]
                        row["approved"] = "yes" if t["confidence"] == "high" else ""
                    print(f"  ok: {len(tags)}/{len(questions_to_tag)} tagged")
            rows.extend(topic_rows)
            continue

        print(f"-> {topic} ({len(questions)} questions)")
        raw, err = draft_topic(subject, topic, questions)
        if raw is None:
            print(f"  ! failed: {err}")
            continue
        concept_list, tags = clean(raw, questions)
        drafts[topic] = concept_list
        map_path.write_text(json.dumps(drafts, indent=2, ensure_ascii=False), encoding="utf-8")
        drafted += 1
        print(f"  ok: {len(concept_list)} concepts, {len(tags)}/{len(questions)} tagged")
        for i, q in enumerate(questions, 1):
            t = tags.get(i)
            rows.append({
                "topic": topic,
                "question": q,
                "suggested_concept": t["concept"] if t else "",
                "confidence": t["confidence"] if t else "low",
                # Pre-approve the confident ones so reviewing means editing exceptions.
                "approved": "yes" if t and t["confidence"] == "high" else "",
            })

    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["topic", "question", "suggested_concept",
                                               "confidence", "approved"])
            w.writeheader()
            w.writerows(rows)

    print(f"\nwrote {map_path} ({drafted} topic(s) drafted this run, {len(drafts)} total)")
    print(f"wrote {csv_path} ({len(rows)} questions)")
    print("Review both, then: python tools/apply_concepts.py --map --tags")


if __name__ == "__main__":
    main()
