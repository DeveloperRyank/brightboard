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
