"""Build data/sat_rw/questions.csv from the authored item files in research/sat_authored/.

Every item here is original writing made for Conceptly: the passages, prompts, data
and options are invented, and only the question TYPES follow the published SAT
Reading and Writing specification. Nothing is copied out of a College Board or Khan
Academy paper, and nothing from the scanned PDF in Downloads is shipped as an item —
tools/ocr_sat_sample.py read it for structure only.

    py -V:3.14 tools/build_sat_rw_bank.py

Input JSON is a list of objects:
  topic      one of the four sat_* topic slugs
  concept    a slug from data/sat_rw/concept_map.json for that topic
  passage    the text the student reads (may contain newlines; data as label lines)
  stem       the question, starting "Which choice..."
  options    four strings, A-D
  answer     "A" | "B" | "C" | "D"
  difficulty "easy" | "medium" | "hard"          (optional)
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "research" / "sat_authored"
OUT = ROOT / "data" / "sat_rw" / "questions.csv"
TARGET = 200

COLUMNS = ["question", "topic", "concept", "options", "answer", "explanation"]


def load_map():
    raw = json.loads((ROOT / "data" / "sat_rw" / "concept_map.json").read_text(encoding="utf-8"))
    return {topic: {c["slug"] for c in entries} for topic, entries in raw.items()}


def build():
    valid = load_map()
    rows, problems, seen = [], [], {}
    for path in sorted(SRC.glob("*.json")):
        items = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(items, dict):
            items = items.get("items", [])
        for n, it in enumerate(items):
            where = f"{path.name}[{n}]"
            topic, concept = it.get("topic", ""), it.get("concept", "")
            options = it.get("options") or []
            answer = (it.get("answer") or "").strip().upper()[:1]
            stem = (it.get("stem") or "").strip()
            passage = (it.get("passage") or "").strip()
            explanation = (it.get("explanation") or "").strip()
            if topic not in valid:
                problems.append(f"{where}: unknown topic {topic!r}")
                continue
            if concept not in valid[topic]:
                problems.append(f"{where}: concept {concept!r} not in {topic}'s map")
                continue
            if len(options) != 4 or not all(str(o).strip() for o in options):
                problems.append(f"{where}: needs exactly four non-empty options")
                continue
            if answer not in list("ABCD"):
                problems.append(f"{where}: answer must be A, B, C or D")
                continue
            # "|" is the option separator in the CSV, so it can appear nowhere in the text.
            text_bits = [stem, passage, explanation, *options]
            if any("|" in str(b) for b in text_bits):
                problems.append(f"{where}: a pipe appears in the text")
                continue
            # Rhetorical synthesis stems open with the writing goal, so the question
            # phrase has to be found anywhere in the stem, not just at the front.
            if "which choice" not in stem.lower() and "what" not in stem.lower():
                problems.append(f"{where}: stem must ask 'Which choice …'")
                continue
            if not passage:
                problems.append(f"{where}: no passage text")
                continue
            question = f"{passage}\n\n{stem}"
            if question in seen:
                problems.append(f"{where}: duplicate of {seen[question]}")
                continue
            seen[question] = where
            rows.append({
                "question": question,
                "topic": topic,
                "concept": concept,
                "options": " | ".join(str(o).strip() for o in options),
                "answer": answer,
                "explanation": explanation,
            })
    return rows, problems


def main():
    rows, problems = build()
    if problems:
        print("REJECTED:")
        for p in problems:
            print("  " + p)
    by_topic, by_concept = {}, {}
    for r in rows:
        by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
        by_concept[(r["topic"], r["concept"])] = by_concept.get((r["topic"], r["concept"]), 0) + 1
    print("\ntopic counts:", json.dumps(by_topic, indent=0))
    print("total:", len(rows), f"(target {TARGET})")
    thin = {k: v for k, v in by_concept.items() if v < 8}
    if thin:
        print("concepts under 8 items:", thin)
    if problems or len(rows) < TARGET:
        sys.exit("not written - fix the items above first")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} items -> {OUT}")


if __name__ == "__main__":
    main()
