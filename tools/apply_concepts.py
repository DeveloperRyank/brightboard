"""Promote reviewed concept drafts into the live app data.

    python tools/apply_concepts.py --map    # concept_map.review.json -> concept_map.json
    python tools/apply_concepts.py --tags   # write approved tags into questions.csv
    python tools/apply_concepts.py --map --tags

Only rows whose `approved` cell is yes/y/true/1/x are tagged; everything else is left
untagged (an untagged question still appears in tests, it just carries no concept
evidence for mastery tracking). The question CSV is backed up to <name>.bak first.

Usage:  python tools/apply_concepts.py [--map] [--tags] [subject]
"""

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import curriculum  # noqa: E402

DATA_DIR = ROOT / "data"
TRUTHY = {"yes", "y", "true", "1", "x"}


def questions_csv(subject):
    """The CSV the app actually reads for this subject (per-subject, else legacy)."""
    primary = DATA_DIR / subject / "questions.csv"
    if primary.exists():
        return primary
    legacy = DATA_DIR / "questions.csv"
    if subject == "chemistry" and legacy.exists():
        return legacy
    return None


def apply_map(subject):
    src = DATA_DIR / subject / "concept_map.review.json"
    dst = DATA_DIR / subject / "concept_map.json"
    if not src.exists():
        print(f"! no draft map at {src} — run tools/suggest_concepts.py first")
        return None
    drafts = json.loads(src.read_text(encoding="utf-8"))
    clean = {}
    for topic, entries in drafts.items():
        concepts = [{"slug": str(e["slug"]).strip().lower(),
                     "name": str(e.get("name") or e["slug"]).strip()}
                    for e in entries if isinstance(e, dict) and e.get("slug")]
        if concepts:
            clean[topic] = concepts
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.copyfile(dst, dst.with_suffix(".json.bak"))
    dst.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ {dst}: {len(clean)} topics, {sum(len(v) for v in clean.values())} concepts")
    return clean


def apply_tags(subject, approved_map=None):
    review = DATA_DIR / subject / "questions.concept_review.csv"
    if not review.exists():
        print(f"! no review CSV at {review} — run tools/suggest_concepts.py first")
        return
    target = questions_csv(subject)
    if target is None:
        print(f"! no question bank found for {subject}")
        return

    tags = {}
    with review.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            concept = (r.get("suggested_concept") or "").strip().lower()
            approved = (r.get("approved") or "").strip().lower()
            if concept and approved in TRUTHY:
                tags[(r.get("topic"), r.get("question"))] = concept

    with target.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        bank = list(reader)
    if "concept" not in fields:
        fields.append("concept")

    unknown, tagged = set(), 0
    for row in bank:
        topic = curriculum.canonical_topic(str(row.get("topic", "")))
        concept = tags.get((topic, row.get("question")))
        if not concept:
            row["concept"] = row.get("concept") or ""
            continue
        if approved_map and topic in approved_map:
            slugs = {c["slug"] for c in approved_map[topic]}
            if concept not in slugs:
                unknown.add(f"{topic}:{concept}")
                row["concept"] = row.get("concept") or ""
                continue
        row["concept"] = concept
        tagged += 1

    shutil.copyfile(target, target.with_suffix(".csv.bak"))
    with target.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(bank)

    print(f"✓ {target}: {tagged}/{len(bank)} questions tagged "
          f"({len(tags)} approved in the review file)")
    if unknown:
        print(f"! skipped tags that are not in the approved map: {', '.join(sorted(unknown))}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not flags:
        sys.exit(__doc__)
    subject = (args[0] if args else "chemistry").strip().lower()
    if not curriculum.get_subject(subject):
        sys.exit(f"unknown subject: {subject}")

    approved_map = None
    if "--map" in flags:
        approved_map = apply_map(subject)
    if "--tags" in flags:
        if approved_map is None:
            live = DATA_DIR / subject / "concept_map.json"
            approved_map = json.loads(live.read_text(encoding="utf-8")) if live.exists() else None
        apply_tags(subject, approved_map)


if __name__ == "__main__":
    main()
