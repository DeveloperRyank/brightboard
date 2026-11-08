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
