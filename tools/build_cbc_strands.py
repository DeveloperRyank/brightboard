"""Turn the transcribed KICD curriculum designs into the taxonomy the app reads.

    python tools/build_cbc_strands.py            # research/strands_round2.json -> data/...
    python tools/build_cbc_strands.py --check    # report, write nothing

`research/` holds the PDFs and pages the strand tables were read out of. It is several
tens of megabytes of third-party documents, so it is neither committed nor deployed;
`data/curriculum/kicd_strands.json` is the checked-in result, and curriculum.py merges it
into the hand-transcribed tables at import.

Two normalisations happen here rather than in curriculum.py, because they are about how the
designs are *printed* and not about how the app stores topics:

  * sub-strand labels lose their table numbering — "1.1.1 Polite Language -Euphemism" and
    "a) Extensive listening" are the design's row markers, and a student picking a lesson
    reads "Polite Language" far more easily than a code;
  * strand rows that repeat within a subject — the KICD junior Religious Education designs
    print "2.0 The Bible" for two different years — become one strand holding both years.

Nothing is invented: a subject with no transcribed design is simply absent from the output,
and the topic page then says the list is still being added.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "research" / "strands_round2.json"
OUT = ROOT / "data" / "curriculum" / "kicd_strands.json"

# research key -> the slug curriculum.py already uses for that subject.
SUBJECTS = {
    ("kjsea", "english"): "kj_english",
    ("kjsea", "kiswahili"): "kj_kiswahili",
    ("kjsea", "mathematics"): "kj_mathematics",
    ("kjsea", "religious_education"): "kj_religious_education",
    ("kcbe", "chemistry"): "kb_chemistry",
    ("kcbe", "biology"): "kb_biology",
    ("kcbe", "physics"): "kb_physics",
    ("kcbe", "mathematics"): "kb_mathematics",
    ("kcbe", "geography"): "kb_geography",
    ("kcbe", "history_and_citizenship"): "kb_history_and_citizenship",
    ("kcbe", "agriculture"): "kb_agriculture",
    ("kcbe", "home_science"): "kb_home_science",
    ("kcbe", "english"): "kb_english",
    ("kcbe", "kiswahili"): "kb_kiswahili",
}

# "1.1.1 ", "2.0 ", "a) " — the table's own row markers, not part of the name.
_CODE = re.compile(r"^(?:\d+(?:\.\d+)*\s+|[a-z]\)\s*)")
# A stray "( " with no closing bracket, or a trailing one, from a broken table cell.
_UNBALANCED = re.compile(r"\(\s*$")
# Provenance is written by hand in research/strands_round2.json and names files as the
# research saw them. They all live under research/ now, so a bare "designs/x.pdf" is
# rewritten to the path that actually resolves — but never inside a URL like
# ".../curriculum-designs/", which the lookbehind leaves alone.
_RESEARCHED = re.compile(r"(?<![\w/-])designs/")


def provenance(text):
    return _RESEARCHED.sub("research/designs/", text or "")


def label(text):
    clean = _CODE.sub("", (text or "").strip())
    clean = _UNBALANCED.sub(")", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -–—,;:")
    return clean


def build():
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    out, dropped = {}, []
    for (stage, key), slug in SUBJECTS.items():
        entry = raw.get(stage, {}).get(key)
        if not entry:
            dropped.append(slug)
            continue
        strands = {}
        for row in entry["strands"]:
            name = label(row["strand"]) or row["strand"]
            by_grade = strands.setdefault(name, {})
            for grade, subs in row["grades"].items():
                bucket = by_grade.setdefault(str(grade), [])
                for sub in subs:
                    text = label(sub)
                    # A name that survives stripping only as its own code ("" or a bare
                    # number) is a table artefact, not a topic a student could study.
                    if not text:
                        continue
                    if text not in bucket:
                        bucket.append(text)
        out[slug] = {"source": provenance(entry["source"]), "strands": strands}
    return out, dropped


def main():
    check = "--check" in sys.argv
    out, dropped = build()
    total = sum(len(subs) for v in out.values() for by_grade in v["strands"].values()
                for subs in by_grade.values())
    for slug, v in sorted(out.items()):
        n = sum(len(s) for by in v["strands"].values() for s in by.values())
        years = sorted({g for by in v["strands"].values() for g in by})
        print(f"{slug:28} {len(v['strands']):>3} strands  {n:>4} sub-strands  "
              f"years {'/'.join(years)}")
    if dropped:
        print("not transcribed:", ", ".join(sorted(dropped)))
    if check:
        print("--check: nothing written")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_by": "tools/build_cbc_strands.py",
                               "source": "research/strands_round2.json",
                               "subjects": out},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} — {total} sub-strands in {len(out)} subjects")


if __name__ == "__main__":
    main()
