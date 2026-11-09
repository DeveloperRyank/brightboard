"""Turn a KCSE notes booklet into the files that unlock a subject's topics.

A lesson needs something true to teach from. For Physics that is no longer a question
bank — it is the notes booklet a teacher would hand out, supplied as a PDF and split
here into one file per topic at data/<subject>/notes/<topic_slug>.md. notes.py reads
those files, and a topic with notes behind it is a topic a student can open.

The booklet is read by its own structure, not by matching topic names: a "FORM TWO"
banner restarts the chapter count, so (form, chapter number) is what identifies a
topic. Display names come from curriculum.py, which is therefore the only place a topic
is ever named.

    python tools/build_physics_notes.py --pdf <booklet.pdf>        # write the notes
    python tools/build_physics_notes.py --text notes.txt --check   # report, write nothing

--pdf needs PySide6, which is a local thing: the server never reads a PDF. --text takes
a dump of the same text, one "===== PAGE n =====" marker per page, for when the
extraction has already been done.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import curriculum  # noqa: E402

# Which chapter of which form is which topic, read off the booklet's own headings.
# Every slug here has to exist in curriculum.py.
CHAPTERS = {
    (1, 1): "intro_to_physics",
    (1, 2): "measurement_i",
    (1, 3): "forces",
    (1, 4): "pressure",
    (1, 5): "particulate_nature_of_matter",
    (1, 6): "thermal_expansion",
    (1, 7): "heat_transfer",
    (1, 8): "rectilinear_propagation",
    (1, 9): "electrostatics_i",
    (1, 10): "cells_and_simple_circuits",
    (2, 1): "magnetism",
    (2, 2): "measurement_ii",
    (2, 3): "turning_effect_of_a_force",
    (2, 4): "equilibrium_and_centre_of_gravity",
    (2, 5): "reflection_at_curved_surfaces",
    (2, 6): "magnetic_effect_of_current",
    (2, 7): "hookes_law",
    (2, 8): "waves_i",
    (2, 9): "sound",
    (2, 10): "fluid_flow",
    (3, 1): "linear_motion",
    (3, 2): "refraction_of_light",
    (3, 3): "newtons_laws",
    (3, 4): "energy_work_power_machines",
    (3, 5): "current_electricity",
    (3, 6): "waves_ii",
    (3, 7): "electrostatics_ii",
    (3, 8): "heating_effect_of_current",
    (3, 9): "quantity_of_heat",
    (3, 10): "gas_laws",
    (4, 1): "thin_lenses",
    (4, 2): "uniform_circular_motion",
    (4, 3): "floating_and_sinking",
    (4, 4): "electromagnetic_spectrum",
    (4, 5): "electromagnetic_induction",
    (4, 6): "mains_electricity",
    (4, 7): "cathode_rays",
    (4, 8): "x_rays",
    (4, 9): "photoelectric_effect",
    (4, 10): "radioactivity",
    (4, 11): "electronics",
}

# A syllabus topic the booklet does not give its own chapter: it is a sub-heading inside
# one. The chapter is cut at that heading, so both topics get their own notes instead of
# one file standing in for two lessons.
SECTIONS = {
    (3, 7): ("capacitors", "Capacitors and capacitance"),
}

WORD_NUMBERS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6,
                "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "ELEVEN": 11,
                "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14, "FIFTEEN": 15}

PAGE_RE = re.compile(r"^===== PAGE (\d+) =====$")
FORM_RE = re.compile(r"^(?:PHYSICS )?FORM (ONE|TWO|THREE|FOUR)(?: NOTES)?$")
CHAPTER_RE = re.compile(r"^CHAPTER ([A-Z]+)$")
FOOTER_RE = re.compile(r"^For free .*Visit https?://\S+$")
# 1. / 1) / a) / ii) — the way these notes head a list, and a reason not to join lines.
LIST_RE = re.compile(r"^(\d{1,3}[\.\)]|[a-zA-Z]{1,3}[\.\)])\s?")
ENDS_SENTENCE = (".", "!", "?", ":", ";", ",", ")")
# A line that stops on one of these did not stop. They are the words a printed page can
# break after but a sentence cannot.
CUT_WORDS = {"a", "an", "and", "as", "at", "be", "but", "by", "for", "from", "had", "has",
             "have", "if", "in", "into", "is", "it", "its", "may", "no", "not", "of", "on",
             "or", "over", "per", "re", "should", "since", "so", "such", "than", "that",
             "the", "their", "them", "then", "to", "under", "up", "was", "were", "which",
             "will", "with", "would"}
# A printed line this long had reached the margin, so its sentence carries on below.
WRAPPED = 55
# A printed line this long had reached the margin, so its sentence carries on below.
WRAPPED = 55


def extract_pdf(path):
    """Page-by-page text, from the PDF's own text layer — no OCR, so a scanned booklet
    would have to be transcribed before anything here could read it."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtPdf import QPdfDocument

    QCoreApplication.instance() or QCoreApplication(sys.argv)
    doc = QPdfDocument()
    status = doc.load(str(path))
    if int(status) != 0:
        raise SystemExit(f"{path}: could not be opened as a PDF (status {status})")
    pages = [(doc.getAllText(i).text() or "") for i in range(doc.pageCount())]
    if sum(len(p) for p in pages) < 100 * len(pages):
        raise SystemExit(f"{path}: almost no text layer — this booklet looks scanned.")
    return "".join(f"===== PAGE {i + 1} =====\n{p}" for i, p in enumerate(pages))


def clean_lines(raw):
    """The page furniture out, the text left: [(page, line)] as it was printed, so a
    paragraph is still broken where the page broke it."""
    out, page = [], None
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = PAGE_RE.match(line.strip())
        if m:
            page = int(m.group(1))
            continue
        s = " ".join(line.split())
        if not s or FOOTER_RE.match(s) or re.fullmatch(r"\d{1,4}", s):
            continue
        out.append((page, s))
    return out


def paragraphs(lines):
    """Re-flow what the page broke. A printed line is joined to the one under it when it
    clearly did not finish there: it ran to the margin, or it ends on a word that cannot
    end a sentence, or the line below starts in lower case. A sentence that did finish is
    left alone, so headings, table rows, worked examples and list items stay on their own
    — a lesson has to be able to stop at one."""
    blocks = []
    for _, s in lines:
        prev = blocks[-1][-1] if blocks else None
        joined = (prev is not None
                  and not prev.endswith(ENDS_SENTENCE)
                  and not LIST_RE.match(s)
                  and (len(prev) >= WRAPPED or s[0].islower() or s[0] in ")]-–—/"
                       or prev.split()[-1].lower() in CUT_WORDS))
        if joined:
            blocks[-1].append(s)
        else:
            blocks.append([s])
    return [" ".join(b) for b in blocks]


def split_chapters(lines):
    """Every CHAPTER heading in booklet order, with the body it owns, its running title
    and the pages it spans."""
    marks, form = [], 1
    for i, (_, s) in enumerate(lines):
        m = FORM_RE.match(s)
        if m:
            form = WORD_NUMBERS[m.group(1)]
            continue
        m = CHAPTER_RE.match(s)
        if not m or m.group(1) not in WORD_NUMBERS:
            continue
        title, j = [], i + 1
        while j < len(lines):
            t = lines[j][1]
            if CHAPTER_RE.match(t) or FORM_RE.match(t) or len(t) > 80 or not t.isupper():
                break
            title.append(t)
            j += 1
        marks.append({"i": i, "body": j, "form": form,
                      "number": WORD_NUMBERS[m.group(1)], "title": " ".join(title)})
    chapters = []
    for k, mk in enumerate(marks):
        stop = marks[k + 1]["i"] if k + 1 < len(marks) else len(lines)
        span = lines[mk["i"]:stop]
        # The banner that opens the next form prints at the foot of the last chapter of
        # this one; it belongs to the form, not to the text.
        body = lines[mk["body"]:stop]
        while body and FORM_RE.match(body[-1][1]):
            body.pop()
        chapters.append({"form": mk["form"], "number": mk["number"], "title": mk["title"],
                         "pages": sorted({p for p, _ in span if p}), "lines": body})
    return chapters


def tidy(text):
    """Print artefacts the text layer leaves behind: a superscript that broke onto its
    own line, a bracket the extractor drifted away from its number."""
    return re.sub(r"([A-Za-z0-9]) ([)\]])", r"\1\2", re.sub(r"[ \t]{2,}", " ", text)).strip()


def build(subject, raw, source_label):
    lines = clean_lines(raw)
    by_slug = {t["slug"]: t for t in curriculum.topics(subject)}
    files, used = {}, set()
    for ch in split_chapters(lines):
        slug = CHAPTERS.get((ch["form"], ch["number"]))
        if slug is None:
            raise SystemExit(f"booklet chapter with no topic: Form {ch['form']} "
                             f"chapter {ch['number']} ({ch['title']})")
        topic = by_slug.get(slug)
        if topic is None:
            raise SystemExit(f"{slug} is in this tool but not in curriculum.py")
        used.add(slug)
        blocks = [tidy(p) for p in paragraphs(ch["lines"])]
        parts = [(slug, topic["name"], blocks, "")]
        section = SECTIONS.get((ch["form"], ch["number"]))
        if section:
            cut, heading = section
            if cut not in by_slug:
                raise SystemExit(f"{cut} is in this tool but not in curriculum.py")
            # Only a short line can be a heading: the same words inside a sentence are
            # not permission to cut the chapter in half.
            i = next((n for n, b in enumerate(blocks)
                      if b.lower().startswith(heading.lower()) and len(b) <= len(heading) + 25), None)
            if i is None:
                raise SystemExit(f"{slug}: no sub-heading {heading!r} to cut at")
            parts = [(slug, topic["name"], blocks[:i], f" up to \u201c{heading}\u201d"),
                     (cut, by_slug[cut]["name"], blocks[i:], f" from \u201c{heading}\u201d")]
            used.add(cut)
        pages = ch["pages"]
        for part_slug, name, part_blocks, span_note in parts:
            body = "\n\n".join(part_blocks)
            head = (f"<!-- Brightboard subject notes. {source_label}\n"
                    f"     Form {ch['form']}, chapter {ch['number']}"
                    + (f", pages {pages[0]}-{pages[-1]}" if pages else "")
                    + f"{span_note}.\n"
                    "     Built by tools/build_physics_notes.py — rebuild it, do not edit this file. -->\n\n"
                    f"# {name}\n\n")
            files[part_slug] = head + body + "\n"
    missing = [s for s in by_slug if s not in used]
    return files, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf")
    ap.add_argument("--text", help="a dump of the PDF text, one PAGE marker per page")
    ap.add_argument("--subject", default="physics")
    ap.add_argument("--check", action="store_true", help="report without writing")
    args = ap.parse_args()

    if args.pdf:
        raw, label = extract_pdf(Path(args.pdf)), f"Transcribed from {Path(args.pdf).name}."
    elif args.text:
        raw = Path(args.text).read_text(encoding="utf-8", errors="replace")
        label = f"Transcribed from {Path(args.text).name}, dumped from the PDF."
    else:
        raise SystemExit("give --pdf or --text")

    files, missing = build(args.subject, raw, label)
    dest = ROOT / "data" / args.subject / "notes"
    for slug, text in sorted(files.items()):
        print(f"  {slug:<32}{len(text):>8} chars")
        if not args.check:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{slug}.md").write_text(text, encoding="utf-8")
    where = f"data/{args.subject}/notes" + ("" if not args.check else " (not written, --check)")
    print(f"{len(files)} topic note files -> {where}, "
          f"{sum(len(t) for t in files.values()):,} chars")
    if missing:
        print(f"topics with no notes: {', '.join(sorted(missing))}")


if __name__ == "__main__":
    main()
