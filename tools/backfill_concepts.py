"""Replay past answers through the concept layer so analytics has a history.

Every question graded before the bank carried concept tags left no BKT evidence:
the answer is in `history`, but nothing reached `concept_mastery` or
`error_tally`, so the analytics concept views are empty. This matches each stored
question back to its tag in the bank and replays the answers in chronological
order, which rebuilds the same mastery numbers a live session would have produced.

    python tools/backfill_concepts.py            # report what would change
    python tools/backfill_concepts.py --apply    # write it

Runs against whichever database the app is configured for: SQLite locally, Neon
when DATABASE_URL is set.

Usage: python tools/backfill_concepts.py [--apply]
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import profile  # noqa: E402
from apply_concepts import questions_csv  # noqa: E402

_WS = re.compile(r"[^a-z0-9]+")


def norm(text):
    """Collapse case and punctuation so a stored question matches the bank's copy."""
    return _WS.sub(" ", str(text or "").lower()).strip()


def bank_tags(subject):
    """{topic: {normalised question: concept}} for one subject's bank."""
    path = questions_csv(subject)
    if path is None:
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "concept" not in rows[0]:
        return {}
    out = defaultdict(dict)
    for r in rows:
        concept = (r.get("concept") or "").strip()
        topic = (r.get("topic") or "").strip()
        if concept and topic:
            out[topic][norm(r.get("question"))] = concept
    return out


def main():
    apply_it = "--apply" in sys.argv[1:]

    with profile._conn() as c:
        rows = c.execute(
            "SELECT id, student_id, topic, question, correct, error_type FROM history "
            "WHERE concept IS NULL ORDER BY student_id, ts"
        ).fetchall()
    if not rows:
        print("nothing to replay — every stored answer already carries a concept")
        return

    subjects = sorted({(r["topic"] or "").partition(":")[0] for r in rows})
    tags = {s: bank_tags(s) for s in subjects}
    missing = [s for s in tags if not tags[s]]
    if missing:
        print(f"! the bank has no concept column for: {', '.join(missing)}")
        print("  run tools/apply_concepts.py --tags first; those answers stay untagged.")

    todo, skipped = [], 0
    for r in rows:
        subject, _, topic = (r["topic"] or "").partition(":")
        concept = tags.get(subject, {}).get(topic, {}).get(norm(r["question"]))
        if concept:
            todo.append((r["id"], r["student_id"], concept,
                         bool(r["correct"]), r["error_type"]))
        else:
            skipped += 1

    print(f"{len(rows)} untagged answer(s) on record: {len(todo)} matchable, "
          f"{skipped} not in the tagged bank (generated or reworded questions)")
    if not todo:
        return
    per_student = defaultdict(int)
    for _, sid, _, _, _ in todo:
        per_student[sid] += 1
    for sid, n in sorted(per_student.items()):
        print(f"  student {sid}: {n} answer(s) to replay")
    if not apply_it:
        print("dry run — add --apply to write")
        return

    for history_id, sid, concept, correct, error_type in todo:
        profile.backfill_concept(history_id, concept, correct,
                                 error_type=error_type, sid=sid)
    print(f"replayed {len(todo)} answer(s); mastery now reads:")
    for sid in sorted(per_student):
        concepts = profile.get_concepts(sid=sid)
        if not concepts:
            print(f"  student {sid}: still empty")
            continue
        weakest = concepts[0]
        print(f"  student {sid}: {len(concepts)} concept(s) tracked, "
              f"weakest {weakest['concept']} at {weakest['p']:.2f}")


if __name__ == "__main__":
    main()
