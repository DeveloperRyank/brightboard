// Some failures arrive as plain text instead of JSON — Vercel answers an oversized upload
// with "Request Entity Too Large", and a bare resp.json() on that throws a parse error the
// student cannot read. Everything goes through here so the text becomes the message.
async function readJSON(resp) {
  const text = await resp.text();
  try {
    return JSON.parse(text);
  } catch {
    return { error: text.trim().slice(0, 300) || `Request failed (${resp.status})` };
  }
}

// The server refuses any POST/PATCH/PUT/DELETE that does not carry this token back, which
// a form on someone else's site cannot read. base.html puts it in the page for the account
// currently signed in.
const CSRF_TOKEN =
  (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

function csrfHeaders(extra) {
  return Object.assign({}, extra, { "X-CSRF-Token": CSRF_TOKEN });
}

// ---------- 2.11: one submit, and a stop before anything irreversible ----------
//
// Forms here POST slowly — an account creation or a delete that is clicked twice
// either makes two accounts or destroys the wrong one. Only forms whose button carries
// data-once are touched, so the pages that talk to /api/ with their own busy state
// keep handling themselves.
document.addEventListener("submit", (ev) => {
  const form = ev.target;
  const btn = form.querySelector("[data-once]");
  if (!btn) return;
  const ask = form.dataset.confirm;
  if (ask && !window.confirm(ask)) { ev.preventDefault(); return; }
  if (form.dataset.submitting) { ev.preventDefault(); return; }
  form.dataset.submitting = "1";
  btn.setAttribute("data-busy", "true");
  btn.setAttribute("aria-busy", "true");
});

// Back-navigating from history would otherwise land on a form whose button is still spent.
window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[data-submitting]").forEach((f) => { delete f.dataset.submitting; });
  document.querySelectorAll("[data-busy]").forEach((b) => {
    b.removeAttribute("data-busy"); b.removeAttribute("aria-busy");
  });
});

// ---------- glyphs for markup that is built here rather than by a template ----------
//
// templates/icons.html is the dictionary the server renders; a string assembled in
// JS cannot call it, so the handful of glyphs those strings need are repeated here,
// with the same paths and the same stroke settings. Add a glyph to both or to neither.
const SVG_GLYPHS = {
  target: '<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3.4"/>',
  alert: '<path d="M12 4.4 20.8 19.6H3.2z"/><path d="M12 10v4.2"/><path d="M12 16.8v.4"/>',
  play: '<path d="M8.2 5.2 19 12 8.2 18.8z"/>',
  pause: '<path d="M9.2 5v14M14.8 5v14"/>',
};

function iconSvg(name) {
  const path = SVG_GLYPHS[name] || "";
  return `<svg class="ico" viewBox="0 0 24 24" width="1em" height="1em" fill="none" ` +
         `stroke="currentColor" stroke-width="1.6" stroke-linecap="round" ` +
         `stroke-linejoin="round" aria-hidden="true" focusable="false">${path}</svg>`;
}

// ---------- 2.5: cached content first, refreshed in the background ----------
//
// sessionStorage rather than localStorage: a lesson belongs to whoever is signed in on
// this tab, and a student's work should not sit on a shared family phone after the tab
// is closed. A full or blocked storage is not an error — it only costs the head start.
const CACHE_TTL = 6 * 3600 * 1000;

function cacheRead(key) {
  try {
    const hit = JSON.parse(sessionStorage.getItem("bb." + key) || "null");
    return hit && Date.now() - hit.at < CACHE_TTL ? hit.value : null;
  } catch { return null; }
}

function cacheWrite(key, value) {
  try {
    sessionStorage.setItem("bb." + key, JSON.stringify({ at: Date.now(), value }));
  } catch { /* nothing cached this time */ }
}

function cacheErase(key) {
  try { sessionStorage.removeItem("bb." + key); } catch { /* already gone */ }
}

// A signed-out page must not be sitting on a tab that still holds the previous
// student's lesson boards, so every guest page sweeps them out.
if (!CSRF_TOKEN) {
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith("bb.lesson.")) sessionStorage.removeItem(key);
    }
  } catch { /* storage blocked: nothing was cached anyway */ }
}
