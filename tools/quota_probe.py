"""Is today's LLM quota open, and if not, when does it reset?

    python tools/quota_probe.py

One tiny request to the chat provider the app is configured for, and nothing else: it
reports the model, the reply or the exact error, and any retry window the provider names.
Useful before a batch of lesson or practice-bank generation, when a spent free tier would
otherwise look like a bug in the app.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import llm  # noqa: E402

if not llm.GEMINI_API_KEY:
    print("no GEMINI_API_KEY in the environment")
    sys.exit(1)

print("model:", llm.GEMINI_MODEL)
url = f"{llm.GEMINI_BASE}/{llm.GEMINI_MODEL}:generateContent?key={llm.GEMINI_API_KEY}"
body = {"contents": [{"parts": [{"text": "Reply with the single word: ok"}]}],
        "generationConfig": {"maxOutputTokens": 5,
                             "thinkingConfig": {"thinkingBudget": llm.GEMINI_THINKING_BUDGET}}}
try:
    r = requests.post(url, json=body, timeout=45)
except Exception as exc:
    print("network error:", exc)
    sys.exit(1)

print("status:", r.status_code)
try:
    data = r.json()
except Exception:
    print(r.text[:400])
    sys.exit(0)
if r.status_code == 200:
    parts = data["candidates"][0]["content"]["parts"]
    print("reply:", "".join(p["text"] for p in parts if "text" in p))
    print("usage:", json.dumps(data.get("usageMetadata", {}))[:200])
else:
    err = data.get("error", {})
    print("message:", err.get("message"))
    print("status:", err.get("status"))
    for d in err.get("details") or []:
        print("detail:", json.dumps(d)[:500])
    print("retry hints:", re.findall(r"retry in ([0-9hms.]+)", r.text))
