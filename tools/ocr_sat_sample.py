"""Step 2 of the SAT style reference: ask Gemini vision about the PNGs produced by
tools/render_sat_pages.py and save the structure it reports. Reference only - no item
from the scanned paper is shipped into the bank verbatim.

    py -V:3.15 tools/render_sat_pages.py
    py -V:3.14 tools/ocr_sat_sample.py
"""
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm

PAGES = Path(__file__).resolve().parent.parent / "research" / "sat_pages"
OUT = Path(__file__).resolve().parent.parent / "research" / "sat_rw_samples.json"
PER_CALL = 4

SYSTEM = (
    "You are an SAT Reading and Writing analyst examining scanned pages from a real SAT "
    "practice document. Report the STRUCTURE of the items you can see: what each question "
    "asks for, how the four options are built, and what makes the wrong options wrong."
)
USER = (
    'For every complete multiple-choice question visible in these pages return JSON '
    '{"items": [{"domain": one of "Information and Ideas" | "Craft and Structure" | '
    '"Expression of Ideas" | "Standard English Conventions" | "other", '
    '"stem_pattern": one line on how the question is worded and what it asks for, '
    '"passage_type": e.g. literary narrative, history/social studies, science, data '
    'table or graph, single or dual passage, '
    '"option_shape": how A-D are built (full sentences / single words / punctuation or '
    'conventions edits / transitions), '
    '"trap": the pattern that makes a distractor tempting, '
    '"difficulty": easy | medium | hard, '
    '"stem_quote": the question stem as printed, for register only}],'
    ' "notes": "two or three sentences on overall tone, passage length and timing"}.'
    ' If a page holds no complete question, say so in "notes" and return an empty list.'
)


def blob(paths):
    """Bundle the page images into one lightweight PDF so a call carries a whole set."""
    imgs = [Image.open(p).convert("RGB") for p in paths]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return base64.b64encode(buf.getvalue()).decode()


def main():
    paths = sorted(PAGES.glob("page*.png"))
    if not paths:
        sys.exit("no PNGs in %s - run tools/render_sat_pages.py first" % PAGES)
    print(f"{len(paths)} page images, {PER_CALL} per call")
    items, notes = [], []
    for start in range(0, len(paths), PER_CALL):
        chunk = paths[start:start + PER_CALL]
        names = [p.stem for p in chunk]
        data = blob(chunk)
        print(f"  {names} -> {len(data) // 1024} KB, asking Gemini...", flush=True)
        try:
            got = llm.chat_json_with_file(
                SYSTEM, USER, {"mime": "application/pdf", "data_b64": data},
                temperature=0.1, max_tokens=6000,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    failed: {exc}")
            continue
        found = got.get("items", [])
        for it in found:
            it["source_pages"] = names
        items.extend(found)
        if got.get("notes"):
            notes.append({"pages": names, "notes": got["notes"]})
        print(f"    {len(found)} items")
    OUT.write_text(json.dumps({"items": items, "notes": notes,
                               "pages_used": [p.stem for p in paths]}, indent=1),
                   encoding="utf-8")
    print(f"wrote {len(items)} items -> {OUT}")


if __name__ == "__main__":
    main()
