// A scanner PDF can be 100 MB while a deployed request carries barely 4 MB, and the model
// itself refuses anything much over 20 MB. So the page re-encodes the scan here — pages are
// rasterised to images the tutor can read and packed back into a small PDF — before the
// upload starts. Nothing is sent twice and the original never leaves the browser.

const PDFJS_SRC = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_WORKER = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
const JSPDF_SRC = "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js";

// Best first: the loop takes the highest pass the budget fits, because a page has to stay
// readable to a tutor that reads handwriting. The first page of a pass is measured and used
// to reject a pass that cannot fit, so an ambitious pass costs one render, not twenty.
const PASSES = [
  { width: 2400, quality: 0.85 },
  { width: 2000, quality: 0.75 },
  { width: 1500, quality: 0.62 },
  { width: 1200, quality: 0.52 },
  { width: 900, quality: 0.42 },
];
const MAX_PAGES = 20;

const _libs = {};

function loadScript(src) {
  if (!_libs[src]) {
    _libs[src] = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("Could not load the scanner at " + src));
      document.head.appendChild(s);
    });
  }
  return _libs[src];
}

async function ensurePdfLibs() {
  if (!window.jspdf) await loadScript(JSPDF_SRC);
  if (!window.pdfjsLib) {
    await loadScript(PDFJS_SRC);
    pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
  }
}

function canvasToBlob(canvas, quality) {
  return new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", quality));
}

async function decode(file) {
  const url = URL.createObjectURL(file);
  try {
    return await createImageBitmap(await fetch(url).then(r => r.blob()));
  } finally {
    URL.revokeObjectURL(url);
  }
}

// One photo: scale it down to a size that still reads, then lower quality if needed.
async function shrinkImage(file, maxBytes) {
  const bitmap = await decode(file);
  for (const pass of PASSES) {
    const scale = Math.min(1, pass.width / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await canvasToBlob(canvas, pass.quality);
    if (blob && blob.size <= maxBytes) {
      bitmap.close();
      return { blob: new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg",
                               { type: "image/jpeg" }),
               note: null };
    }
  }
  bitmap.close();
  throw new Error("That photo could not be compressed enough to send.");
}

// A scanned paper: render every page to an image, then rebuild a much smaller PDF.
async function shrinkPdf(file, maxBytes, progress) {
  await ensurePdfLibs();
  const data = new Uint8Array(await file.arrayBuffer());
  const doc = await pdfjsLib.getDocument({ data }).promise;
  const pages = Math.min(doc.numPages, MAX_PAGES);
  const trimmed = doc.numPages > pages ? doc.numPages - pages : 0;

  let best = null;
  for (const pass of PASSES) {
    const { jsPDF } = window.jspdf;
    let out = null, bytes = 0, wontFit = false;
    for (let n = 1; n <= pages; n++) {
      progress(`Compressing page ${n} of ${pages}…`);
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: pass.width / base.width });
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(viewport.width);
      canvas.height = Math.round(viewport.height);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";                             // pages render onto transparency
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      await page.render({ canvasContext: ctx, viewport }).promise;
      const blob = await canvasToBlob(canvas, pass.quality);
      if (!blob) throw new Error("A page could not be re-encoded.");
      // The first page prices the whole pass: 12 pages at 400 KB each cannot fit 4 MB, and
      // there is no point rendering eleven more to find that out.
      if (n === 1 && blob.size * pages > maxBytes) { wontFit = true; break; }
      bytes += blob.size;
      if (bytes > maxBytes) { wontFit = true; break; }    // denser pages than the first one
      const url = await new Promise(res => { const r = new FileReader();
                                             r.onload = () => res(r.result);
                                             r.readAsDataURL(blob); });
      if (!out) {
        out = new jsPDF({ unit: "px", format: [canvas.width, canvas.height], orientation:
                         canvas.width > canvas.height ? "landscape" : "portrait" });
      } else {
        out.addPage([canvas.width, canvas.height]);
      }
      out.addImage(url, "JPEG", 0, 0, canvas.width, canvas.height);
    }
    if (wontFit || !out) continue;
    const packed = out.output("blob");
    if (packed.size > maxBytes) continue;   // the container itself tipped it over
    best = { blob: packed, note: trimmed, pass };
    break;                                  // the passes left below are only worse
  }
  if (!best) {
    throw new Error(`Even compressed, those ${pages} pages are still too large. ` +
                    "Try sending fewer pages at a time.");
  }
  return { blob: new File([best.blob], file.name.replace(/\.pdf$/i, "") + "-compressed.pdf",
                          { type: "application/pdf" }),
           note: best.note };
}

async function shrinkScan(file, maxBytes, progress) {
  if (file.size <= maxBytes) return { blob: file, note: 0 };
  progress(`Compressing your ${(file.size / 1048576).toFixed(1)} MB scan…`);
  if (/^image\/(png|jpe?g|webp|bmp)$/i.test(file.type)) return shrinkImage(file, maxBytes);
  if (file.type === "application/pdf") return shrinkPdf(file, maxBytes, progress);
  throw new Error("That type of file cannot be compressed in the browser. " +
                  "Upload a PDF or a photo.");
}
