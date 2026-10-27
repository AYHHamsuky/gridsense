"""Day 1 check: one Muse Spark call, one full agent triage, optional voice note.

Usage:
  python scripts/smoke_test.py               # chat + agent
  python scripts/smoke_test.py note.wav      # also transcribe a WAV voice note
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import agent, meta_client  # noqa: E402
from backend.app.config import AGENT_MODEL  # noqa: E402
from backend.app.store import store  # noqa: E402

c = meta_client.chat_client()
r = c.chat.completions.create(model=AGENT_MODEL, messages=[
    {"role": "user", "content": "Translate to English: 'Babu wuta a Barnawa tun safe.'"}])
print("1) Muse Spark says:", r.choices[0].message.content, "\n")

print("2) Agent triage of a Hausa report:")
res = agent.triage({"id": "RPT-SMOKE1", "text": "Transformer ya kone kusa da Kawo bridge, akwai hayaki."})
print(json.dumps(res, indent=2, ensure_ascii=False))
print("\nTool calls:")
for e in store.events:
    if e["kind"] == "tool":
        print(" -", e["detail"]["name"], e["detail"]["args"])

if len(sys.argv) > 1:
    print("\n3) Transcribing", sys.argv[1])
    print(json.dumps(meta_client.transcribe(Path(sys.argv[1]).read_bytes()), indent=2, ensure_ascii=False))
