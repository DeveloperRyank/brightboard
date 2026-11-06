"""Make a big, scanner-looking multi-page PDF plus a huge photo, as upload fixtures.

Usage: python make_big_scan.py [pages] [name-prefix]
"""
import io, os, random, sys
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))
FONTS = ["C:/Windows/Fonts/Candara.ttf", "C:/Windows/Fonts/arial.ttf",
         "C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/georgia.ttf"]

QUESTIONS = [
    "1. (a) State the type of reaction that takes place when propane is burnt in oxygen.",
    "   (b) Write a balanced chemical equation for the complete combustion of propane.",
    "2. The chart below shows the energy changes during a reaction. Use it to answer the",
    "   questions that follow: label the activation energy and the enthalpy change.",
    "3. 5.6 g of iron reacted with excess dilute hydrochloric acid. Calculate the volume of",
    "   gas produced at r.t.p. (Fe = 56.0, molar gas volume at r.t.p. = 24 dm3).",
    "4. Name the process used to separate: (a) ethanol from water; (b) iodine from sand;",
    "   (c) pall from a mixture of sand and pall. State what property each relies on.",
    "5. Define the term electrolyte. Describe what happens at each electrode when dilute",
    "   sulphuric acid is electrolysed using inert electrodes, with ionic equations.",
    "6. A student prepared a dry sample of potassium sulphate crystals in the laboratory.",
    "   Describe the procedure and explain why the method chosen is suitable.",
]


def paper(w, h):
    """Per-pixel grain, the way a scanner pad leaves it: this is what defeats compression."""
    noise = Image.frombytes("L", (w, h), os.urandom(w * h))
    return noise.point(lambda v: 232 + (v % 24)).convert("RGB")


def page(seed):
    random.seed(seed)
    w, h = 2480, 3508            # A4 at 300 dpi, the size a real scanner exports
    img = paper(w, h)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(random.choice(FONTS), 52)
    small = ImageFont.truetype(random.choice(FONTS), 40)
    d.text((130, 100), "KENYA NATIONAL EXAMINATIONS COUNCIL", font=small, fill=(40, 40, 40))
    d.text((130, 165), "CHEMISTRY - PAPER 1", font=small, fill=(40, 40, 40))
    d.line([(110, 230), (w - 110, 230)], fill=(30, 30, 30), width=3)
    y = 320
    for line in QUESTIONS:
        v = random.randint(15, 55)
        d.text((145, y), line, font=font, fill=(v, v, v))
        y += 80
    d.rectangle([145, y + 60, 1050, y + 240], outline=(35, 35, 35), width=3)
    for _ in range(420):                        # stray pen strokes and smudges
        x0, yy0 = random.randint(110, w - 160), random.randint(260, h - 160)
        v = random.randint(90, 170)
        d.line([(x0, yy0), (x0 + random.randint(-70, 70), yy0 + random.randint(-55, 55))],
               fill=(v, v, v), width=random.randint(1, 3))
    return img


PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 12
PREFIX = sys.argv[2] if len(sys.argv) > 2 else "scan_big"
photos = [page(i) for i in range(PAGES)]
buf = io.BytesIO()
photos[0].save(buf, save_all=True, append_images=photos[1:], format="PDF", resolution=300.0, quality=92)
pdf_path = os.path.join(OUT, PREFIX + ".pdf")
open(pdf_path, "wb").write(buf.getvalue())

jpg = io.BytesIO()
page(99).save(jpg, format="JPEG", quality=98)
jpg_path = os.path.join(OUT, PREFIX + "_photo.jpg")
open(jpg_path, "wb").write(jpg.getvalue())

for p in (pdf_path, jpg_path):
    print(os.path.basename(p), round(os.path.getsize(p) / 1048576, 2), "MB")
